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
    device = importlib.import_module("openscan_firmware.controllers.device")
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
    from openscan_firmware.models.scanner import ScannerDevice
    from openscan_firmware.models.camera import Camera, CameraType
    from openscan_firmware.models.motor import Motor
    from openscan_firmware.models.light import Light
    from openscan_firmware.config.camera import CameraSettings
    from openscan_firmware.config.motor import MotorConfig
    from openscan_firmware.config.light import LightConfig

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
    from openscan_firmware.models.scanner import ScannerDevice
    dev = ScannerDevice(name="X", model=None, shield=None, cameras={}, motors={}, lights={}, endstops={}, initialized=True)
    monkeypatch.setattr(device, "_scanner_device", dev, raising=True)

    info = device.get_device_info()
    assert info["name"] == "X"
    assert "cam" in info["cameras"] and info["cameras"]["cam"]["ok"] is True
    assert "rotor" in info["motors"] and info["motors"]["rotor"]["ok"] is True
    assert "ring" in info["lights"] and info["lights"]["ring"]["ok"] is True


@pytest.mark.asyncio
async def test_set_device_config_calls_initialize(monkeypatch):
    device = _import_device(monkeypatch)

    called = {}

    def fake_load(path=None):
        called["load"] = path or True
        return {"name": "Y", "model": None, "shield": None, "cameras": {}, "motors": {}, "lights": {}, "endstops": {}}

    async def fake_init(cfg, detect_cameras=False):
        called["init"] = cfg

    monkeypatch.setattr(device, "load_device_config", fake_load)
    monkeypatch.setattr(device, "initialize", fake_init)

    ok = await device.set_device_config("/tmp/some.json")
    assert ok is True
    assert called.get("load") == "/tmp/some.json"
    assert isinstance(called.get("init"), dict)


@pytest.mark.asyncio
async def test_set_device_config_persists_loaded_config(monkeypatch, tmp_path):
    device = _import_device(monkeypatch)

    config_file = tmp_path / "device_config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(device, "DEVICE_CONFIG_FILE", config_file, raising=True)

    preset = tmp_path / "preset.json"
    preset.write_text(json.dumps({
        "name": "Preset",
        "model": "mini",
        "shield": "greenshield",
        "cameras": {},
        "motors": {},
        "lights": {},
        "endstops": {},
        "motors_timeout": 5.0,
        "startup_mode": device.ScannerStartupMode.STARTUP_IDLE.value,
        "calibrate_mode": device.ScannerCalibrateMode.CALIBRATE_ON_WAKE.value,
    }))

    async def fake_initialize(config, detect_cameras=False):
        device._scanner_device = device.ScannerDevice(
            name=config["name"],
            model=None,
            shield=None,
            cameras={},
            motors={},
            lights={},
            endstops={},
            motors_timeout=config["motors_timeout"],
            startup_mode=device.ScannerStartupMode(config["startup_mode"]),
            calibrate_mode=device.ScannerCalibrateMode(config["calibrate_mode"]),
        )
        device._scanner_device._initialized = True

    monkeypatch.setattr(device, "initialize", fake_initialize, raising=True)

    ok = await device.set_device_config(str(preset))
    assert ok is True

    persisted = json.loads(config_file.read_text())
    assert persisted["name"] == "Preset"
    assert persisted["motors_timeout"] == 5.0
    assert persisted["startup_mode"] == device.ScannerStartupMode.STARTUP_IDLE.value
    assert persisted["calibrate_mode"] == device.ScannerCalibrateMode.CALIBRATE_ON_WAKE.value


def _write_minimal_preset(target: Path):
    content = {
        "name": "Preset",
        "model": "mini",
        "shield": "greenshield",
        "cameras": {},
        "motors": {},
        "lights": {},
        "endstops": {},
    }
    target.write_text(json.dumps(content))


