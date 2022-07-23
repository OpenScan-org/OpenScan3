import json
import math
import os
import pathlib
import tempfile
import time
from typing import IO
from zipfile import ZIP_DEFLATED, ZipFile
import orjson

import requests

from app.config import config
from app.controllers import projects


def _cloud_request(method: str, path: str, params=None) -> requests.Response:
    r_params = {"token": config.cloud.key}
    if params is not None:
        r_params.update(params)

    return requests.request(
        method,
        f"{config.cloud.host}/{path}",
        auth=(config.cloud.user, config.cloud.password),
        params=r_params,
    )


def get_token_info():
    return _cloud_request("get", "getTokenInfo")


def _create_project(project_name: str, photos: int, filesize: int, parts: int):
    return _cloud_request(
        "get",
        "createProject",
        params={
            "photos": photos,
            "filesize": filesize,
            "parts": parts,
            "project": project_name,
        },
    )


def _upload_file(file: IO[bytes], ulink: str):
    return requests.post(
        ulink, data=file, headers={"Content-type": "application/octet-stream"}
    )


def upload_project(project_name: str):
    project = projects.get_project(project_name)
    photos = projects.get_project_photos(project)

    cloud_project_name = f"{project.name}_{int(time.time())}"

    # compress
    zip = projects.compress_project_photos(project)
    zip_size = zip.tell()
    # split
    nchunks = math.ceil(zip_size / config.cloud.split_size)
    # create project
    response = _create_project(cloud_project_name, len(photos), zip_size, nchunks)

    print(cloud_project_name, len(photos), zip_size, nchunks)

    print(response)
    print(response.text)

    if response.status_code == 200:
        info = response.json()
        ulinks = info["ulink"]
        # upload parts
        counter = 0
        for chunk in projects.split_file(zip):
            print(f"Uploading to {ulinks[counter]}")
            response2 = _upload_file(chunk, ulinks[counter])
            chunk.close()
            print(response2)
            print(response2.text)
            counter += 1
        # start processing
        response3 = _start_project(cloud_project_name)
        print(response3)
        print(response3.text)

        print(get_project_info(cloud_project_name).json())

    zip.close()


def _start_project(project_name: str):
    return _cloud_request("get", "startProject", params={"project": project_name})


def get_project_info(project_name: str):
    return _cloud_request("get", "getProjectInfo", params={"project": project_name})
