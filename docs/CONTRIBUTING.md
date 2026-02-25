# Contributing to OpenScan3

Thanks for helping improve OpenScan3! This project powers real hardware, so please keep changes easy to review.

## Getting Started
Setup instructions (desktop + Raspberry Pi) are in [`docs/DEVELOP.md`](docs/DEVELOP.md).

## Workflow (Branches & PRs)
- Fork the repo (or create a branch if you have write access).
- Create a descriptive branch from `develop` (e.g. `feature/exposure-stacking`).
- Keep commits small and focused with clear messages.
- Open PRs against `develop`.
- In the PR description: explain **why** the change is needed and the user-facing impact.
- Link related issues (e.g. `Fixes #123`).

## HTTP API / Router Versioning
- Breaking changes or new endpoints go to `openscan_firmware/routers/next/` first.
- Versioned routers (e.g. `routers/v0_7/`) only receive proven bugfix backports.
- When promoting from `next`, document the migration path (release notes and/or docs).

## Code Style & Architecture
- Public classes/functions need Google-style docstrings (FastAPI uses the first line as summary).
- Prefer Pydantic BaseModel types for structured with Field(..., description="...") metadata so downstream API clients get rich schema docs.
- Keep hardware abstractions modular: add controllers under `app/controllers/hardware/` and avoid firmware-specific coupling.

## Testing Expectations
See [`docs/DEVELOP.md#testing-expectations`](docs/DEVELOP.md#testing-expectations) for what to run locally and how to guard hardware tests.

## Documentation & Releases
- Update relevant docs when behavior, APIs, or workflows change.
- New config options must be documented in the appropriate `settings/` docs/README.
- We use SemVer. Only maintainers merge `develop` → `main`; each `main` merge is a tagged release.

## Reviews & Communication
- Expect at least one maintainer review before merge.
- For larger changes (architecture/hardware), discuss early in the community channels linked in the repo.