def test_load_device_config_ignores_existing_scanner_state(monkeypatch, tmp_path):
    device = _import_device(monkeypatch)

    config_path = tmp_path / "device_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(device, "DEVICE_CONFIG_FILE", config_path)

    preset = tmp_path / "preset.json"
    _write_minimal_preset(preset)

    device._scanner_device.motors_timeout = 180.0
    device._scanner_device.startup_mode = device.ScannerStartupMode.STARTUP_IDLE
    device._scanner_device.calibrate_mode = device.ScannerCalibrateMode.CALIBRATE_ON_WAKE

    loaded = device.load_device_config(str(preset))

    assert loaded["motors_timeout"] == 0.0
    assert loaded["startup_mode"] == device.ScannerStartupMode.STARTUP_ENABLED.value
    assert loaded["calibrate_mode"] == device.ScannerCalibrateMode.CALIBRATE_MANUAL.value


def test_load_device_config_overwrites_persisted_values(monkeypatch, tmp_path):
    device = _import_device(monkeypatch)

    config_path = tmp_path / "device_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(device, "DEVICE_CONFIG_FILE", config_path)

    config_path.write_text(json.dumps({
        "name": "Custom",
        "model": "custom",
        "shield": "custom",
        "cameras": {},
        "motors": {},
        "lights": {},
        "endstops": {},
        "motors_timeout": 999.0,
        "startup_mode": device.ScannerStartupMode.STARTUP_IDLE.value,
        "calibrate_mode": device.ScannerCalibrateMode.CALIBRATE_ON_WAKE.value,
    }))

    preset = tmp_path / "preset.json"
    _write_minimal_preset(preset)

    loaded = device.load_device_config(str(preset))

    persisted = json.loads(config_path.read_text())
    for cfg in (loaded, persisted):
        assert cfg["motors_timeout"] == 0.0
        assert cfg["startup_mode"] == device.ScannerStartupMode.STARTUP_ENABLED.value
        assert cfg["calibrate_mode"] == device.ScannerCalibrateMode.CALIBRATE_MANUAL.value


