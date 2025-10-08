from pydantic import BaseModel

from openscan.config.light import LightConfig


class Light(BaseModel):
    name: str

    settings: LightConfig
