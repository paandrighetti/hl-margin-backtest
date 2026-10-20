"""Network access: the Hyperliquid info endpoint and data.binance.vision. Nothing here interprets data."""

from __future__ import annotations

import hashlib
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

HL_INFO = "https://api.hyperliquid.xyz/info"
BINANCE = "https://data.binance.vision"
BINANCE_LISTING = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
KLINES_PREFIX = "data/futures/um/monthly/klines/"
_S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"


class NotFound(Exception):
    pass


def _get(url: str, body: dict | None = None, tries: int = 5) -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from e
            if attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == tries - 1:
                raise
        time.sleep(min(60, 5 * 2**attempt))
    raise AssertionError("unreachable")


def hl_info(body: dict) -> object:
    return json.loads(_get(HL_INFO, body))


INTERVAL_MS = {"4h": 4 * 3_600_000, "1d": 86_400_000}


def hl_candles(coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    """Hyperliquid candles opening in [start_ms, end_ms], paged from the oldest.

    Time-range responses can be capped (Hyperliquid documentation, Info endpoint), so each page restarts after
    the last candle received. A candle request weighs 20 plus one per 60 candles returned, against a limit of
    1,200 per minute per address (Rate limits and user limits); the pause after each page keeps the pace under
    1,000 per minute.
    """
    out: list[dict] = []
    while start_ms <= end_ms:
        page = hl_info({"type": "candleSnapshot",
                        "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}})
        time.sleep(60 * (20 + len(page) / 60) / 1000)
        page = [c for c in page if start_ms <= int(c["t"]) <= end_ms]
        if not page:
            break
        out.extend(page)
        start_ms = int(page[-1]["t"]) + INTERVAL_MS[interval]
    return out


def binance_kline_symbols() -> set[str]:
    """Symbols that have a monthly 1-minute kline folder on data.binance.vision (USD-M futures)."""
    symbols: set[str] = set()
    marker = ""
    while True:
        query = urllib.parse.urlencode({"delimiter": "/", "prefix": KLINES_PREFIX, "marker": marker})
        root = ET.fromstring(_get(f"{BINANCE_LISTING}?{query}"))
        prefixes = [p.findtext(f"{_S3}Prefix") for p in root.iter(f"{_S3}CommonPrefixes")]
        symbols.update(p[len(KLINES_PREFIX):].strip("/") for p in prefixes if p)
        if root.findtext(f"{_S3}IsTruncated") != "true":
            return symbols
        marker = root.findtext(f"{_S3}NextMarker") or prefixes[-1]


def monthly_klines_zip(symbol: str, month: str) -> tuple[bytes, str]:
    """The zip file of one month of 1-minute klines and the SHA-256 published next to it.

    Raises NotFound if the month does not exist for this symbol.
    """
    path = f"{BINANCE}/{KLINES_PREFIX}{symbol}/1m/{symbol}-1m-{month}.zip"
    blob = _get(path)
    published = _get(path + ".CHECKSUM").decode().split()[0].lower()
    return blob, published


def sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def parse_klines(blob: bytes) -> np.ndarray:
    """Rows of (open time in ms, open, high, low, close) from a Binance kline zip, header or not."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        (name,) = z.namelist()
        text = z.read(name).decode()
    lines = text.splitlines()
    if lines and not lines[0][:1].isdigit():
        lines = lines[1:]
    if not lines:
        return np.empty((0, 5))
    rows = np.loadtxt(lines, delimiter=",", usecols=(0, 1, 2, 3, 4), dtype=np.float64, ndmin=2)
    if rows[0, 0] > 1e14:  # microseconds
        rows[:, 0] = np.floor(rows[:, 0] / 1000)
    return rows
