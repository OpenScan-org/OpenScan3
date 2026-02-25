"""Tests for firmware state lockfile helper."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

import openscan_firmware.utils.firmware_state as firmware_state


@pytest.fixture
def firmware_state_override(tmp_path: Path):
    """Override firmware state path with a temporary file for each test."""

    original_path = firmware_state.STATE_PATH
    firmware_state.override_state_path(tmp_path / "firmware_state.lock")
    yield firmware_state
    firmware_state.override_state_path(original_path)


def test_mark_unclean_and_clean_cycle(firmware_state_override):
    state_module = firmware_state_override

    unclean = state_module.mark_unclean_shutdown()
    assert unclean["unclean_shutdown"] is True
    assert unclean["last_shutdown_was_unclean"] is False
    assert state_module.STATE_PATH.exists()
    on_disk = json.loads(state_module.STATE_PATH.read_text(encoding="utf-8"))
    assert on_disk["unclean_shutdown"] is True
    assert on_disk["last_shutdown_was_unclean"] is False

    clean = state_module.mark_clean_shutdown()
    assert clean["unclean_shutdown"] is False
    assert clean["last_shutdown_was_unclean"] is False
    on_disk = json.loads(state_module.STATE_PATH.read_text(encoding="utf-8"))
    assert on_disk["unclean_shutdown"] is False
    assert on_disk["last_shutdown_was_unclean"] is False


def test_handle_startup_warns_on_unclean(caplog: pytest.LogCaptureFixture, firmware_state_override):
    state_module = firmware_state_override
    state_module.mark_unclean_shutdown()

    caplog.clear()
    with caplog.at_level(logging.WARNING), patch.object(state_module, "LOGGER", logging.getLogger("test.state")):
        updated = state_module.handle_startup()

    assert any("unclean" in record.message for record in caplog.records)
    assert updated["unclean_shutdown"] is True
    assert updated["last_shutdown_was_unclean"] is True
