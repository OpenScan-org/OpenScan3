import pytest

from openscan_firmware.models.paths import PathMethod, PolarPoint3D
from openscan_firmware.utils.paths.paths import get_constrained_path


def test_constrained_path_allows_fixed_theta() -> None:
    path = get_constrained_path(
        method=PathMethod.FIBONACCI,
        num_points=5,
        min_theta=45.0,
        max_theta=45.0,
        min_phi=0.0,
        max_phi=180.0,
    )

    assert len(path) == 5
    assert {point.theta for point in path} == {45.0}
    assert len({point.fi for point in path}) > 1


def test_constrained_path_allows_fixed_phi() -> None:
    path = get_constrained_path(
        method=PathMethod.FIBONACCI,
        num_points=5,
        min_theta=10.0,
        max_theta=120.0,
        min_phi=90.0,
        max_phi=90.0,
    )

    assert len(path) == 5
    assert {point.fi for point in path} == {90.0}
    assert len({point.theta for point in path}) > 1


def test_constrained_path_collapses_fully_fixed_position_to_one_point() -> None:
    path = get_constrained_path(
        method=PathMethod.FIBONACCI,
        num_points=130,
        min_theta=45.0,
        max_theta=45.0,
        min_phi=90.0,
        max_phi=90.0,
    )

    assert path == [PolarPoint3D(theta=45.0, fi=90.0, r=1.0)]


def test_constrained_path_still_rejects_reversed_theta_range() -> None:
    with pytest.raises(ValueError, match="less than or equal"):
        get_constrained_path(
            method=PathMethod.FIBONACCI,
            num_points=5,
            min_theta=120.0,
            max_theta=10.0,
        )
