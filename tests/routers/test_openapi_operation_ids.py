"""Keep generated OpenAPI operation IDs stable and readable."""

import pytest

from openscan_firmware.main import make_version_app


@pytest.mark.parametrize("version", ["0.8", "0.9", "next"])
def test_camera_operation_ids_use_unversioned_route_names(version: str) -> None:
    schema = make_version_app(version).openapi()

    expected = {
        ("/cameras/", "get"): "get_cameras",
        ("/cameras/{camera_name}", "get"): "get_camera",
        ("/cameras/{camera_name}/preview", "get"): "get_preview",
        ("/cameras/{camera_name}/photo", "get"): "get_photo",
        ("/cameras/{camera_name}/restart", "post"): "restart_camera",
        ("/cameras/{name}/settings", "get"): "get_camera_name_settings",
    }

    if version in ("0.9", "next"):
        expected[("/cameras/{camera_name}/photo/payload/{payload_id}", "get")] = "get_photo_payload"

    for (path, method), operation_id in expected.items():
        assert schema["paths"][path][method]["operationId"] == operation_id

    operation_ids = [
        operation["operationId"]
        for path_item in schema["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))
