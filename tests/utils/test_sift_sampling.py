from types import SimpleNamespace
import sys
import numpy as np

from openscan_firmware.utils.photos.analysis import (
    _strongest_grid_features, _sample_mutual_matches, analyze_sift_pair,
)


class ExactMatcher:
    def __init__(self):
        self.pool_sizes = []

    def knnMatch(self, source, target, k):
        self.pool_sizes.append((len(source), len(target)))
        distances = np.abs(source[:, 0, None] - target[None, :, 0])
        indices = np.argsort(distances, axis=1)[:, :k]
        return [[SimpleNamespace(queryIdx=i, trainIdx=int(j), distance=float(distances[i,j]))
                 for j in row] for i, row in enumerate(indices)]


def test_grid_sample_takes_strongest_per_cell_and_redistributes_unused_budget():
    points = [SimpleNamespace(pt=(1, 1), response=float(i)) for i in range(20)]
    points += [SimpleNamespace(pt=(9, 1), response=float(i)) for i in range(2)]
    selected = _strongest_grid_features(points, (10, 10), 1, 2, budget=6)
    assert set(selected) == {16, 17, 18, 19, 20, 21}
    assert selected == _strongest_grid_features(points, (10, 10), 1, 2, budget=6)
    assert len(_strongest_grid_features(points, (10, 10), 1, 2, budget=100)) == len(points)


def test_reverse_check_includes_unsampled_competing_features():
    source = np.array([[0.0], [0.09], [20.0]])
    target = np.array([[0.05], [100.0]])
    matcher = ExactMatcher()
    mutual, candidates = _sample_mutual_matches(matcher, source, target, [0], 0.75)
    assert candidates == [(0, 0)]
    assert mutual == []  # Target actually prefers the unsampled source feature 1.
    assert matcher.pool_sizes == [(1, 2), (1, 3)]


def test_matching_uses_full_target_pool_not_only_selected_features():
    source = np.array([[0.0], [10.0], [20.0]])
    target = np.array([[20.1], [100.0], [0.1]])
    matcher = ExactMatcher()
    mutual, _ = _sample_mutual_matches(matcher, source, target, [0], 0.75)
    assert mutual == [(0, 2)]
    assert matcher.pool_sizes == [(1, 3), (1, 3)]


def test_response_counts_all_features_but_matchability_only_sample(monkeypatch):
    points = [SimpleNamespace(pt=((i%4)*20+1, ((i//4)%4)*20+1), response=float(i))
              for i in range(1200)]
    descriptors = np.arange(1200, dtype=np.float32)[:, None]
    matcher = ExactMatcher()
    cv = SimpleNamespace(
        SIFT_create=lambda: SimpleNamespace(detectAndCompute=lambda image, mask: (points, descriptors)),
        BFMatcher=lambda norm: matcher, NORM_L2=0, FM_RANSAC=0, error=RuntimeError,
        findFundamentalMat=lambda first, second, *args: (None, np.ones((len(first),1), dtype=np.uint8)),
    )
    monkeypatch.setitem(sys.modules, 'cv2', cv)
    r = analyze_sift_pair(np.zeros((80,80), dtype=np.uint8), np.zeros((80,80), dtype=np.uint8))
    assert r['feature_count'] == {'first':1200,'second':1200}
    assert r['feature_density'] == {'first':187500.0,'second':187500.0}
    assert r['matchable_feature_count'] == {'first':1000,'second':1000}
    assert r['matchable_feature_ratio'] == {'first':1.0,'second':1.0}
    assert r['unmatched_feature_count'] == {'first':0,'second':0}
    assert r['mutual_match_count'] == r['geometric_inlier_count'] == 1000
    assert all(target == 1200 for _,target in matcher.pool_sizes)
    assert sum(map(sum,r['spatial_distribution']['first']['feature_counts'])) == 1200
    assert sum(map(sum,r['spatial_distribution']['first']['matchable_feature_counts'])) == 1000
    from openscan_firmware.routers.next.scan_analysis import SiftAnalysis
    assert SiftAnalysis.model_validate({**r, 'turntable_angle': 5}).quality.geometry_quality == 'great'
