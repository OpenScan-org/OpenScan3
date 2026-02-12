# OpenScan Firmware – Environment & Startup Reference

This document summarizes the runtime configuration knobs for the firmware
process. Use it as a quick reference when deploying the service on a
Raspberry Pi or when running it locally for development.

## Directory overrides

| Env var | Purpose | Default (system) | Fallback (project) |
| --- | --- | --- | --- |
| `OPENSCAN_SETTINGS_DIR` | Base directory for persisted JSON configs (device, firmware, cloud). | `/etc/openscan3` | `./settings` |
| `OPENSCAN_LOG_DIR` | Location for `openscan_firmware.log` and JSON logs. | `/var/log/openscan3` | `./logs` |
| `OPENSCAN_PROJECT_DIR` | Storage for captured scan projects (images, metadata). | `/var/openscan3/projects` | `./projects` |
| `OPENSCAN_RUNTIME_DIR` | Holds runtime state such as `firmware_state.lock`. | `/var/openscan3` | `./data` |
| `OPENSCAN_COMMUNITY_TASKS_DIR` | Optional directory with community task packages. | `/var/openscan3/community-tasks` | `./openscan_firmware/tasks/community` |

All helpers live in `openscan_firmware.utils.dir_paths`. They resolve the
path in this order: explicit env var → system path (if it exists) → project
fallback. Subdirectories (e.g. `resolve_settings_dir("device")`) append to the
resolved base path.

## Cloud credentials

Set the following variables to enable uploads via
`openscan_firmware.config.cloud.load_cloud_settings_from_env()`:

| Env var | Required | Description |
| --- | --- | --- |
| `OPENSCANCLOUD_USER` | ✅ | Basic-auth username for the cloud API. |
| `OPENSCANCLOUD_PASSWORD` | ✅ | Basic-auth password. |
| `OPENSCANCLOUD_TOKEN` | ✅ | API token identifying the device/user. |
| `OPENSCANCLOUD_HOST` | Optional | Override the cloud base URL (defaults to `http://openscanfeedback.dnsuser.de:1334`). |
| `OPENSCANCLOUD_SPLIT_SIZE` | Optional | Max upload chunk size in bytes (defaults to `200_000_000`). |

At startup the firmware tries, in order:

1. `openscan_firmware.controllers.services.cloud_settings.load_persistent_cloud_settings()`
   (JSON file under `settings/firmware/cloud.json`).
2. Environment variables (table above).
3. If neither is present, uploads remain disabled and a warning is logged.

## Task autodiscovery settings

Task management is configured via `settings/firmware/openscan_firmware.json`:

- `task_autodiscovery_enabled` (default `true`).
- `task_autodiscovery_namespaces`: list of Python packages to scan.
- `task_autodiscovery_include_subpackages`: recurse into subpackages.
- `task_autodiscovery_ignore_modules`: skip modules by name.
- `task_raise_on_missing_name`: keep failing when a task omits/violates its explicit name.
- Required core tasks (`scan_task`, `focus_stacking_task`, `cloud_upload_task`, `cloud_download_task`) are enforced directly in code; startup fails fast if any are missing.

When autodiscovery is disabled, `ScanTask`, `FocusStackingTask`, `CloudUploadTask`, and
`CloudDownloadTask` are registered manually for convenience.

## Firmware state & telemetry

- The firmware writes `firmware_state.lock` inside `OPENSCAN_RUNTIME_DIR` to
  track:
  - Schema version (`version`).
  - `last_seen_firmware_version` (current package version).
  - `unclean_shutdown`: runtime flag set to `True` on startup and to `False`
    in `cleanup_and_exit()`.
  - `last_shutdown_was_unclean`: snapshot of the state before the current
    boot, so clients can highlight a previously failed shutdown.
  - `updated_at`: ISO timestamp of the last write.
- `/next/openscan` exposes `last_shutdown_was_unclean`, disk usage for runtime
  and projects, plus `uptime_seconds`. The response is typed via
  `SoftwareInfoResponse` for easy client consumption.

## Startup & shutdown flow

1. `uvicorn` launches `openscan_firmware.main:app` (default host `0.0.0.0`,
   port `8000`).
2. The FastAPI `lifespan` handler:
   - Calls `setup_logging()` using `advanced_logging.json` if available.
   - Logs firmware version and API compatibility.
   - Invokes `handle_startup()` to warn about unclean shutdowns and mark the
     new run as "unclean" until cleanup finishes.
   - Initializes hardware via `device_controller.initialize(...)`.
   - Instantiates the `TaskManager`, runs autodiscovery, and restores
     persisted tasks.
3. On shutdown the lifespan context closes hardware controllers via
   `device_controller.cleanup_and_exit()`, calls `mark_clean_shutdown()`, and
   flushes logging.

With this overview you can quickly locate the relevant knobs when deploying
OpenScan3 or debugging field issues.
