from dataclasses import dataclass


@dataclass
class CameraSettings:
    shutter: int
    saturation: int
    contrast: int
    awbg_red: int
    awbg_blue: int
    gain: int
    jpeg_quality: int
    AF: bool
