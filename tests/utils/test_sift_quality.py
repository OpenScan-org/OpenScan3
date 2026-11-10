"""Quality regressions from the four scanner examples, without camera metadata."""
import copy
import json
from pathlib import Path
import pytest

from openscan_firmware.utils.photos.analysis import (
    SiftQualityThresholds, _add_sift_quality, _quality_band,
)


@pytest.fixture
def examples():
    return json.loads((Path(__file__).parents[1] / 'fixtures/sift_quality_examples.json').read_text())


def assess(result):
    return _add_sift_quality(copy.deepcopy(result), SiftQualityThresholds())['quality']


@pytest.mark.parametrize('name,status', [
    ('benchy','great'), ('car','okay'), ('polarized_car','good'), ('cork','bad'),
])
def test_real_object_regressions(examples, name, status):
    assert assess(examples[name])['overall_status'] == status


def test_cork_keeps_measured_ratios_but_suppresses_unsupported_green_ratings(examples):
    q=assess(examples['cork'])
    assert q['feature_quality'] == 'bad'
    assert q['matching_quality'] == q['geometry_quality'] == q['spatial_coverage'] == 'insufficient_data'
    assert set(q['metrics']['geometric_coverage_status'].values()) == {'insufficient_data'}
    assert q['metrics']['geometric_feature_ratio']['first'] == pytest.approx(15/56)
    assert 'INSUFFICIENT_FEATURES' in {w['code'] for w in q['warnings']}


def test_background_in_crop_does_not_downgrade_core_quality(examples):
    q=assess(examples['polarized_car'])
    assert q['spatial_coverage'] == 'okay'
    assert q['overall_status'] == 'good'
    assert q['metrics']['geometric_coverage'] == {'first':6/16,'second':6/16}


def test_density_not_raw_count_drives_supported_feature_quality(examples):
    result=examples['benchy'];result['feature_density']={'first':100,'second':100}
    assert assess(result)['feature_quality'] == 'bad'


@pytest.mark.parametrize('matches', [0, 24, 49])
def test_too_few_matches_cannot_produce_positive_geometry(examples, matches):
    result=examples['benchy'];result['mutual_match_count']=matches
    q=assess(result)
    assert q['matching_quality'] == q['geometry_quality'] == 'insufficient_data'
    assert q['overall_status'] == 'insufficient_data'


def test_missing_geometry_cannot_produce_positive_matching(examples):
    result=examples['benchy'];result['geometric_inlier_ratio']=None
    for im in ('first','second'): result['spatial_distribution'][im]['geometric_inlier_counts']=None
    assert assess(result)['geometry_quality'] == 'insufficient_data'


@pytest.mark.parametrize('value,status', [(299.9,'bad'),(300,'okay'),(1500,'good'),(5000,'great')])
def test_feature_density_boundaries(value,status):
    assert _quality_band(value,300,1500,5000) == status


def test_geometric_fraction_uses_selected_features_not_pair_union(examples):
    q=assess(examples['benchy'])
    assert q['metrics']['geometric_feature_ratio'] == {'first':0.632,'second':0.621}
