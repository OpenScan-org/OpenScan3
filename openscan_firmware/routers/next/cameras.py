import asyncio

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.models.camera import Camera, CameraType
from openscan_firmware.controllers.hardware.cameras.camera import get_all_camera_controllers, get_camera_controller
#from openscan_firmware.controllers.services.scans import get_active_scan_manager
from openscan_firmware.controllers.hardware.motors import get_all_motor_controllers
from .settings_utils import create_settings_endpoints

router = APIRouter(
    prefix="/cameras",
    tags=["cameras"],
    responses={404: {"description": "Not found"}},
)


class CameraStatusResponse(BaseModel):
    name: str
    type: CameraType
    busy: bool
    settings: CameraSettings


class AutoCalibrateAwbRequest(BaseModel):
    warmup_frames: int = Field(
        default=12,
        description="Number of frames to discard before reading AWB metadata.",
        ge=0,
    )
    stable_frames: int = Field(
        default=4,
        description="Consecutive frames that must meet the stability tolerance.",
        ge=1,
    )
    eps: float = Field(
        default=0.01,
        description="Maximum delta between gain values to consider them stable.",
        gt=0,
    )
    timeout_s: float = Field(
        default=2.0,
        description="Maximum time budget for the calibration loop in seconds.",
        gt=0,
    )


class AutoCalibrateAwbResponse(BaseModel):
    red_gain: float
    blue_gain: float


@router.get("/", response_model=dict[str, CameraStatusResponse])
async def get_cameras():
    """Get all cameras with their current status

    Returns:
        dict[str, CameraStatusResponse]: A dictionary of camera name to a camera status object
    """
    return {
        name: controller.get_status()
        for name, controller in  get_all_camera_controllers().items()
    }


@router.get("/{camera_name}", response_model=CameraStatusResponse)
async def get_camera(camera_name: str):
    """Get a camera with its current status

    Args:
        camera_name: The name of the camera to get the status of

    Returns:
        CameraStatusResponse: A response object containing the status of the camera
    """
    try:
        return get_camera_controller(camera_name).get_status()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{camera_name}/preview")
async def get_preview(
    camera_name: str,
    mode: str = Query(default="stream", pattern="^(stream|snapshot)$"),
    fps: int = Query(default=50, ge=1, le=50),
):
    """Get a camera preview stream in lower resolution

    Note: The preview is not rotated by orientation_flag and has to be rotated by client.

    Args:
        camera_name: The name of the camera to get the preview stream from
        mode: Either ``stream`` for the MJPEG stream or ``snapshot`` for a single JPEG frame
        fps: Target frames per second for the stream, clamped between 1 and 50 (only used in stream mode)

    Returns:
        StreamingResponse: A streaming response containing the preview stream
    """
    controller = get_camera_controller(camera_name)

    if mode == "snapshot":
        if controller.is_busy():
            raise HTTPException(status_code=409, detail="Camera is busy. If this is a bug, please restart the camera.")
        try:
            frame = controller.preview()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(content=frame, media_type="image/jpeg")

    frame_delay = 1 / fps

    async def generate():
        while True:
            # Check if any motors are busy
            motor_busy = any(
                motor_controller.is_busy()
                for motor_controller in get_all_motor_controllers().values()
            )


            # Stop preview (wait) if motor or scan is busy, otherwise continue with 0.02s delay
            # if motor_busy or scan_busy:
             #   await asyncio.sleep(0.1)  # Small sleep to prevent busy waiting
             #   continue  # Skip frame generation and yield
            if not controller.is_busy():
                try:
                    frame = controller.preview()
                except RuntimeError:
                    break
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            await asyncio.sleep(frame_delay)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace;boundary=frame")


@router.get("/{camera_name}/photo")
async def get_photo(camera_name: str):
    """Get a camera photo

    Args:
        camera_name: The name of the camera to get the photo from

    Returns:
        Response: A response containing the photo
    """
    controller = get_camera_controller(camera_name)
    try:
        if not controller.is_busy():
            return Response(content=controller.photo().data.getvalue(), media_type="image/jpeg")
    except Exception as e:
        return Response(status_code=500, content=str(e))
    return Response(status_code=409, content="Camera is busy. If this is a bug, please restart the camera.")

@router.post("/{camera_name}/restart")
async def restart_camera(camera_name: str):
    """Restart a camera

    Args:
        camera_name: The name of the camera to restart

    Returns:
        Response: A response containing the status code
    """
    controller = get_camera_controller(camera_name)
    controller.restart_camera()
    return Response(status_code=200)


@router.post(
    "/{camera_name}/awb-calibration",
    response_model=AutoCalibrateAwbResponse,
    summary="Run automatic white balance calibration and lock the gains.",
)
async def auto_calibrate_awb(
    camera_name: str,
    params: AutoCalibrateAwbRequest = Body(default=AutoCalibrateAwbRequest()),
):
    """Expose the camera controller's automatic white balance calibration if available.

    Args:
        camera_name: Target camera identifier.
        params: Optional tuning parameters forwarded to the controller implementation.

    Returns:
        AutoCalibrateAwbResponse: Locked gains after the calibration.

    Raises:
        HTTPException: When the controller is busy, unsupported, or calibration fails.
    """

    controller = get_camera_controller(camera_name)

    if controller.is_busy():
        raise HTTPException(status_code=409, detail="Camera is busy. Retry once it is idle.")

    calibrate_fn = getattr(controller, "calibrate_awb_and_lock", None)
    if not callable(calibrate_fn):
        raise HTTPException(
            status_code=501,
            detail="This camera does not support automatic white balance calibration.",
        )

    try:
        red_gain, blue_gain = calibrate_fn(**params.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return AutoCalibrateAwbResponse(red_gain=red_gain, blue_gain=blue_gain)

create_settings_endpoints(
    router=router,
    resource_name="camera_name",
    get_controller=get_camera_controller,
    settings_model=CameraSettings
)