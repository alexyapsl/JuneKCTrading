"""
Unit tests for the 2026-10-25 entry filters (direction_filter / atr_min).

Blocked signals must still be emitted, flagged direction_blocked / atr_blocked,
so the JSONL funnel keeps full accounting; the execution gate in
run_kc_live.py skips order placement for flagged signals.

Run:
    python -m unittest tests.test_signal_filters -v
"""

import unittest
from datetime import datetime, timezone
from dataclasses import dataclass

from config import CONFIG
from src.signal_detector import SignalDetector


@dataclass
class FakeBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class FakeKC:
    mid: float
    upper: float
    lower: float
    atr: float


class TestEntryFilters(unittest.TestCase):
    def setUp(self):
        self._saved = (CONFIG.direction_filter, CONFIG.atr_min)
        CONFIG.direction_filter = ""
        CONFIG.atr_min = 0.0
        self.detector = SignalDetector()
        self.now = datetime.now(timezone.utc)
        # prime prev-band state (no signal on first bar)
        self.detector.check(
            FakeBar(self.now, 105.0, 106.0, 104.0, 105.0),
            FakeKC(mid=105, upper=110, lower=100, atr=2.0),
        )

    def tearDown(self):
        CONFIG.direction_filter, CONFIG.atr_min = self._saved

    def _short_signal(self, atr=2.0):
        """Bar that triggers SHORT vs prev upper=110."""
        bar = FakeBar(self.now, 112.0, 113.0, 107.0, 108.0)
        kc = FakeKC(mid=105, upper=110, lower=100, atr=atr)
        return self.detector.check(bar, kc)

    def _long_signal(self, atr=2.0):
        """Bar that triggers LONG vs prev lower=100."""
        bar = FakeBar(self.now, 98.0, 103.0, 97.0, 102.0)
        kc = FakeKC(mid=105, upper=110, lower=100, atr=atr)
        return self.detector.check(bar, kc)

    def test_no_filters_no_flags(self):
        sig = self._short_signal()
        self.assertIsNotNone(sig)
        self.assertFalse(sig.direction_blocked)
        self.assertFalse(sig.atr_blocked)

    def test_direction_filter_blocks_long(self):
        CONFIG.direction_filter = "SHORT"
        sig = self._long_signal()
        self.assertIsNotNone(sig, "blocked signals must still be emitted")
        self.assertEqual(sig.direction, "LONG")
        self.assertTrue(sig.direction_blocked)
        self.assertFalse(sig.atr_blocked)

    def test_direction_filter_allows_matching_side(self):
        CONFIG.direction_filter = "SHORT"
        sig = self._short_signal()
        self.assertIsNotNone(sig)
        self.assertFalse(sig.direction_blocked)

    def test_atr_filter_blocks_low_atr(self):
        CONFIG.atr_min = 35.0
        sig = self._short_signal(atr=20.0)
        self.assertIsNotNone(sig)
        self.assertTrue(sig.atr_blocked)
        self.assertFalse(sig.direction_blocked)

    def test_atr_filter_allows_high_atr(self):
        CONFIG.atr_min = 35.0
        sig = self._short_signal(atr=47.5)
        self.assertIsNotNone(sig)
        self.assertFalse(sig.atr_blocked)

    def test_atr_filter_boundary_inclusive(self):
        CONFIG.atr_min = 35.0
        sig = self._short_signal(atr=35.0)
        self.assertIsNotNone(sig)
        self.assertFalse(sig.atr_blocked, "ATR exactly at threshold must pass")

    def test_both_blocked_when_both_fail(self):
        CONFIG.direction_filter = "SHORT"
        CONFIG.atr_min = 35.0
        sig = self._long_signal(atr=10.0)
        self.assertIsNotNone(sig)
        self.assertTrue(sig.direction_blocked)
        self.assertTrue(sig.atr_blocked)


if __name__ == "__main__":
    unittest.main()
