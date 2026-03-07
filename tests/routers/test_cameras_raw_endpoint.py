"""Tests for the GET /cameras/{camera_name}/raw endpoint."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.models.camera import CameraMetadata, PhotoData


def _make_dng_photo(sharpness: float | None = None) -> PhotoData:
    metadata = CameraMetadata(
        camera_name="mock_camera",
        camera_settings=CameraSettings(),
        raw_metadata={},
        sharpness_score=sharpness,
    )
    return PhotoData(
        data=io.BytesIO(b"fake_dng_bytes"),
        format="dng",
        camera_metadata=metadata,
    )


def _make_controller(photo: PhotoData, busy: bool = False) -> MagicMock:
    controller = MagicMock()
    controller.is_busy.return_value = busy
    controller.camera.name = "mock_camera"

    async def _photo_async(image_format: str = "jpeg"):
        return controller.photo(image_format)

    controller.photo_async = AsyncMock(side_effect=_photo_async)
    controller.photo.return_value = photo
    return controller


@pytest.fixture
def test_app(latest_router_path) -> FastAPI:
    """Minimal FastAPI app with only the cameras router — no lifespan/GPIO."""
    from importlib import import_module
    cameras_module = import_module(latest_router_path("cameras"))
    app = FastAPI()
    app.include_router(cameras_module.router)
    return app


class TestGetRaw:
    def test_returns_dng_bytes(self, test_app, latest_router_path):
        photo = _make_dng_photo()
        controller = _make_controller(photo)

        with patch(
            f"{latest_router_path('cameras')}.get_camera_controller",
            return_value=controller,
        ):
            with TestClient(test_app) as client:
                response = client.get("/cameras/mock_camera/raw")


        assert response.status_code == 200
        assert response.content == b"fake_dng_bytes"
        assert response.headers["content-type"] == "image/x-adobe-dng"
        assert 'filename="mock_camera_raw.dng"' in response.headers["content-disposition"]

    def test_sharpness_header_present_when_quality_gate_enabled(self, test_app, latest_router_path):
        photo = _make_dng_photo(sharpness=47.3)
        controller = _make_controller(photo)

        with patch(
            f"{latest_router_path('cameras')}.get_camera_controller",
            return_value=controller,
        ):
            with TestClient(test_app) as client:
                response = client.get("/cameras/mock_camera/raw")


        assert response.status_code == 200
        assert response.headers["x-sharpness-score"] == "47.3"

    def test_sharpness_header_absent_when_no_score(self, test_app, latest_router_path):
        photo = _make_dng_photo(sharpness=None)
        controller = _make_controller(photo)

        with patch(
            f"{latest_router_path('cameras')}.get_camera_controller",
            return_value=controller,
        ):
            with TestClient(test_app) as client:
                response = client.get("/cameras/mock_camera/raw")


        assert response.status_code == 200
        assert "x-sharpness-score" not in response.headers

    def test_returns_409_when_camera_busy(self, test_app, latest_router_path):
        controller = _make_controller(_make_dng_photo(), busy=True)

        with patch(
            f"{latest_router_path('cameras')}.get_camera_controller",
            return_value=controller,
        ):
            with TestClient(test_app) as client:
                response = client.get("/cameras/mock_camera/raw")


        assert response.status_code == 409

    def test_returns_404_when_camera_not_found(self, test_app, latest_router_path):
        with patch(
            f"{latest_router_path('cameras')}.get_camera_controller",
            side_effect=ValueError("camera not found"),
        ):
            with TestClient(test_app) as client:
                response = client.get("/cameras/missing_camera/raw")


        assert response.status_code == 404

    def test_returns_500_on_capture_error(self, test_app, latest_router_path):
        controller = MagicMock()
        controller.is_busy.return_value = False
        controller.photo_async = AsyncMock(side_effect=RuntimeError("sensor error"))

        with patch(
            f"{latest_router_path('cameras')}.get_camera_controller",
            return_value=controller,
        ):
            with TestClient(test_app) as client:
                response = client.get("/cameras/mock_camera/raw")


        assert response.status_code == 500

    def test_photo_async_called_with_dng_format(self, test_app, latest_router_path):
        photo = _make_dng_photo()
        controller = _make_controller(photo)

        with patch(
            f"{latest_router_path('cameras')}.get_camera_controller",
            return_value=controller,
        ):
            with TestClient(test_app) as client:
                client.get("/cameras/mock_camera/raw")


        controller.photo_async.assert_awaited_once_with(image_format="dng")
