"""Tests for the next cameras photo endpoints."""

from __future__ import annotations

import asyncio
import io
import time
from importlib import import_module
from typing import Callable

import httpx
import numpy as np
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openscan_firmware.config.camera import CameraSettings
from openscan_firmware.models.camera import CameraMetadata, PhotoData


def _next_router_module_path(name: str) -> str:
    return f"openscan_firmware.routers.next.{name}"


class _FakeCameraController:
    def __init__(self, photo_data: PhotoData):
        self._photo_data = photo_data
        self.requested_formats: list[str] = []

    async def photo_async(self, image_format: str = "jpeg") -> PhotoData:
        self.requested_formats.append(image_format)
        return self._photo_data


class _ConcurrentFakeCameraController:
    def __init__(self):
        self.preview_calls = 0
        self.photo_calls = 0
        metadata = CameraMetadata(
            camera_name="cam0",
            camera_settings=CameraSettings(),
            raw_metadata={"driver": "test"},
        )
        self._photo_data = PhotoData(
            data=io.BytesIO(b"parallel-jpeg"),
            format="jpeg",
            camera_metadata=metadata,
        )

    async def preview_async(self):
        self.preview_calls += 1
        if self.preview_calls > 40:
            raise RuntimeError("stop stream")
        return b"frame-jpeg"

    async def photo_async(self, image_format: str = "jpeg") -> PhotoData:
        self.photo_calls += 1
        await asyncio.sleep(0)
        return self._photo_data


class _SnapshotBusyController:
    def is_busy(self) -> bool:
        return True

    def preview(self):
        raise AssertionError("preview() must not be called when controller is busy")


class _SlowPhotoConcurrentController:
    def __init__(self, delay_s: float = 2.0):
        self.delay_s = delay_s
        self.preview_calls = 0
        self.photo_calls = 0
        self.photo_in_flight = False
        metadata = CameraMetadata(
            camera_name="cam0",
            camera_settings=CameraSettings(),
            raw_metadata={"driver": "test"},
        )
        self._photo_data = PhotoData(
            data=io.BytesIO(b"slow-photo-jpeg"),
            format="jpeg",
            camera_metadata=metadata,
        )

    async def preview_async(self):
        self.preview_calls += 1
        if self.preview_calls > 60:
            raise RuntimeError("stop stream")
        return b"frame-jpeg"

    async def photo_async(self, image_format: str = "jpeg") -> PhotoData:
        self.photo_calls += 1
        self.photo_in_flight = True
        try:
            await asyncio.sleep(self.delay_s)
            return self._photo_data
        finally:
            self.photo_in_flight = False


class _SlowMetadataController:
    def __init__(self, delay_s: float = 2.0):
        self.delay_s = delay_s
        self.photo_calls = 0
        metadata = CameraMetadata(
            camera_name="cam0",
            camera_settings=CameraSettings(),
            raw_metadata={"driver": "slow-meta-test"},
        )
        self._photo_data = PhotoData(
            data=io.BytesIO(b"slow-metadata-dng"),
            format="dng",
            camera_metadata=metadata,
        )

    async def photo_async(self, image_format: str = "jpeg") -> PhotoData:
        self.photo_calls += 1
        await asyncio.sleep(self.delay_s)
        return self._photo_data


class _UnsupportedFormatController:
    def __init__(self, unsupported_formats: set[str]):
        self.unsupported_formats = unsupported_formats
        metadata = CameraMetadata(
            camera_name="cam0",
            camera_settings=CameraSettings(),
            raw_metadata={"driver": "unsupported-test"},
        )
        self._fallback_photo = PhotoData(
            data=io.BytesIO(b"jpeg-bytes"),
            format="jpeg",
            camera_metadata=metadata,
        )

    async def photo_async(self, image_format: str = "jpeg") -> PhotoData:
        if image_format in self.unsupported_formats:
            raise ValueError(f"Unsupported image format: {image_format}")
        return self._fallback_photo


