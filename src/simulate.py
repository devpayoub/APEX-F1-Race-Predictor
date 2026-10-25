"""Vectorised lap-by-lap Monte Carlo race simulation."""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np

from .config import (DIRTY_AIR_PENALTY, DIRTY_AIR_WINDOW, FUEL_EFFECT,
                     GRID_GAP, PASS_BASE, PASS_WINDOW, RACE_FORM_SIGMA,
                     START_FUEL_KG)
from .features import DriverFeature

warnings.filterwarnings("ignore", category=RuntimeWarning)

TWO_STOP_SHARE = 0.22


@dataclass
class SimResult:
    win: np.ndarray
    podium: np.ndarray
    points: np.ndarray
    top10: np.ndarray
    expected_finish: np.ndarray
    band_low: np.ndarray
    band_high: np.ndarray
    dnf_prob: np.ndarray
    expected_gap: np.ndarray
    pos_dist: np.ndarray
    two_stop_share: np.ndarray
    n_sims: int = 0


def simulate(feats: list[DriverFeature], circuit: dict, n_sims: int = 20000,
             seed: int = 42) -> SimResult:
    rng = np.random.default_rng(seed)
    laps = int(circuit["laps"])
    n = len(feats)
    S = n_sims

    base = np.array([f.base_lap for f in feats], dtype=float)
    deg = np.array([f.deg_rate for f in feats], dtype=float)
    sig = np.array([f.lap_sigma for f in feats], dtype=float)
    dnfp = np.array([f.dnf_rate for f in feats], dtype=float)
    # grid is used only to seed the initial running order
    grid = np.array([max(1, f.grid) for f in feats], dtype=float)

    # cars start physically spread down the grid, not level: pole leads by
    # GRID_GAP per slot. Track position is a real advantage, not a tie-break.
    cum = np.tile((grid - 1.0) * GRID_GAP, (S, 1))
    tyre_age = np.ones((S, n))
    deg_mult = np.ones((S, n))
    dirty = np.zeros((S, n))

    # each car carries its own setup/form offset for this race; this is what
    # stops a pace advantage from converting into a near-certain win
    form = rng.normal(0.0, RACE_FORM_SIGMA, (S, n))
    base_sim = base[None, :] + form

    # --- strategy: 1-stop M->H (default) or 2-stop M->M->S -------------------
    two_stop = rng.random((S, n)) < TWO_STOP_SHARE
    stop1 = np.where(two_stop, rng.integers(9, 21, (S, n)), rng.integers(16, 30, (S, n)))
    stop2 = np.where(two_stop, rng.integers(31, 45, (S, n)), -1)

    pit_loss = float(circuit["pit_loss"])
    overtake = float(circuit.get("overtake", 0.6))
    pit_noise = rng.normal(0.0, 0.45, (S, n))

    # --- safety car ----------------------------------------------------------
    has_sc = rng.random(S) < float(circuit["sc_prob"])
    sc_lap = rng.integers(5, max(6, laps - 8), S)
    sc_len = rng.integers(3, 6, S)
    sc_done = np.zeros(S, dtype=bool)

    dead = np.zeros((S, n), dtype=bool)

    for lap in range(1, laps + 1):
        fuel = FUEL_EFFECT * START_FUEL_KG * (1.0 - (lap - 1) / laps)
        lt = (base_sim
              + deg[None, :] * deg_mult * tyre_age
              + fuel
              + rng.normal(0.0, 1.0, (S, n)) * sig[None, :]
              + dirty)

        sc_now = has_sc & (lap >= sc_lap) & (lap < sc_lap + sc_len)

        is_stop = (stop1 == lap) | (stop2 == lap)
        pl = np.where(sc_now[:, None], pit_loss * 0.62, pit_loss) + pit_noise
        lt = lt + np.where(is_stop, pl, 0.0)

        deg_mult = np.where(is_stop & (stop2 == lap), 1.45,
                            np.where(is_stop, 0.65, deg_mult))
        tyre_age = np.where(is_stop, 1.0, tyre_age + 1.0)

        # bunch the field up the first lap the SC appears
        newly = sc_now & ~sc_done
        if newly.any():
            mn = cum.min(axis=1, keepdims=True)
            spread = np.where(np.isfinite(cum), cum - mn, 0.0)
            compressed = mn + spread * 0.12
            cum = np.where(newly[:, None],
                           np.where(np.isfinite(cum), compressed, np.inf),
                           cum)
            sc_done |= newly

        cum = cum + lt

        new_dnf = (rng.random((S, n)) < (dnfp / laps)) & ~dead
        dead |= new_dnf
        cum = np.where(dead, np.inf, cum)

        # --- running order, overtaking, then dirty-air state for next lap ----
        order = np.argsort(cum, axis=1)
        sorted_cum = np.take_along_axis(cum, order, axis=1)
        gap = np.diff(sorted_cum, axis=1)

        # a car close enough can complete a pass; cost is 0.12s but it wins the
        # position. Cars on a pit lap cannot pass.
        in_range = np.isfinite(gap) & (gap < PASS_WINDOW)
        stopping_ahead = np.take_along_axis(is_stop, order[:, 1:], axis=1)
        attempt = in_range & ~stopping_ahead & (rng.random((S, n - 1)) < PASS_BASE * overtake)
        if attempt.any():
            passed = np.where(attempt, sorted_cum[:, :-1] - 0.12, sorted_cum[:, 1:])
            sorted_cum = np.concatenate([sorted_cum[:, :1], passed], axis=1)
            cum = np.empty_like(cum)
            np.put_along_axis(cum, order, sorted_cum, axis=1)
            order = np.argsort(cum, axis=1)
            sorted_cum = np.take_along_axis(cum, order, axis=1)
            gap = np.diff(sorted_cum, axis=1)

        pen = np.where(np.isfinite(gap) & (gap < DIRTY_AIR_WINDOW), DIRTY_AIR_PENALTY, 0.0)
        dirty_sorted = np.concatenate([np.zeros((S, 1)), pen], axis=1)

        ranks = np.empty((S, n), dtype=np.int16)
        np.put_along_axis(ranks, order,
                          np.broadcast_to(np.arange(n, dtype=np.int16), (S, n)),
                          axis=1)
        dirty = np.take_along_axis(dirty_sorted, ranks, axis=1).astype(float)

    # initial grid position shapes the very first lap only; the rest is pace
    ranks = np.argsort(np.argsort(cum, axis=1), axis=1) + 1
    ranks = ranks.astype(np.int16)

    win = (ranks == 1).mean(axis=0)
    podium = (ranks <= 3).mean(axis=0)
    points = (ranks <= 10).mean(axis=0)
    top10 = points
    dnf_prob = dead.mean(axis=0)

    # expected finish and the position band are reported *conditional on
    # finishing*; retirement risk is exposed separately as dnf_prob
    ranks_fin = np.where(dead, np.nan, ranks.astype(float))
    exp_fin = np.nanmean(ranks_fin, axis=0)
    band_low = np.nanpercentile(ranks_fin, 10, axis=0)
    band_high = np.nanpercentile(ranks_fin, 90, axis=0)

    winner_time = cum.min(axis=1)
    gaps = np.where(np.isfinite(cum), cum - winner_time[:, None], np.nan)
    exp_gap = np.nanmean(gaps, axis=0)

    pos_dist = np.zeros((n, n))
    for d in range(n):
        counts = np.bincount(ranks[:, d] - 1, minlength=n)
        pos_dist[d] = counts / ranks.shape[0]

    return SimResult(win=win, podium=podium, points=points, top10=top10,
                     expected_finish=exp_fin, band_low=band_low, band_high=band_high,
                     dnf_prob=dnf_prob, expected_gap=exp_gap, pos_dist=pos_dist,
                     two_stop_share=two_stop.mean(axis=0), n_sims=S)
