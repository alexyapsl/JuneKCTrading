"""
Signal Detector for Keltner Channel Strategy

Implements the exact rules provided:
- Short signal: bar opens above upper KC, closes back below upper KC
- Long signal : bar opens below lower KC, closes back above lower KC

Signals are evaluated on COMPLETED bars only.
Each signal includes:
  - direction (LONG / SHORT)
  - signal_id (unique per bar)
  - signal_bar timestamp + OHLC
  - kc values at signal time
  - entry_price (configurable offset from high/low)
  - stop_loss   (configurable offset from opposite extreme)
  - experiment_name + config_id (from config.py)

This module does NOT place orders. It only identifies signals.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Literal
from collections import deque
import logging
import uuid

from config import CONFIG

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    signal_id: str
    timestamp_utc: datetime
    direction: Literal["LONG", "SHORT"]
    bar_open: float
    bar_high: float
    bar_low: float
    bar_close: float
    kc_mid: float
    kc_upper: float
    kc_lower: float
    kc_atr: float
    entry_price: float
    stop_loss: float
    experiment_name: str
    config_id: str


class SignalDetector:
    """
    Stateful detector that evaluates the KC breakout-reversion rules
    on each completed bar.
    """

    def __init__(self, cooldown_bars: int = 2):
        self._prev_upper: Optional[float] = None
        self._prev_lower: Optional[float] = None
        self.cooldown_bars = cooldown_bars
        self._last_signal_bar: Optional[datetime] = None
        self._last_signal_dir: Optional[str] = None
        # Slope regime filter state (active only when CONFIG.slope_k > 0).
        # Holds the last k+1 KC mids (incl. current bar) for slope_norm.
        self._mids: Optional[deque] = (
            deque(maxlen=CONFIG.slope_k + 1) if CONFIG.slope_k > 0 else None
        )

    def reset(self):
        """Clear previous KC state (useful when restarting mid-session)."""
        self._prev_upper = None
        self._prev_lower = None
        self._last_signal_bar = None
        self._last_signal_dir = None
        if self._mids is not None:
            self._mids.clear()

    def _slope_regime(self, kc_values) -> tuple:
        """
        Return (regime, slope_norm) using shadow/backtest semantics:
          slope_norm = (mid[i] - mid[i-k]) / (k * atr[i])
          > +T -> 'up' | < -T -> 'down' | else 'range'
        regime is 'range' until k+1 mids are available.
        """
        mid = kc_values.mid
        atr = kc_values.atr
        if len(self._mids) < CONFIG.slope_k + 1:
            return "range", None
        mid_k = self._mids[0]
        slope_norm = (mid - mid_k) / (CONFIG.slope_k * atr) if atr > 0 else 0.0
        if slope_norm > CONFIG.slope_T:
            return "up", slope_norm
        if slope_norm < -CONFIG.slope_T:
            return "down", slope_norm
        return "range", slope_norm

    def check(self, bar, kc_values) -> Optional[Signal]:
        """
        Evaluate the completed bar against the previous bar's KC levels.

        Parameters
        ----------
        bar : Bar
            The completed 3-minute bar (with open, high, low, close, timestamp).
        kc_values : KeltnerValues
            KC values computed on THIS bar (mid, upper, lower, atr).

        Returns
        -------
        Signal or None
            Signal object if a valid long or short signal is detected.
        """
        current_upper = kc_values.upper
        current_lower = kc_values.lower

        # Track mids for the slope filter (every completed bar, incl. first)
        if self._mids is not None:
            self._mids.append(kc_values.mid)

        if self._prev_upper is None or self._prev_lower is None:
            # First bar ever seen — cannot evaluate signals yet
            self._prev_upper = current_upper
            self._prev_lower = current_lower
            return None

        # === SHORT SIGNAL RULE ===
        # Previous bar closed above upper KC (we use prev_upper for the rule)
        # Current bar opens above prev_upper AND closes below prev_upper
        if (bar.open > self._prev_upper) and (bar.close < self._prev_upper):
            if self._is_in_cooldown(bar.timestamp, "SHORT"):
                self._prev_upper = current_upper
                self._prev_lower = current_lower
                return None
            self._last_signal_bar = bar.timestamp
            self._last_signal_dir = "SHORT"
            if self._mids is not None:
                regime, slope_norm = self._slope_regime(kc_values)
                if regime == "up":
                    logger.info(
                        f"[SIGNAL] SHORT sig_{bar.timestamp.strftime('%Y%m%d_%H%M')}_short "
                        f"BLOCKED by slope filter (regime=up, slope_norm={slope_norm:+.3f})"
                    )
                    self._prev_upper = current_upper
                    self._prev_lower = current_lower
                    return None
            signal = self._build_signal(
                bar=bar,
                kc_values=kc_values,
                direction="SHORT",
                entry_price=round(bar.low - CONFIG.entry_offset, 4),
                stop_loss=round(bar.high + CONFIG.stop_offset, 4),
            )
            # Update state before returning
            self._prev_upper = current_upper
            self._prev_lower = current_lower
            return signal

        # === LONG SIGNAL RULE ===
        # Previous bar closed below lower KC
        # Current bar opens below prev_lower AND closes above prev_lower
        if (bar.open < self._prev_lower) and (bar.close > self._prev_lower):
            if self._is_in_cooldown(bar.timestamp, "LONG"):
                self._prev_upper = current_upper
                self._prev_lower = current_lower
                return None
            self._last_signal_bar = bar.timestamp
            self._last_signal_dir = "LONG"
            if self._mids is not None:
                regime, slope_norm = self._slope_regime(kc_values)
                if regime == "down":
                    logger.info(
                        f"[SIGNAL] LONG sig_{bar.timestamp.strftime('%Y%m%d_%H%M')}_long "
                        f"BLOCKED by slope filter (regime=down, slope_norm={slope_norm:+.3f})"
                    )
                    self._prev_upper = current_upper
                    self._prev_lower = current_lower
                    return None
            signal = self._build_signal(
                bar=bar,
                kc_values=kc_values,
                direction="LONG",
                entry_price=round(bar.high + CONFIG.entry_offset, 4),
                stop_loss=round(bar.low - CONFIG.stop_offset, 4),
            )
            self._prev_upper = current_upper
            self._prev_lower = current_lower
            return signal

        # No signal — update state
        self._prev_upper = current_upper
        self._prev_lower = current_lower
        return None

    def _is_in_cooldown(self, bar_ts: datetime, direction: str) -> bool:
        if self._last_signal_bar is None:
            return False
        # Same direction within cooldown window
        if direction == self._last_signal_dir:
            delta = (bar_ts - self._last_signal_bar).total_seconds() / 60
            if delta < (self.cooldown_bars * CONFIG.bar_minutes):
                return True
        return False

    def _build_signal(self, bar, kc_values, direction, entry_price, stop_loss) -> Signal:
        """Construct a Signal dataclass instance with full traceability."""
        signal_id = f"sig_{bar.timestamp.strftime('%Y%m%d_%H%M')}_{direction.lower()}"

        return Signal(
            signal_id=signal_id,
            timestamp_utc=bar.timestamp,
            direction=direction,
            bar_open=bar.open,
            bar_high=bar.high,
            bar_low=bar.low,
            bar_close=bar.close,
            kc_mid=kc_values.mid,
            kc_upper=kc_values.upper,
            kc_lower=kc_values.lower,
            kc_atr=kc_values.atr,
            entry_price=entry_price,
            stop_loss=stop_loss,
            experiment_name=CONFIG.experiment_name,
            config_id=CONFIG.config_id,
        )


# Quick manual test
if __name__ == "__main__":
    from keltner import KeltnerChannel, Bar as KCBar   # reuse existing Bar

    # Tiny test set
    bars = [
        KCBar(datetime(2026, 7, 9, 21, 0), 52340.0, 52355.0, 52335.0, 52350.0),
        KCBar(datetime(2026, 7, 9, 21, 3), 52352.0, 52370.0, 52348.0, 52365.0),  # close above upper?
        KCBar(datetime(2026, 7, 9, 21, 6), 52368.0, 52375.0, 52320.0, 52325.0),  # potential short
        KCBar(datetime(2026, 7, 9, 21, 9), 52322.0, 52330.0, 52295.0, 52305.0),  # potential long
    ]

    kc = KeltnerChannel(period=CONFIG.kc_period, multiplier=CONFIG.kc_multiplier)
    detector = SignalDetector()

    print("=== Signal Detector Test ===")
    for b in bars:
        kc_val = kc.update(b)
        sig = detector.check(b, kc_val)
        if sig:
            print(f"{sig.timestamp_utc} | {sig.direction} | entry={sig.entry_price} stop={sig.stop_loss} | "
                  f"exp={sig.experiment_name}")
        else:
            print(f"{b.timestamp} | no signal | upper={kc_val.upper:.2f} lower={kc_val.lower:.2f}")