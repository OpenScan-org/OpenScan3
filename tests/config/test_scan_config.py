from openscan_firmware.config.external_trigger_run import ExternalTriggerRunSettings
from openscan_firmware.config.scan import ScanSetting


def test_scan_settings_omit_unset_phi_fields_from_json_dump() -> None:
    settings = ScanSetting()

    payload = settings.model_dump(mode="json")

    assert "min_phi" not in payload
    assert "max_phi" not in payload


def test_external_trigger_run_settings_omit_unset_phi_fields_from_json_dump() -> None:
    settings = ExternalTriggerRunSettings(trigger_name="external-camera")

    payload = settings.model_dump(mode="json")

    assert "min_phi" not in payload
    assert "max_phi" not in payload


def test_external_trigger_run_settings_transfer_optional_phi_values() -> None:
    settings = ExternalTriggerRunSettings(
        trigger_name="external-camera",
        min_phi=45.0,
        max_phi=135.0,
    )

    scan_settings = settings.to_scan_settings()

    assert scan_settings.min_phi == 45.0
    assert scan_settings.max_phi == 135.0
