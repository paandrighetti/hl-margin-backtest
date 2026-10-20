# hl-margin-backtest

How long do Hyperliquid's maintenance margins cover price moves?

When a Hyperliquid position hits its maintenance margin, the exchange liquidates it. Until the position is
closed, the maintenance margin is the only buffer between the trader's loss and a loss for the liquidator vault
(HLP) or, through auto-deleveraging, for traders on the other side. This repository back-tests that buffer the
way central counterparties back-test their initial margin: count the windows in which the adverse price move
exceeds the margin, compare the achieved coverage with the 99% standard of the CPMI-IOSCO principles and of
EMIR's technical standards, and find the longest close-out horizon at which the margin still covers 99% of
moves.

Scope: every perpetual of Hyperliquid's first perp exchange with a Binance USD-M price history, January 2023 to
September 2026, horizons from 1 minute to 1 day, plus the crash of 10 October 2025 and the link between margin
breaches and HLP's returns.

## Status

- `PROTOCOL.md` was frozen before any real data was read; `PROTOCOL.sha256` holds its hash and `scripts/run.py`
  refuses to run if the protocol changed.
- The run happens once, in the `run` workflow of this repository, which commits a start mark before reading any
  data and commits the outputs at the end. Results will appear in `outputs/` and in `REPORT.md`.

## Method in brief

| Item | Choice |
| --- | --- |
| Margin parameters | Hyperliquid `meta` snapshot at run time; maintenance margin rate = 1 / (2 × first-tier max leverage) |
| Buffers | the maintenance margin rate (book liquidation) and 2/3 of it (liquidator vault takeover) |
| Prices | Binance USD-M 1-minute klines, checksums verified, as a proxy for Hyperliquid's mark price |
| Windows | non-overlapping, aligned on UTC midnight, 1 minute to 1 day |
| Measures | adverse move at the end of the window, and worst move inside it |
| Reporting | CPMI-IOSCO public quantitative disclosure items 6.5: exceptions, observations, achieved coverage, peak and average excess |
| Inference | stationary bootstrap over days, Holm's procedure across assets; Kupiec and Christoffersen tests for comparison |
| Checks | data gates per asset-month, an independent recount of exceptions, Hyperliquid 4-hour candles against the proxy |

## Run it

```
pip install -e ".[dev]"
pytest -q
python scripts/run.py --out outputs --cache cache    # reads Hyperliquid and data.binance.vision
python scripts/recompute.py --out outputs --cache cache
```

The tests include a full run on synthetic sources.

## Layout

- `src/hlmb/`: data access (`sources.py`), margin parameters (`params.py`), gates and windows (`windows.py`),
  cells (`analysis.py`), tests and bootstrap (`stats.py`), the independent recount (`recount.py`).
- `scripts/run.py`: the single run; `scripts/recompute.py`: the recount.
- `outputs/`: everything the run writes.

## License

Code under the MIT license (`LICENSE`). Report text and figures under CC BY 4.0.
