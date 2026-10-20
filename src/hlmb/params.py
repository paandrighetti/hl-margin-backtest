"""Margin parameters from Hyperliquid's `meta` response, and the mapping to Binance symbols (protocol 2 and 3)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Asset:
    name: str
    max_leverage: int  # first margin tier
    field_leverage: int  # the asset's maxLeverage field
    delisted: bool

    @property
    def mmr(self) -> float:
        return 1.0 / (2 * self.max_leverage)


def first_tier_leverage(item: dict, tables: dict[int, dict]) -> int:
    """Maximum leverage of the asset's first margin tier.

    Hyperliquid documentation, Margin tiers: "For IDs less than 50, there is a single tier with max leverage
    equal to the ID." Higher IDs point to a table in `meta.marginTables`.
    """
    table_id = item.get("marginTableId")
    if table_id is None:
        return int(item["maxLeverage"])
    if table_id < 50:
        return int(table_id)
    tiers = sorted(tables[table_id]["marginTiers"], key=lambda t: float(t["lowerBound"]))
    return int(tiers[0]["maxLeverage"])


def assets_from_meta(meta: dict) -> list[Asset]:
    tables = {int(i): t for i, t in meta.get("marginTables", [])}
    return [
        Asset(
            name=item["name"],
            max_leverage=first_tier_leverage(item, tables),
            field_leverage=int(item["maxLeverage"]),
            delisted=bool(item.get("isDelisted", False)),
        )
        for item in meta["universe"]
    ]


def binance_symbol(name: str, available: set[str]) -> str | None:
    """`{NAME}USDT`, or `1000{X}USDT` for a thousand-unit contract `k{X}`; None if neither is available."""
    if f"{name}USDT" in available:
        return f"{name}USDT"
    if name.startswith("k") and name[1:2].isupper() and f"1000{name[1:]}USDT" in available:
        return f"1000{name[1:]}USDT"
    return None
