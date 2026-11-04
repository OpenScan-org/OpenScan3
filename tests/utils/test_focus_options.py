"""Executable stack options and coverage without frontend quality policy."""
import pytest

from openscan_firmware.config.scan import ScanSetting
from openscan_firmware.utils.photos.focus_options import build_stacking_options


def _position(af=0.5, offset=0):
    return {
        "turntable_offset": offset, "autofocus_position": None,
        "regions": {
            "0": {"weight": 0.5, "autofocus": af, "manual": {"10": 1.0, "12": 0.2, "15": 0.2}},
            "1": {"weight": 0.5, "autofocus": af, "manual": {"10": 0.2, "12": 1.0, "15": 0.2}},
        },
    }


def test_two_complementary_planes_remove_useless_extra_frames():
    options = build_stacking_options([_position()])
    assert options == [{
        "focus_range": [10.0, 12.0], "focus_stacks": 2,
        "coverage": 1.0, "worst_coverage": 1.0, "gain": 0.5, "estimated": False,
    }]


def test_af_without_stacking_benefit_has_no_stack_options():
    assert build_stacking_options([_position(af=1.0)]) == []


def test_missing_af_does_not_invent_an_af_baseline():
    options = build_stacking_options([_position(af=None)])
    assert len(options) == 1
    assert options[0]["gain"] is None


def test_missing_or_flat_manual_data_does_not_invent_options():
    assert build_stacking_options([]) == []
    assert build_stacking_options([{"regions": {}}]) == []
    position = _position()
    position["regions"]["0"]["manual"] = {"12": 1.0, "15": None}
    assert build_stacking_options([position]) == []


def test_no_extrapolation_and_settings_match_scan_task_spacing():
    positions = [_position(), _position(offset=120)]
    positions[1]["regions"]["0"]["manual"] = {"11": 0.5, "12": 1.0, "14": 0.5}
    options = build_stacking_options(positions)
    assert options
    for option in options:
        settings = ScanSetting(focus_range=option["focus_range"], focus_stacks=option["focus_stacks"])
        assert settings.focus_positions[0] >= 11
        assert settings.focus_positions[-1] <= 14
        assert all(abs(value * 10 - round(value * 10)) < 1e-8 for value in option["focus_range"])
        assert 0 <= option["worst_coverage"] <= option["coverage"] <= 1
        assert option["gain"] == pytest.approx(option["coverage"] - 0.5, abs=1e-6)


def test_af_lens_position_contributes_to_candidate_curve():
    position = _position(af=1.0)
    position["autofocus_position"] = 14.5
    position["regions"]["0"]["autofocus"] = 0.2
    position["regions"]["1"]["manual"] = {"10": 0.2, "12": 0.2, "15": 0.2}
    options = build_stacking_options([position])
    assert options[0]["focus_range"] == [10.0, 14.5]
    assert options[0]["coverage"] == 1.0
    assert options[0]["gain"] == 0.4


def test_gain_and_worst_view_use_all_positions():
    first, second = _position(af=0.8), _position(af=0.4, offset=120)
    # Second view has an AF best value in one region, beyond its manual sweep values.
    second["regions"]["0"]["manual"] = {"10": 0.8, "12": 0.8, "15": 0.8}
    options = build_stacking_options([first, second])
    assert options[0]["coverage"] == 0.95
    assert options[0]["worst_coverage"] == 0.9
    assert options[0]["gain"] == 0.35


def test_every_retained_option_improves_mean_or_worst_over_cheaper_options():
    positions = [_position()]
    positions[0]["autofocus_position"] = 13.7
    positions[0]["regions"]["1"]["manual"]["15"] = 1.0
    options = build_stacking_options(positions)
    for index, option in enumerate(options):
        for cheaper in options[:index]:
            assert (option["coverage"] > cheaper["coverage"] + 0.001
                    or option["worst_coverage"] > cheaper["worst_coverage"] + 0.001)
