"""
Developer endpoints

These may be removed or changed at any time.
"""

import base64
import json
import subprocess
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, status, Response, Query
from fastapi.responses import PlainTextResponse

from openscan_firmware.controllers.hardware.cameras.camera import get_all_camera_controllers
from openscan_firmware.controllers.services.tasks.task_manager import get_task_manager
from openscan_firmware.models.camera import CameraType
from openscan_firmware.models.task import TaskStatus, Task

from openscan_firmware.models.paths import PolarPoint3D
from openscan_firmware.controllers.hardware.motors import move_to_point

from openscan_firmware.utils.paths import paths
from openscan_firmware.cli import DEFAULT_RELOAD_TRIGGER

CAMERA_REPORT_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "camera_report.sh"


router = APIRouter(
    prefix="/develop",
    tags=["develop"],
    responses={404: {"description": "Not found"}},
)


def _gp_text(value) -> str:  # noqa: ANN001
    if value is None:
        return ""
    return str(getattr(value, "text", value))


def _extract_widget_choices(widget) -> list[str]:  # noqa: ANN001
    try:
        count = widget.count_choices()
    except Exception:
        return []
    choices: list[str] = []
    for idx in range(count):
        try:
            choices.append(str(widget.get_choice(idx)))
        except Exception:
            continue
    return choices


def _walk_config_widgets(widget, prefix: str = "") -> list[dict]:  # noqa: ANN001
    entries: list[dict] = []
    try:
        name = str(widget.get_name())
    except Exception:
        name = "unknown"
    path = f"{prefix}/{name}" if prefix else f"/{name}"

    try:
        label = str(widget.get_label())
    except Exception:
        label = ""

    try:
        value = str(widget.get_value())
    except Exception:
        value = None

    try:
        readonly = bool(widget.get_readonly())
    except Exception:
        readonly = None

    try:
        widget_type = str(widget.get_type())
    except Exception:
        widget_type = None

    entries.append(
        {
            "name": name,
            "label": label,
            "path": path,
            "type": widget_type,
            "readonly": readonly,
            "value": value,
            "choices": _extract_widget_choices(widget),
        }
    )

    try:
        child_count = widget.count_children()
    except Exception:
        child_count = 0

    for child_idx in range(child_count):
        try:
            child = widget.get_child(child_idx)
        except Exception:
            continue
        entries.extend(_walk_config_widgets(child, path))
    return entries


def _collect_gphoto2_diagnostics() -> dict:
    """Collect gphoto2 diagnostics via Python API with lazy import."""
    try:
        import gphoto2 as gp
    except Exception as exc:
        return {
            "available": False,
            "error": f"python gphoto2 module unavailable: {exc}",
            "detected": [],
            "cameras": [],
        }

    try:
        detected = gp.Camera.autodetect()
    except Exception as exc:
        return {
            "available": True,
            "error": f"autodetect failed: {exc}",
            "detected": [],
            "cameras": [],
        }

    rows: list[dict[str, str]] = []
    try:
        count = detected.count()
        for idx in range(count):
            rows.append({"model": detected.get_name(idx), "path": detected.get_value(idx)})
    except Exception:
        try:
            rows = [{"model": item[0], "path": item[1]} for item in detected]
        except Exception as exc:
            return {
                "available": True,
                "error": f"Failed to parse autodetect result: {exc}",
                "detected": [],
                "cameras": [],
            }

    gphoto2_controllers = []
    for controller in get_all_camera_controllers().values():
        camera_model = getattr(controller, "camera", None)
        if camera_model is None:
            continue
        if getattr(camera_model, "type", None) != CameraType.GPHOTO2:
            continue
        gphoto2_controllers.append(controller)

    def _find_active_controller(model: str | None, path: str | None):
        for ctrl in gphoto2_controllers:
            cam = getattr(ctrl, "camera", None)
            if cam is None:
                continue
            if path and getattr(cam, "path", None) == path:
                return ctrl
            if model and getattr(cam, "name", None) == model:
                return ctrl
        return None

    cameras: list[dict] = []
    for row in rows:
        model = row.get("model")
        path = row.get("path")
        active_controller = _find_active_controller(model, path)
        if active_controller is not None:
            get_diag = getattr(active_controller, "get_diagnostics", None)
            if callable(get_diag):
                try:
                    cameras.append(get_diag())
                    continue
                except Exception as exc:
                    cameras.append(
                        {
                            "model": model,
                            "path": path,
                            "summary": None,
                            "about": None,
                            "config_groups": [],
                            "relevant_config": [],
                            "in_use_by_openscan": True,
                            "error": f"controller diagnostics failed: {exc}",
                        }
                    )
                    continue

        camera_diag = {
            "model": model,
            "path": path,
            "summary": None,
            "about": None,
            "config_groups": [],
            "relevant_config": [],
            "in_use_by_openscan": False,
            "error": None,
        }
        camera = None
        try:
            camera = gp.Camera()
            camera.init()
            try:
                camera_diag["summary"] = _gp_text(camera.get_summary()).strip()
            except Exception:
                camera_diag["summary"] = None
            try:
                camera_diag["about"] = _gp_text(camera.get_about()).strip()
            except Exception:
                camera_diag["about"] = None
            try:
                config = camera.get_config()
                child_count = config.count_children()
                groups: list[str] = []
                for child_idx in range(child_count):
                    child = config.get_child(child_idx)
                    groups.append(f"{child.get_name()}: {child.get_label()}")
                camera_diag["config_groups"] = groups
                all_widgets = _walk_config_widgets(config)
                key_candidates = {
                    "capturetarget",
                    "capture",
                    "recordingmedia",
                    "shutterspeed",
                    "shutter_speed",
                    "aperture",
                    "f-number",
                    "iso",
                    "imageformat",
                    "imagequality",
                    "imgquality",
                    "eosremoterelease",
                    "viewfinder",
                    "focusmode",
                    "autoexposuremode",
                }
                camera_diag["relevant_config"] = [
                    item for item in all_widgets if item["name"].lower() in key_candidates
                ]
            except Exception:
                camera_diag["config_groups"] = []
                camera_diag["relevant_config"] = []
        except Exception as exc:
            camera_diag["error"] = str(exc)
        finally:
            if camera is not None:
                try:
                    camera.exit()
                except Exception:
                    pass
        cameras.append(camera_diag)

    return {
        "available": True,
        "error": None,
        "detected": rows,
        "cameras": cameras,
    }


