"""Generate OpenAPI JSON schemas for every supported API version."""

from pathlib import Path
import sys
import json

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parents[0]
print(f"[openapi] script dir: {SCRIPT_DIR}")
print(f"[openapi] repo root candidate: {ROOT_DIR}")

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
    print(f"[openapi] inserted repo root at sys.path[0]")

print("[openapi] sys.path head:")
for idx, entry in enumerate(sys.path[:5]):
    print(f"  {idx}: {entry}")

from openscan import __file__ as openscan_file  # type: ignore
from openscan.main import make_version_app, SUPPORTED_VERSIONS, LATEST

print(f"[openapi] using openscan module at: {openscan_file}")
print(f"[openapi] supported versions: {SUPPORTED_VERSIONS}")
print(f"[openapi] latest alias: {LATEST}")


def write_schema_for_app(output_path: Path, app) -> None:
    app.openapi_schema = None  # Reset cache to ensure fresh generation
    schema = app.openapi()
    output_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")


def write_schema_for_version(version: str, output_dir: Path) -> Path:
    app = make_version_app(version)
    output_path = output_dir / f"openapi_v{version}.json"
    write_schema_for_app(output_path=output_path, app=app)
    return output_path


def main() -> None:
    output_dir = SCRIPT_DIR / "openapi"
    output_dir.mkdir(exist_ok=True)

    for version in SUPPORTED_VERSIONS:
        try:
            print(f"[openapi] generating schema for v{version} ...")
            write_schema_for_version(version, output_dir)
            print(f"[openapi] wrote {output_dir / f'openapi_v{version}.json'}")
        except Exception as exc:  # pragma: no cover - diagnostics for CI/remote
            print(f"[openapi] ERROR while generating v{version}: {exc!r}")
            raise

    special_targets = {"latest": LATEST, "next": "next"}

    for alias, version in special_targets.items():
        descriptor = f"v{version}" if alias == "latest" else version
        print(f"[openapi] generating schema for /{alias} alias ({descriptor}) ...")
        app = make_version_app(version)
        write_schema_for_app(output_path=output_dir / f"openapi_{alias}.json", app=app)
        print(f"[openapi] wrote {output_dir / f'openapi_{alias}.json'}")


if __name__ == "__main__":
    main()