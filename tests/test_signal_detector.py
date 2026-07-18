"""
Unit tests for SignalDetector (Keltner Channel breakout-reversion rules).

Run:
    python -m unittest tests.test_signal_detector -v
"""

import unittest
from datetime import datetime, timezone
from dataclasses import dataclass

# --- Minimal stand-ins for the real classes (avoid import side-effects) ---
from src.signal_detector import SignalDetector, Signal


@dataclass
class FakeBar:
    """Mock bar object used by SignalDetector.check()."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class FakeKC:
    """Mock KC values container."""
    mid: float
    upper: float
    lower: float
    atr: float


class TestSignalDetector(unittest.TestCase):
    def setUp(self):
        self.detector = SignalDetector()
        self.now = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ #
    #                              HELPERS                               #
    # ------------------------------------------------------------------ #

    def _make_long_signal_bar(self):
        """
        Creates a bar that should produce a LONG signal:
        - Previous KC lower = 100
        - Current bar opens below 100 (98), closes above 100 (102)
        """
        prev_kc = FakeKC(mid=105, upper=110, lower=100, atr=2.0)
        bar = FakeBar(
            timestamp=self.now,
            open=98.0,
            high=103.0,
            low=97.0,
            close=102.0
        )
        curr_kc = FakeKC(mid=105, upper=110, lower=100, atr=2.0)
        return prev_kc, bar, curr_kc

    def _make_short_signal_bar(self):
        """
        Creates a bar that should produce a SHORT signal:
        - Previous KC upper = 110
        - Current bar opens above 110 (112), closes below 110 (108)
        """
        prev_kc = FakeKC(mid=105, upper=110, lower=100, atr=2.0)
        bar = FakeBar(
            timestamp=self.now,
            open=112.0,
            high=113.0,
            low=107.0,
            close=108.0
        )
        curr_kc = FakeKC(mid=105, upper=110, lower=100, atr=2.0)
        return prev_kc, bar, curr_kc

    # ------------------------------------------------------------------ #
    #                               TESTS                                #
    # ------------------------------------------------------------------ #

    def test_long_signal_generated(self):
        prev_kc, bar, curr_kc = self._make_long_signal_bar()
        # Seed previous state by calling check once with the "prev" KC
        _ = self.detector.check(bar=FakeBar(timestamp=self.now, open=99, high=101, low=98, close=100), kc_values=prev_kc)
        # Real bar (opens below, closes above lower)
        signal = self.detector.check(bar=bar, kc_values=curr_kc)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "LONG")
        self.assertGreater(signal.bar_close, prev_kc.lower)
        self.assertLess(signal.bar_open, prev_kc.lower)

    def test_short_signal_generated(self):
        prev_kc, bar, curr_kc = self._make_short_signal_bar()
        # Seed previous state
        _ = self.detector.check(bar=FakeBar(timestamp=self.now, open=109, high=111, low=108, close=109), kc_values=prev_kc)
        # Real bar (opens above, closes below upper)
        signal = self.detector.check(bar=bar, kc_values=curr_kc)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "SHORT")
        self.assertLess(signal.bar_close, prev_kc.upper)
        self.assertGreater(signal.bar_open, prev_kc.upper)

    def test_no_signal_when_condition_not_met(self):
        # Bar that stays entirely above lower KC → no long signal
        prev_kc = FakeKC(mid=105, upper=110, lower=100, atr=2.0)
        bar = FakeBar(timestamp=self.now, open=101.0, high=103.0, low=100.5, close=102.0)
        curr_kc = FakeKC(mid=105, upper=110, lower=100, atr=2.0)

        _ = self.detector.check(bar=FakeBar(timestamp=self.now, open=99, high=101, low=98, close=100), kc_values=prev_kc)
        signal = self.detector.check(bar=bar, kc_values=curr_kc)
        self.assertIsNone(signal)

    def test_reset_clears_state(self):
        prev_kc = FakeKC(mid=105, upper=110, lower=100, atr=2.0)
        bar = FakeBar(timestamp=self.now, open=98.0, high=103.0, low=97.0, close=102.0)
        curr_kc = FakeKC(mid=105, upper=110, lower=100, atr=2.0)

        # First check seeds state
        _ = self.detector.check(bar=FakeBar(timestamp=self.now, open=99, high=101, low=98, close=100), kc_values=prev_kc)
        self.detector.reset()
        # After reset the detector should treat the next bar as the first one → no signal
        signal = self.detector.check(bar=bar, kc_values=curr_kc)
        self.assertIsNone(signal)

    def test_signal_contains_expected_fields(self):
        prev_kc, bar, curr_kc = self._make_long_signal_bar()
        _ = self.detector.check(bar=FakeBar(timestamp=self.now, open=99, high=101, low=98, close=100), kc_values=prev_kc)
        signal = self.detector.check(bar=bar, kc_values=curr_kc)

        self.assertIsInstance(signal, Signal)
        self.assertTrue(hasattr(signal, "signal_id"))
        self.assertTrue(hasattr(signal, "entry_price"))
        self.assertTrue(hasattr(signal, "stop_loss"))
        self.assertTrue(hasattr(signal, "experiment_name"))
        self.assertTrue(hasattr(signal, "config_id"))


if __name__ == "__main__":
    unittest.main(verbosity=2)