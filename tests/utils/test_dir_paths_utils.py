from pathlib import Path

import openscan_firmware.utils.dir_paths as dir_paths


def test_resolve_settings_dir_uses_env_base(tmp_path, monkeypatch):
    env_base = tmp_path / "custom"
    env_base.mkdir()
    (env_base / "device").mkdir()
    monkeypatch.setenv("OPENSCAN_SETTINGS_DIR", str(env_base))

    result = dir_paths.resolve_settings_dir("device")
    assert result == env_base / "device"


def test_resolve_runtime_dir_uses_env_override(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "runtime"
    sub_dir = runtime_dir / "state"
    sub_dir.mkdir(parents=True)
    monkeypatch.setenv("OPENSCAN_RUNTIME_DIR", str(runtime_dir))

    assert dir_paths.resolve_runtime_dir("state") == sub_dir


def test_resolve_runtime_dir_falls_back_when_env_and_system_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENSCAN_RUNTIME_DIR", raising=False)

    fallback = tmp_path / "runtime-fallback"
    monkeypatch.setitem(
        dir_paths.PATH_PROFILES,
        "runtime",
        dir_paths.PathProfile(
            env_var="OPENSCAN_RUNTIME_DIR",
            system_path=tmp_path / "missing-system",
            fallback_path=fallback,
        ),
    )

    assert dir_paths.resolve_runtime_dir() == fallback


def test_resolve_logs_dir_prefers_system_when_env_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENSCAN_LOG_DIR", raising=False)
    system_dir = tmp_path / "logs"
    system_dir.mkdir()

    monkeypatch.setitem(
        dir_paths.PATH_PROFILES,
        "logs",
        dir_paths.PathProfile(
            env_var="OPENSCAN_LOG_DIR",
            system_path=system_dir,
            fallback_path=Path("./logs-fallback"),
        ),
    )

    assert dir_paths.resolve_logs_dir() == system_dir


def test_resolve_projects_dir_returns_fallback_when_system_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENSCAN_PROJECT_DIR", raising=False)
    fallback_dir = tmp_path / "projects"

    monkeypatch.setitem(
        dir_paths.PATH_PROFILES,
        "projects",
        dir_paths.PathProfile(
            env_var="OPENSCAN_PROJECT_DIR",
            system_path=tmp_path / "missing-system",
            fallback_path=fallback_dir,
        ),
    )

    assert dir_paths.resolve_projects_dir() == fallback_dir


def test_resolve_community_tasks_dir_uses_env_and_subdir_if_exists(tmp_path, monkeypatch):
    base_dir = tmp_path / "community"
    sub_dir = base_dir / "plugins"
    sub_dir.mkdir(parents=True)
    monkeypatch.setenv("OPENSCAN_COMMUNITY_TASKS_DIR", str(base_dir))

    assert dir_paths.resolve_community_tasks_dir("plugins") == sub_dir


def test_resolve_community_tasks_dir_falls_back_to_base_if_subdir_missing(tmp_path, monkeypatch):
    base_dir = tmp_path / "community"
    base_dir.mkdir()
    monkeypatch.setenv("OPENSCAN_COMMUNITY_TASKS_DIR", str(base_dir))

    assert dir_paths.resolve_community_tasks_dir("unknown") == base_dir / "unknown"



def test_resolve_settings_dir_path_without_subdir(tmp_path, monkeypatch):
    env_subdir = tmp_path / "custom" / "device"
    env_subdir.mkdir(parents=True)
    monkeypatch.setenv("OPENSCAN_SETTINGS_DIR", str(env_subdir))

    result = dir_paths.resolve_settings_dir("device")
    assert result == env_subdir / "device"

