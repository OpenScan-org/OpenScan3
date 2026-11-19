"""Compact focus profiles preserve crop weights and regional measurements."""
import pytest

from openscan_firmware.utils.photos.analysis import analyze_focus_series


def _report(mode, values, focus=None, offset=0):
    return {
        "turntable_offset": offset,
        "focus_mode": mode,
        "requested_focus": focus,
        "camera_metadata": {
            "camera_settings": {"orientation_flag": 1},
            "raw_metadata": {"extended_debug": {"focus": {"regions": [
                {"index": index, "focus_value": value,
                 "rect_normalized": [index / len(values), 0, 1 / len(values), 1]}
                for index, value in enumerate(values)
            ]}}},
        },
    }


def _analyze(first, second, af=None, crop=None):
    reports = [_report("manual", first, 10), _report("manual", second, 12)]
    if af is not None:
        reports.insert(0, _report("autofocus", af))
    return analyze_focus_series(reports, crop_recommendation={"rect_normalized": crop} if crop else None)


def test_compact_profile_normalizes_per_region_including_af():
    result = _analyze([60, 80], [100, 40], [90, 160])
    assert result["positions"] == [{"turntable_offset": 0.0, "autofocus_position": None, "regions": {
        "0": {"weight": 0.5, "autofocus": 0.9, "manual": {"10": 0.6, "12": 1.0}},
        "1": {"weight": 0.5, "autofocus": 1.0, "manual": {"10": 0.5, "12": 0.25}},
    }}]


def test_overlap_weights_are_normalized():
    result = _analyze([100, 40], [40, 100], crop=[0, 0, 0.55, 1])
    regions = result["positions"][0]["regions"]
    assert regions["0"]["weight"] == pytest.approx(1 / 1.1)
    assert regions["1"]["weight"] == pytest.approx(0.1 / 1.1)
    assert sum(r["weight"] for r in regions.values()) == pytest.approx(1)


def test_regions_outside_crop_are_excluded():
    result = _analyze([100, 40], [40, 100], crop=[0, 0, 0.5, 1])
    regions = result["positions"][0]["regions"]
    assert set(regions) == {"0"}
    assert regions["0"]["weight"] == 1


def test_missing_af_and_missing_manual_cells_are_null():
    reports = [_report("manual", [100, 40], 10), _report("manual", [40, 100], 12)]
    reports[1]["camera_metadata"]["raw_metadata"]["extended_debug"]["focus"]["regions"].pop()
    regions = analyze_focus_series(reports)["positions"][0]["regions"]
    assert regions["1"] == {"weight": 0.5, "autofocus": None, "manual": {"10": 1.0, "12": None}}


def test_missing_entire_capture_keeps_its_focus_key():
    reports = [_report("manual", [100, 40], 10), _report("manual", [40, 100], 12)]
    reports[1]["camera_metadata"]["raw_metadata"] = {}
    regions = analyze_focus_series(reports)["positions"][0]["regions"]
    assert all(region["manual"]["12"] is None for region in regions.values())


def test_no_isp_focus_statistics_returns_none():
    assert analyze_focus_series([]) is None
    report = _report("autofocus", [100])
    report["camera_metadata"]["raw_metadata"] = {}
    assert analyze_focus_series([report]) is None


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_unnormalizable_measurements_are_null(value):
    region = _analyze([value], [value], [value])["positions"][0]["regions"]["0"]
    assert region == {"weight": 1.0, "autofocus": None, "manual": {"10": None, "12": None}}


def test_real_zero_is_preserved_when_reference_is_positive():
    region = _analyze([0], [100], [50])["positions"][0]["regions"]["0"]
    assert region["manual"] == {"10": 0.0, "12": 1.0}


def test_positions_are_normalized_independently():
    reports = [
        _report("manual", [100], 10, offset=0),
        _report("manual", [200], 10, offset=120),
    ]
    result = analyze_focus_series(reports)
    assert [p["turntable_offset"] for p in result["positions"]] == [0, 120]
    assert all(p["regions"]["0"]["manual"]["10"] == 1 for p in result["positions"])


def test_orientation_is_applied_before_crop_intersection():
    report = _report("manual", [100, 50], 12)
    report["camera_metadata"]["camera_settings"]["orientation_flag"] = 8
    result = analyze_focus_series([report], crop_recommendation={"rect_normalized": [0, 0, 1, 0.5]})
    assert set(result["positions"][0]["regions"]) == {"1"}


def test_focus_profile_matches_api_model():
    from openscan_firmware.models.scan_analysis import FocusAnalysisResult
    result = _analyze([100, 40], [40, 100], [60, 60])
    assert FocusAnalysisResult.model_validate(result).model_dump(mode="json") == result


def test_af_position_comes_from_af_capture_and_stays_separate_per_position():
    reports = []
    for offset, lens in [(0, 11.3), (-120, 13.2)]:
        af = _report("autofocus", [100], offset=offset)
        af["actual_lens_position"] = lens
        manual = _report("manual", [80], 15, offset=offset)
        manual["actual_lens_position"] = 15
        reports.extend([af, manual])
    result = analyze_focus_series(reports)
    assert [p["autofocus_position"] for p in result["positions"]] == [11.3, 13.2]


@pytest.mark.parametrize("lens", [None, float("nan"), float("inf")])
def test_missing_or_invalid_af_position_is_null(lens):
    af = _report("autofocus", [100])
    af["actual_lens_position"] = lens
    assert analyze_focus_series([af])["positions"][0]["autofocus_position"] is None
