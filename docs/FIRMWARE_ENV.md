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

## Task-related flags

| Env var | Purpose | Default | Notes |
| --- | --- | --- | --- |
| `OPENSCAN_TASK_AUTODISCOVERY` | Enables namespace scanning for tasks. | `0` (disabled) | When `0`, the firmware registers the built-in core tasks manually for stability. Set to `1` for development/power users. Full behavior is documented in [docs/TASKS.md](./TASKS.md#autodiscovery). |
| `OPENSCAN_TASK_OVERRIDE_ON_CONFLICT` | Allow later registrations to replace existing task names. | `0` | Only meaningful when autodiscovery is enabled. Use with care when swapping core tasks (see [docs/TASKS.md](./TASKS.md#advanced-override-power-users-only)). |

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

