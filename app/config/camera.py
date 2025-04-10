from pydantic import BaseModel
from typing import Optional, Tuple


#@dataclass
class CameraSettings(BaseModel):
    shutter: Optional[int] = None
    saturation: Optional[float] = None
    contrast: Optional[float] = None
    awbg_red: Optional[float] = None
    awbg_blue: Optional[float] = None
    gain: Optional[float] = None
    jpeg_quality: Optional[int] = None
    AF: Optional[bool] = None
    manual_focus: Optional[float] = None
    preview_resolution: Optional[Tuple[int, int]] = None
    photo_resolution: Optional[Tuple[int, int]] = None
