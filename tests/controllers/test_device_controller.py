import builtins
import io
import json
import types
import sys
from pathlib import Path

import pytest


# --- Helpers -----------------------------------------------------------------

def _install_fake_hw_modules(monkeypatch):
    """Install fake hardware modules so importing device.py does not require real hw libs."""
    # linuxpy.video.device with iter_video_capture_devices
    linuxpy = types.ModuleType("linuxpy")
    linuxpy_video = types.ModuleType("linuxpy.video")
    linuxpy_video_device = types.ModuleType("linuxpy.video.device")

    def _iter_video_capture_devices():
        class Dummy:
            def __init__(self):
                self.info = types.SimpleNamespace(card="dummy",)
                self.filename = "/dev/video0"

            def open(self):
                pass

            def close(self):
                pass
        return []  # return empty list to avoid side effects

    linuxpy_video_device.iter_video_capture_devices = _iter_video_capture_devices

    # gphoto2
    gphoto2 = types.ModuleType("gphoto2")

    class _GPCamera:
        @staticmethod
        def autodetect():
            return []

    gphoto2.Camera = _GPCamera

    # picamera2
    picamera2 = types.ModuleType("picamera2")

    class _PiCam:
        def __init__(self):
            self.camera_properties = {"Model": "imx519", "Location": "0"}

        def close(self):
            pass

    picamera2.Picamera2 = _PiCam

    monkeypatch.setitem(sys.modules, "linuxpy", linuxpy)
    monkeypatch.setitem(sys.modules, "linuxpy.video", linuxpy_video)
    monkeypatch.setitem(sys.modules, "linuxpy.video.device", linuxpy_video_device)
    monkeypatch.setitem(sys.modules, "gphoto2", gphoto2)
    monkeypatch.setitem(sys.modules, "picamera2", picamera2)


def _import_device(monkeypatch):
    _install_fake_hw_modules(monkeypatch)
    import importlib
    device = importlib.import_module("openscan.controllers.device")
    return device


# --- Tests -------------------------------------------------------------------

@pytest.fixture
def device_module(monkeypatch):
    return _import_device(monkeypatch)


def test_save_device_config_writes_json(tmp_path, monkeypatch,
                                        motor_model_instance,
                                        light_model_instance,
                                        ):
    device = _import_device(monkeypatch)

    # Redirect DEVICE_CONFIG_FILE to a temp file
    tmp_file = tmp_path / "device_config.json"
    monkeypatch.setattr(device, "DEVICE_CONFIG_FILE", tmp_file)

    # Build a minimal ScannerDevice model with settings
    from openscan.models.scanner import ScannerDevice
    from openscan.models.camera import Camera, CameraType
    from openscan.models.motor import Motor
    from openscan.models.light import Light
    from openscan.config.camera import CameraSettings
    from openscan.config.motor import MotorConfig
    from openscan.config.light import LightConfig

    cam = Camera(name="cam1", type=CameraType.PICAMERA2, path="/dev/video0", settings=CameraSettings(shutter=123))
    motor = motor_model_instance
    light = light_model_instance

    scanner = ScannerDevice(
        name="TestDevice",
        model=None,
        shield=None,
        cameras={"cam1": cam},
        motors={"rotor": motor},
        lights={"ring": light},
        endstops={},
        initialized=True,
    )

    # Patch module-level _scanner_device
    monkeypatch.setattr(device, "_scanner_device", scanner, raising=True)

    ok = device.save_device_config()
    assert ok is True
    assert tmp_file.exists()

    data = json.loads(tmp_file.read_text())
    assert data["name"] == "TestDevice"
    assert "cam1" in data["cameras"]
    assert isinstance(data["cameras"]["cam1"], dict)
    assert data["cameras"]["cam1"]["settings"].get("shutter") == 123
    # motors/lights saved as settings
    assert data["motors"]["rotor"].get("max_speed") == 7500
    assert data["lights"]["ring"].get("pins") == [1, 2]


