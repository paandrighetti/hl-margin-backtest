# Protocol: Hyperliquid maintenance margin backtest

Written and frozen before any real data is read. `scripts/run.py` compares the SHA-256 of this file with
`PROTOCOL.sha256` and refuses to run if they differ.

## 1. Question

Hyperliquid liquidates a perpetual position when the account's equity falls below its maintenance margin.
From that trigger to the close-out, the maintenance margin is the only buffer. If the price moves against the
position by more than the maintenance margin rate before the position is closed, the loss falls on the
liquidator vault (part of the HLP vault) or, through auto-deleveraging, on traders on the other side.

For each perpetual listed on Hyperliquid's first perp exchange, the study measures how often the adverse price
move over a close-out horizon of h minutes exceeds the maintenance margin rate, and up to which horizon the
margin covers 99% of moves. 99% is the single-tailed confidence level central counterparties must meet for
initial margin (CPMI-IOSCO, Principles for financial market infrastructures, 2012, principle 6, key
consideration 3; Commission Delegated Regulation (EU) No 153/2013, article 24, for instruments other than OTC
derivatives).

It is a measurement. Nothing is fitted or selected on the data.

## 2. Margin parameters

- Source: the `meta` response of the Hyperliquid info endpoint (`POST https://api.hyperliquid.xyz/info`, body
  `{"type": "meta"}`, first perp exchange), read once at the start of the run. The raw response and its SHA-256
  are kept in `outputs/`.
- For each asset, the maximum leverage of its first margin tier, `Lmax`, gives the maintenance margin rate
  `MMR = 1 / (2 × Lmax)`. The documentation states that "the maintenance margin is currently set to half of the
  initial margin at max leverage" (Hyperliquid documentation, Margining). If the asset's margin table has tiers,
  the first tier's maximum leverage is used; if it differs from the asset's `maxLeverage` field, the table wins
  and the difference is listed.
- Two buffers:
  - B1 (primary): `MMR`, the distance from the liquidation trigger to bankruptcy for a position at the trigger;
  - B2: `2/3 × MMR`, what is left when the liquidator vault takes over a position ("positions that are below
    2/3 of the maintenance margin can be taken over by the liquidator vault", Hyperliquid documentation,
    Liquidations).
- Today's parameters are applied to the whole sample, so the question is whether the current margins would
  have covered the moves of 2023 to 2026. Parameter changes during the sample (BTC and ETH at 50x before March
  2025, margin tiers from May 2025, leverage cuts on single assets) are listed in the report, not modelled.

## 3. Universe and price proxy

- Assets of the `meta` snapshot that are not delisted.
- Prices: Binance USD-M perpetual 1-minute klines from data.binance.vision. The symbol is `{NAME}USDT`, or
  `1000{X}USDT` for a Hyperliquid name `k{X}` (thousand-unit contracts). A symbol is available if its monthly
  1-minute kline folder appears in the data.binance.vision listing. Assets with no available symbol are
  excluded and listed. Returns do not depend on the contract unit.
- Why a proxy: the info endpoint returns only the latest 5,000 candles, so Hyperliquid's own 1-minute history
  covers about three and a half days. Hyperliquid's oracle is a weighted median of spot prices from eight
  venues in which Binance has the largest weight (3 of 12), and its mark price uses Binance's perpetual mid as
  one of three inputs (Hyperliquid documentation, Oracle; Robust price indices). Section 7 measures the gap.

## 4. Sample and data gates

- Period: 1 January 2023 to 30 September 2026, monthly files
  `data/futures/um/monthly/klines/{SYMBOL}/1m/{SYMBOL}-1m-{YYYY-MM}.zip`.
