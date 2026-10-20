"""The single run of PROTOCOL.md. Usage: python scripts/run.py --out outputs --cache cache"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hlmb import analysis, params, sources, stats
from hlmb import windows as w

FIRST_MONTH, LAST_MONTH = "2023-01", "2026-09"
MIN_MONTHS, MIN_ASSETS = 12, 50
SEED = 20261020
PRIMARY_H, PRIMARY_REPS, OTHER_REPS = 5, 10_000, 2_000
RECOMPUTE = ("BTC", "ETH", "SOL", "DOGE", "XRP", "HYPE", "kPEPE")
PROXY_ASSETS = 30
OCT10 = datetime(2025, 10, 10, 20, tzinfo=UTC)
HLP = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
FOUR_HOURS_MS = 4 * 3600 * 1000
DAY_MS = 86_400_000
MIN_IDENTITY_DAYS, MIN_IDENTITY_CORR = 60, 0.9


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Run:
    def __init__(self, out: Path, cache: Path):
        self.out, self.cache = out, cache
        out.mkdir(parents=True, exist_ok=True)
        (out / "raw").mkdir(exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)

    def log(self, msg: str) -> None:
        line = f"{now()} {msg}"
        print(line, flush=True)
        with open(self.out / "run.log", "a") as fh:
            fh.write(line + "\n")

    def check_protocol(self) -> None:
        frozen = (ROOT / "PROTOCOL.sha256").read_text().split()[0]
        if sources.sha256((ROOT / "PROTOCOL.md").read_bytes()) != frozen:
            sys.exit("PROTOCOL.md differs from PROTOCOL.sha256: stopping")

    def guards(self) -> None:
        """Mark the start of the run, or its single restart if it stopped before writing coverage.csv."""
        self.check_protocol()
        started = self.out / "run.started"
        if started.exists():
            if (self.out / "coverage.csv").exists() or (self.out / "run.restarted").exists():
                sys.exit(f"a run is already recorded ({started.read_text().strip()}): stopping")
            (self.out / "run.restarted").write_text(now() + "\n")
            self.log("restart of a run that stopped before writing coverage.csv (protocol 10)")
        else:
            started.write_text(now() + "\n")

    def ready_to_compute(self) -> None:
        self.check_protocol()
        if not (self.out / "run.started").exists() or (self.out / "coverage.csv").exists():
            sys.exit("compute needs a start mark and no previous results: stopping")

    def save_json(self, name: str, obj: object) -> str:
        blob = json.dumps(obj, indent=1, sort_keys=True).encode()
        (self.out / "raw" / name).write_bytes(blob)
        return sources.sha256(blob)


def month_list(first: str, last: str) -> list[str]:
    return [str(p) for p in pd.period_range(first, last, freq="M")]


def fetch_month(symbol: str, month: str) -> dict:
    try:
        blob, published = sources.monthly_klines_zip(symbol, month)
    except sources.NotFound:
        return {"month": month, "status": "absent"}
    if sources.sha256(blob) != published:
        blob, published = sources.monthly_klines_zip(symbol, month)
        if sources.sha256(blob) != published:
            return {"month": month, "status": "G1 checksum mismatch"}
    rows = sources.parse_klines(blob)
    reason = w.gate_month(rows, month)
    return {"month": month, "status": reason or "ok", "sha256": sources.sha256(blob),
            "minutes": len(rows), "rows": rows if reason is None else None, "blob": blob}


def identity(asset: str, grid: np.ndarray, grid_start: int) -> tuple[float, int]:
    """G6: correlation of daily returns, Hyperliquid 1-day candles against Binance days, over common days."""
    candles = sources.hl_candles(asset, "1d", grid_start, grid_start + (grid.shape[1] - 1) * w.MINUTE_MS)
    o, c, _, _, valid = w.windows(grid, w.DAY_MINUTES)
    hl = {(int(x["t"]) - grid_start) // DAY_MS: float(x["c"]) / float(x["o"]) - 1 for x in candles}
    days = [d for d in sorted(hl) if 0 <= d < len(o) and valid[d]]
    if len(days) < 3:
        return np.nan, len(days)
    return float(np.corrcoef([hl[d] for d in days], c[days] / o[days] - 1)[0, 1]), len(days)


def primary_and_horizons(daily: dict, assets: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    prim, hor = [], []
    seed = SEED
    for asset in sorted(assets):
        upper = {}
        for h in w.HORIZONS:
            for side in ("long", "short"):
                x, n = daily[asset][(h, side)]
                rate = x.sum() / n.sum()
                reps_n = PRIMARY_REPS if h == PRIMARY_H else OTHER_REPS
                reps = stats.bootstrap_rates(x, n, reps_n, seed)
                seed += 1
                lo, hi = np.quantile(reps, [0.025, 0.975])
                upper[(h, side)] = (rate, hi)
                if h == PRIMARY_H:
                    p_below, p_above = stats.bootstrap_pvalues(rate, reps)
                    prim.append({"asset": asset, "side": side, "observations": int(n.sum()),
                                 "exceptions": int(x.sum()), "exception_rate": rate, "rate_lo": lo,
                                 "rate_hi": hi, "p_below_99": p_below, "p_above_99": p_above})
        h_star = h_conf = 0
        for h in w.HORIZONS:
            if all(upper[(h, s)][0] <= 0.01 for s in ("long", "short")) and h_star == hor_prev(h):
                h_star = h
            if all(upper[(h, s)][1] <= 0.01 for s in ("long", "short")) and h_conf == hor_prev(h):
                h_conf = h
        hor.append({"asset": asset, "h_star_min": h_star, "h_star_conf_min": h_conf})
    prim = pd.DataFrame(prim)
    below = stats.holm(prim["p_below_99"].to_numpy())
    above = stats.holm(prim["p_above_99"].to_numpy())
    prim["classification"] = np.where(below, "below 99%", np.where(above, "at or above 99%", "not distinguishable"))
    return prim, pd.DataFrame(hor)


def hor_prev(h: int) -> int:
    """The horizon before h in the grid, 0 before the first: h* must hold at every shorter horizon too."""
    i = w.HORIZONS.index(h)
    return w.HORIZONS[i - 1] if i else 0


def proxy_check(run: Run, top: list[str], fourh: dict[str, pd.DataFrame], mmr: dict[str, float]) -> pd.DataFrame:
    end = int(time.time() * 1000)
    rows, windows_kept = [], []
    for asset in top:
        candles = sources.hl_candles(asset, "4h", end - 5000 * FOUR_HOURS_MS, end)
        hl = pd.DataFrame([{"t_ms": int(c["t"]), "hl_open": float(c["o"]), "hl_high": float(c["h"]),
                            "hl_low": float(c["l"]), "hl_close": float(c["c"])} for c in candles])
        both = hl.merge(fourh[asset].drop(columns="asset"), on="t_ms") if len(hl) else hl
        both.insert(0, "asset", asset)
        windows_kept.append(both)
        if len(both) < 30:
            rows.append({"asset": asset, "windows": len(both)})
            continue
        b1 = mmr[asset]
        hl_ret, bn_ret = both.hl_close / both.hl_open - 1, both.close / both.open - 1
        hl_range = (both.hl_high - both.hl_low) / both.hl_open
        bn_range = (both.high - both.low) / both.open
        disagree = ((1 - both.hl_low / both.hl_open > b1) != (1 - both.low / both.open > b1)).sum() + \
                   ((both.hl_high / both.hl_open - 1 > b1) != (both.high / both.open - 1 > b1)).sum()
        crash = both[both.t_ms == int(OCT10.timestamp() * 1000)]
        rows.append({
            "asset": asset, "windows": len(both), "first": pd.to_datetime(both.t_ms.min(), unit="ms").date(),
            "return_correlation": float(np.corrcoef(hl_ret, bn_ret)[0, 1]),
            "median_range_ratio": float(np.nanmedian(hl_range / bn_range.replace(0, np.nan))),
            "m2_disagreement_share": disagree / (2 * len(both)),
            "oct10_hl_long_m2": float(1 - crash.hl_low.iloc[0] / crash.hl_open.iloc[0]) if len(crash) else np.nan,
            "oct10_binance_long_m2": float(1 - crash.low.iloc[0] / crash.open.iloc[0]) if len(crash) else np.nan,
        })
    if windows_kept:
        pd.concat(windows_kept).to_csv(run.out / "proxy_windows.csv.gz", index=False)
    return pd.DataFrame(rows)


def hlp(run: Run, daily_total: pd.Series) -> dict:
    details = sources.hl_info({"type": "vaultDetails", "vaultAddress": HLP})
    run.save_json("hlp_vault_details.json", details)
    hist = dict(details["portfolio"])["allTime"]
    av = pd.DataFrame(hist["accountValueHistory"], columns=["t_ms", "account_value"]).astype(float)
    pnl = pd.DataFrame(hist["pnlHistory"], columns=["t_ms", "pnl"]).astype(float)
    h = av.merge(pnl, on="t_ms").sort_values("t_ms").reset_index(drop=True)
    h["time"] = pd.to_datetime(h.t_ms, unit="ms", utc=True)
    h["ret"] = h.pnl.diff() / h.account_value.shift()
    h.to_csv(run.out / "hlp.csv", index=False)
    h["start"] = h.time.shift()
    r = h.dropna(subset=["ret"])
    spacing_days = float((r.time - r.start).median() / pd.Timedelta(days=1))
    index = (1 + r.ret).cumprod()
    years = (r.time.iloc[-1] - r.start.iloc[0]) / pd.Timedelta(days=365.25)
    crash = r[(r.start < pd.Timestamp(OCT10)) & (r.time >= pd.Timestamp(OCT10))]
    summary = {
        "points": len(h), "first": str(h.time.iloc[0]), "last": str(h.time.iloc[-1]),
        "median_spacing_days": spacing_days,
        "annual_return": float(index.iloc[-1] ** (1 / years) - 1),
        "annual_volatility": float(r.ret.std() * np.sqrt(365.25 / spacing_days)),
        "max_drawdown": float((index / index.cummax() - 1).min()),
        "worst_periods": [{"end": str(t), "return": float(x)} for t, x in r.nsmallest(5, "ret")[["time", "ret"]].values],
        "oct10_period": {"start": str(crash.start.iloc[0]), "end": str(crash.time.iloc[0]),
                         "return": float(crash.ret.iloc[0])} if len(crash) else None,
    }
    if spacing_days <= 2:
        # A day's exceptions go to the period that contains the end of that day; periods outside the
        # exception sample are left out.
        day_end = daily_total.index + pd.Timedelta(days=1)
        inside = r[(r.start >= daily_total.index[0]) & (r.time <= day_end[-1])]
        edges = pd.concat([inside.start.iloc[:1], inside.time])
        counts = pd.Series(daily_total.to_numpy(), index=day_end).groupby(
            pd.cut(day_end, edges, right=True), observed=False).sum().to_numpy()
        rho, q025, q95 = stats.spearman_bootstrap(inside.ret.to_numpy(), counts.astype(float), 10_000, SEED)
        summary.update({"test_periods": len(inside), "spearman_rho": rho, "spearman_q025": q025,
                        "spearman_q95": q95, "negative_link_rejects_null": bool(q95 < 0)})
    else:
        summary["test"] = "not run: median spacing above two days (protocol 8)"
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "outputs")
    ap.add_argument("--cache", type=Path, default=ROOT / "cache")
    ap.add_argument("--phase", choices=("all", "guard", "compute"), default="all",
                    help="the workflow commits the start mark between 'guard' and 'compute'")
    args = ap.parse_args()
    run = Run(args.out, args.cache)
    if args.phase in ("all", "guard"):
        run.guards()
        if args.phase == "guard":
            return
    run.ready_to_compute()
    run.log("run starts")

    meta = sources.hl_info({"type": "meta"})
    ctx = sources.hl_info({"type": "metaAndAssetCtxs"})
    run.log(f"meta sha256 {run.save_json('meta.json', meta)}; asset contexts sha256 {run.save_json('meta_and_asset_ctxs.json', ctx)}")
    assets = params.assets_from_meta(meta)
    oi = {a["name"]: float(c["openInterest"]) * float(c["markPx"]) for a, c in zip(ctx[0]["universe"], ctx[1])}
    available = sources.binance_kline_symbols()
    run.log(f"{len(assets)} assets in meta, {len(available)} Binance kline folders")

    months = month_list(FIRST_MONTH, LAST_MONTH)
    grid_start = w.month_bounds(months[0])[0]
    total = sum(w.month_bounds(m)[1] for m in months)
    oct10_index = int((OCT10.timestamp() * 1000 - grid_start) // w.MINUTE_MS)

    param_rows, manifest, coverage, oct_rows, fourh, daily = [], [], [], [], {}, {}
    for a in assets:
        symbol = None if a.delisted else params.binance_symbol(a.name, available)
        row = {"asset": a.name, "binance_symbol": symbol, "max_leverage": a.max_leverage,
               "field_max_leverage": a.field_leverage, "mmr": a.mmr, "open_interest_usd": oi.get(a.name)}
        param_rows.append(row)
        if a.delisted or symbol is None:
            row["status"] = "excluded: delisted" if a.delisted else "excluded: no Binance symbol"
            continue
        with ThreadPoolExecutor(8) as ex:
            got = list(ex.map(fetch_month, [symbol] * len(months), months))
        grid = np.full((4, total), np.nan)
        included = 0
        for g in got:
            manifest.append({"asset": a.name, "symbol": symbol, **{k: g.get(k) for k in ("month", "status", "sha256", "minutes")}})
            if g["status"] == "ok":
                w.place(grid, g["rows"], grid_start)
                included += 1
                if a.name in RECOMPUTE:
                    (run.cache / f"{symbol}-1m-{g['month']}.zip").write_bytes(g["blob"])
        row["months_included"] = included
        if included < MIN_MONTHS:
            row["status"] = f"excluded: {included} months pass the gates"
            continue
        corr, days = identity(a.name, grid, grid_start)
        row["identity_correlation"], row["identity_days"] = corr, days
        if days < MIN_IDENTITY_DAYS or not corr >= MIN_IDENTITY_CORR:
            row["status"] = f"excluded: G6 identity check ({days} common days, correlation {corr:.3f})"
            continue
        row["status"] = "included"
        rows, daily[a.name] = analysis.cells(a.name, a.mmr, grid)
        coverage.extend(rows)
        oct_rows.extend(analysis.oct10(a.name, a.mmr, grid, oct10_index))
        fourh[a.name] = analysis.four_hour_windows(a.name, grid, grid_start)
        run.log(f"{a.name} ({symbol}): {included} months")

    pd.DataFrame(param_rows).to_csv(run.out / "params.csv", index=False)
    pd.DataFrame(manifest).to_csv(run.out / "manifest.csv", index=False)
    included = [r["asset"] for r in param_rows if r.get("status") == "included"]
    if len(included) < MIN_ASSETS:
        run.log(f"G5: {len(included)} assets qualify, fewer than {MIN_ASSETS}: the run stops without results")
        sys.exit(1)
    pd.DataFrame(coverage).to_csv(run.out / "coverage.csv", index=False)
    pd.DataFrame(oct_rows).to_csv(run.out / "oct10.csv", index=False)
    run.log(f"G5 passed: {len(included)} assets; coverage.csv written")

    primary, horizons = primary_and_horizons(daily, included)
    primary.to_csv(run.out / "primary.csv", index=False)
    horizons.to_csv(run.out / "horizons.csv", index=False)
    days = pd.date_range(months[0] + "-01", periods=total // w.DAY_MINUTES, freq="D", tz="UTC")
    exc = sum(daily[a][(PRIMARY_H, s)][0] for a in included for s in ("long", "short"))
    obs = sum(daily[a][(PRIMARY_H, s)][1] for a in included for s in ("long", "short"))
    daily_total = pd.Series(exc, index=days)
    pd.DataFrame({"date": days.date, "exceptions": exc, "observations": obs}).to_csv(run.out / "daily_exceptions.csv", index=False)
    run.log("primary classification, horizons and daily exceptions written")

    mmr = {r["asset"]: r["mmr"] for r in param_rows}
    top = sorted(included, key=lambda x: -(oi.get(x) or 0))[:PROXY_ASSETS]
    proxy_check(run, top, fourh, mmr).to_csv(run.out / "proxy.csv", index=False)
    run.log("proxy check written")

    summary = hlp(run, daily_total)
    (run.out / "hlp_summary.json").write_text(json.dumps(summary, indent=1))
    run.log("HLP written; run ends")


if __name__ == "__main__":
    main()
