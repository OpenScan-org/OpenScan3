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

## `collect_benchy_socket_analysis.py`

Collects scan-analysis reports for the Benchy on the 3 cm, 5 cm, 7 cm, and 10 cm
turntable sockets. It waits for ENTER between sockets and switches the ringlight
off while the socket is being changed.

```sh
python scripts/collect_benchy_socket_analysis.py
```

The default device URL is `http://openscan3-imx519/api`. Camera and light names
are discovered through the API when omitted. Override them with
`--camera-name` and `--light-name` if needed. Use `--base-url` and
`--output-dir` to choose the device and where complete task responses are written.
Each position captures autofocus plus fixed focus values `10`, `12`, and `15` by
default. Use `--focus-values` to change the fixed series, `--no-autofocus` to
skip autofocus, or `--no-debug-images` to skip the small inspection images.

## `analyze_benchy_raw_series.py`

Analyzes the stored original RGB arrays without contacting the scanner. It
selects the newest complete socket run by default and writes one ROI mask per
focus series, a consensus mask, a cleaned object mask, and `summary.json` to
`debug/benchy-raw-series/`.

```sh
python scripts/analyze_benchy_raw_series.py
```

Use `--run 20261006-184514` to select a specific run.

## `collect_benchy_raw_series.py`

Captures one serial Benchy series through the camera photo endpoint and stores
each original RGB array as `.npy`, with its API metadata in a neighboring JSON
file. The camera settings and turntable position are restored when the script
finishes.

```sh
python scripts/collect_benchy_raw_series.py
```

The script waits for ENTER between the 3 cm, 5 cm, 7 cm, and 10 cm sockets.
It starts at turntable angle `90` and captures autofocus plus fixed focus values
`10`, `12`, and `15` at offsets `0`, `120`, and `-120`. Use
`--socket-height-cm 5` to capture one socket only.
