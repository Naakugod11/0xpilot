"""Technical analysis tools — currently FVG (fair value gap) detection.

Designed for traders looking at sub-hour timeframes. Uses Bybit v5 public
API for kline data (no auth needed, works from DE, 1000 req/min limit).

A fair value gap (FVG) — also known as imbalance — is a 3-candle pattern
where the middle candle creates an unfilled price gap relative to its
neighbors:

  Bullish FVG: candle[i-1].high < candle[i+1].low
    → gap range is (candle[i-1].high, candle[i+1].low)

  Bearish FVG: candle[i-1].low > candle[i+1].high
    → gap range is (candle[i+1].high, candle[i-1].low)

A gap is "filled" when price later wicks back into the range. Unfilled
gaps are commonly watched as potential support/resistance.
"""

from __future__ import annotations

from typing import Any

from app.clients.bybit import BybitClient
from app.observability.logger import get_logger
from app.tools.base import BaseTool

logger = get_logger(__name__)


# Common short-form aliases → Binance pair symbols
# Agent or user can also pass full pair (ETHUSDT) — we accept both.
_SYMBOL_ALIASES: dict[str, str] = {
    "ETH": "ETHUSDT",
    "BTC": "BTCUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
    "XRP": "XRPUSDT",
    "ADA": "ADAUSDT",
    "DOGE": "DOGEUSDT",
    "AVAX": "AVAXUSDT",
    "LINK": "LINKUSDT",
    "MATIC": "MATICUSDT",
    "ARB": "ARBUSDT",
    "OP": "OPUSDT",
    "SUI": "SUIUSDT",
}


def _resolve_symbol(symbol: str) -> str:
    upper = symbol.upper()
    return _SYMBOL_ALIASES.get(upper, upper)


def _detect_fvgs(klines: list[list[Any]]) -> list[dict[str, Any]]:
    """Scan a chronological list of candles for FVG patterns.

    Returns gaps with timestamp, range, direction, and fill status.
    The fill check uses ALL subsequent candles' wicks (high/low) — if any
    wick enters the gap range, it's marked filled.
    """
    if len(klines) < 3:
        return []

    gaps: list[dict[str, Any]] = []

    # Each kline: [open_time, open, high, low, close, volume, ...]
    for i in range(1, len(klines) - 1):
        prev_candle = klines[i - 1]
        next_candle = klines[i + 1]

        prev_high = float(prev_candle[2])
        prev_low = float(prev_candle[3])
        next_high = float(next_candle[2])
        next_low = float(next_candle[3])

        bullish_gap = prev_high < next_low
        bearish_gap = prev_low > next_high

        if not (bullish_gap or bearish_gap):
            continue

        if bullish_gap:
            gap_low, gap_high = prev_high, next_low
            direction = "bullish"
        else:
            gap_low, gap_high = next_high, prev_low
            direction = "bearish"

        # Mid-candle of the pattern is the FVG-creating candle (index i)
        # Open time of next candle = when the gap was confirmed
        gap_open_time_ms = int(next_candle[0])

        # Fill check: scan candles AFTER the next one for any wick into range
        filled = False
        filled_at_ms: int | None = None
        for later in klines[i + 2:]:
            later_high = float(later[2])
            later_low = float(later[3])
            # Wick overlaps gap range?
            if later_low <= gap_high and later_high >= gap_low:
                filled = True
                filled_at_ms = int(later[0])
                break

        gaps.append(
            {
                "timestamp_ms": gap_open_time_ms,
                "direction": direction,
                "range_low": round(gap_low, 8),
                "range_high": round(gap_high, 8),
                "size_pct": round(((gap_high - gap_low) / gap_low) * 100, 4),
                "filled": filled,
                "filled_at_ms": filled_at_ms,
            }
        )

    return gaps


class DetectFairValueGapsTool(BaseTool):
    name = "detect_fair_value_gaps"
    description = (
        "Detect fair value gaps (FVGs / imbalances) in a price chart. "
        "An FVG is a 3-candle pattern where price leaves an unfilled "
        "range — bullish FVG when candle 1 high < candle 3 low, bearish "
        "when candle 1 low > candle 3 high. Returns all FVGs in the "
        "lookback window with direction, range, size %, and whether "
        "the gap has been filled by later wicks. Useful for ICT/SMC-style "
        "TA, identifying potential support/resistance levels, and "
        "recent structural imbalances. Uses Bybit v5 public API for "
        "OHLC data — works only for tickers traded on Bybit Spot "
        "(majors like ETH, BTC, SOL, plus most top-100 alts)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": (
                    "Token ticker (e.g. 'ETH', 'BTC', 'SOL') OR full Bybit "
                    "pair (e.g. 'ETHUSDT'). Common aliases auto-resolve to "
                    "USDT pairs."
                ),
            },
            "interval": {
                "type": "string",
                "description": "Candle timeframe.",
                "enum": [
                    "1m", "5m", "15m", "30m",
                    "1h", "4h", "1d",
                ],
            },
            "lookback": {
                "type": "integer",
                "description": "Number of candles to scan. Default 200, max 1000.",
                "minimum": 10,
                "maximum": 1000,
            },
        },
        "required": ["symbol", "interval"],
    }

    def __init__(self, client: BybitClient | None = None) -> None:
        self._client = client or BybitClient()

    async def execute(
        self,
        symbol: str,
        interval: str,
        lookback: int = 200,
        **_: Any,
    ) -> dict[str, Any]:
        resolved = _resolve_symbol(symbol)
        klines = await self._client.get_klines(resolved, interval, limit=lookback)
        gaps = _detect_fvgs(klines)

        unfilled = [g for g in gaps if not g["filled"]]

        # Cap response size — agent doesn't need 50 entries
        # Sort by recency (most recent first), keep top 25
        gaps_recent = sorted(gaps, key=lambda g: g["timestamp_ms"], reverse=True)[:25]
        unfilled_recent = sorted(
            unfilled, key=lambda g: g["timestamp_ms"], reverse=True
        )[:10]

        return {
            "symbol": resolved,
            "interval": interval,
            "candles_scanned": len(klines),
            "fvg_count_total": len(gaps),
            "fvg_count_unfilled": len(unfilled),
            "most_recent_fvg": gaps_recent[0] if gaps_recent else None,
            "most_recent_unfilled_fvg": unfilled_recent[0] if unfilled_recent else None,
            "recent_fvgs_sample": gaps_recent,
            "recent_unfilled_sample": unfilled_recent,
        }
