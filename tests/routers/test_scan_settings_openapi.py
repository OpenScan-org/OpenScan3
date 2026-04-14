from openscan_firmware.main import make_version_app


def _schema_properties(schema: dict, schema_name: str) -> tuple[dict, list]:
    component = schema["components"]["schemas"][schema_name]
    return component["properties"], component.get("required", [])


def test_next_projects_openapi_exposes_optional_phi_scan_settings() -> None:
    schema = make_version_app("next").openapi()

    properties, required = _schema_properties(schema, "ScanSetting")

    assert "min_phi" in properties
    assert "max_phi" in properties
    assert "min_phi" not in required
    assert "max_phi" not in required


def test_next_external_trigger_openapi_exposes_optional_phi_settings() -> None:
    schema = make_version_app("next").openapi()

    properties, required = _schema_properties(schema, "ExternalTriggerRunSettings")

    assert "min_phi" in properties
    assert "max_phi" in properties
    assert "min_phi" not in required
    assert "max_phi" not in required
