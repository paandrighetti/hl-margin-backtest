"""End-to-end run on synthetic sources: guards, every output file, and the single-run rule."""

import hashlib
import io
import json
import sys
import zipfile

import numpy as np
import pandas as pd
import pytest
import run as runmod

from hlmb import sources
from hlmb import windows as w

MONTHS = ("2025-09", "2025-10")


def minute_prices(symbol: str) -> tuple[np.ndarray, np.ndarray]:
    start = w.month_bounds(MONTHS[0])[0]
    n = sum(w.month_bounds(m)[1] for m in MONTHS)
    rng = np.random.default_rng(sum(map(ord, symbol)))
    close = 100 * np.exp(np.cumsum(rng.standard_t(3, n) * 0.003))
    return start + np.arange(n, dtype=np.int64) * w.MINUTE_MS, close


def month_zip(symbol: str, month: str) -> bytes:
    t, close = minute_prices(symbol)
    start, minutes = w.month_bounds(month)
    i = np.flatnonzero((t >= start) & (t < start + minutes * w.MINUTE_MS))
    o = np.r_[close[0], close[:-1]][i]
    c = close[i]
    lines = [f"{t[k]},{a},{max(a, b)},{min(a, b)},{b},1,0,0,0,0,0,0" for k, a, b in zip(i, o, c)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("k.csv", "\n".join(lines))
    return buf.getvalue()


META = {"universe": [{"name": "AAA", "maxLeverage": 20}, {"name": "kBBB", "maxLeverage": 10},
                     {"name": "CCC", "maxLeverage": 3, "isDelisted": True}, {"name": "DDD", "maxLeverage": 5}],
        "marginTables": []}


def fake_info(body):
    kind = body["type"]
    if kind == "meta":
        return META
    if kind == "metaAndAssetCtxs":
        return [META, [{"openInterest": "10", "markPx": "100"}, {"openInterest": "5", "markPx": "1"},
                       {"openInterest": "0", "markPx": "1"}, {"openInterest": "1", "markPx": "1"}]]
    if kind == "candleSnapshot":
        symbol = {"AAA": "AAAUSDT", "kBBB": "1000BBBUSDT"}[body["req"]["coin"]]
        t, close = minute_prices(symbol)
        o = np.r_[close[0], close[:-1]]
        step = {"4h": 240, "1d": 1440}[body["req"]["interval"]]
        lo, hi = body["req"]["startTime"], body["req"]["endTime"]
        out = []
        for k in range(0, len(t), step):
            sl = slice(k, k + step)
            if lo <= t[k] <= hi:
                out.append({"t": int(t[k]), "o": str(o[k]), "c": str(close[k + step - 1]),
                            "h": str(max(o[sl].max(), close[sl].max())), "l": str(min(o[sl].min(), close[sl].min()))})
        return out
    if kind == "vaultDetails":
        days = pd.date_range("2025-08-01", "2025-11-30", freq="D", tz="UTC")
        rng = np.random.default_rng(3)
        pnl = np.cumsum(rng.normal(1e4, 5e4, len(days)))
        ts = [int(d.timestamp() * 1000) for d in days]
        return {"portfolio": [["allTime", {"accountValueHistory": [[t, str(3e8)] for t in ts],
                                           "pnlHistory": [[t, str(p)] for t, p in zip(ts, pnl)]}]]}
    raise AssertionError(kind)


def fake_zip(symbol, month):
    blob = month_zip(symbol, month)
    return blob, hashlib.sha256(blob).hexdigest()


@pytest.fixture
def patched(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "PROTOCOL.md").write_text("protocol\n")
    (root / "PROTOCOL.sha256").write_text(hashlib.sha256(b"protocol\n").hexdigest() + "  PROTOCOL.md\n")
    monkeypatch.setattr(runmod, "ROOT", root)
    monkeypatch.setattr(runmod, "FIRST_MONTH", MONTHS[0])
    monkeypatch.setattr(runmod, "LAST_MONTH", MONTHS[1])
    monkeypatch.setattr(runmod, "MIN_MONTHS", 1)
    monkeypatch.setattr(runmod, "MIN_ASSETS", 2)
    monkeypatch.setattr(runmod, "MIN_IDENTITY_DAYS", 30)
    monkeypatch.setattr(runmod, "PRIMARY_REPS", 200)
    monkeypatch.setattr(runmod, "OTHER_REPS", 100)
    monkeypatch.setattr(runmod.time, "sleep", lambda s: None)
    monkeypatch.setattr(sources, "hl_info", fake_info)
    monkeypatch.setattr(sources, "hl_info_raw", lambda body: json.dumps(fake_info(body)).encode())
    monkeypatch.setattr(sources, "binance_kline_symbols", lambda: {"AAAUSDT", "1000BBBUSDT"})
    monkeypatch.setattr(sources, "monthly_klines_zip", fake_zip)
    return tmp_path


def test_full_run(patched, monkeypatch):
    out, cache = patched / "out", patched / "cache"
    monkeypatch.setattr(sys, "argv", ["run.py", "--out", str(out), "--cache", str(cache)])
    runmod.main()
    p = pd.read_csv(out / "params.csv").set_index("asset")
    assert p.loc["AAA", "status"] == "included" and p.loc["kBBB", "binance_symbol"] == "1000BBBUSDT"
    assert p.loc["CCC", "status"] == "excluded: delisted"
    assert p.loc["DDD", "status"] == "excluded: no Binance symbol"
    cov = pd.read_csv(out / "coverage.csv")
    assert len(cov) == 2 * len(w.HORIZONS) * 4 * 2
    prim = pd.read_csv(out / "primary.csv")
    assert set(prim.classification) <= {"below 99%", "at or above 99%", "not distinguishable"}
    assert len(pd.read_csv(out / "oct10.csv")) == 2 * 3 * 4
    proxy = pd.read_csv(out / "proxy.csv")
    assert (proxy.return_correlation > 0.999).all()
    summary = pd.read_json(out / "hlp_summary.json", typ="series")
    assert summary["median_spacing_days"] == pytest.approx(1.0) and "spearman_rho" in summary
    daily = pd.read_csv(out / "daily_exceptions.csv")
    assert len(daily) == 61
    with pytest.raises(SystemExit):
        runmod.main()  # the single-run rule


def test_protocol_change_stops(patched, monkeypatch):
    (runmod.ROOT / "PROTOCOL.md").write_text("changed\n")
    monkeypatch.setattr(sys, "argv", ["run.py", "--out", str(patched / "o"), "--cache", str(patched / "c")])
    with pytest.raises(SystemExit):
        runmod.main()


def test_report_builds(patched, monkeypatch):
    import build_report

    out, cache = patched / "out", patched / "cache"
    monkeypatch.setattr(sys, "argv", ["run.py", "--out", str(out), "--cache", str(cache)])
    runmod.main()
    text = build_report.build(out)
    assert "## 4. Primary cell by asset" in text and "AAA" in text and "n/a" not in text.split("## 8")[0]
