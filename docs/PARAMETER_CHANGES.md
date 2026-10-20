# Margin parameter changes during the sample

The run applies the parameters of its `meta` snapshot to the whole sample (PROTOCOL.md, section 2). Hyperliquid
publishes no history of maximum leverage per asset. The changes below come from its official announcement
channel (t.me/s/hyperliquid_announcements, post numbers given). Dates are approximate when the post does not
state one. Most posts do not list the old and new value of every asset.

| Approximate date | Change | Post |
| --- | --- | --- |
| December 2024 | "Max leverage has been updated for various assets", values not listed | 277 |
| 12 to 13 March 2025 | BTC and ETH maximum leverage lowered to 40x and 25x (from 50x), "to increase maintenance margin requirements for larger positions" | 323 |
| from 15 March 2025 | 20% margin ratio required to transfer margin; partial liquidations live | 324, 327 |
| March 2025 | "Max leverage has been decreased for various assets", not listed | 325 |
| 22 May 2025 | margin tiers live for DOGE, ARB and OP | 367 |
| 25 May 2025 | margin tiers for 30 assets | 374 |
| late May 2025 | margin transfer requirement lowered from 20% to 10% | 377 |
| 2 June 2025 | margin tiers for BTC, ETH, SOL, XRP, HYPE and FARTCOIN; HYPE and FARTCOIN raised to 10x | 379 |
| September 2025 | WLFI from 3x to 5x; XPL converted to a regular perpetual, up to 10x | 425, 444 |
| October 2025 | PAXG and PUMP raised to 10x, ASTER to 5x; BIGTIME, GRIFFAIN, NXPC, SAGA, SCR and SOPH lowered to 3x | 463 |
| November 2025 | ZEC raised to 10x; about 28 assets lowered to 3x and WIF to 5x | 472, 477 |
| 2026 | INJ, LDO, OP, SEI and TIA lowered to 5x | 533 |

Consequence for the reading: an asset whose maximum leverage was cut during the sample is tested here at its
lower, current leverage, so with a wider maintenance margin than it had at the time. BTC and ETH before March
2025 had maintenance margins of 1% (50x), narrower than the 1.25% and 2% tested.
