import asyncio
import io
import time
from dataclasses import dataclass
from threading import Lock
from typing import Literal, Optional
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import numpy as np
from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.models.camera import Camera, CameraMetadata, CameraType, PhotoData
from openscan_firmware.models.scan import ScanMetadata
from openscan_firmware.controllers.hardware.cameras.camera import (
    get_all_camera_controllers,
    get_camera_controller,
)

from .settings_utils import create_settings_endpoints

router = APIRouter(
    prefix="/cameras",
    tags=["cameras"],
    responses={404: {"description": "Not found"}},
)

PhotoFormat = Literal["jpeg", "raw", "dng", "rgb_array", "yuv_array"]
_PAYLOAD_TTL_SECONDS = 90
_MAX_PAYLOAD_CACHE_ENTRIES = 8
_MAX_PAYLOAD_CACHE_BYTES = 256 * 1024 * 1024


@dataclass
class _CachedPhotoPayload:
    camera_name: str
    content: bytes
    media_type: str
    filename: str
    size_bytes: int
    expires_at_monotonic: float


_photo_payload_cache: dict[str, _CachedPhotoPayload] = {}
_photo_payload_cache_lock = Lock()


class PhotoMetadataResponse(BaseModel):
    format: PhotoFormat
    media_type: str
    filename: str
    camera_metadata: CameraMetadata
    scan_metadata: Optional[ScanMetadata] = None
    payload_url: str
    expires_in_s: int


def _prune_expired_payloads(now_monotonic: float) -> None:
    expired_ids = [
        payload_id
        for payload_id, payload in _photo_payload_cache.items()
        if payload.expires_at_monotonic <= now_monotonic
    ]
    for payload_id in expired_ids:
        _photo_payload_cache.pop(payload_id, None)


def _enforce_payload_cache_size_limit() -> None:
    # Evict entries that expire first to keep newer payloads available.
    sorted_ids = sorted(
        _photo_payload_cache,
        key=lambda payload_id: _photo_payload_cache[payload_id].expires_at_monotonic,
    )

    while len(_photo_payload_cache) > _MAX_PAYLOAD_CACHE_ENTRIES and sorted_ids:
        _photo_payload_cache.pop(sorted_ids.pop(0), None)

    total_size_bytes = sum(payload.size_bytes for payload in _photo_payload_cache.values())
    while total_size_bytes > _MAX_PAYLOAD_CACHE_BYTES and sorted_ids:
        payload_id = sorted_ids.pop(0)
        removed = _photo_payload_cache.pop(payload_id, None)
        if removed is not None:
            total_size_bytes -= removed.size_bytes


def _serialize_photo_payload(photo: PhotoData) -> tuple[bytes, str, str]:
    if photo.format == "jpeg":
        media_type = "image/jpeg"
        filename = "photo.jpg"
    elif photo.format in ("raw", "dng"):
        media_type, filename = _infer_raw_file_info(photo)
    elif photo.format in ("rgb_array", "yuv_array"):
        media_type = "application/x-npy"
        filename = f"photo_{photo.format}.npy"
    else:
        raise ValueError(f"Unsupported photo format: {photo.format}")

    if photo.format in ("jpeg", "raw", "dng"):
        if isinstance(photo.data, io.BytesIO):
            content = photo.data.getvalue()
        elif isinstance(photo.data, (bytes, bytearray)):
            content = bytes(photo.data)
        elif hasattr(photo.data, "seek") and hasattr(photo.data, "read"):
            photo.data.seek(0)
            content = photo.data.read()
        else:
            raise TypeError(f"Expected byte stream for {photo.format}, got {type(photo.data).__name__}")
    else:
        if not isinstance(photo.data, np.ndarray):
            raise TypeError(f"Expected numpy array for {photo.format}, got {type(photo.data).__name__}")
        buffer = io.BytesIO()
        np.save(buffer, photo.data)
        content = buffer.getvalue()

    return content, media_type, filename


