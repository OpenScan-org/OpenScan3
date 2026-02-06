from openscan_firmware.utils.dir_paths import resolve_settings_dir


def test_resolve_settings_dir_uses_env_base(tmp_path, monkeypatch):
    env_base = tmp_path / "custom"
    env_base.mkdir()
    monkeypatch.setenv("OPENSCAN_SETTINGS_DIR", str(env_base))

    result = resolve_settings_dir("device")
    assert result == env_base

    device_subdir = env_base / "device"
    device_subdir.mkdir()

    result_with_subdir = resolve_settings_dir("device")
    assert result_with_subdir == device_subdir



def test_resolve_settings_dir_path_without_subdir(tmp_path, monkeypatch):
    env_subdir = tmp_path / "custom" / "device"
    env_subdir.mkdir(parents=True)
    monkeypatch.setenv("OPENSCAN_SETTINGS_DIR", str(env_subdir))

    result = resolve_settings_dir("device")
    assert result == env_subdir

