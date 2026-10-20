"""Data gates, the minute grid and the window measures (protocol 4 and 5)."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime

import numpy as np

MINUTE_MS = 60_000
DAY_MINUTES = 1440
HORIZONS = (1, 2, 5, 10, 15, 30, 60, 120, 240, 480, 1440)
MIN_MONTH_COVERAGE = 0.95
MIN_WINDOW_PRESENT = 0.8


def month_bounds(month: str) -> tuple[int, int]:
    """Start (ms) and number of minutes of a month written YYYY-MM."""
    year, mon = map(int, month.split("-"))
    start = int(datetime(year, mon, 1, tzinfo=UTC).timestamp() * 1000)
    return start, calendar.monthrange(year, mon)[1] * DAY_MINUTES


def gate_month(rows: np.ndarray, month: str) -> str | None:
    """None if the month passes gates G2 to G4, otherwise the reason it fails."""
    start, minutes = month_bounds(month)
    if len(rows) == 0:
        return "G4 no rows"
    t = rows[:, 0]
    if np.any((t - start) % MINUTE_MS) or t[0] < start or t[-1] >= start + minutes * MINUTE_MS:
        return "G2 open times not whole minutes inside the month"
    if np.any(np.diff(t) <= 0):
        return "G2 open times not strictly increasing"
    o, h, lo, c = rows[:, 1], rows[:, 2], rows[:, 3], rows[:, 4]
    if np.any(rows[:, 1:5] <= 0) or not np.all(np.isfinite(rows[:, 1:5])):
        return "G3 non-positive or missing price"
    if np.any(lo > np.minimum(o, c)) or np.any(h < np.maximum(o, c)):
        return "G3 low or high inconsistent with open and close"
    if len(rows) < MIN_MONTH_COVERAGE * minutes:
        return f"G4 {len(rows) / minutes:.1%} of minutes present"
    return None


def place(grid: np.ndarray, rows: np.ndarray, grid_start_ms: int) -> None:
    """Write (open, high, low, close) rows into a 4 x minutes grid that starts at grid_start_ms."""
    idx = ((rows[:, 0] - grid_start_ms) // MINUTE_MS).astype(np.int64)
    grid[:, idx] = rows[:, 1:5].T


def windows(grid: np.ndarray, h: int) -> tuple[np.ndarray, ...]:
    """Open of the first minute, close of the last, lowest low, highest high and validity of each window.

    The grid starts at midnight UTC and its length is a whole number of days, so with h dividing 1,440 the
    windows are the consecutive non-overlapping blocks of h minutes of each UTC day.
    """
    o, hi, lo, c = (row.reshape(-1, h) for row in grid)
    present = ~np.isnan(o)
    valid = present[:, 0] & present[:, -1] & (present.mean(axis=1) >= MIN_WINDOW_PRESENT)
    with np.errstate(all="ignore"):
        low = np.where(valid, np.nanmin(np.where(present, lo, np.inf), axis=1), np.nan)
        high = np.where(valid, np.nanmax(np.where(present, hi, -np.inf), axis=1), np.nan)
    return o[:, 0], c[:, -1], low, high, valid


def adverse_moves(o: np.ndarray, c: np.ndarray, low: np.ndarray, high: np.ndarray) -> dict[tuple[str, str], np.ndarray]:
    """Adverse moves as positive fractions: M1 at the end of the window, M2 at the worst point inside it."""
    return {
        ("M1", "long"): 1 - c / o,
        ("M1", "short"): c / o - 1,
        ("M2", "long"): 1 - low / o,
        ("M2", "short"): high / o - 1,
    }