def _infer_raw_file_info(photo: PhotoData) -> tuple[str, str]:
    raw_metadata = photo.camera_metadata.raw_metadata if photo.camera_metadata else {}
    capture_name = str(raw_metadata.get("capture_name", "")).lower()

    if capture_name.endswith(".cr2"):
        return "image/x-canon-cr2", "photo.cr2"
    if capture_name.endswith(".cr3"):
        return "image/x-canon-cr3", "photo.cr3"
    if capture_name.endswith(".crw"):
        return "image/x-canon-crw", "photo.crw"
    if capture_name.endswith(".dng"):
        return "image/x-adobe-dng", "photo.dng"
    if capture_name.endswith(".raw"):
        return "application/octet-stream", "photo.raw"

    # Legacy fallback for controllers that still report dng without capture_name.
    if photo.format == "dng":
        return "image/x-adobe-dng", "photo.dng"

    return "application/octet-stream", "photo.raw"


def _store_photo_payload(
    camera_name: str,
    content: bytes,
    media_type: str,
    filename: str,
) -> tuple[str, int]:
    now_monotonic = time.monotonic()
    payload_id = uuid4().hex
    expires_at_monotonic = now_monotonic + _PAYLOAD_TTL_SECONDS
    with _photo_payload_cache_lock:
        _prune_expired_payloads(now_monotonic)
        _photo_payload_cache[payload_id] = _CachedPhotoPayload(
            camera_name=camera_name,
            content=content,
            media_type=media_type,
            filename=filename,
            size_bytes=len(content),
            expires_at_monotonic=expires_at_monotonic,
        )
        _enforce_payload_cache_size_limit()
    return payload_id, _PAYLOAD_TTL_SECONDS


def _encode_url_path(url: str) -> str:
    split = urlsplit(url)
    encoded_path = quote(split.path, safe="/")
    return urlunsplit((split.scheme, split.netloc, encoded_path, split.query, split.fragment))


def _get_cached_photo_payload(camera_name: str, payload_id: str) -> _CachedPhotoPayload:
    now_monotonic = time.monotonic()
    with _photo_payload_cache_lock:
        _prune_expired_payloads(now_monotonic)
        payload = _photo_payload_cache.get(payload_id)
        if payload is None or payload.camera_name != camera_name:
            raise HTTPException(status_code=404, detail="Photo payload not found or expired.")
    return payload


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
            try:
                frame = await controller.preview_async()
            except RuntimeError:
                break
            if frame is not None:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            await asyncio.sleep(frame_delay)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace;boundary=frame")


@router.get("/{camera_name}/photo")
async def get_photo(
    camera_name: str,
    request: Request,
    image_format: PhotoFormat = Query(default="jpeg"),
    with_metadata: bool = Query(default=False),
):
    """Get a camera photo

    Args:
        camera_name: The name of the camera to get the photo from

    Returns:
        Response: A response containing the photo
    """
    controller = get_camera_controller(camera_name)
    try:
        photo = await controller.photo_async(image_format=image_format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        content, media_type, filename = _serialize_photo_payload(photo)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not with_metadata:
        return Response(content=content, media_type=media_type)

    payload_id, expires_in_s = _store_photo_payload(
        camera_name=camera_name,
        content=content,
        media_type=media_type,
        filename=filename,
    )
    payload_url = _encode_url_path(
        str(
            request.url_for(
                "get_photo_payload",
                camera_name=camera_name,
                payload_id=payload_id,
            )
        )
    )
    return PhotoMetadataResponse(
        format=photo.format,
        media_type=media_type,
        filename=filename,
        camera_metadata=photo.camera_metadata,
        scan_metadata=photo.scan_metadata,
        payload_url=payload_url,
        expires_in_s=expires_in_s,
    )


@router.get("/{camera_name}/photo/payload/{payload_id}", name="get_photo_payload")
async def get_photo_payload(camera_name: str, payload_id: str):
    payload = _get_cached_photo_payload(camera_name=camera_name, payload_id=payload_id)
    return Response(
        content=payload.content,
        media_type=payload.media_type,
        headers={"Content-Disposition": f'inline; filename="{payload.filename}"'},
    )

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
