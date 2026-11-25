"""The scalar fast path of the lookup tables gives exactly what the vector path gives."""

from __future__ import annotations

import numpy as np
import pytest


@pytest.mark.parametrize("interpolation", ["linear", "nearest", "flat"])
@pytest.mark.parametrize("extrapolation", ["clip", "linear"])
def test_scalar_locate_matches_vector_locate(interpolation: str, extrapolation: str) -> None:
    from nervus.blocks.lookup import locate, locate_scalar

    bp = np.array([-1.0, 0.0, 0.5, 2.0, 10.0])
    xs = np.concatenate([bp, (bp[:-1] + bp[1:]) / 2, np.linspace(-5.0, 15.0, 97), [np.nan, np.inf, -np.inf]])
    i, f = locate(xs, bp, interpolation, extrapolation)
    for x, ii, ff in zip(xs, i, f, strict=True):
        k, g = locate_scalar(float(x), bp.tolist(), interpolation, extrapolation)
        assert k == ii and (g == ff or (np.isnan(g) and np.isnan(ff))), (x, k, g, ii, ff)


def test_lookup_follows_a_changed_table() -> None:
    from nervus.blocks import Lookup1D

    blk = Lookup1D(breakpoints=[0.0, 1.0], table=[0.0, 10.0])
    u = [np.array([0.5])]
    assert blk.outputs(0.0, u, np.zeros(0), np.zeros(0))[0][0] == 5.0
    blk.params["table"] = [0.0, 20.0]  # e.g. calibrated over XCP
    assert blk.outputs(0.0, u, np.zeros(0), np.zeros(0))[0][0] == 10.0
