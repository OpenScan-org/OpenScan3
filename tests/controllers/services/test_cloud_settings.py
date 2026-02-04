import json
from pathlib import Path

from openscan_firmware.config.cloud import CloudSettings
from openscan_firmware.controllers.services import cloud_settings


def _build_cloud_settings() -> CloudSettings:
    return CloudSettings(
        user="api-user",
        password="secret",
        token="token-value",
        host="http://example.com",
        split_size=1024,
    )


def _patch_resolver(monkeypatch, base_dir: Path) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)

    def fake_resolve(subdir: str, filename: str) -> Path:
        return base_dir / subdir / filename

    monkeypatch.setattr(cloud_settings, "resolve_settings_file", fake_resolve)


def test_save_persistent_cloud_settings_targets_firmware_dir(tmp_path, monkeypatch):
    base_dir = tmp_path / "settings"
    _patch_resolver(monkeypatch, base_dir)

    settings = _build_cloud_settings()
    target = base_dir / "firmware" / "cloud.json"

    written_path = cloud_settings.save_persistent_cloud_settings(settings)

    assert written_path == target
    payload = json.loads(target.read_text())
    assert payload["user"] == "api-user"
    assert target.exists()


def test_load_persistent_cloud_settings_reads_new_location(tmp_path, monkeypatch):
    base_dir = tmp_path / "settings"
    _patch_resolver(monkeypatch, base_dir)

    firmware_file = base_dir / "firmware" / "cloud.json"
    firmware_file.parent.mkdir(parents=True, exist_ok=True)
    settings = _build_cloud_settings()
    firmware_file.write_text(settings.model_dump_json())

    loaded = cloud_settings.load_persistent_cloud_settings()

    assert loaded == settings


def test_settings_file_exists_detects_firmware_location(tmp_path, monkeypatch):
    base_dir = tmp_path / "settings"
    _patch_resolver(monkeypatch, base_dir)

    # No files yet
    assert cloud_settings.settings_file_exists() is False

    firmware_file = base_dir / "firmware" / "cloud.json"
    firmware_file.parent.mkdir(parents=True, exist_ok=True)
    firmware_file.write_text("{}", encoding="utf-8")
    assert cloud_settings.settings_file_exists() is True
