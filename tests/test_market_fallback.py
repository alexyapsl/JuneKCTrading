"""Tests for the v6 market-order fallback (2026-10-29).

Scenario being guarded: a STOP working order is broker-REJECTED (normal
response, not an exception) with ATTACHED_ORDER_LEVEL_ERROR because price
already traded through the entry — the momentum condition has effectively
triggered. The single retry converts to a MARKET order when:
  - fresh-quote slippage vs intended entry <= CONFIG.market_fallback_max_slippage
  - RR re-computed at the estimated fill still clears CONFIG.min_risk_reward
Otherwise it re-POSTs the original STOP as before.

Root example: sig_20261029_0945_short — primary missed a +45.5 trade because
a stale-token delay let price drop 3.1 pts through the SELL STOP entry before
submission; control (fresh session) entered at the same second and profited.
"""

from datetime import datetime, timezone

from config import CONFIG
from src.order_manager import OrderManager
from src.signal_detector import Signal


def _make_signal(direction="SHORT"):
    if direction == "SHORT":
        # entry 100, stop 110, target(kc_lower) 70 -> RR = 30/10 = 3.0
        return Signal(
            signal_id="sig_test_fb",
            timestamp_utc=datetime(2026, 10, 29, 9, 45, tzinfo=timezone.utc),
            direction="SHORT",
            bar_open=101.0,
            bar_high=101.5,
            bar_low=99.5,
            bar_close=100.0,
            kc_mid=105.0,
            kc_atr=10.0,
            kc_upper=130.0,
            kc_lower=70.0,
            entry_price=100.0,
            stop_loss=110.0,
            experiment_name="test",
            config_id="testcfg",
        )
    # LONG: entry 100, stop 90, target(kc_upper) 130 -> RR = 3.0
    return Signal(
        signal_id="sig_test_fb_long",
        timestamp_utc=datetime(2026, 10, 29, 9, 45, tzinfo=timezone.utc),
        direction="LONG",
        bar_open=99.0,
        bar_high=100.5,
        bar_low=98.5,
        bar_close=100.0,
        kc_mid=95.0,
        kc_atr=10.0,
        kc_upper=130.0,
        kc_lower=70.0,
        entry_price=100.0,
        stop_loss=90.0,
        experiment_name="test",
        config_id="testcfg",
    )


class _FakeKC:
    upper = 130.0
    lower = 70.0
    mid = 105.0


class _FakeFallbackClient:
    """Attempt 1: REJECTED response (order level). Attempt 2: market accepted."""

    def __init__(self, snapshot1, snapshot2):
        self.snapshots = [snapshot1, snapshot2]
        self.snapshot_calls = 0
        self.wo_calls = 0
        self.market_calls = []

    def fetch_current_price(self, epic):
        snap = self.snapshots[min(self.snapshot_calls, len(self.snapshots) - 1)]
        self.snapshot_calls += 1
        return snap

    def create_working_order(self, **kwargs):
        self.wo_calls += 1
        return {
            "dealStatus": "REJECTED",
            "reason": "ATTACHED_ORDER_LEVEL_ERROR",
            "dealReference": f"WO{self.wo_calls}",
        }

    def create_market_order(self, **kwargs):
        self.market_calls.append(kwargs)
        return {
            "dealStatus": "ACCEPTED",
            "status": "OPEN",
            "reason": "SUCCESS",
            "dealReference": "MKT1",
            "dealId": "DIAFAKEMKT",
            "level": 97.0,
        }

    def fetch_transaction_reference(self, deal_id):
        return "TXFAKE"

    def get_working_orders(self):
        return {"workingOrders": []}

    def get_open_positions(self):
        return {"positions": []}


def _make_manager(tmp_path, monkeypatch, fake):
    monkeypatch.setattr("src.order_manager.time.sleep", lambda s: None)
    manager = object.__new__(OrderManager)
    manager.ig_client = fake
    manager.experiment_dir = tmp_path
    manager.processed_file = tmp_path / "processed_signals.json"
    manager.processed_signal_ids = set()
    manager._pending = []
    manager._positions = []
    return manager