class _ConcurrentPhotoController:
    def __init__(self, delay_s: float = 0.2, fail_calls: set[int] | None = None):
        self.delay_s = delay_s
        self.fail_calls = fail_calls or set()
        self._lock = asyncio.Lock()
        self.call_count = 0
        self.in_flight = 0
        self.max_in_flight = 0

    async def photo_async(self, image_format: str = "jpeg") -> PhotoData:
        self.call_count += 1
        call_index = self.call_count
        async with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                await asyncio.sleep(self.delay_s)
                if call_index in self.fail_calls:
                    raise RuntimeError("simulated capture failure")
                metadata = CameraMetadata(
                    camera_name="cam0",
                    camera_settings=CameraSettings(),
                    raw_metadata={"call_index": call_index},
                )
                return PhotoData(
                    data=io.BytesIO(f"jpeg-{call_index}".encode("ascii")),
                    format="jpeg",
                    camera_metadata=metadata,
                )
            finally:
                self.in_flight -= 1


@pytest.fixture
def cameras_router_path() -> Callable[[str], str]:
    return _next_router_module_path


@pytest.fixture
def cameras_app(monkeypatch: pytest.MonkeyPatch, cameras_router_path) -> FastAPI:
    module_path = cameras_router_path("cameras")
    cameras_router = import_module(module_path)
    cameras_router._photo_payload_cache.clear()

    app = FastAPI()
    app.include_router(cameras_router.router, prefix="/next")
    return app


@pytest_asyncio.fixture
async def cameras_client(cameras_app: FastAPI, cameras_router_path) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=cameras_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    cameras_router = import_module(cameras_router_path("cameras"))
    cameras_router._photo_payload_cache.clear()


def _make_photo_data(data, data_format: str) -> PhotoData:
    metadata = CameraMetadata(
        camera_name="cam0",
        camera_settings=CameraSettings(),
        raw_metadata={"driver": "test"},
    )
    return PhotoData(data=data, format=data_format, camera_metadata=metadata)


@pytest.mark.asyncio
async def test_get_photo_legacy_returns_raw_jpeg(monkeypatch, cameras_client, cameras_router_path):
    module_path = cameras_router_path("cameras")
    controller = _FakeCameraController(
        _make_photo_data(io.BytesIO(b"jpeg-bytes"), "jpeg")
    )
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    response = await cameras_client.get("/next/cameras/cam0/photo")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"jpeg-bytes"
    assert controller.requested_formats == ["jpeg"]


