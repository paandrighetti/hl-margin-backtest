import io
import zipfile

import numpy as np
import pytest
from scipy.stats import chi2

from hlmb import analysis, params, sources, stats
from hlmb import windows as w
from hlmb.recount import recount

START = w.month_bounds("2024-02")[0]


def rows_from(prices: np.ndarray, start: int = START, drop=()) -> np.ndarray:
    """Rows (t, o, h, l, c) with open = previous close, high and low = max and min of open and close."""
    o = np.r_[prices[0], prices[:-1]]
    c = prices
    t = start + np.arange(len(prices)) * w.MINUTE_MS
    rows = np.column_stack([t, o, np.maximum(o, c), np.minimum(o, c), c])
    return np.delete(rows, list(drop), axis=0)


def grid_of(rows: np.ndarray, minutes: int) -> np.ndarray:
    g = np.full((4, minutes), np.nan)
    w.place(g, rows, START)
    return g


def test_month_bounds_and_gates():
    _, minutes = w.month_bounds("2024-02")
    assert minutes == 29 * 1440
    good = rows_from(np.full(minutes, 100.0))
    assert w.gate_month(good, "2024-02") is None
    assert w.gate_month(good[: int(0.9 * minutes)], "2024-02").startswith("G4")
    assert w.gate_month(good[::-1], "2024-02").startswith("G2")
    bad = good.copy()
    bad[5, 3] = 101.0  # low above open and close
    assert w.gate_month(bad, "2024-02").startswith("G3")
    shifted = good.copy()
    shifted[:, 0] += 30_000
    assert w.gate_month(shifted, "2024-02").startswith("G2")


def test_windows_and_moves():
    prices = np.full(1440, 100.0)
    prices[7] = 90.0  # minute 7 closes at 90, minute 8 opens at 90 and closes at 100
    g = grid_of(rows_from(prices), 1440)
    o, c, low, high, valid = w.windows(g, 5)
    assert valid.all() and len(o) == 288
    assert (o[1], c[1], low[1], high[1]) == (100.0, 100.0, 90.0, 100.0)
    m = w.adverse_moves(o, c, low, high)
    assert m[("M1", "long")][1] == pytest.approx(0.0)
    assert m[("M2", "long")][1] == pytest.approx(0.1)
    o1, c1, *_ = w.windows(g, 1)
    assert w.adverse_moves(o1, c1, o1, c1)[("M1", "long")][7] == pytest.approx(0.1)


def test_window_validity():
    g = grid_of(rows_from(np.full(1440, 100.0), drop=[0, 12, 13]), 1440)
    *_, valid = w.windows(g, 5)
    assert not valid[0]  # first minute missing
    assert not valid[2]  # minutes 10-14 with 12 and 13 missing: 60% present
    assert valid[1] and valid[3]
    *_, valid10 = w.windows(g, 10)
    assert valid10[1]  # minutes 10-19, 80% present, first and last there


def test_kupiec_and_christoffersen():
    assert kup0() == pytest.approx(0.0)
    lr, p = stats.kupiec(20, 1000, 0.01)
    expected = -2 * (980 * np.log(0.99) + 20 * np.log(0.01)) + 2 * (980 * np.log(0.98) + 20 * np.log(0.02))
    assert lr == pytest.approx(expected) and p == pytest.approx(chi2.sf(expected, 1))
    clustered = np.zeros(1000, dtype=bool)
    clustered[100:110] = True
    _, p_c = stats.christoffersen(*stats.transitions(clustered, np.ones(1000, dtype=bool)))
    assert p_c < 1e-6
    spread = np.zeros(1000, dtype=bool)
    spread[::100] = True
    assert stats.christoffersen(*stats.transitions(spread, np.ones(1000, dtype=bool)))[1] > 0.05


def kup0():
    return stats.kupiec(10, 1000, 0.01)[0]


def test_transitions_skip_invalid():
    e = np.array([True, True, False, True])
    v = np.array([True, False, True, True])
    assert stats.transitions(e, v) == (0, 1, 0, 0)


def test_stationary_indices():
    rng = np.random.default_rng(0)
    idx = stats.stationary_indices(500, 20.0, 400, rng)
    assert idx.shape == (400, 500) and idx.min() >= 0 and idx.max() < 500
    starts = (np.diff(idx, axis=1) != 1) & (np.diff(idx, axis=1) != -499)
    assert 1 / starts.mean() == pytest.approx(20, rel=0.15)


