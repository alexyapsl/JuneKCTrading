"""Regression tests for the v3 anti-duplicate order-placement fix (2026-09-26).

Root cause being guarded against: trading_ig's create_working_order POSTs
successfully (HTTP 200) but its confirms polling 404s while IG demo indexes
the order, raising error.service.execution.find. A single immediate
GET /workingorders check is racy — the order may not be visible yet — so the
old code re-POSTed and created a duplicate working order (both later filled:
sig_20260922_1500_long and sig_20260922_1651_short).

The fix polls broker state with backoff before believing a failure, and again
immediately before any re-POST.
"""

from datetime import datetime, timezone

from src.order_manager import OrderManager
from src.signal_detector import Signal


def _make_signal(direction="LONG"):
    return Signal(
        signal_id="sig_test_dup",
        timestamp_utc=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
        direction=direction,
        bar_open=99.0,
        bar_high=99.5,
        bar_low=98.5,
        bar_close=99.0,
        kc_mid=105.0,
        kc_atr=10.0,
        kc_upper=130.0,
        kc_lower=80.0,
        entry_price=100.0,
        stop_loss=90.0,  # RR = (130-100)/(100-90) = 3.0 >= min_risk_reward
        experiment_name="test",
        config_id="testcfg",
    )


class _FakeKC:
    upper = 130.0
    lower = 80.0
    mid = 105.0


class _FakeIGClient:
    """create_working_order always 'fails' with the confirm-timeout error."""

    def __init__(self, order_visible_after_checks=None):
        self.create_calls = 0
        self.wo_polls = 0
        # after how many get_working_orders polls the order becomes visible
        # (None = never visible -> genuine failure)
        self.order_visible_after_checks = order_visible_after_checks

    def fetch_current_price(self, epic):
        return {"offer": 99.5, "bid": 99.0}

    def create_working_order(self, **kwargs):
        self.create_calls += 1
        raise RuntimeError("error.service.execution.find")

    def get_working_orders(self):
        self.wo_polls += 1
        visible = (
            self.order_visible_after_checks is not None
            and self.wo_polls >= self.order_visible_after_checks
        )
        if not visible:
            return {"workingOrders": []}
        return {
            "workingOrders": [
                {
                    "workingOrderData": {
                        "dealId": "DIAAAAFAKE",
                        "direction": "BUY",
                        "orderLevel": 100.0,
                    },
                    "marketData": {"epic": "IX.D.DOW.IFS.IP"},
                }
            ]
        }

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


def test_confirm_timeout_does_not_double_post(tmp_path, monkeypatch):
    """Order exists but confirms 404: must verify at broker and NOT re-POST."""
    fake = _FakeIGClient(order_visible_after_checks=3)  # visible on 3rd poll
    manager = _make_manager(tmp_path, monkeypatch, fake)

    result = manager.place(_make_signal(), _FakeKC())

    assert result["status"] == "ACCEPTED"
    assert "verified_via_broker_state" in (result["reason"] or "")
    assert fake.create_calls == 1, "second POST would have created the duplicate"
    assert "sig_test_dup" in manager.processed_signal_ids
    assert len(manager._pending) == 1


def test_genuine_failure_still_retries_once(tmp_path, monkeypatch):
    """Order truly never created: retry path preserved, ends REJECTED."""
    fake = _FakeIGClient(order_visible_after_checks=None)
    manager = _make_manager(tmp_path, monkeypatch, fake)

    result = manager.place(_make_signal(), _FakeKC())

    assert result["status"] == "REJECTED"
    assert fake.create_calls == 2  # attempt 1 + one classified retry
    assert "sig_test_dup" not in manager.processed_signal_ids
    assert len(manager._pending) == 0


def test_validation_error_never_retries(tmp_path, monkeypatch):
    """IG validation errors fail identically every retry — single attempt."""

    class _ValidationFake(_FakeIGClient):
        def create_working_order(self, **kwargs):
            self.create_calls += 1
            raise RuntimeError("error.invalid_order")

    fake = _ValidationFake(order_visible_after_checks=None)
    manager = _make_manager(tmp_path, monkeypatch, fake)

    result = manager.place(_make_signal(), _FakeKC())

    assert result["status"] == "REJECTED"
    assert fake.create_calls == 1