@router.put("/scanner-position")
async def move_to_position(point: PolarPoint3D):
    """Move Rotor and Turntable to a polar point"""
    await move_to_point(point)


@router.post("/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart_application() -> dict[str, str]:
    """Trigger a Firmware reload by touching the reload sentinel file.

    Note: The application has to be started with the --reload-trigger option to enable this endpoint."""
    DEFAULT_RELOAD_TRIGGER.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_RELOAD_TRIGGER.write_text(str(time.time()), encoding="utf-8")
    # Ensure mtime changes even on file systems with coarse-grained timestamps
    DEFAULT_RELOAD_TRIGGER.touch()
    return {"detail": "Reload triggered"}


@router.get("/camera-report")
async def get_camera_report(
    format: Literal["json", "text"] = Query(default="json"),
):
    """Run the camera diagnostics script and return a bundled report."""
    if not CAMERA_REPORT_SCRIPT.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Camera report script not found: {CAMERA_REPORT_SCRIPT}",
        )

    result = subprocess.run(
        ["bash", str(CAMERA_REPORT_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    report = result.stdout.strip()
    stderr = result.stderr.strip()
    gphoto2_diag = _collect_gphoto2_diagnostics()

    if format == "text":
        text_output = report or stderr or "No output produced."
        gphoto2_section = "===== GPhoto2 python diagnostics =====\n" + json.dumps(gphoto2_diag, indent=2)
        text_output = f"{text_output}\n\n{gphoto2_section}"
        status_code = status.HTTP_200_OK if result.returncode == 0 else status.HTTP_500_INTERNAL_SERVER_ERROR
        return PlainTextResponse(content=text_output, status_code=status_code)

    payload = {
        "ok": result.returncode == 0,
        "return_code": result.returncode,
        "script": str(CAMERA_REPORT_SCRIPT),
        "report": report,
        "stderr": stderr,
        "gphoto2": gphoto2_diag,
    }

    if result.returncode != 0:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=payload)

    return payload


@router.get("/crop_image", summary="Run crop task and return visualization image", response_class=Response)
async def crop_image(camera_name: str, threshold: int | None = Query(default=None, ge=0, le=255)) -> Response:
    """Run the crop task and return the visualization image with bounding boxes.

    Args:
        camera_name: Name of the camera controller to use.
        threshold: Optional Canny threshold passed to the analysis (tutorial uses a trackbar). If not set, defaults inside the task.

    Returns:
        Response: JPEG image showing contours, rectangles and circles as detected by the task.
    """
    task_manager = get_task_manager()

    # Start task
    task = await task_manager.create_and_run_task("crop_task", camera_name, threshold=threshold)

    # Wait for completion (default TaskManager timeout is fine for demo; can be adjusted if needed)
    try:
        final_task = await task_manager.wait_for_task(task.id, timeout=120.0)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Waiting for task failed: {e}")

    if final_task.status != TaskStatus.COMPLETED:
        detail = final_task.error or f"Task did not complete successfully (status={final_task.status})."
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)

    result = final_task.result or {}
    if not isinstance(result, dict) or "image_base64" not in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Task result does not contain an image.")

    try:
        img_bytes = base64.b64decode(result["image_base64"])
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to decode image from task result.")

    return Response(content=img_bytes, media_type=result.get("mime", "image/jpeg"))



@router.post("/hello-world-async", response_model=Task)
async def hello_world_async(total_steps: int, delay: float):
    """Start the async hello world demo task."""

    task_manager = get_task_manager()

    # Updated to explicit task_name with required _task suffix
    task = await task_manager.create_and_run_task("hello_world_async_task", total_steps=total_steps, delay=delay)
    return task


@router.post("/qr-scan", response_model=Task, status_code=status.HTTP_202_ACCEPTED)
async def start_qr_scan(
    camera_name: str = Query(description="Name of the camera controller to use"),
):
    """Start a background task that scans for WiFi QR codes via the camera.

    The task runs indefinitely, capturing frames and looking for QR codes.
    When it finds an Android/iOS WiFi share QR code it connects to the
    network via nmcli and completes.  Cancel the task to stop scanning.

    Args:
        camera_name: Name of the camera controller to use for captures.

    Returns:
        Task: The created task model (poll via /tasks/{id} for progress).
    """
    task_manager = get_task_manager()
    task = await task_manager.create_and_run_task(
        "qr_scan_task",
        camera_name=camera_name,
    )
    return task


@router.get("/{method}", response_model=list[paths.CartesianPoint3D])
async def get_path(method: paths.PathMethod, points: int):
    """Get a list of coordinates by path method and number of points"""
    return paths.get_path(method, points)
