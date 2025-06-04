from pydantic import BaseModel

from app.config.light import LightConfig


class Light(BaseModel):
    name: str

    settings: LightConfig
