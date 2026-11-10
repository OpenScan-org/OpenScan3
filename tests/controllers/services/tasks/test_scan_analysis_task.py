from __future__ import annotations

import gc
import weakref
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from openscan_firmware.controllers.services.tasks.core.scan_analysis_task import ScanAnalysisTask
from openscan_firmware.models.camera import CameraMetadata, PhotoData
from openscan_firmware.models.paths import PolarPoint3D
from openscan_firmware.models.task import Task
from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.utils.photos.analysis import (
    AnalysisFrame,
    _calculate_position_imbalance,
    _combine_cropping_roi_masks,
    _crop_recommendation,
    _detect_cropping_roi_mask,
    _remove_narrow_cropping_roi_tail,
    prepare_analysis_frame,
)


def test_prepare_analysis_frame_keeps_metadata_and_only_analysis_image():
    image = np.zeros((1200, 1600, 3), dtype=np.uint8)
    photo = PhotoData(
        data=image,
        format="rgb_array",
        camera_metadata=CameraMetadata(
            camera_name="camera0",
            camera_settings=CameraSettings(),
            raw_metadata={"ExposureTime": 1000},
        ),
    )

    frame = prepare_analysis_frame(photo, position=PolarPoint3D(theta=90, fi=0, r=1), index=0)

    assert frame.analysis_image.shape == (640, 480, 3)
    assert frame.analysis_image is not image
    assert frame.camera_metadata.raw_metadata["ExposureTime"] == 1000


def test_detect_cropping_roi_mask_returns_union_from_multiple_views():
    background = np.zeros((100, 120, 3), dtype=np.uint8)
    frames = []
    for index, x_start in enumerate((20, 50, 80)):
        image = background.copy()
        image[20:80, x_start:x_start + 20] = (200, 80, 40)
        frames.append(
            AnalysisFrame(
                index=index,
                position=PolarPoint3D(theta=90, fi=index * 120, r=1),
                camera_metadata=CameraMetadata(
                    camera_name="camera0",
                    camera_settings=CameraSettings(),
                    raw_metadata={},
                ),
                analysis_image=image,
            )
        )

    result = _detect_cropping_roi_mask(frames)

    y_coordinates, x_coordinates = np.where(result.mask)
    assert result.mask.any()
    assert [
        int(x_coordinates.min()),
        int(y_coordinates.min()),
        int(x_coordinates.max()) + 1,
        int(y_coordinates.max()) + 1,
    ] == [20, 20, 100, 80]


def test_position_imbalance_reports_vertical_and_horizontal_distribution():
    mask = np.zeros((100, 100), dtype=bool)
    mask[:25, :50] = True

    report = _calculate_position_imbalance(mask)

    assert report["vertical_imbalance"] == 1.0
    assert report["horizontal_imbalance"] == 1.0


def test_consensus_mask_and_crop_remove_a_narrow_bottom_tail():
    mask = np.zeros((100, 100), dtype=bool)
    mask[10:60, 20:80] = True
    mask[60:90, 46:54] = True
    detection = SimpleNamespace(mask=mask)

    consensus = _combine_cropping_roi_masks({"12": detection})
    object_mask = _remove_narrow_cropping_roi_tail(consensus, width_ratio=0.4, min_rows=12)
    crop = _crop_recommendation(object_mask)

    assert not object_mask[60:].any()
    assert crop["bbox"] == [20, 10, 60, 50]


