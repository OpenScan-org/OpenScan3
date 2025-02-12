import asyncio
import time
import os
from fastapi.encoders import jsonable_encoder
from typing import AsyncGenerator, Tuple


from app.config import config
from controllers.hardware import gpio
from controllers.hardware.motors import MotorControllerFactory
from controllers.services import projects
from controllers.hardware.cameras.camera import CameraControllerFactory
from app.models.camera import Camera
from app.models.paths import CartesianPoint3D, PolarPoint3D
from app.models.project import Project
from app.services.paths import paths


async def move_to_point(point: paths.PolarPoint3D):
    """Move motors to specified polar coordinates"""
    # Get motor controllers
    turntable = MotorControllerFactory.get_controller(config.motors["turntable"])
    rotor = MotorControllerFactory.get_controller(config.motors["rotor"])

    # wait until motors are ready
    while turntable.is_busy() or rotor.is_busy():
        await asyncio.sleep(0.01)

    # Move both motors concurrently to specified point
    await asyncio.gather(
        turntable.move_to(point.fi),
        rotor.move_to(point.theta)
    )


async def scan(project: Project, camera: Camera, path: list[CartesianPoint3D]) -> AsyncGenerator[Tuple[int, int], None]:
    camera_controller = CameraControllerFactory.get_controller(camera)
    total = len(path)
    next_point = None

    for index, current_point in enumerate(path):
        start = time.time()

        # prepare next coordinate
        if index < total - 1:
            next_point = paths.cartesian_to_polar(path[index + 1])

        # current position
        current_polar = paths.cartesian_to_polar(current_point)

        # move to current position
        await move_to_point(current_polar)
        # take photo
        photo = camera_controller.photo()

        # do concurrent: save photo and move to next point
        if next_point:
            await asyncio.gather(
                projects.add_photo_async(project, photo),
                move_to_point(next_point)
            )
        else:
            # in case of last photo just save photo
            await projects.add_photo_async(project, photo)

        yield index + 1, total

    # cleanup: move back to origin position
    await move_to_point(PolarPoint3D(0, 0))

def get_status():
    """Get current system status"""
    return {
        "status": "ok",
        "cameras": CameraControllerFactory.get_all_controllers().items(),
        "motors": {
            name: controller.get_status()
            for name, controller in MotorControllerFactory.get_all_controllers().items()
        },
        "projects": projects.get_projects(),
        "path_methods": []
    }

def trigger_external_cam():
    gpio.set_pin(config.external_camera_pin, True)
    time.sleep(config.external_camera_delay)
    gpio.set_pin(config.external_camera_pin, False)


def reboot():
    os.system("sudo reboot")


def shutdown():
    os.system("sudo shutdown now")
