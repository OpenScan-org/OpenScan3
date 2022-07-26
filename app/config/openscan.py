import json
import pathlib

from app.config.camera import CameraSettings
from app.config.cloud import CloudSettings
from app.config.motor import MotorConfig
from app.models.motor import Motor


class OpenScanConfig:
    def __init__(self):
        OpenScanConfig.reload()

    @classmethod
    def reload(cls):
        # cls.scanner = ScannerConfig(turntable_mode=False)
        # cls.cloud = OpenScanCloudConfig("", "", "", "")
        cls.cameras: dict[str, CameraSettings] = OpenScanConfig._get_camera_configs()
        cls.motors: dict[str, Motor] = {
            # "tt": OpenScanConfig._load_motor_config("turntable"),
            # "rotor": OpenScanConfig._load_motor_config("rotor"),
            "tt": Motor("tt", MotorConfig(9, 22, 11, 1, 200, 0.0001, 1, 3200)),
            "rotor": Motor("rotor", MotorConfig(5, 23, 6, 1, 2000, 0.0001, 1, 48000)),
        }
        cls.projects_path = pathlib.PurePath("projects")
        cls.cloud = CloudSettings(
            "openscan",
            "free",
            "******",
            "http://openscanfeedback.dnsuser.de:1334",
        )
        cls.ring_light_pins = (17, 27)

        cls.external_camera_pin = 10
        cls.external_camera_delay = 0.1

    # @staticmethod
    # def _load_motor_config(name: str) -> MotorConfig:
    #     with open(f"settings/motor_{name}.json") as f:
    #         config = json.load(f)
    #         return MotorConfig(**config)

    @staticmethod
    def _load_camera_config(name: str) -> CameraSettings:
        return {}

    @staticmethod
    def _get_camera_configs() -> dict[str, CameraSettings]:
        return {}