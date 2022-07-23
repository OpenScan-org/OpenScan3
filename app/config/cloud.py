from dataclasses import dataclass


@dataclass
class CloudSettings:
    user: str
    password: str
    key: str

    host: str

    split_size: int = 200000000 #200MB is the maximum part size (total zip file can be up to 2GB)
