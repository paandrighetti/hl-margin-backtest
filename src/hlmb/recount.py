"""Second, independent count of exceptions (protocol 10): pandas group-by on the raw rows, no minute grid."""

from __future__ import annotations

import numpy as np
import pandas as pd


def recount(rows: np.ndarray, grid_start_ms: int, h: int, mmr: float) -> dict[tuple[str, str, str], tuple[int, int]]:
    """(exceptions, observations) per (measure, side, buffer) for windows of h minutes."""
    df = pd.DataFrame(rows, columns=["t", "o", "hi", "lo", "c"])
    minute = (df.t.astype(np.int64) - grid_start_ms) // 60_000
    df["win"], df["pos"] = minute // h, minute % h
    agg = df.groupby("win").agg(first=("pos", "min"), last=("pos", "max"), count=("pos", "size"),
                                low=("lo", "min"), high=("hi", "max"))
    agg["open"] = df[df.pos == 0].set_index("win").o
    agg["close"] = df[df.pos == h - 1].set_index("win").c
    ok = agg[(agg["first"] == 0) & (agg["last"] == h - 1) & (agg["count"] >= 0.8 * h)]
    moves = {
        ("M1", "long"): 1 - ok.close / ok.open,
        ("M1", "short"): ok.close / ok.open - 1,
        ("M2", "long"): 1 - ok.low / ok.open,
        ("M2", "short"): ok.high / ok.open - 1,
    }
    out = {}
    for (measure, side), move in moves.items():
        for name, b in (("B1", mmr), ("B2", 2 * mmr / 3)):
            out[(measure, side, name)] = (int((move > b).sum()), len(ok))
    return out
