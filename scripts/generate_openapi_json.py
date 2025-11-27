"""Generate OpenAPI JSON schemas for every supported API version."""

from pathlib import Path
import json

from openscan.main import make_version_app, SUPPORTED_VERSIONS, LATEST


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
    output_dir = Path("openapi")
    output_dir.mkdir(exist_ok=True)

    for version in SUPPORTED_VERSIONS:
        write_schema_for_version(version, output_dir)

    latest_app = make_version_app(LATEST)
    write_schema_for_app(output_path=output_dir / "openapi_latest.json", app=latest_app)


if __name__ == "__main__":
    main()