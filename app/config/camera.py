from pydantic import BaseModel, Field, conint
from typing import Optional, Tuple


class CameraSettings(BaseModel):
    shutter: Optional[int] = Field(40000, ge=1, le=435918849, description="Shutter speed in microseconds.") # 435918849 is the maximum of Arducam Hawkeye
    saturation: Optional[float] = Field(1.0, ge=0.0, le=32.0, description="Image color saturation from 0 to 32")
    contrast: Optional[float] = Field(1.0, ge=0.0, le=32.0, description="Image contrast from 0 to 32.")
    awbg_red: Optional[float] = Field(1.0, ge=0.0, le=32.0, description="Red Gain from 0 to 32.")
    awbg_blue: Optional[float] = Field(1.0, ge=0.0, le=32.0, description="Blue Gain from 0 to 32.")
    gain: Optional[float] = Field(1.0, ge=0.0, le=32.0, description="Analogue Gain from 0 to 32.")

    jpeg_quality: Optional[int] = Field(75, ge=0, le=100, description="JPEG image quality from 0 to 100")

    AF: Optional[bool] = Field(True, description="Enable Autofocus. This will ignore manual_focus settings.")
    AF_window: Optional[Tuple[
        conint(ge=0),  # x coordinate of the upper left corner
        conint(ge=0),  # y coordinate of the upper left corner
        conint(ge=0),  # width of the focus window
        conint(ge=0)  # height of the focus window
    ]] = Field(None,
               description="Autofocus window (x,y,w,h) in pixels. "
                           "(x,y) specify the position of the upper left corner of the window. "
                           "This will be ignored if AF is disabled.")
    manual_focus: Optional[float] = Field(None, ge=0.0, le=15.0, description="Manual focus position in diopters. "
                                                                             "This is only applied if autofocus is disabled.")

    crop_width: Optional[int] = Field(0, ge=0, le=100, description="Cropping width in percent.")
    crop_height: Optional[int] = Field(0, ge=0, le=100, description="Cropping on height in percent.")

    preview_resolution: Optional[Tuple[int, int]] = Field(None, description="Preview resolution (x,y). Changing resolution can break cropping.")
    photo_resolution: Optional[Tuple[int, int]] = Field(None, description="Preview resolution (x,y). Changing resolution can break cropping.")