- Gates, per asset-month, before any move is computed:
  - G1: the SHA-256 of the zip file matches its `.CHECKSUM` file (one retry);
  - G2: open times are whole minutes inside the month, strictly increasing;
  - G3: prices are positive and low ≤ min(open, close) ≤ max(open, close) ≤ high on every row;
  - G4: at least 95% of the minutes of the month are present.

  A month that fails is excluded and listed with the reason. An asset needs at least 12 included months.
- G6, identity check per asset, against a Binance ticker that names another token: the daily returns of
  Hyperliquid's 1-day candles and of the Binance UTC days must correlate at 0.9 or more over at least 60 common
  days. Otherwise the asset is excluded and listed.
- G5, for the run: at least 50 assets qualify. Otherwise the run stops without results.

## 5. Measures

For each asset, horizon h in {1, 2, 5, 10, 15, 30, 60, 120, 240, 480, 1440} minutes and side (long, short),
each UTC day is cut into consecutive, non-overlapping windows of h minutes starting at midnight. Non-overlapping
windows stop two exceptions from sharing the same price move, which the coverage tests assume.

With O the open of the window's first minute, C the close of its last minute, L the lowest low and H the
highest high inside the window:

- M1 (primary), move at the end of the window: long `1 − C/O`, short `C/O − 1`;
- M2, worst move inside the window: long `1 − L/O`, short `H/O − 1`.

A window counts only if its first and last minutes are present and at least 80% of its minutes are present.

An exception is a window whose adverse move is strictly larger than the buffer. Each cell (asset, horizon,
side, measure, buffer) reports the back-testing items of the CPMI-IOSCO public quantitative disclosure
standards for central counterparties (2015, item 6.5): exceptions, observations, achieved coverage
(1 − exceptions / observations), peak and average excess of the move over the buffer. It adds the 99%, 99.5%
and 99.9% quantiles of the adverse move and the ratio of the 99% quantile to the buffer.

## 6. Tests and decision rules

Primary cell: M1, B1, h = 5 minutes, each side. Five minutes is the first horizon of the grid above the time
Hyperliquid needs to close a large position in normal markets: positions above 100,000 USDC are liquidated in
parts, with a 30-second cooldown after a partial liquidation (Hyperliquid documentation, Liquidations).

- Interval: stationary bootstrap of UTC days (Politis and Romano, 1994), mean block length 20 days; seed
  20261020 for the first cell, plus one for each following cell in the order (asset name, horizon, side);
  percentile 95% interval of the exception rate. 10,000 replicates for the primary cell, 2,000 for
  the other M1 cells.
- Classification of each asset-side at the primary cell. Two one-sided nulls, each with Holm's step-down
  procedure at a 5% family-wise level over all asset-sides:
  - "below 99%" if "exception rate ≤ 1%" is rejected;
  - "at or above 99%" if "exception rate ≥ 1%" is rejected;
  - "not distinguishable" otherwise.

  Basic bootstrap p-values: `(1 + #{b : r_b − r ≥ r − 1%}) / (B + 1)` for the first null, where r is the
  observed rate and r_b its replicates, and `(1 + #{b : r_b − r ≤ r − 1%}) / (B + 1)` for the second.
- Standard tests for every cell: Kupiec's proportion-of-failures test against 1% (Kupiec, 1995), and
  Christoffersen's independence and conditional coverage tests (Christoffersen, 1998). They assume independent
  exceptions, which volatility clustering breaks. They are reported for comparison with central counterparty
  disclosures and decide nothing.
- Horizon of coverage: for each asset, h* is the largest grid horizon such that M1 under B1 has an achieved
  coverage of at least 99% on both sides at that horizon and at every shorter one. h*_conf uses the upper bound
  of the bootstrap interval of the exception rate instead of the point estimate. Both are descriptive.
- 10 October 2025: for each asset, the largest adverse move between 20:00 and 22:00 UTC over windows of 1, 5
  and 15 minutes starting at any minute, for M1 and M2 and both sides, compared with B1 and B2. The report
  gives the number of assets in breach.

## 7. Proxy check

