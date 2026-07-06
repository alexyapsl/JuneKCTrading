"""
Keltner Channel Calculator (EMA + Wilder ATR)

Designed for both:
- Batch analysis (warmup from historical bars)
- Real-time incremental updates (one bar at a time from streamer)

Usage:
    kc = KeltnerChannel(period=13, multiplier=1.6)
    kc.warmup(historical_bars)                 # seed state
    values = kc.update(new_bar)                # returns KeltnerValues

    # Or fully batch:
    values_list = kc.calculate(bars)           # returns list of KeltnerValues

Formula (confirmed):
    Middle = EMA(close, period)
    ATR    = Wilder ATR (RMA of True Range)
    Upper  = Middle + ATR * multiplier
    Lower  = Middle - ATR * multiplier
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class Bar:
    """Simple OHLC bar. timestamp can be any comparable value (datetime recommended)."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class KeltnerValues:
    """Keltner Channel values for a single bar."""
    mid: float
    upper: float
    lower: float
    atr: float


class KeltnerChannel:
    """
    Stateful Keltner Channel calculator.

    - Uses EMA for the middle band
    - Uses Wilder's ATR (RMA) for the channel width
    - Supports both batch warmup and incremental updates
    """

    def __init__(self, period: int = 13, multiplier: float = 1.6):
        if period < 1:
            raise ValueError("period must be >= 1")
        if multiplier <= 0:
            raise ValueError("multiplier must be > 0")

        self.period = period
        self.multiplier = multiplier
        self.alpha = 1.0 / period          # same alpha for EMA and Wilder's ATR (RMA)

        # Internal state
        self._ema: Optional[float] = None
        self._atr: Optional[float] = None
        self._prev_close: Optional[float] = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def warmup(self, bars: List[Bar]) -> None:
        """Seed internal state from a list of historical bars. Does NOT return values."""
        if not bars:
            return

        # Initialize EMA and ATR from the first bar
        first = bars[0]
        self._ema = float(first.close)
        self._atr = float(first.high) - float(first.low)   # first TR = high - low
        self._prev_close = float(first.close)

        # Process remaining bars
        for bar in bars[1:]:
            self._update_internal(bar)

        self._initialized = True

    def update(self, bar: Bar) -> KeltnerValues:
        """Update state with a new bar and return the Keltner Values for that bar."""
        if not self._initialized:
            # First bar ever: treat it as warmup
            self.warmup([bar])
        else:
            self._update_internal(bar)

        if self._ema is None or self._atr is None:
            # Should not happen, but guard anyway
            raise RuntimeError("KeltnerChannel internal state is invalid")

        mid = self._ema
        atr = self._atr
        offset = atr * self.multiplier

        return KeltnerValues(
            mid=round(mid, 4),
            upper=round(mid + offset, 4),
            lower=round(mid - offset, 4),
            atr=round(atr, 4),
        )

    def calculate(self, bars: List[Bar]) -> List[KeltnerValues]:
        """
        Batch calculation. Returns one KeltnerValues per input bar.
        This resets internal state.
        """
        self.reset()
        if not bars:
            return []

        results: List[KeltnerValues] = []
        for bar in bars:
            results.append(self.update(bar))
        return results

    def reset(self) -> None:
        """Clear all internal state."""
        self._ema = None
        self._atr = None
        self._prev_close = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _update_internal(self, bar: Bar) -> None:
        """Update EMA and ATR state with a new bar (assumes already initialized)."""
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)

        # True Range
        tr = self._true_range(bar, self._prev_close)

        # Update EMA (middle band)
        self._ema = self.alpha * close + (1 - self.alpha) * self._ema

        # Update Wilder's ATR (RMA)
        self._atr = self.alpha * tr + (1 - self.alpha) * self._atr

        self._prev_close = close

    @staticmethod
    def _true_range(bar: Bar, prev_close: Optional[float]) -> float:
        """Calculate True Range for a bar."""
        high = float(bar.high)
        low = float(bar.low)

        if prev_close is None:
            return high - low

        prev = float(prev_close)
        tr1 = high - low
        tr2 = abs(high - prev)
        tr3 = abs(low - prev)
        return max(tr1, tr2, tr3)


# ----------------------------------------------------------------------
# Quick manual test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Tiny smoke test
    bars = [
        Bar(datetime(2026, 7, 2, 9, 30), 42450.0, 42470.0, 42440.0, 42455.0),
        Bar(datetime(2026, 7, 2, 9, 33), 42455.0, 42480.0, 42450.0, 42472.0),
        Bar(datetime(2026, 7, 2, 9, 36), 42472.0, 42490.0, 42460.0, 42465.0),
        Bar(datetime(2026, 7, 2, 9, 39), 42465.0, 42485.0, 42455.0, 42478.0),
        Bar(datetime(2026, 7, 2, 9, 42), 42478.0, 42500.0, 42470.0, 42495.0),
    ]

    kc = KeltnerChannel(period=13, multiplier=1.6)
    print("=== Batch (calculate) ===")
    results = kc.calculate(bars)
    for b, v in zip(bars, results):
        print(f"{b.timestamp} | mid={v.mid:,.2f}  upper={v.upper:,.2f}  lower={v.lower:,.2f}  atr={v.atr:,.2f}")

    print("\n=== Incremental (update) ===")
    kc2 = KeltnerChannel(period=13, multiplier=1.6)
    for b in bars:
        v = kc2.update(b)
        print(f"{b.timestamp} | mid={v.mid:,.2f}  upper={v.upper:,.2f}  lower={v.lower:,.2f}  atr={v.atr:,.2f}")

    print("\nKeltnerChannel module ready.")