@pytest.mark.asyncio
async def test_initialize_recreates_controllers_on_reinitialize(monkeypatch, tmp_path):
    device = _import_device(monkeypatch)

    # fresh scanner state and redirected config path
    monkeypatch.setattr(device, "_scanner_device", device._create_default_scanner_device(), raising=True)
    config_path = tmp_path / "device_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(device, "DEVICE_CONFIG_FILE", config_path, raising=True)

    config_payload = {
        "name": "Preset",
        "model": "mini",
        "shield": "greenshield",
        "cameras": {},
        "motors": {
            "rotor": {
                "direction_pin": 5,
                "enable_pin": 23,
                "step_pin": 6,
                "acceleration": 20000,
                "max_speed": 5000,
                "direction": 1,
                "steps_per_rotation": 42667,
                "min_angle": 0,
                "max_angle": 360,
                "home_angle": 90,
            },
            "turntable": {
                "direction_pin": 9,
                "enable_pin": 22,
                "step_pin": 11,
                "acceleration": 5000,
                "max_speed": 5000,
                "direction": 1,
                "steps_per_rotation": 3200,
                "min_angle": 0,
                "max_angle": 360,
                "home_angle": 0,
            },
        },
        "lights": {
            "ring": {
                "pins": [17, 27],
                "pwm_support": False,
            }
        },
        "endstops": {},
        "motors_timeout": 0.0,
        "startup_mode": device.ScannerStartupMode.STARTUP_ENABLED.value,
        "calibrate_mode": device.ScannerCalibrateMode.CALIBRATE_MANUAL.value,
    }
    config_path.write_text(json.dumps(config_payload))

    controllers = {"motors": {}, "lights": {}}
    creation_log = {"motors": [], "lights": []}
    removal_log = {"motors": [], "lights": []}

    class DummyMotorController:
        def __init__(self, model):
            self.model = model
            self.angle = getattr(model, "angle", 0.0)

        def set_idle_callbacks(self, *_, **__):
            return None

        def refresh(self):
            return None

        def get_status(self):
            return {
                "name": self.model.name,
                "angle": self.model.angle,
                "busy": False,
                "target_angle": None,
                "settings": self.model.settings,
                "endstop": None,
            }

    class DummyLightController:
        def __init__(self, model):
            self.model = model

        def set_idle_callbacks(self, *_, **__):
            return None

        def refresh(self):
            return None

        async def turn_on(self):
            return None

        def get_status(self):
            settings = self.model.settings
            payload = settings.model_dump() if hasattr(settings, "model_dump") else {}
            return {"name": self.model.name, "is_on": False, "settings": payload}

    def _create_motor_controller(motor):
        controller = DummyMotorController(motor)
        controllers["motors"][motor.name] = controller
        creation_log["motors"].append(motor.name)
        return controller

    def _remove_motor_controller(name):
        removal_log["motors"].append(name)
        controllers["motors"].pop(name, None)
        return True

    def _create_light_controller(light):
        controller = DummyLightController(light)
        controllers["lights"][light.name] = controller
        creation_log["lights"].append(light.name)
        return controller

    def _remove_light_controller(name):
        removal_log["lights"].append(name)
        controllers["lights"].pop(name, None)
        return True

    monkeypatch.setattr(device, "create_motor_controller", _create_motor_controller, raising=True)
    monkeypatch.setattr(device, "remove_motor_controller", _remove_motor_controller, raising=True)
    monkeypatch.setattr(device, "get_all_motor_controllers", lambda: controllers["motors"].copy(), raising=True)

    monkeypatch.setattr(device, "create_light_controller", _create_light_controller, raising=True)
    monkeypatch.setattr(device, "remove_light_controller", _remove_light_controller, raising=True)
    monkeypatch.setattr(device, "get_all_light_controllers", lambda: controllers["lights"].copy(), raising=True)

    monkeypatch.setattr(device, "create_camera_controller", lambda *_, **__: None, raising=True)
    monkeypatch.setattr(device, "remove_camera_controller", lambda *_: True, raising=True)
    monkeypatch.setattr(device, "get_all_camera_controllers", lambda: {}, raising=True)
    monkeypatch.setattr(device, "get_available_camera_types", lambda: {}, raising=True)
    monkeypatch.setattr(device, "_detect_cameras", lambda: {}, raising=True)

    dummy_timer = types.SimpleNamespace(
        set_timeout=lambda *_: None,
        enable=lambda: None,
        disable=lambda: None,
        start=lambda: None,
        stop=lambda: None,
        reset=lambda: None,
        on_timeout=None,
    )
    monkeypatch.setattr(device, "inactivity_timer", dummy_timer, raising=True)
    monkeypatch.setattr(device, "cleanup_all_pins", lambda: None, raising=True)
    monkeypatch.setattr(device, "schedule_device_status_broadcast", lambda *_, **__: None, raising=True)
    monkeypatch.setattr(device, "get_project_manager", lambda: types.SimpleNamespace(), raising=True)
    monkeypatch.setattr(device, "load_persistent_cloud_settings", lambda: None, raising=True)
    monkeypatch.setattr(device, "load_cloud_settings_from_env", lambda: None, raising=True)
    monkeypatch.setattr(device, "set_cloud_settings", lambda *_: None, raising=True)
    monkeypatch.setattr(device, "set_active_source", lambda *_: None, raising=True)

    await device.initialize(config=config_payload, detect_cameras=False)

    assert creation_log["motors"] == ["rotor", "turntable"]
    assert creation_log["lights"] == ["ring"]
    first_status = device.get_device_info()
    assert set(first_status["motors"].keys()) == {"rotor", "turntable"}
    assert set(first_status["lights"].keys()) == {"ring"}

    await device.initialize(detect_cameras=False)

    assert removal_log["motors"] == ["rotor", "turntable"]
    assert removal_log["lights"] == ["ring"]
    assert creation_log["motors"] == ["rotor", "turntable", "rotor", "turntable"]
    assert creation_log["lights"] == ["ring", "ring"]

    second_status = device.get_device_info()
    assert set(second_status["motors"].keys()) == {"rotor", "turntable"}
    assert set(second_status["lights"].keys()) == {"ring"}


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