@pytest.mark.asyncio
async def test_get_photo_with_metadata_returns_payload_url_for_dng(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    controller = _FakeCameraController(
        _make_photo_data(io.BytesIO(b"dng-bytes"), "dng")
    )
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    response = await cameras_client.get(
        "/next/cameras/cam0/photo",
        params={"image_format": "dng", "with_metadata": "true"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "dng"
    assert payload["media_type"] == "image/x-adobe-dng"
    assert payload["filename"] == "photo.dng"
    assert payload["camera_metadata"]["camera_name"] == "cam0"
    assert payload["camera_metadata"]["raw_metadata"] == {"driver": "test"}
    assert payload["scan_metadata"] is None
    assert payload["expires_in_s"] == 90
    assert "/next/cameras/cam0/photo/payload/" in payload["payload_url"]
    assert controller.requested_formats == ["dng"]

    payload_response = await cameras_client.get(payload["payload_url"])

    assert payload_response.status_code == 200
    assert payload_response.headers["content-type"] == "image/x-adobe-dng"
    assert payload_response.content == b"dng-bytes"


@pytest.mark.asyncio
async def test_get_photo_with_metadata_returns_payload_url_for_raw_cr2(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    photo = _make_photo_data(io.BytesIO(b"raw-bytes"), "raw")
    photo.camera_metadata.raw_metadata["capture_name"] = "IMG_0001.CR2"
    controller = _FakeCameraController(photo)
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    response = await cameras_client.get(
        "/next/cameras/cam0/photo",
        params={"image_format": "raw", "with_metadata": "true"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "raw"
    assert payload["media_type"] == "image/x-canon-cr2"
    assert payload["filename"] == "photo.cr2"
    assert controller.requested_formats == ["raw"]

    payload_response = await cameras_client.get(payload["payload_url"])
    assert payload_response.status_code == 200
    assert payload_response.headers["content-type"] == "image/x-canon-cr2"
    assert payload_response.content == b"raw-bytes"


@pytest.mark.asyncio
async def test_get_photo_with_metadata_encodes_camera_name_in_payload_url(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    photo = _make_photo_data(io.BytesIO(b"jpeg-bytes"), "jpeg")
    controller = _FakeCameraController(photo)
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    response = await cameras_client.get(
        "/next/cameras/Canon%20EOS%20700D/photo",
        params={"image_format": "jpeg", "with_metadata": "true"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "Canon%20EOS%20700D" in payload["payload_url"]
    assert "Canon EOS 700D" not in payload["payload_url"]


@pytest.mark.asyncio
async def test_get_photo_rgb_array_returns_npy_payload(monkeypatch, cameras_client, cameras_router_path):
    module_path = cameras_router_path("cameras")
    array = np.array([[1, 2], [3, 4]], dtype=np.uint8)
    controller = _FakeCameraController(
        _make_photo_data(array, "rgb_array")
    )
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    response = await cameras_client.get(
        "/next/cameras/cam0/photo",
        params={"image_format": "rgb_array"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-npy"
    restored = np.load(io.BytesIO(response.content))
    np.testing.assert_array_equal(restored, array)
    assert controller.requested_formats == ["rgb_array"]


@pytest.mark.asyncio
async def test_payload_endpoint_returns_404_after_cache_miss(monkeypatch, cameras_client, cameras_router_path):
    module_path = cameras_router_path("cameras")
    controller = _FakeCameraController(
        _make_photo_data(io.BytesIO(b"jpeg-bytes"), "jpeg")
    )
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    response = await cameras_client.get("/next/cameras/cam0/photo/payload/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "Photo payload not found or expired."


@pytest.mark.asyncio
async def test_preview_stream_allows_parallel_photo_requests(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    controller = _ConcurrentFakeCameraController()
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    async with cameras_client.stream(
        "GET",
        "/next/cameras/cam0/preview",
        params={"mode": "stream", "fps": 10},
    ) as preview_response:
        assert preview_response.status_code == 200
        assert preview_response.headers["content-type"].startswith("multipart/x-mixed-replace")

        async def _consume_one_chunk():
            async for chunk in preview_response.aiter_bytes():
                if chunk:
                    return chunk
            return b""

        preview_task = asyncio.create_task(_consume_one_chunk())
        await asyncio.sleep(0.05)

        photo_response_1 = await cameras_client.get("/next/cameras/cam0/photo")
        photo_response_2 = await cameras_client.get(
            "/next/cameras/cam0/photo",
            params={"image_format": "jpeg"},
        )

        assert photo_response_1.status_code == 200
        assert photo_response_1.content == b"parallel-jpeg"
        assert photo_response_2.status_code == 200
        assert photo_response_2.content == b"parallel-jpeg"

        first_preview_chunk = await preview_task
        assert b"Content-Type: image/jpeg" in first_preview_chunk

    assert controller.preview_calls >= 1
    assert controller.photo_calls == 2


@pytest.mark.asyncio
async def test_preview_snapshot_returns_409_when_busy(monkeypatch, cameras_client, cameras_router_path):
    module_path = cameras_router_path("cameras")
    controller = _SnapshotBusyController()
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    response = await cameras_client.get(
        "/next/cameras/cam0/preview",
        params={"mode": "snapshot"},
    )

    assert response.status_code == 409
    assert "Camera is busy" in response.json()["detail"]


@pytest.mark.asyncio
async def test_preview_stream_continues_while_photo_capture_is_slow(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    controller = _SlowPhotoConcurrentController(delay_s=2.0)
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    async with cameras_client.stream(
        "GET",
        "/next/cameras/cam0/preview",
        params={"mode": "stream", "fps": 10},
    ) as preview_response:
        assert preview_response.status_code == 200

        photo_task = asyncio.create_task(
            cameras_client.get("/next/cameras/cam0/photo", params={"image_format": "jpeg"})
        )
        await asyncio.sleep(0)

        preview_chunks_with_jpeg_header = 0
        async for chunk in preview_response.aiter_bytes():
            if b"Content-Type: image/jpeg" in chunk:
                preview_chunks_with_jpeg_header += 1
            if preview_chunks_with_jpeg_header >= 3:
                break

        assert preview_chunks_with_jpeg_header >= 1
        # Slow capture should still be running while preview keeps producing frames.
        assert photo_task.done() is False

        photo_response = await asyncio.wait_for(photo_task, timeout=5)
        assert photo_response.status_code == 200
        assert photo_response.content == b"slow-photo-jpeg"

    assert controller.preview_calls >= 1
    assert controller.photo_calls == 1


@pytest.mark.asyncio
async def test_with_metadata_slow_capture_returns_payload_url_and_cached_payload(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    controller = _SlowMetadataController(delay_s=2.0)
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    metadata_task = asyncio.create_task(
        cameras_client.get(
            "/next/cameras/cam0/photo",
            params={"image_format": "dng", "with_metadata": "true"},
        )
    )
    await asyncio.sleep(0.1)
    assert metadata_task.done() is False

    metadata_response = await asyncio.wait_for(metadata_task, timeout=6)
    assert metadata_response.status_code == 200
    metadata_payload = metadata_response.json()
    assert metadata_payload["format"] == "dng"
    assert metadata_payload["media_type"] == "image/x-adobe-dng"
    assert metadata_payload["camera_metadata"]["raw_metadata"] == {"driver": "slow-meta-test"}
    assert "/next/cameras/cam0/photo/payload/" in metadata_payload["payload_url"]

    payload_response = await cameras_client.get(metadata_payload["payload_url"])
    assert payload_response.status_code == 200
    assert payload_response.headers["content-type"] == "image/x-adobe-dng"
    assert payload_response.content == b"slow-metadata-dng"

    assert controller.photo_calls == 1


@pytest.mark.asyncio
async def test_payload_url_returns_404_after_expiry(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    cameras_router = import_module(module_path)
    controller = _FakeCameraController(
        _make_photo_data(io.BytesIO(b"jpeg-bytes"), "jpeg")
    )
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    metadata_response = await cameras_client.get(
        "/next/cameras/cam0/photo",
        params={"image_format": "jpeg", "with_metadata": "true"},
    )
    assert metadata_response.status_code == 200
    payload_url = metadata_response.json()["payload_url"]
    payload_id = payload_url.rsplit("/", 1)[-1]

    assert payload_id in cameras_router._photo_payload_cache
    cameras_router._photo_payload_cache[payload_id].expires_at_monotonic = time.monotonic() - 1

    expired_payload_response = await cameras_client.get(payload_url)
    assert expired_payload_response.status_code == 404
    assert expired_payload_response.json()["detail"] == "Photo payload not found or expired."
    assert payload_id not in cameras_router._photo_payload_cache


@pytest.mark.asyncio
async def test_payload_url_returns_404_for_camera_mismatch(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    controller = _FakeCameraController(
        _make_photo_data(io.BytesIO(b"jpeg-bytes"), "jpeg")
    )
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    metadata_response = await cameras_client.get(
        "/next/cameras/cam0/photo",
        params={"image_format": "jpeg", "with_metadata": "true"},
    )
    assert metadata_response.status_code == 200
    payload_url = metadata_response.json()["payload_url"]
    payload_id = payload_url.rsplit("/", 1)[-1]

    wrong_camera_response = await cameras_client.get(
        f"/next/cameras/not-cam0/photo/payload/{payload_id}"
    )
    assert wrong_camera_response.status_code == 404
    assert wrong_camera_response.json()["detail"] == "Photo payload not found or expired."


@pytest.mark.asyncio
async def test_photo_returns_400_when_controller_rejects_requested_format(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    controller = _UnsupportedFormatController(unsupported_formats={"dng"})
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    response = await cameras_client.get(
        "/next/cameras/cam0/photo",
        params={"image_format": "dng"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported image format: dng"


@pytest.mark.asyncio
async def test_photo_with_metadata_returns_400_when_format_unsupported(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    controller = _UnsupportedFormatController(unsupported_formats={"rgb_array"})
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    response = await cameras_client.get(
        "/next/cameras/cam0/photo",
        params={"image_format": "rgb_array", "with_metadata": "true"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported image format: rgb_array"


@pytest.mark.asyncio
async def test_concurrent_photo_requests_are_serialized_by_controller(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    controller = _ConcurrentPhotoController(delay_s=0.2)
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    response_1_task = asyncio.create_task(cameras_client.get("/next/cameras/cam0/photo"))
    response_2_task = asyncio.create_task(cameras_client.get("/next/cameras/cam0/photo"))

    response_1, response_2 = await asyncio.gather(response_1_task, response_2_task)

    assert response_1.status_code == 200
    assert response_2.status_code == 200
    assert response_1.content in (b"jpeg-1", b"jpeg-2")
    assert response_2.content in (b"jpeg-1", b"jpeg-2")
    assert response_1.content != response_2.content
    assert controller.call_count == 2
    assert controller.max_in_flight == 1


@pytest.mark.asyncio
async def test_concurrent_photo_requests_mixed_metadata_and_raw(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    controller = _ConcurrentPhotoController(delay_s=0.2)
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    metadata_task = asyncio.create_task(
        cameras_client.get(
            "/next/cameras/cam0/photo",
            params={"with_metadata": "true", "image_format": "jpeg"},
        )
    )
    raw_task = asyncio.create_task(cameras_client.get("/next/cameras/cam0/photo"))

    metadata_response, raw_response = await asyncio.gather(metadata_task, raw_task)

    assert metadata_response.status_code == 200
    assert raw_response.status_code == 200
    metadata_payload = metadata_response.json()
    assert "/next/cameras/cam0/photo/payload/" in metadata_payload["payload_url"]
    payload_response = await cameras_client.get(metadata_payload["payload_url"])
    assert payload_response.status_code == 200
    assert payload_response.content in (b"jpeg-1", b"jpeg-2")
    assert raw_response.content in (b"jpeg-1", b"jpeg-2")
    assert controller.call_count == 2
    assert controller.max_in_flight == 1


@pytest.mark.asyncio
async def test_concurrent_with_metadata_requests_have_distinct_payload_urls(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    controller = _ConcurrentPhotoController(delay_s=0.2)
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    response_1_task = asyncio.create_task(
        cameras_client.get("/next/cameras/cam0/photo", params={"with_metadata": "true"})
    )
    response_2_task = asyncio.create_task(
        cameras_client.get("/next/cameras/cam0/photo", params={"with_metadata": "true"})
    )

    response_1, response_2 = await asyncio.gather(response_1_task, response_2_task)
    assert response_1.status_code == 200
    assert response_2.status_code == 200

    payload_url_1 = response_1.json()["payload_url"]
    payload_url_2 = response_2.json()["payload_url"]
    assert payload_url_1 != payload_url_2

    payload_1, payload_2 = await asyncio.gather(
        cameras_client.get(payload_url_1),
        cameras_client.get(payload_url_2),
    )
    assert payload_1.status_code == 200
    assert payload_2.status_code == 200
    assert payload_1.content in (b"jpeg-1", b"jpeg-2")
    assert payload_2.content in (b"jpeg-1", b"jpeg-2")
    assert payload_1.content != payload_2.content
    assert controller.call_count == 2
    assert controller.max_in_flight == 1


@pytest.mark.asyncio
async def test_concurrent_photo_requests_one_fails_one_succeeds(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    controller = _ConcurrentPhotoController(delay_s=0.1, fail_calls={1})
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    response_1_task = asyncio.create_task(cameras_client.get("/next/cameras/cam0/photo"))
    response_2_task = asyncio.create_task(cameras_client.get("/next/cameras/cam0/photo"))

    response_1, response_2 = await asyncio.gather(response_1_task, response_2_task)

    status_codes = sorted([response_1.status_code, response_2.status_code])
    assert status_codes == [200, 503]
    success_response = response_1 if response_1.status_code == 200 else response_2
    failure_response = response_1 if response_1.status_code == 503 else response_2
    assert success_response.content == b"jpeg-2"
    assert failure_response.json()["detail"] == "simulated capture failure"
    assert controller.call_count == 2
    assert controller.max_in_flight == 1


@pytest.mark.asyncio
async def test_payload_url_can_be_reused_before_expiry(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    controller = _FakeCameraController(_make_photo_data(io.BytesIO(b"jpeg-bytes"), "jpeg"))
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: controller)

    metadata_response = await cameras_client.get(
        "/next/cameras/cam0/photo",
        params={"with_metadata": "true", "image_format": "jpeg"},
    )
    assert metadata_response.status_code == 200
    payload_url = metadata_response.json()["payload_url"]

    first_payload_response = await cameras_client.get(payload_url)
    second_payload_response = await cameras_client.get(payload_url)

    assert first_payload_response.status_code == 200
    assert second_payload_response.status_code == 200
    assert first_payload_response.content == b"jpeg-bytes"
    assert second_payload_response.content == b"jpeg-bytes"


@pytest.mark.asyncio
async def test_payload_cache_is_capped_to_prevent_unbounded_growth(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    cameras_router = import_module(module_path)
    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: _FakeCameraController(
        _make_photo_data(io.BytesIO(b"jpeg-bytes"), "jpeg")
    ))
    monkeypatch.setattr(cameras_router, "_MAX_PAYLOAD_CACHE_ENTRIES", 3)

    created_payload_ids: list[str] = []
    for _ in range(6):
        metadata_response = await cameras_client.get(
            "/next/cameras/cam0/photo",
            params={"with_metadata": "true", "image_format": "jpeg"},
        )
        assert metadata_response.status_code == 200
        payload_url = metadata_response.json()["payload_url"]
        created_payload_ids.append(payload_url.rsplit("/", 1)[-1])

    assert len(cameras_router._photo_payload_cache) == 3
    remaining_ids = set(cameras_router._photo_payload_cache.keys())
    assert remaining_ids.issubset(set(created_payload_ids))
    assert set(created_payload_ids[-3:]).issubset(remaining_ids)


@pytest.mark.asyncio
async def test_payload_cache_byte_limit_evicts_old_entries(
    monkeypatch,
    cameras_client,
    cameras_router_path,
):
    module_path = cameras_router_path("cameras")
    cameras_router = import_module(module_path)
    monkeypatch.setattr(cameras_router, "_MAX_PAYLOAD_CACHE_ENTRIES", 10)
    monkeypatch.setattr(cameras_router, "_MAX_PAYLOAD_CACHE_BYTES", 10)

    class _LargePayloadController:
        async def photo_async(self, image_format: str = "jpeg") -> PhotoData:
            metadata = CameraMetadata(
                camera_name="cam0",
                camera_settings=CameraSettings(),
                raw_metadata={"driver": "test"},
            )
            return PhotoData(
                data=io.BytesIO(b"123456"),
                format="jpeg",
                camera_metadata=metadata,
            )

    monkeypatch.setattr(f"{module_path}.get_camera_controller", lambda _name: _LargePayloadController())

    response_1 = await cameras_client.get(
        "/next/cameras/cam0/photo",
        params={"with_metadata": "true", "image_format": "jpeg"},
    )
    response_2 = await cameras_client.get(
        "/next/cameras/cam0/photo",
        params={"with_metadata": "true", "image_format": "jpeg"},
    )
    assert response_1.status_code == 200
    assert response_2.status_code == 200

    payload_1 = response_1.json()["payload_url"]
    payload_2 = response_2.json()["payload_url"]

    # Byte cap is 10, each payload is 6 bytes -> only the newer payload survives.
    first_payload_response = await cameras_client.get(payload_1)
    second_payload_response = await cameras_client.get(payload_2)
    assert first_payload_response.status_code == 404
    assert second_payload_response.status_code == 200
