import json
import pathlib
import os

from linuxpy.video.device import iter_video_capture_devices
import gphoto2 as gp
from picamera2 import Picamera2


from app.config.camera import CameraSettings
from app.models.camera import Camera, CameraType
from app.config.cloud import CloudSettings
from app.config.motor import MotorConfig
from app.models.motor import Motor
from app.config.light import LightConfig
from app.models.light import Light, LightType

from controllers.hardware.cameras.camera import CameraControllerFactory
from controllers.hardware.motors import MotorControllerFactory

from dotenv import load_dotenv

class OpenScanConfig:
    def __init__(self):
        OpenScanConfig.reload()

    @classmethod
    def reload(cls):
        load_dotenv()
        # cls.scanner = ScannerConfig(turntable_mode=False)
        # cls.cloud = OpenScanCloudConfig("", "", "", "")
        cls.cameras = OpenScanConfig._get_cameras()
        for cam in cls.cameras:
            CameraControllerFactory.get_controller(cam)
        cls.motors = {
            motor.name: motor
            for motor in [
                Motor("turntable", OpenScanConfig._load_motor_config("turntable")),
                Motor("rotor", OpenScanConfig._load_motor_config("rotor"))
            ]
        }
        # Controller initialisieren
        for motor in cls.motors.values():
            MotorControllerFactory.get_controller(motor)

        #cls.motors: dict[MotorType, Motor] = {
        #    # "tt": OpenScanConfig._load_motor_config("turntable"),
        #    # "rotor": OpenScanConfig._load_motor_config("rotor"),
        #    MotorType.TURNTABLE: Motor(MotorConfig(9, 22, 11, 1, 200, 0.0001, 1, 3200)),
        #    MotorType.ROTOR: Motor(MotorConfig(5, 23, 6, 1, 2000, 0.0001, 1, 17067)),
        #}
        cls.projects_path = pathlib.PurePath("projects")
        cls.cloud = CloudSettings(
            "openscan",
            "free",
            os.getenv("OPENSCANCLOUD_KEY"),
            "http://openscanfeedback.dnsuser.de:1334",
        )
        #cls.lights = {LightType.RINGLIGHT: OpenScanConfig._load_light_configs("ringlight")}
        cls.lights: dict[LightType, Light] = {LightType.RINGLIGHT: Light(False, OpenScanConfig._load_light_configs("ringlight")) }
        #cls.ring_light_enabled = False
        #cls.ring_light_pins = (17, 27)

        cls.external_camera_pin = 10
        cls.external_camera_delay = 0.1


    @staticmethod
    def _load_motor_config(name: str) -> MotorConfig:
        with open(f"settings/motor_{name}.json") as f:
            config = json.load(f)
            return MotorConfig(**config)

    @staticmethod
    def _load_camera_config(name: str):
        with open(f"settings/camera_{name}.json") as f:
            config = json.load(f)
            return CameraSettings(**config)

    @staticmethod
    def _get_camera_configs() -> dict[str, CameraSettings]:
        return {}

    @staticmethod
    def _load_light_configs(name: str) -> LightConfig:
        with open(f"settings/light_{name}.json") as f:
            config = json.load(f)
            # make sure that pins are an iterable list:
            pins = config.get("pins")
            pin = config.get("pin")
            if pin is not None and pins is None:
                config["pins"] = [pin]
            elif pins is None:
                config["pins"] = []
            return LightConfig(**config)

    @classmethod
    def _get_cameras(cls):
        linuxpycameras = iter_video_capture_devices()
        gphoto2_cameras = gp.Camera.autodetect()
        cameras = []
        for cam in linuxpycameras:
            cam.open()
            if cam.info.card not in ("unicam", "bcm2835-isp"):
                cameras.append(Camera(
                    type=CameraType.LINUXPY,
                    name=cam.info.card,
                    path=cam.filename,
                    settings=None
                ))
            cam.close()
        for c in gphoto2_cameras:
            cameras.append(Camera(
                type=CameraType.GPHOTO2,
                name=c[0],
                path=c[1],
                settings=None
            ))
        picam = Picamera2()
        picam_name = picam.camera_properties.get("Model")
        cameras.append(Camera(
            type=CameraType.PICAMERA2,
            name=picam_name,
            path="/dev/video" + str(picam.camera_properties.get("Location")),
            settings=OpenScanConfig._load_camera_config(picam_name)
        ))
        picam.close()
        del picam
        return cameras

