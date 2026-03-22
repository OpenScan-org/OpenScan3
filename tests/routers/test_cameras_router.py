from __future__ import annotations

import io
from unittest.mock import AsyncMock

import pytest

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.models.camera import CameraMetadata, PhotoData
import openscan_firmware.routers.next.cameras as cameras_next_module
import openscan_firmware.routers.v0_8.cameras as cameras_v0_8_module


def _make_photo_data(payload: bytes = b"jpeg-bytes") -> PhotoData:
    return PhotoData(
        data=io.BytesIO(payload),
        format="jpeg",
        camera_metadata=CameraMetadata(
            camera_name="mock_camera",
            camera_settings=CameraSettings(shutter=400),
            raw_metadata={},
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "grayscale", "expected_format"),
    [
        (cameras_next_module, False, "jpeg"),
        (cameras_next_module, True, "grayscale_jpeg"),
        (cameras_v0_8_module, False, "jpeg"),
        (cameras_v0_8_module, True, "grayscale_jpeg"),
    ],
)
async def test_get_photo_selects_requested_format(
    monkeypatch: pytest.MonkeyPatch,
    module,
    grayscale: bool,
    expected_format: str,
):
    controller = AsyncMock()
    controller.photo_async.return_value = _make_photo_data()

    monkeypatch.setattr(
        module,
        "get_camera_controller",
        lambda camera_name: controller,
        raising=False,
    )

    response = await module.get_photo("mock_camera", grayscale=grayscale)

    controller.photo_async.assert_awaited_once_with(expected_format)
    assert response.status_code == 200
    assert response.body == b"jpeg-bytes"
    assert response.media_type == "image/jpeg"


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [cameras_next_module, cameras_v0_8_module])
async def test_get_photo_returns_500_on_capture_error(monkeypatch: pytest.MonkeyPatch, module):
    controller = AsyncMock()
    controller.photo_async.side_effect = RuntimeError("camera exploded")

    monkeypatch.setattr(
        module,
        "get_camera_controller",
        lambda camera_name: controller,
        raising=False,
    )

    response = await module.get_photo("mock_camera", grayscale=True)

    controller.photo_async.assert_awaited_once_with("grayscale_jpeg")
    assert response.status_code == 500
    assert response.body == b"camera exploded"
