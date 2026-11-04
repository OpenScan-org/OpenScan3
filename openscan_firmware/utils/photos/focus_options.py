"""Practical fixed-focus stack options derived from sparse regional samples."""
from __future__ import annotations

from typing import Any

import numpy as np


def build_stacking_options(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Search executable, evenly spaced stacks on a 0.1 dpt endpoint grid.

    Scores use linear interpolation inside each region's measured focus range.
    Views have equal weight; regions retain their crop weights. Keep one best
    mean-score range per stack count, then discard options dominated in both
    mean and worst-view score by AF or a cheaper stack. Differences <= 0.001
    are treated as negligible for this coarse setup heuristic.
    """
    if not positions or any(not position["regions"] for position in positions):
        return []
    curves = []
    autofocus_scores = []
    autofocus_complete = True
    lower, upper = 0.0, 15.0
    for view, position in enumerate(positions):
        af_score = 0.0
        for region in position["regions"].values():
            points = {
                float(focus): value for focus, value in region["manual"].items()
                if value is not None
            }
            # Require a usable manual sweep, rather than inventing a curve
            # from AF alone or a single valid capture.
            if len(points) < 2:
                return []
            af = region["autofocus"]
            af_position = position["autofocus_position"]
            if af is None:
                autofocus_complete = False
            else:
                af_score += region["weight"] * af
            if af is not None and af_position is not None:
                points[af_position] = max(points.get(af_position, 0.0), af)
            xs, ys = zip(*sorted(points.items()))
            lower, upper = max(lower, xs[0]), min(upper, xs[-1])
            curves.append((view, region["weight"], xs, ys))
        autofocus_scores.append(af_score)

    grid = np.arange(np.ceil(lower * 10), np.floor(upper * 10) + 1) / 10
    left, right = np.triu_indices(len(grid), 1)
    starts, ends = grid[left], grid[right]
    # Avoid extremely narrow stacks created by noise around one focus point.
    keep = ends - starts >= 0.5 - 1e-9
    starts, ends = starts[keep], ends[keep]
    if not len(starts):
        return []

    af_mean = float(np.mean(autofocus_scores)) if autofocus_complete else None
    frontier = [(af_mean, min(autofocus_scores))] if autofocus_complete else []
    options = []
    epsilon = 0.001
    for count in range(2, 11):
        focuses = starts[:, None] + (ends - starts)[:, None] * np.linspace(0, 1, count)
        scores = np.zeros((len(starts), len(positions)))
        for view, weight, xs, ys in curves:
            values = np.interp(focuses.ravel(), xs, ys).reshape(focuses.shape)
            scores[:, view] += weight * values.max(axis=1)
        means, worst = scores.mean(axis=1), scores.min(axis=1)
        # Stable tie breaking: worst view, then narrower range, then lower start.
        best = int(np.lexsort((starts, ends - starts, -worst, -means))[0])
        mean, minimum = float(means[best]), float(worst[best])
        if any(old_mean >= mean - epsilon and old_worst >= minimum - epsilon
               for old_mean, old_worst in frontier):
            continue
        selected = focuses[best]
        estimated = any(
            not any(abs(float(focus) - x) < 1e-8 for x in xs)
            for _, _, xs, _ in curves for focus in selected
        )
        options.append({
            "focus_range": [float(starts[best]), float(ends[best])],
            "focus_stacks": count,
            "coverage": round(mean, 6),
            "worst_coverage": round(minimum, 6),
            "gain": round(mean - af_mean, 6) if af_mean is not None else None,
            "estimated": estimated,
        })
        frontier.append((mean, minimum))
    return options