- Hyperliquid 4-hour candles (`candleSnapshot`, latest 5,000 per asset) for the 30 assets of the universe with
  the largest open interest in the `metaAndAssetCtxs` snapshot, against 4-hour windows built from the Binance
  1-minute data over the common period: correlation of 4-hour returns, median ratio of high-low ranges
  (Hyperliquid over Binance), share of windows where the two sources disagree on an M2 exception under B1, and
  the 20:00 to 24:00 UTC candle of 10 October 2025 side by side.
- Interpretation gate, not a run gate: if the median correlation of 4-hour returns is below 0.95, the report
  states that the proxy is unreliable and presents every result as Binance-based only.

## 8. HLP

- `vaultDetails` of the HLP vault 0xdfc24b077bc1425ad1dea75bcb6f8158e10df303, "allTime" history of account value
  and cumulative profit and loss.
- Period return: change in cumulative profit and loss divided by the account value at the start of the period.
- Descriptive: annualised geometric return, annualised volatility, maximum drawdown of the return index, the
  five worst periods with dates, and the period that contains 10 October 2025.
- Test, only if the median spacing of the history is at most two days: Spearman correlation between HLP period
  returns and the number of primary-cell exceptions summed over assets and sides in the same period. Null
  hypothesis ρ ≥ 0, rejected at 5% if the upper bound of the one-sided 95% stationary bootstrap interval (mean
  block 20 periods, 10,000 replicates) is below zero. Otherwise descriptive only.

## 9. Outputs and report

Everything lands in `outputs/`. The report is generated from these files by `scripts/build_report.py`; no
figure is typed by hand. Excluded assets and months are listed with their reasons.

## 10. Run discipline

- `scripts/run.py` refuses to start if `outputs/run.started` exists or if this file's SHA-256 differs from
  `PROTOCOL.sha256`. A run that fails before writing `outputs/coverage.csv` may be restarted once; the restart
  is logged in `outputs/run.log`.
- An independent implementation, `scripts/recompute.py`, recounts exceptions and observations of every M1 and
  M2 cell for BTC, ETH, SOL, DOGE, XRP, HYPE and kPEPE (those present in the universe) and for the asset with
  the most primary-cell exceptions. Any mismatch is reported.

## 11. Limitations stated before the run

- Binance prices stand in for Hyperliquid's mark price; Binance-specific wicks, such as those of 10 October
  2025, can overstate moves.
- Current parameters on past data; first margin tier only, so positions larger than the first tier bound
  (150 million USDC for BTC, 3 to 70 million for most others) are outside the test.
- No fees, funding, slippage, or price impact of the liquidation order itself.
- One-minute resolution: Hyperliquid updates its mark price about every three seconds.
- Each position is tested alone: cross-margin offsets between positions are ignored.
- Assets delisted before the snapshot are excluded (survivorship).

## References

- Christoffersen, P. F. (1998). Evaluating interval forecasts. International Economic Review, 39(4), 841-862.
- Commission Delegated Regulation (EU) No 153/2013 on requirements for central counterparties, articles 24 to
  28 and 49.
- CPMI-IOSCO (2012). Principles for financial market infrastructures. Principle 6.
- CPMI-IOSCO (2015). Public quantitative disclosure standards for central counterparties. Item 6.5.
- Holm, S. (1979). A simple sequentially rejective multiple test procedure. Scandinavian Journal of
  Statistics, 6(2), 65-70.
- Hyperliquid documentation: Margining; Margin tiers; Liquidations; Oracle; Robust price indices; Info
  endpoint; Historical data (hyperliquid.gitbook.io).
- Kupiec, P. H. (1995). Techniques for verifying the accuracy of risk measurement models. The Journal of
  Derivatives, 3(2), 73-84.
- Politis, D. N. and Romano, J. P. (1994). The stationary bootstrap. Journal of the American Statistical
  Association, 89(428), 1303-1313.
