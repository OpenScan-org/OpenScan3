from openscan_firmware.config.cloud import (
    DEFAULT_CLOUD_HOST,
    DEFAULT_CLOUD_PASSWORD,
    DEFAULT_CLOUD_USER,
    DEFAULT_SPLIT_SIZE,
    load_cloud_settings_from_env,
)


def test_load_cloud_settings_from_env_requires_token(monkeypatch):
    monkeypatch.delenv("OPENSCANCLOUD_TOKEN", raising=False)

    assert load_cloud_settings_from_env() is None


def test_load_cloud_settings_from_env_uses_project_defaults(monkeypatch):
    monkeypatch.setenv("OPENSCANCLOUD_TOKEN", "token-123")
    monkeypatch.setenv("OPENSCANCLOUD_USER", "custom-user")
    monkeypatch.setenv("OPENSCANCLOUD_PASSWORD", "custom-pass")
    monkeypatch.setenv("OPENSCANCLOUD_HOST", "http://custom-host")
    monkeypatch.setenv("OPENSCANCLOUD_SPLIT_SIZE", "987654")

    settings = load_cloud_settings_from_env()

    assert settings is not None
    assert settings.user == DEFAULT_CLOUD_USER
    assert settings.password == DEFAULT_CLOUD_PASSWORD
    assert str(settings.host).rstrip("/") == DEFAULT_CLOUD_HOST.rstrip("/")
    assert settings.split_size == DEFAULT_SPLIT_SIZE
    assert settings.token == "token-123"
