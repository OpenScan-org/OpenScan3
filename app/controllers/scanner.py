import time
import os

from fastapi.encoders import jsonable_encoder

from app.config import config
from app.controllers import gpio
from app.controllers import motors
from app.controllers import projects
from app.controllers.cameras import cameras
from app.models.camera import Camera
from app.models.paths import CartesianPoint3D, PathMethod, PolarPoint3D
from app.models.project import Project
from app.services.paths import paths


def toggle_lights():
    ...


def lights_on():
    ...


def lights_off():
    ...


def move_to_point(point: paths.PolarPoint3D):
    turntable = motors.get_motor(motors.MotorType.TURNTABLE)
    rotor = motors.get_motor(motors.MotorType.ROTOR)

    motors.move_motor_to(turntable, point.fi)
    motors.move_motor_to(rotor, point.theta)


def scan(project: Project, camera: Camera, path: list[CartesianPoint3D]):
    
    total = len(path)
    index = 0
    for point in path:
        camera_controller = cameras.get_camera_controller(camera)
        photo = camera_controller.photo(camera)
        move_to_point(paths.cartesian_to_polar(point))
        time.sleep(0.2)
        projects.add_photo(project, photo)
        photo.close()
        index = index + 1
        yield (index,total,)

    move_to_point(PolarPoint3D(0, 0))


def trigger_external_cam():
    gpio.set_pin(config.external_camera_pin, True)
    time.sleep(config.external_camera_delay)
    gpio.set_pin(config.external_camera_pin, False)


def reboot():
    os.system("sudo reboot")


def shutdown():
    os.system("sudo shutdown now")

def get_status():
    return {
        "status": "ok",
        "cameras": jsonable_encoder(cameras.get_cameras()),
        "motors": motors.get_motors(),
        "projects": projects.get_projects(),
        "path_methods": []
    }