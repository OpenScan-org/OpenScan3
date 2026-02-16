# Scripts

This folder contains helper utilities that aren't part of the runtime firmware but assist with development workflows.

## `generate_openapi_json.py`
This script renders the OpenAPI schemas for every API version supported by `openscan_firmware`.

### How it works
1. It ensures the repo root is on `sys.path` so the package can be imported without installation.
2. It imports `make_version_app`, `SUPPORTED_VERSIONS`, and `LATEST` from `openscan_firmware.main`.
3. For each version in `SUPPORTED_VERSIONS`, it instantiates the corresponding FastAPI app and writes `scripts/openapi/openapi_v<VERSION>.json`.
4. It additionally produces alias files:
   - `openapi_latest.json` (points to the version marked as `LATEST`)
   - `openapi_next.json` (always generated from the `next` router)

### Usage
Run the script from the repo root (the script adjusts `sys.path`, so no extra install step is required):

```sh
python scripts/generate_openapi_json.py
```

All artifacts land in `scripts/openapi/`. Commit these files when you change API routes or schemas so downstream clients can stay in sync.

### Configuration
The script has no CLI switches. Behavior is driven by the firmware code:
- `SUPPORTED_VERSIONS` controls which `/vX.Y/` routers get exported.
- `LATEST` selects the version used for `openapi_latest.json`.
- The `next` alias is always generated to reflect the bleeding-edge router.

If you need to add knobs (e.g., output path, specific versions), extend the script and document the new options here.
