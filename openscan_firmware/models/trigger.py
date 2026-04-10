from pydantic import BaseModel

from openscan_firmware.config.trigger import TriggerConfig


class Trigger(BaseModel):
    name: str
    settings: TriggerConfig
