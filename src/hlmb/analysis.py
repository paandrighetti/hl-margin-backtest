"""Per-asset computation of every cell, the 10 October 2025 case and the 4-hour windows of the proxy check."""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from scipy.stats import chi2

from .stats import christoffersen, kupiec, transitions
from .windows import DAY_MINUTES, HORIZONS, adverse_moves, windows

OCT10_HORIZONS = (1, 5, 15)


def buffers(mmr: float) -> dict[str, float]:
    return {"B1": mmr, "B2": 2 * mmr / 3}


def cells(asset: str, mmr: float, grid: np.ndarray) -> tuple[list[dict], dict]:
    """One row per (horizon, measure, side, buffer); daily exception and observation counts for M1."""
    n_days = grid.shape[1] // DAY_MINUTES
    rows, daily = [], {}
    for h in HORIZONS:
        o, c, low, high, valid = windows(grid, h)
        day = np.arange(len(o)) * h // DAY_MINUTES
        n_obs = int(valid.sum())
        daily_n = np.bincount(day[valid], minlength=n_days)
        for (measure, side), move in adverse_moves(o, c, low, high).items():
            used = move[valid]
            q = np.quantile(used, [0.99, 0.995, 0.999]) if n_obs else [np.nan] * 3
            for name, b in buffers(mmr).items():
                exc = valid & (move > b)
                x = int(exc.sum())
                excess = move[exc] - b
                kup_lr, kup_p = kupiec(x, n_obs)
                ind_lr, ind_p = christoffersen(*transitions(exc, valid))
                cc_lr = kup_lr + ind_lr if np.isfinite(ind_lr) else np.nan
                rows.append({
                    "asset": asset, "horizon_min": h, "measure": measure, "side": side, "buffer": name,
                    "buffer_rate": b, "observations": n_obs, "exceptions": x,
                    "exception_rate": x / n_obs if n_obs else np.nan,
                    "coverage": 1 - x / n_obs if n_obs else np.nan,
                    "peak_excess": float(excess.max()) if x else 0.0,
                    "mean_excess": float(excess.mean()) if x else 0.0,
                    "q99": q[0], "q995": q[1], "q999": q[2], "q99_over_buffer": q[0] / b,
                    "kupiec_lr": kup_lr, "kupiec_p": kup_p,
                    "christoffersen_ind_lr": ind_lr, "christoffersen_ind_p": ind_p,
                    "christoffersen_cc_lr": cc_lr, "christoffersen_cc_p": float(chi2.sf(cc_lr, 2)) if np.isfinite(cc_lr) else np.nan,
                })
                if measure == "M1" and name == "B1":
                    daily[(h, side)] = (np.bincount(day[exc], minlength=n_days), daily_n)
    return rows, daily


def worst_moves(grid: np.ndarray, start: int, end: int, h: int) -> dict[tuple[str, str], float]:
    """Largest adverse moves over windows of h minutes starting at any minute in [start, end - h].

    NaN if a minute of the period is missing.
    """
    o, hi, lo, c = grid[:, start:end]
    if np.isnan(o).any():
        return {key: np.nan for key in adverse_moves(o[:1], c[:1], lo[:1], hi[:1])}
    first = o[: len(o) - h + 1]
    last = c[h - 1:]
    low = sliding_window_view(lo, h).min(axis=1)
    high = sliding_window_view(hi, h).max(axis=1)
    return {key: float(np.max(v)) for key, v in adverse_moves(first, last, low, high).items()}


def oct10(asset: str, mmr: float, grid: np.ndarray, start: int) -> list[dict]:
    """10 October 2025, 20:00 to 22:00 UTC; `start` is the grid index of 20:00."""
    rows = []
    for h in OCT10_HORIZONS:
        for (measure, side), move in worst_moves(grid, start, start + 120, h).items():
            row = {"asset": asset, "horizon_min": h, "measure": measure, "side": side, "worst_move": move}
            row.update({f"breach_{k}": None if np.isnan(move) else bool(move > b) for k, b in buffers(mmr).items()})
            rows.append(row)
    return rows


def four_hour_windows(asset: str, grid: np.ndarray, grid_start_ms: int) -> pd.DataFrame:
    o, c, low, high, valid = windows(grid, 240)
    t = grid_start_ms + np.arange(len(o), dtype=np.int64) * 240 * 60_000
    return pd.DataFrame({"asset": asset, "t_ms": t, "open": o, "high": high, "low": low, "close": c})[valid]