@pytest.mark.asyncio
async def test_scan_analysis_task_captures_rgb_frames_and_restores_position(monkeypatch):
    raw_refs = []
    raw_refs_alive_during_capture = []

    async def capture_photo(**kwargs):
        assert move_to_point.await_args.args[0].theta == 90.0
        raw_refs_alive_during_capture.append([ref() is not None for ref in raw_refs])
        image = np.zeros((240, 320, 3), dtype=np.uint8)
        raw_refs.append(weakref.ref(image))
        return PhotoData(
            data=image,
            format="rgb_array",
            camera_metadata=CameraMetadata(
                camera_name="camera0",
                camera_settings=CameraSettings(),
                raw_metadata={"ExposureTime": 1000},
            ),
        )

    camera_settings = SimpleNamespace(
        model=CameraSettings(),
        update=MagicMock(),
        replace=MagicMock(),
    )
    camera = SimpleNamespace(
        photo_async=AsyncMock(side_effect=capture_photo),
        settings=camera_settings,
    )
    rotor = SimpleNamespace(model=SimpleNamespace(angle=45.0))
    turntable = SimpleNamespace(model=SimpleNamespace(angle=30.0))
    move_to_point = AsyncMock()

    monkeypatch.setattr(
        "openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller",
        lambda name: camera,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.hardware.motors.get_motor_controller",
        lambda name: rotor if name == "rotor" else turntable,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.hardware.motors.move_to_point",
        move_to_point,
    )

    task = ScanAnalysisTask(Task(name="scan_analysis_task", task_type="scan_analysis_task"))
    progress = [
        update
        async for update in task.run(
            "camera0",
            turntable_offsets=[-90.0, 90.0],
            settle_time_s=0,
            focus_values=[12.0],
        )
    ]

    assert len(progress) == 5
    assert camera.photo_async.await_count == 4
    assert all(
        call.kwargs == {"image_format": "rgb_array", "allow_af_fallback": False}
        for call in camera.photo_async.await_args_list
    )
    result = task._task_model.result
    assert result["crop_recommendation"] is None
    assert result["position_analysis"]["vertical_imbalance"] == 0.0
    assert result["position_analysis"]["horizontal_imbalance"] == 0.0
    gc.collect()
    assert raw_refs_alive_during_capture[0] == []
    assert all(not any(alive) for alive in raw_refs_alive_during_capture)
    assert all(ref() is None for ref in raw_refs)
    assert move_to_point.await_args_list[0].args[0] == PolarPoint3D(theta=90, fi=30, r=1)
    assert all(call.args[0].theta == 90 for call in move_to_point.await_args_list[:-1])
    assert move_to_point.await_args_list[-1].args[0].theta == 45.0
    assert move_to_point.await_args_list[-1].args[0].fi == 30.0


@pytest.mark.asyncio
async def test_scan_analysis_task_captures_focus_series_and_restores_camera_settings(monkeypatch):
    capture_count = 0

    async def capture_photo(**kwargs):
        nonlocal capture_count
        capture_count += 1
        image = np.zeros((120, 160, 3), dtype=np.uint8)
        x_start = 40 if capture_count <= 4 else 65
        image[20:80, x_start:x_start + 50] = (200, 80, 40)
        return PhotoData(
            data=image,
            format="rgb_array",
            camera_metadata=CameraMetadata(
                camera_name="camera0",
                camera_settings=CameraSettings(),
                raw_metadata={"LensPosition": 11.5, "FocusFoM": 42000},
            ),
        )

    settings = SimpleNamespace(
        model=CameraSettings(AF=True, manual_focus=12),
        update=MagicMock(),
        replace=MagicMock(),
    )
    camera = SimpleNamespace(photo_async=AsyncMock(side_effect=capture_photo), settings=settings)
    rotor = SimpleNamespace(model=SimpleNamespace(angle=90.0))
    turntable = SimpleNamespace(model=SimpleNamespace(angle=30.0))

    monkeypatch.setattr(
        "openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller",
        lambda name: camera,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.hardware.motors.get_motor_controller",
        lambda name: rotor if name == "rotor" else turntable,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.hardware.motors.move_to_point",
        AsyncMock(),
    )

    task = ScanAnalysisTask(Task(name="scan_analysis_task", task_type="scan_analysis_task"))
    [update async for update in task.run(
        "camera0",
        turntable_offsets=[-90.0, 90.0],
        settle_time_s=0,
        focus_values=[10.0, 12.0, 15.0],
        debug_images=True,
    )]

    result = task._task_model.result
    assert camera.photo_async.await_count == 8
    assert result["crop_recommendation"] is None
    assert "vertical_imbalance" in result["position_analysis"]
    assert "horizontal_imbalance" in result["position_analysis"]
    assert len(result["debug_images"]["captured_frames"]) == 8
    assert result["debug_images"]["captured_frames"][1]["camera_metadata"]["raw_metadata"] == {
        "LensPosition": 11.5,
        "FocusFoM": 42000,
    }
    assert set(result["debug_images"]["cropping_roi_masks"]) == {
        "autofocus", "10.0", "12.0", "15.0"
    }
    assert result["debug_images"]["consensus_mask_base64"]
    assert result["debug_images"]["object_mask_base64"]
    assert settings.update.call_count == 8
    settings.replace.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["move", "calibration"])
async def test_analysis_sets_rotor_before_calibration_and_restores_on_failure(monkeypatch, failure):
    original = PolarPoint3D(theta=45, fi=30, r=1)
    analysis = PolarPoint3D(theta=90, fi=30, r=1)
    move = AsyncMock(side_effect=[RuntimeError("Setup failed"), None] if failure == "move" else None)

    def calibrate_awb():
        assert move.await_args.args[0] == analysis
        raise RuntimeError("Setup failed")

    camera = SimpleNamespace(
        calibrate_awb_and_lock=MagicMock(side_effect=calibrate_awb),
        calibrate_exposure_and_lock=MagicMock(),
        photo_async=AsyncMock(),
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.hardware.cameras.camera.get_camera_controller", lambda name: camera,
    )
    monkeypatch.setattr(
        "openscan_firmware.controllers.hardware.motors.get_motor_controller",
        lambda name: SimpleNamespace(model=SimpleNamespace(angle=45 if name == "rotor" else 30)),
    )
    monkeypatch.setattr("openscan_firmware.controllers.hardware.motors.move_to_point", move)
    task = ScanAnalysisTask(Task(name="scan_analysis_task", task_type="scan_analysis_task"))
    with pytest.raises(RuntimeError, match="Setup failed"):
        [update async for update in task.run("camera0", settle_time_s=0)]
    assert [call.args[0] for call in move.await_args_list] == [analysis, original]
    assert camera.calibrate_awb_and_lock.call_count == (failure == "calibration")
    camera.photo_async.assert_not_awaited()