def test_bootstrap_classification():
    x = np.full(1000, 5)
    n = np.full(1000, 100)  # 5% exceptions every day
    reps = stats.bootstrap_rates(x, n, 500, seed=1)
    p_below, p_above = stats.bootstrap_pvalues(0.05, reps)
    assert p_below < 0.01 and p_above > 0.5
    assert stats.holm(np.array([0.01, 0.04, 0.03, 0.005]), 0.05).tolist() == [True, False, False, True]


def test_params_and_mapping():
    tables = {56: {"marginTiers": [{"lowerBound": "150000000.0", "maxLeverage": 20},
                                   {"lowerBound": "0.0", "maxLeverage": 40}]}}
    assert params.first_tier_leverage({"maxLeverage": 40, "marginTableId": 56}, tables) == 40
    assert params.first_tier_leverage({"maxLeverage": 10, "marginTableId": 10}, tables) == 10
    assert params.first_tier_leverage({"maxLeverage": 3}, tables) == 3
    meta = {"universe": [{"name": "BTC", "maxLeverage": 40, "marginTableId": 56},
                         {"name": "OLD", "maxLeverage": 3, "isDelisted": True}],
            "marginTables": [[56, tables[56]]]}
    btc, old = params.assets_from_meta(meta)
    assert btc.mmr == pytest.approx(0.0125) and old.delisted
    available = {"BTCUSDT", "1000PEPEUSDT"}
    assert params.binance_symbol("BTC", available) == "BTCUSDT"
    assert params.binance_symbol("kPEPE", available) == "1000PEPEUSDT"
    assert params.binance_symbol("HYPE", available) is None


def zipped(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", text)
    return buf.getvalue()


def test_parse_klines():
    body = "1706745600000,1,2,0.5,1.5,10,1706745659999,0,0,0,0,0\n1706745660000,1.5,2,1,1,1,0,0,0,0,0,0\n"
    a = sources.parse_klines(zipped(body))
    b = sources.parse_klines(zipped("open_time,open,high,low,close,volume,close_time,a,b,c,d,e\n" + body))
    assert a.shape == (2, 5) and np.array_equal(a, b)
    micro = sources.parse_klines(zipped(body.replace("1706745600000", "1706745600000000")
                                        .replace("1706745660000", "1706745660000000")))
    assert micro[0, 0] == 1706745600000


def test_cells_match_independent_recount():
    rng = np.random.default_rng(7)
    minutes = 29 * 1440
    prices = 100 * np.exp(np.cumsum(rng.standard_t(3, minutes) * 0.004))
    rows = rows_from(prices, drop=rng.choice(minutes, 300, replace=False))
    g = grid_of(rows, minutes)
    mmr = 1 / (2 * 20)
    cells, daily = analysis.cells("X", mmr, g)
    for h in (1, 5, 60):
        again = recount(rows, START, h, mmr)
        for c in (c for c in cells if c["horizon_min"] == h):
            assert again[(c["measure"], c["side"], c["buffer"])] == (c["exceptions"], c["observations"])
    x, n = daily[(5, "long")]
    five = next(c for c in cells if c["horizon_min"] == 5 and c["measure"] == "M1" and c["side"] == "long" and c["buffer"] == "B1")
    assert (x.sum(), n.sum()) == (five["exceptions"], five["observations"])


def test_worst_moves():
    prices = np.full(300, 100.0)
    prices[150:155] = 80.0
    g = grid_of(rows_from(prices), 300)
    worst = analysis.worst_moves(g, 100, 220, 5)
    assert worst[("M1", "long")] == pytest.approx(0.2)
    assert worst[("M1", "short")] == pytest.approx(0.25)
    g[0, 120] = np.nan
    assert np.isnan(analysis.worst_moves(g, 100, 220, 5)[("M1", "long")])


def test_identity_check(monkeypatch):
    import run as runmod

    rng = np.random.default_rng(5)
    minutes = 29 * 1440
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, minutes)))
    g = grid_of(rows_from(prices), minutes)
    o, c, *_ = w.windows(g, 1440)
    same = [{"t": START + d * 86_400_000, "o": o[d], "c": c[d]} for d in range(29)]
    other = [{"t": x["t"], "o": 1.0, "c": 1.0 + rng.normal(0, 0.05)} for x in same]
    monkeypatch.setattr(sources, "hl_candles", lambda *a, **k: same)
    corr, days = runmod.identity("X", g, START)
    assert days == 29 and corr == pytest.approx(1.0)
    monkeypatch.setattr(sources, "hl_candles", lambda *a, **k: other)
    assert runmod.identity("X", g, START)[0] < 0.9
