from pydantic import BaseModel

from openscan_firmware.config.light import LightConfig


class Light(BaseModel):
    name: str

    settings: LightConfig
