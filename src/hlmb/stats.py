"""Coverage tests and the stationary bootstrap (protocol 6)."""

from __future__ import annotations

import numpy as np
from scipy.stats import chi2, spearmanr


def _xlogy(x: float, y: float) -> float:
    return 0.0 if x == 0 else x * np.log(y)


def kupiec(x: int, n: int, p: float = 0.01) -> tuple[float, float]:
    """Proportion-of-failures likelihood ratio (Kupiec, 1995) and its chi-square(1) p-value."""
    if n == 0:
        return np.nan, np.nan
    phat = x / n
    lr = -2 * (_xlogy(n - x, 1 - p) + _xlogy(x, p)) + 2 * (_xlogy(n - x, 1 - phat) + _xlogy(x, phat))
    lr = max(lr, 0.0)
    return lr, float(chi2.sf(lr, 1))


def transitions(exceptions: np.ndarray, valid: np.ndarray) -> tuple[int, int, int, int]:
    """Counts n00, n01, n10, n11 between consecutive windows that are both valid."""
    both = valid[:-1] & valid[1:]
    a, b = exceptions[:-1][both], exceptions[1:][both]
    return (int(np.sum(~a & ~b)), int(np.sum(~a & b)), int(np.sum(a & ~b)), int(np.sum(a & b)))


def christoffersen(n00: int, n01: int, n10: int, n11: int) -> tuple[float, float]:
    """Independence likelihood ratio (Christoffersen, 1998) and its chi-square(1) p-value."""
    n = n00 + n01 + n10 + n11
    if n == 0 or n01 + n11 == 0:
        return np.nan, np.nan
    pi = (n01 + n11) / n
    pi0 = n01 / (n00 + n01) if n00 + n01 else 0.0
    pi1 = n11 / (n10 + n11) if n10 + n11 else 0.0
    restricted = _xlogy(n00 + n10, 1 - pi) + _xlogy(n01 + n11, pi)
    free = _xlogy(n00, 1 - pi0) + _xlogy(n01, pi0) + _xlogy(n10, 1 - pi1) + _xlogy(n11, pi1)
    lr = max(-2 * (restricted - free), 0.0)
    return lr, float(chi2.sf(lr, 1))


def stationary_indices(n: int, mean_block: float, reps: int, rng: np.random.Generator) -> np.ndarray:
    """reps x n index paths of the stationary bootstrap (Politis and Romano, 1994), wrapping around."""
    starts = rng.integers(0, n, size=(reps, n))
    new = rng.random((reps, n)) < 1.0 / mean_block
    new[:, 0] = True
    t = np.arange(n)
    last = np.maximum.accumulate(np.where(new, t, 0), axis=1)
    return (np.take_along_axis(starts, last, axis=1) + (t - last)) % n


def bootstrap_rates(daily_x: np.ndarray, daily_n: np.ndarray, reps: int, seed: int,
                    mean_block: float = 20.0, chunk: int = 1000) -> np.ndarray:
    """Exception rates of `reps` stationary bootstrap resamples of days (days with observations only)."""
    keep = daily_n > 0
    x, n = daily_x[keep].astype(np.float64), daily_n[keep].astype(np.float64)
    rng = np.random.default_rng(seed)
    out = np.empty(reps)
    for lo in range(0, reps, chunk):
        idx = stationary_indices(len(x), mean_block, min(chunk, reps - lo), rng)
        out[lo:lo + len(idx)] = x[idx].sum(axis=1) / n[idx].sum(axis=1)
    return out


def bootstrap_pvalues(rate: float, reps: np.ndarray, target: float = 0.01) -> tuple[float, float]:
    """Basic bootstrap p-values for the nulls 'rate <= target' and 'rate >= target'."""
    b = len(reps)
    p_below = (1 + np.sum(reps - rate >= rate - target)) / (b + 1)
    p_above = (1 + np.sum(reps - rate <= rate - target)) / (b + 1)
    return float(p_below), float(p_above)


def holm(pvalues: np.ndarray, alpha: float = 0.05) -> np.ndarray:
    """Holm's step-down procedure (Holm, 1979): which nulls are rejected at family-wise level alpha."""
    p = np.asarray(pvalues, dtype=float)
    order = np.argsort(p)
    reject = np.zeros(len(p), dtype=bool)
    for rank, i in enumerate(order):
        if p[i] > alpha / (len(p) - rank):
            break
        reject[i] = True
    return reject


def spearman_bootstrap(a: np.ndarray, b: np.ndarray, reps: int, seed: int,
                       mean_block: float = 20.0) -> tuple[float, float, float]:
    """Spearman correlation and the 2.5% and 95% quantiles of its stationary bootstrap distribution."""
    rho = float(spearmanr(a, b).statistic)
    rng = np.random.default_rng(seed)
    idx = stationary_indices(len(a), mean_block, reps, rng)
    sims = np.array([spearmanr(a[i], b[i]).statistic for i in idx])
    return rho, float(np.nanquantile(sims, 0.025)), float(np.nanquantile(sims, 0.95))