def test_analysis_frame_can_crop_oriented_native_image_without_resizing():
    from openscan_firmware.utils.photos.analysis import apply_orientation, crop_normalized_image
    image = np.arange(120 * 160 * 3, dtype=np.uint8).reshape(120, 160, 3)
    photo = PhotoData(data=image, format='rgb_array', camera_metadata=CameraMetadata(
        camera_name='camera0', camera_settings=CameraSettings(orientation_flag=8), raw_metadata={},
    ))
    crop = (0.25, 0.25, 0.5, 0.5)
    frame = prepare_analysis_frame(photo, PolarPoint3D(theta=90, fi=0, r=1), 0,
                                   max_dimension=None, crop=crop)
    expected = crop_normalized_image(apply_orientation(image, 8), crop)
    np.testing.assert_array_equal(frame.analysis_image, expected)
    assert frame.analysis_image.shape == (80, 60, 3)
    assert not np.shares_memory(frame.analysis_image, image)


@pytest.mark.asyncio
async def test_sift_capture_scales_after_crop(monkeypatch):
    from openscan_firmware.models.scan_analysis import NormalizedCrop
    image = np.zeros((120, 160, 3), dtype=np.uint8)
    image[30:90, 40:120] = 200
    photo = PhotoData(data=image, format='rgb_array', camera_metadata=CameraMetadata(
        camera_name='camera0', camera_settings=CameraSettings(orientation_flag=1), raw_metadata={},
    ))
    context = SimpleNamespace(camera=SimpleNamespace(photo_async=AsyncMock(return_value=photo)),
                              camera_settings=SimpleNamespace(update=MagicMock()))
    task = ScanAnalysisTask(Task(name='scan_analysis_task', task_type='scan_analysis_task'))
    frame, _ = await task._capture_frame(
        context=context, position=PolarPoint3D(theta=90, fi=0, r=1), index=-1,
        capture_index=0, offset=0, focus_mode='manual', requested_focus=12,
        analysis_max_dimension=40, analysis_crop=NormalizedCrop((0.25, 0.25, 0.5, 0.5)),
    )
    assert frame.analysis_image.shape == (30, 40, 3)
    assert np.all(frame.analysis_image == 200)


@pytest.mark.asyncio
async def test_sift_probe_limits_crop_and_runs_matching_off_event_loop(monkeypatch):
    import threading
    from importlib import import_module
    from openscan_firmware.models.scan_analysis import NormalizedCrop
    module = import_module('openscan_firmware.controllers.services.tasks.core.scan_analysis_task')
    main_thread = threading.get_ident()
    def analyze(first, second, **kwargs):
        assert threading.get_ident() != main_thread
        return {'feature_count': {'first': 10, 'second': 10}}
    monkeypatch.setattr(module, 'analyze_sift_pair', analyze)
    task = ScanAnalysisTask(Task(name='scan_analysis_task', task_type='scan_analysis_task'))
    frame = SimpleNamespace(analysis_image=np.zeros((10,10,3),dtype=np.uint8))
    task._capture_frame = AsyncMock(return_value=(frame, {}))
    crop = NormalizedCrop((0.1,0.1,0.8,0.8))
    context = SimpleNamespace(
        motors=SimpleNamespace(move_to_point=AsyncMock()),
        analysis_position=PolarPoint3D(theta=90,fi=0,r=1), settle_time_s=0,
        camera_settings=SimpleNamespace(model=CameraSettings(AF=False,manual_focus=12)),
        crop=crop, initial_calibration_steps=1,total_captures=12,
        per_position_calibration_steps=1,position_count=3,total_work=20,
    )
    result={}
    [update async for update in task._capture_sift_probe(
        context=context,sift_turntable_angle=5,sift_grid_rows=4,sift_grid_columns=4,
        sift_benchmark=False,result=result,
    )]
    assert result['analysis']['turntable_angle'] == 5
    for call in task._capture_frame.await_args_list:
        assert call.kwargs['analysis_max_dimension'] == 1280
        assert call.kwargs['analysis_crop'] is crop