def test_wrong_side_rejection_converts_to_market_short(tmp_path, monkeypatch):
    # snapshot1: bid 101 -> entry_dist +1 (right side). Attempt 1 REJECTED.
    # snapshot2: bid 97 -> dist -3 (wrong side, slippage 3 <= 10).
    # est fill 97: risk |97-110|=13, reward |70-97|=27 -> rr 2.08 >= 1.75.
    fake = _FakeFallbackClient(
        {"bid": 101.0, "offer": 101.5},
        {"bid": 97.0, "offer": 97.5},
    )
    manager = _make_manager(tmp_path, monkeypatch, fake)

    result = manager.place(_make_signal("SHORT"), _FakeKC())

    assert result["status"] == "ACCEPTED"
    assert result.get("entry_via") == "market_fallback"
    assert fake.wo_calls == 1, "original STOP should not be re-POSTed"
    assert len(fake.market_calls) == 1
    assert fake.market_calls[0]["direction"] == "SELL"
    assert fake.market_calls[0]["stop_level"] == 110.0
    assert fake.market_calls[0]["limit_level"] == 70.0
    fb_attempt = result["attempts"][-1]
    assert fb_attempt["order_type"] == "MARKET_FALLBACK"
    assert fb_attempt["fallback"]["slippage_points"] == 3.0
    assert fb_attempt["fallback"]["rr_at_estimated_fill"] >= CONFIG.min_risk_reward
    # Market fill tracked as an OPEN position (by deal_id), not pending order
    assert len(manager._positions) == 1
    assert manager._positions[0]["deal_id"] == "DIAFAKEMKT"
    assert manager._positions[0]["fill_level"] == 97.0
    assert len(manager._pending) == 0
    assert "sig_test_fb" in manager.processed_signal_ids


def test_wrong_side_rejection_converts_to_market_long(tmp_path, monkeypatch):
    # LONG: entry 100. snapshot2 offer 103 -> dist -3, est fill 103:
    # risk |103-90|=13, reward |130-103|=27 -> rr 2.08 -> fallback BUY.
    fake = _FakeFallbackClient(
        {"bid": 98.5, "offer": 99.0},
        {"bid": 102.5, "offer": 103.0},
    )
    manager = _make_manager(tmp_path, monkeypatch, fake)

    result = manager.place(_make_signal("LONG"), _FakeKC())

    assert result["status"] == "ACCEPTED"
    assert result.get("entry_via") == "market_fallback"
    assert len(fake.market_calls) == 1
    assert fake.market_calls[0]["direction"] == "BUY"
    assert fake.market_calls[0]["stop_level"] == 90.0
    assert fake.market_calls[0]["limit_level"] == 130.0


def test_slippage_beyond_cap_reposts_stop(tmp_path, monkeypatch):
    # snapshot2 bid 89 -> slippage 11 > 10 cap -> no market conversion.
    fake = _FakeFallbackClient(
        {"bid": 101.0, "offer": 101.5},
        {"bid": 89.0, "offer": 89.5},
    )
    manager = _make_manager(tmp_path, monkeypatch, fake)

    result = manager.place(_make_signal("SHORT"), _FakeKC())

    assert result["status"] == "REJECTED"
    assert len(fake.market_calls) == 0
    assert fake.wo_calls == 2  # original behaviour: re-POST the STOP
    assert len(manager._positions) == 0


def test_rr_at_estimated_fill_below_min_reposts_stop(tmp_path, monkeypatch):
    # snapshot2 bid 92 -> slippage 8 <= 10, but risk 18 / reward 22 -> rr 1.22 < 1.75.
    fake = _FakeFallbackClient(
        {"bid": 101.0, "offer": 101.5},
        {"bid": 92.0, "offer": 92.5},
    )
    manager = _make_manager(tmp_path, monkeypatch, fake)

    result = manager.place(_make_signal("SHORT"), _FakeKC())

    assert result["status"] == "REJECTED"
    assert len(fake.market_calls) == 0
    assert fake.wo_calls == 2


def test_right_side_rejection_keeps_normal_retry(tmp_path, monkeypatch):
    # snapshot2 still right side (bid 101 -> dist +1): no fallback trigger.
    fake = _FakeFallbackClient(
        {"bid": 101.0, "offer": 101.5},
        {"bid": 101.0, "offer": 101.5},
    )
    manager = _make_manager(tmp_path, monkeypatch, fake)

    result = manager.place(_make_signal("SHORT"), _FakeKC())

    assert result["status"] == "REJECTED"
    assert len(fake.market_calls) == 0
    assert fake.wo_calls == 2


def test_fallback_disabled_by_config(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG, "market_fallback", False)
    fake = _FakeFallbackClient(
        {"bid": 101.0, "offer": 101.5},
        {"bid": 97.0, "offer": 97.5},
    )
    manager = _make_manager(tmp_path, monkeypatch, fake)

    result = manager.place(_make_signal("SHORT"), _FakeKC())

    assert result["status"] == "REJECTED"
    assert len(fake.market_calls) == 0
    assert fake.wo_calls == 2