def test_get_device_info_uses_controller_status(monkeypatch):
    device = _import_device(monkeypatch)

    class DummyCtrl:
        def __init__(self, name):
            self._name = name
        def get_status(self):
            return {"name": self._name, "ok": True}

    monkeypatch.setattr(device, "get_all_camera_controllers", lambda: {"cam": DummyCtrl("cam")})
    monkeypatch.setattr(device, "get_all_motor_controllers", lambda: {"rotor": DummyCtrl("rotor")})
    monkeypatch.setattr(device, "get_all_light_controllers", lambda: {"ring": DummyCtrl("ring")})

    # also provide a simple _scanner_device for name/model/shield fields
    from openscan.models.scanner import ScannerDevice
    dev = ScannerDevice(name="X", model=None, shield=None, cameras={}, motors={}, lights={}, endstops={}, initialized=True)
    monkeypatch.setattr(device, "_scanner_device", dev, raising=True)

    info = device.get_device_info()
    assert info["name"] == "X"
    assert "cam" in info["cameras"] and info["cameras"]["cam"]["ok"] is True
    assert "rotor" in info["motors"] and info["motors"]["rotor"]["ok"] is True
    assert "ring" in info["lights"] and info["lights"]["ring"]["ok"] is True


def test_set_device_config_calls_initialize(monkeypatch):
    device = _import_device(monkeypatch)

    called = {}

    def fake_load(path=None):
        called["load"] = path or True
        return {"name": "Y", "model": None, "shield": None, "cameras": {}, "motors": {}, "lights": {}, "endstops": {}}

    def fake_init(cfg):
        called["init"] = cfg

    monkeypatch.setattr(device, "load_device_config", fake_load)
    monkeypatch.setattr(device, "initialize", fake_init)

    ok = device.set_device_config("/tmp/some.json")
    assert ok is True
    assert called.get("load") == "/tmp/some.json"
    assert isinstance(called.get("init"), dict)


def test_reboot_and_shutdown_call_system(monkeypatch):
    device = _import_device(monkeypatch)

    sys_calls = []

    def fake_system(cmd):
        sys_calls.append(cmd)
        return 0

    monkeypatch.setattr(device.os, "system", fake_system)
    monkeypatch.setattr(device, "save_device_config", lambda: True)

    device.reboot(with_saving=True)
    device.shutdown(with_saving=True)

    assert any("reboot" in c for c in sys_calls)
    assert any("shutdown" in c for c in sys_calls)


def test_get_available_configs_lists_jsons(monkeypatch, tmp_path):
    device = _import_device(monkeypatch)

    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()

    valid = settings_dir / "a.json"
    invalid = settings_dir / "c.json"
    ignored = settings_dir / "b.txt"

    valid.write_text(json.dumps({"name": "DevA", "model": "M1", "shield": "S1"}))
    invalid.write_text("{invalid}")
    ignored.write_text("not json")

    monkeypatch.setenv("OPENSCAN_SETTINGS_DIR", str(settings_dir))

    class _EmptyPackage:
        def iterdir(self):
            return []

    monkeypatch.setattr(device.resources, "files", lambda *_: _EmptyPackage())

    configs = device.get_available_configs()
    local_configs = [c for c in configs if c.get("path", "").startswith(str(settings_dir))]

    assert len(local_configs) == 2

    indexed = {c["filename"]: c for c in local_configs}

    assert indexed["a.json"].get("name") == "DevA"
    assert "name" not in indexed["c.json"]


def test_cleanup_and_exit_calls_cleanup(monkeypatch):
    device = _import_device(monkeypatch)

    called = {"cleanup": False, "pins": False}

    class DummyCam:
        def cleanup(self):
            called["cleanup"] = True

    monkeypatch.setattr(device, "get_all_camera_controllers", lambda: {"cam": DummyCam()})

    def fake_cleanup_all_pins():
        called["pins"] = True

    monkeypatch.setattr(device, "cleanup_all_pins", fake_cleanup_all_pins)

    device.cleanup_and_exit()

    assert called["cleanup"] is True
    assert called["pins"] is True
