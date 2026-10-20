"""Independent recount of exceptions for a fixed list of assets (protocol 10).

Usage: python scripts/recompute.py --out outputs --cache cache
Months come from the run's cache, or are downloaded again and must match the SHA-256 in manifest.csv.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hlmb import sources
from hlmb import windows as w
from hlmb.recount import recount

sys.path.insert(0, str(ROOT / "scripts"))
from run import FIRST_MONTH, PRIMARY_H, RECOMPUTE


def month_rows(cache: Path, symbol: str, month: str, digest: str) -> np.ndarray:
    path = cache / f"{symbol}-1m-{month}.zip"
    blob = path.read_bytes() if path.exists() else sources.monthly_klines_zip(symbol, month)[0]
    if sources.sha256(blob) != digest:
        raise RuntimeError(f"{symbol} {month}: SHA-256 differs from the manifest")
    return sources.parse_klines(blob)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "outputs")
    ap.add_argument("--cache", type=Path, default=ROOT / "cache")
    args = ap.parse_args()
    params = pd.read_csv(args.out / "params.csv", float_precision="round_trip").set_index("asset")
    manifest = pd.read_csv(args.out / "manifest.csv")
    coverage = pd.read_csv(args.out / "coverage.csv")
    primary = pd.read_csv(args.out / "primary.csv")
    included = set(params.index[params.status == "included"])
    most = primary.groupby("asset").exceptions.sum().idxmax()
    targets = [a for a in RECOMPUTE if a in included] + ([most] if most not in RECOMPUTE else [])
    grid_start = w.month_bounds(FIRST_MONTH)[0]
    rows_out = []
    for asset in targets:
        symbol, mmr = params.loc[asset, "binance_symbol"], params.loc[asset, "mmr"]
        ok = manifest[(manifest.asset == asset) & (manifest.status == "ok")]
        rows = np.vstack([month_rows(args.cache, symbol, m, d) for m, d in zip(ok.month, ok.sha256)])
        cov = coverage[coverage.asset == asset].set_index(["horizon_min", "measure", "side", "buffer"])
        for h in w.HORIZONS:
            for (measure, side, buf), (x, n) in recount(rows, grid_start, h, mmr).items():
                ref = cov.loc[(h, measure, side, buf)]
                rows_out.append({"asset": asset, "horizon_min": h, "measure": measure, "side": side, "buffer": buf,
                                 "exceptions": x, "observations": n, "run_exceptions": int(ref.exceptions),
                                 "run_observations": int(ref.observations),
                                 "match": x == ref.exceptions and n == ref.observations})
        print(asset, "done", flush=True)
    res = pd.DataFrame(rows_out)
    res.to_csv(args.out / "recompute.csv", index=False)
    bad = res[~res.match]
    print(f"{len(res)} cells recounted for {len(targets)} assets, {len(bad)} mismatches; primary horizon {PRIMARY_H}")
    if len(bad):
        print(bad.to_string())


if __name__ == "__main__":
    main()
