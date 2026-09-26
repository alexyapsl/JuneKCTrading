"""
Order Manager – Phase 1

High-level service that:
- Receives a Signal from the detector
- Validates Risk-Reward ratio (at signal time only)
- Prevents duplicate orders (by signal_id)
- Resolves the correct IG account/credentials
- Places a working order via IGRestClient
- Persists processed signal_ids (basic deduplication for Phase 1)

This module does NOT handle:
- Dynamic stop/target amendments (Phase 2)
- Timeouts (Phase 2)
- Position management (Phase 2)

It is intentionally kept simple for the MVP.
"""

import json
import logging
import time
import os
from pathlib import Path
from typing import Optional, Set, Dict, Any, List
from dataclasses import asdict

from config import CONFIG
from src.signal_detector import Signal
from src.account_resolver import resolve_credentials
from src.ig_rest_client import IGRestClient

logger = logging.getLogger(__name__)

# IG validation errors fail identically on every retry — never re-attempt these
_VALIDATION_HINTS = (
    "attached_order_level_error",
    "invalid_order",
    "order_level",
    "validation",
    "error.invalid",
)


def _is_validation_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(h in msg for h in _VALIDATION_HINTS)


class OrderManager:
    """
    Manages order placement for a single experiment/account.

    Usage
    -----
    om = OrderManager(experiment_dir=CONFIG.experiment_dir)
    om.place(signal, current_kc_values)
    """

    def __init__(self, experiment_dir: Path):
        self.experiment_dir = Path(experiment_dir)
        self.experiment_dir.mkdir(parents=True, exist_ok=True)

        self.processed_file = self.experiment_dir / "processed_signals.json"
        self.processed_signal_ids: Set[str] = self._load_processed_ids()

        # Resolve credentials once at startup
        self.credentials = resolve_credentials(
            account_name=CONFIG.account_name,
            paper_trading=CONFIG.paper_trading
        )

        self.ig_client = IGRestClient(self.credentials)
        self.ig_client.login()

        # v2: track accepted-but-unfilled working orders for pending_bar_timeout cancels
        self._pending: List[Dict[str, Any]] = []

        # v2: track filled positions for filled_bar_timeout market-close (matches sim)
        self._positions: List[Dict[str, Any]] = []

        # v2: cancel leftover working orders for our epic from previous (crashed) runs
        try:
            self._sweep_stale_working_orders_on_startup()
        except Exception as e:
            logger.warning(f"[OrderManager] Startup working-order sweep failed: {e}")

        # v2: adopt untracked open positions on our epic for the timeout sweep.
        # We can't know their exact fill bar, so we start the timeout clock now —
        # at worst they get one extra full timeout window before being closed.
        try:
            self._adopt_existing_open_positions()
        except Exception as e:
            logger.warning(f"[OrderManager] Startup open-position adoption failed: {e}")

        logger.info(
            f"[OrderManager] Initialized for account={CONFIG.account_name}, "
            f"paper_trading={CONFIG.paper_trading}, size=£{CONFIG.size}/pt"
        )

    # ------------------------------------------------------------------ #
    #                         PERSISTENCE (Phase 1)                      #
    # ------------------------------------------------------------------ #

    def _load_processed_ids(self) -> Set[str]:
        if self.processed_file.exists():
            try:
                data = json.loads(self.processed_file.read_text())
                return set(data.get("processed_signal_ids", []))
            except Exception as e:
                logger.warning(f"Could not load processed_signals.json: {e}")
        return set()

    def _save_processed_ids(self):
        try:
            payload = {"processed_signal_ids": sorted(list(self.processed_signal_ids))}
            tmp = self.processed_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self.processed_file)  # atomic on Windows + POSIX
        except Exception as e:
            logger.error(f"Failed to save processed_signals.json: {e}")

    def _is_duplicate(self, signal_id: str) -> bool:
        return signal_id in self.processed_signal_ids

    def _mark_processed(self, signal_id: str):
        self.processed_signal_ids.add(signal_id)
        self._save_processed_ids()

    # ------------------------------------------------------------------ #
    #                 PENDING ORDER TRACKING (v2 safety)                 #
    # ------------------------------------------------------------------ #

    def _register_pending(self, signal: Signal) -> None:
        """Track an accepted working order so it can be cancelled if it stays unfilled."""
        deal_id = None
        try:
            existing = self._find_existing_working_order(signal)
            if existing:
                deal_id = existing.get("dealId")
        except Exception as e:
            logger.warning(f"[OrderManager] Could not resolve dealId for pending tracking: {e}")
        self._pending.append({
            "signal": signal,
            "signal_id": signal.signal_id,
            "deal_id": deal_id,
            "placed_bar_ts": signal.timestamp_utc,
        })
        logger.info(f"[OrderManager] Tracking pending order {signal.signal_id} (deal_id={deal_id})")

    def _find_existing_working_order(self, signal: Signal) -> Optional[Dict[str, Any]]:
        """Match a live working order at IG by epic + direction + entry level (±1 pt)."""
        ig_direction = "SELL" if signal.direction == "SHORT" else "BUY"
        orders = self.ig_client.get_working_orders().get("workingOrders", []) or []
        for o in orders:
            if not isinstance(o, dict):
                continue
            wod = o.get("workingOrderData", {})
            md = o.get("marketData", {})
            if md.get("epic") and md.get("epic") != "IX.D.DOW.IFS.IP":
                continue
            if wod.get("direction") != ig_direction:
                continue
            level = wod.get("orderLevel", wod.get("level"))
            if level is None:
                continue
            if abs(float(level) - float(signal.entry_price)) <= 1.0:
                return {"dealId": wod.get("dealId"), "level": float(level)}
        return None

    def _verify_order_at_broker(
        self, signal: Signal, max_checks: int = 4, delay_s: float = 2.5
    ) -> Optional[Dict[str, Any]]:
        """
        Poll broker state to prove an order (or its fill) exists before
        believing a create_working_order failure.

        IG demo is eventually consistent: right after a 200 POST the order can
        be invisible to GET /workingorders for several seconds while the
        confirms endpoint 404s (error.service.execution.find). A single
        immediate lookup is racy — that race caused double placements on
        2026-09-22/23 (sig_20260922_1500_long, sig_20260922_1651_short).

        Returns {"kind": "working_order"|"position", "dealId": ..., ...} or None.
        """
        for i in range(max_checks):
            try:
                existing = self._find_existing_working_order(signal)
            except Exception as e:
                logger.warning(f"[OrderManager] verify: working-orders check {i + 1} failed: {e}")
                existing = None
            if existing:
                return {"kind": "working_order", **existing}
            try:
                pos = self._find_open_position(signal)
            except Exception as e:
                logger.warning(f"[OrderManager] verify: open-positions check {i + 1} failed: {e}")
                pos = None
            if pos:
                return {"kind": "position", "dealId": pos.get("dealId"), "level": pos.get("level")}
            if i < max_checks - 1:
                time.sleep(delay_s)
        return None

    def _accept_via_broker_state(
        self,
        signal: Signal,
        result: Dict[str, Any],
        attempt_record: Dict[str, Any],
        verified: Dict[str, Any],
        reason: str,
    ) -> None:
        """Shared bookkeeping for all 'order secretly exists at broker' accept paths."""
        attempt_record["status"] = "ACCEPTED"
        attempt_record["deal_id"] = verified.get("dealId")
        attempt_record["reason"] = f"{reason} ({verified.get('kind')})"
        result["status"] = "ACCEPTED"
        result["deal_id"] = verified.get("dealId")
        result["reason"] = attempt_record["reason"]
        self._mark_processed(signal.signal_id)
        self._register_pending(signal)

    def _sweep_stale_working_orders_on_startup(self) -> None:
        """Cancel leftover working orders on our epic from previous (crashed) runs."""
        orders = self.ig_client.get_working_orders().get("workingOrders", []) or []
        for o in orders:
            if not isinstance(o, dict):
                continue
            wod = o.get("workingOrderData", {})
            md = o.get("marketData", {})
            if md.get("epic") != "IX.D.DOW.IFS.IP":
                continue
            deal_id = wod.get("dealId")
            if not deal_id:
                continue
            try:
                self.ig_client.cancel_working_order(deal_id)
                logger.info(f"[OrderManager] Startup sweep: cancelled leftover working order deal_id={deal_id}")
            except Exception as e:
                logger.error(f"[OrderManager] Startup sweep cancel failed for {deal_id}: {e}")

    def _adopt_existing_open_positions(self) -> None:
        """Track any already-open positions on our epic for the timeout sweep."""
        from datetime import datetime, timezone
        positions = self.ig_client.get_open_positions().get("positions", []) or []
        for p in positions:
            if not isinstance(p, dict):
                continue
            if p.get("epic") != "IX.D.DOW.IFS.IP":
                continue
            deal_id = p.get("dealId")
            if not deal_id:
                continue
            self._positions.append({
                "signal": None,
                "signal_id": f"adopted_{deal_id}",
                "deal_id": deal_id,
                "direction": p.get("direction"),
                "size": float(p.get("size", CONFIG.size)),
                "fill_bar_ts": datetime.now(timezone.utc),
            })
            logger.info(
                f"[OrderManager] Adopted existing open position deal_id={deal_id} "
                f"({p.get('direction')} {p.get('size')}) for timeout tracking"
            )

    def _find_open_position(self, signal: Signal) -> Optional[Dict[str, Any]]:
        """Match an open IG position by epic + direction + entry level (±1 pt)."""
        ig_direction = "SELL" if signal.direction == "SHORT" else "BUY"
        try:
            positions = self.ig_client.get_open_positions().get("positions", []) or []
        except Exception as e:
            logger.warning(f"[OrderManager] get_open_positions failed: {e}")
            return None
        for p in positions:
            if not isinstance(p, dict):
                continue
            # fetch_open_positions returns a flat DataFrame -> records with keys:
            # epic, direction, size, level, dealId, ... (verified 2026-08-22)
            if p.get("epic") and p.get("epic") != "IX.D.DOW.IFS.IP":
                continue
            if p.get("direction") != ig_direction:
                continue
            level = p.get("level")
            if level is None:
                continue
            if abs(float(level) - float(signal.entry_price)) <= 1.0:
                return {
                    "dealId": p.get("dealId"),
                    "direction": ig_direction,
                    "size": float(p.get("size", CONFIG.size)),
                    "level": float(level),
                }
        return None

    def _refresh_filled_positions(self, current_bar_ts) -> None:
        """
        For each accepted signal, check whether its working order has become an
        open position; if so, register it for the filled_bar_timeout sweep.
        """
        # Walk accepted signals we still track as pending and see if they filled
        for po in list(self._pending):
            signal = po["signal"]
            if self._find_existing_working_order(signal) is not None:
                continue  # still a working order, not filled yet
            pos = self._find_open_position(signal)
            if pos is None:
                continue  # neither working nor open — cancelled/rejected elsewhere
            self._positions.append({
                "signal": signal,
                "signal_id": signal.signal_id,
                "deal_id": pos["dealId"],
                "direction": pos["direction"],
                "size": pos["size"],
                "fill_bar_ts": current_bar_ts,
            })
            self._pending.remove(po)
            logger.info(
                f"[OrderManager] Position opened for {signal.signal_id} "
                f"(deal_id={pos['dealId']}) — timeout tracking started"
            )

    def close_timed_out_positions(self, current_bar_ts) -> None:
        """
        Close filled positions that have been open >= CONFIG.filled_bar_timeout bars
        (mirrors the sim shadow's TIMEOUT exit at market).
        """
        if not self._positions:
            return
        for pos in list(self._positions):
            age_bars = (current_bar_ts - pos["fill_bar_ts"]).total_seconds() / 60.0 / CONFIG.bar_minutes
            if age_bars < CONFIG.filled_bar_timeout:
                continue
            signal = pos["signal"]
            # Confirm the position is still open before closing
            if signal is not None:
                current = self._find_open_position(signal)
            else:
                # Adopted position: verify by deal_id against the open list
                current = None
                for p in self.ig_client.get_open_positions().get("positions", []) or []:
                    if not isinstance(p, dict):
                        continue
                    if p.get("dealId") == pos["deal_id"]:
                        current = {
                            "dealId": p.get("dealId"),
                            "direction": p.get("direction"),
                            "size": float(p.get("size", pos["size"])),
                        }
                        break
            if current is None:
                logger.info(
                    f"[OrderManager] {pos['signal_id']} no longer open (SL/TP hit) — stop tracking"
                )
                self._positions.remove(pos)
                continue
            try:
                self.ig_client.close_position(
                    deal_id=current["dealId"],
                    direction=current["direction"],
                    size=current["size"],
                )
                logger.info(
                    f"[OrderManager] TIMEOUT-CLOSED {pos['signal_id']} "
                    f"(deal_id={current['dealId']}, age={age_bars:.1f} bars >= {CONFIG.filled_bar_timeout})"
                )
            except Exception as e:
                logger.error(f"[OrderManager] Timeout close failed for {pos['signal_id']}: {e}")
                continue  # retry next bar
            self._positions.remove(pos)

    def manage_open_positions(self, bar, kc_values) -> None:
        """
        Per-completed-bar dynamic management (2026-08-25, Alex's rules):

        1. Target follows the current KC band every bar
           (upper for LONG/BUY, lower for SHORT/SELL).
        2. Break-even: once a completed bar's range fully clears the entry
           (bar.low > entry for LONG / bar.high < entry for SHORT),
           stop ratchets to the entry level.
        3. KC-mid ratchet: once a completed bar's range fully clears the KC mid
           (bar.low > mid for LONG / bar.high < mid for SHORT),
           stop ratchets to the mid.

        Stops only ever move toward profit. Amendments apply from the NEXT bar
        (mirrors the sim shadow, which amends after exit resolution).
        Failed amends are logged and retried on the next bar.
        """
        if not self._positions:
            return
        try:
            open_positions = self.ig_client.get_open_positions().get("positions", []) or []
        except Exception as e:
            logger.warning(f"[OrderManager] get_open_positions failed in manage_open_positions: {e}")
            return
        by_deal = {p.get("dealId"): p for p in open_positions if isinstance(p, dict) and p.get("dealId")}

        for pos in list(self._positions):
            ig = by_deal.get(pos["deal_id"])
            if ig is None:
                continue  # closed at broker (SL/TP) — timeout sweep will untrack
            is_long = pos.get("direction") == "BUY"
            # Entry: prefer the signal; adopted positions fall back to broker level
            if pos.get("signal") is not None:
                entry = float(pos["signal"].entry_price)
            else:
                try:
                    entry = float(ig.get("level"))
                except (TypeError, ValueError):
                    continue

            def _f(v):
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            cur_stop = _f(ig.get("stopLevel"))
            cur_limit = _f(ig.get("limitLevel"))
            mid = float(kc_values.mid)

            new_limit = round(kc_values.upper if is_long else kc_values.lower, 4)
            new_stop = cur_stop
            if is_long:
                if bar.low > entry:
                    new_stop = entry if new_stop is None else max(new_stop, entry)
                if bar.low > mid:
                    new_stop = mid if new_stop is None else max(new_stop, mid)
            else:
                if bar.high < entry:
                    new_stop = entry if new_stop is None else min(new_stop, entry)
                if bar.high < mid:
                    new_stop = mid if new_stop is None else min(new_stop, mid)

            limit_changed = cur_limit is None or abs(new_limit - cur_limit) >= 0.05
            stop_changed = new_stop is not None and (cur_stop is None or abs(new_stop - cur_stop) >= 0.05)
            if not (limit_changed or stop_changed):
                continue
            try:
                self.ig_client.update_open_position(
                    deal_id=pos["deal_id"],
                    limit_level=new_limit,
                    stop_level=round(new_stop, 4) if new_stop is not None else cur_stop,
                )
                logger.info(
                    f"[OrderManager] AMENDED {pos['signal_id']} (deal_id={pos['deal_id']}): "
                    f"limit {cur_limit} -> {new_limit}, stop {cur_stop} -> "
                    f"{round(new_stop, 4) if new_stop is not None else cur_stop}"
                )
            except Exception as e:
                # e.g. IG minimum-distance rejection when price is close to the new stop
                logger.warning(
                    f"[OrderManager] Amend failed for {pos['signal_id']} "
                    f"(deal_id={pos['deal_id']}): {e} — will retry next bar"
                )

    def cancel_stale_pending(self, current_bar_ts) -> None:
        """Cancel tracked working orders unfilled for >= CONFIG.pending_bar_timeout bars."""
        if not self._pending:
            return
        for po in list(self._pending):
            age_bars = (current_bar_ts - po["placed_bar_ts"]).total_seconds() / 60.0 / CONFIG.bar_minutes
            if age_bars < CONFIG.pending_bar_timeout:
                continue
            signal = po["signal"]
            try:
                existing = self._find_existing_working_order(signal)
            except Exception as e:
                logger.warning(f"[OrderManager] Working-order check failed for {po['signal_id']}: {e}")
                continue  # retry next bar
            if existing is None:
                logger.info(f"[OrderManager] {po['signal_id']} no longer working (filled or gone) — stop tracking")
                self._pending.remove(po)
                continue
            deal_id = existing.get("dealId") or po.get("deal_id")
            try:
                self.ig_client.cancel_working_order(deal_id)
                logger.info(
                    f"[OrderManager] CANCELLED stale working order {po['signal_id']} "
                    f"(deal_id={deal_id}, age={age_bars:.1f} bars >= {CONFIG.pending_bar_timeout})"
                )
            except Exception as e:
                logger.error(f"[OrderManager] Failed to cancel {po['signal_id']} (deal_id={deal_id}): {e}")
            self._pending.remove(po)

    def flatten_for_weekend(self, current_bar_ts=None) -> Dict[str, Any]:
        """
        Cancel every working order and close every open position on our epic.

        Called shortly before the Friday IG weekend close. This is deliberately
        broker-state driven rather than relying only on in-memory tracking, so a
        runner restart still flattens leftovers from an earlier process.
        """
        cancelled = 0
        closed = 0
        errors: List[str] = []

        # 1) Cancel all working orders on our epic.
        try:
            orders = self.ig_client.get_working_orders().get("workingOrders", []) or []
            for order in orders:
                if not isinstance(order, dict):
                    continue
                wod = order.get("workingOrderData", {})
                md = order.get("marketData", {})
                if md.get("epic") != "IX.D.DOW.IFS.IP":
                    continue
                deal_id = wod.get("dealId")
                if not deal_id:
                    errors.append(f"working order missing dealId: {order}")
                    continue
                try:
                    self.ig_client.cancel_working_order(deal_id)
                    cancelled += 1
                    logger.info(f"[OrderManager] WEEKEND-FLATTEN cancelled working order deal_id={deal_id}")
                except Exception as e:
                    errors.append(f"cancel working order {deal_id}: {e}")
        except Exception as e:
            errors.append(f"fetch working orders: {e}")

        # 2) Close all open positions on our epic.
        try:
            positions = self.ig_client.get_open_positions().get("positions", []) or []
            for position in positions:
                if not isinstance(position, dict):
                    continue
                if position.get("epic") != "IX.D.DOW.IFS.IP":
                    continue
                deal_id = position.get("dealId")
                direction = position.get("direction")
                try:
                    size = float(position.get("size", CONFIG.size))
                except Exception:
                    size = None
                if not deal_id or not direction or size is None:
                    errors.append(f"open position missing dealId/direction/size: {position}")
                    continue
                try:
                    self.ig_client.close_position(deal_id=deal_id, direction=direction, size=size)
                    closed += 1
                    logger.info(
                        f"[OrderManager] WEEKEND-FLATTEN closed position deal_id={deal_id} "
                        f"({direction} {size})"
                    )
                except Exception as e:
                    errors.append(f"close position {deal_id}: {e}")
        except Exception as e:
            errors.append(f"fetch open positions: {e}")

        ok = not errors
        if ok:
            # Broker state is flat for our epic; local trackers must not resurrect it.
            self._pending.clear()
            self._positions.clear()

        ts_text = current_bar_ts.isoformat() if hasattr(current_bar_ts, "isoformat") else str(current_bar_ts)
        if ok:
            logger.info(
                f"[OrderManager] WEEKEND-FLATTEN complete at {ts_text}: "
                f"cancelled={cancelled}, closed={closed}"
            )
        else:
            logger.error(
                f"[OrderManager] WEEKEND-FLATTEN incomplete at {ts_text}: "
                f"cancelled={cancelled}, closed={closed}, errors={errors}"
            )
        return {"ok": ok, "cancelled": cancelled, "closed": closed, "errors": errors}

    # ------------------------------------------------------------------ #
    #                           RISK-REWARD CHECK                        #
    # ------------------------------------------------------------------ #

    def _calculate_risk_reward(self, signal: Signal, current_kc: Any) -> float:
        """
        Calculate RR using the KC values at the moment the signal was generated.

        Short: Risk = stop - entry, Reward = entry - target (KC lower)
        Long : Risk = entry - stop, Reward = target (KC upper) - entry
        """
        if signal.direction == "SHORT":
            risk = signal.stop_loss - signal.entry_price
            # target = current KC lower at signal time
            target = current_kc.lower if hasattr(current_kc, "lower") else signal.kc_lower
            reward = signal.entry_price - target
        else:  # LONG
            risk = signal.entry_price - signal.stop_loss
            target = current_kc.upper if hasattr(current_kc, "upper") else signal.kc_upper
            reward = target - signal.entry_price

        if risk <= 0:
            return 0.0
        return round(reward / risk, 4)

    # ------------------------------------------------------------------ #
    #                           MAIN ENTRY POINT                         #
    # ------------------------------------------------------------------ #

    def place(self, signal: Signal, current_kc: Any) -> Dict[str, Any]:
        """
        Attempt to place a working order for the given signal.

        Always returns a structured execution result so the caller can
        enrich the JSONL record (Tier 1 design).

        Returns
        -------
        dict
            {
                "status": "IGNORED_RR" | "DUPLICATE" | "ATTEMPTED" | "REJECTED" | "ACCEPTED",
                "deal_reference": str | None,
                "deal_id": str | None,
                "rr": float | None,
                "reason": str | None,
                "response": dict | None,   # raw IG response when available
                "market_price_snapshot": dict | None,   # bid/ask snapshot at decision time
                "entry_distance_points": float | None # positive = how far market was from our entry
            }
        """
        result = {
            "status": "UNKNOWN",
            "deal_reference": None,
            "deal_id": None,
            "rr": None,
            "reason": None,
            "response": None,
            "attempts": None,
            "attempt_count": 0,
        }

        if self._is_duplicate(signal.signal_id):
            logger.info(f"[OrderManager] Skipping duplicate signal: {signal.signal_id}")
            result["status"] = "DUPLICATE"
            result["reason"] = "duplicate signal_id"
            return result

        rr = self._calculate_risk_reward(signal, current_kc)
        result["rr"] = rr

        if rr < CONFIG.min_risk_reward:
            logger.info(
                f"[OrderManager] Signal {signal.signal_id} rejected: RR={rr} < {CONFIG.min_risk_reward}"
            )
            result["status"] = "IGNORED_RR"
            result["reason"] = f"RR < {CONFIG.min_risk_reward}"
            return result

        # === Market price snapshot at entry decision time (new data collection) ===
        market_snapshot = None
        entry_distance = None
        try:
            market_snapshot = self.ig_client.fetch_current_price("IX.D.DOW.IFS.IP")
            # Derive a simple numeric entry distance (we use offer for LONG, bid for SHORT)
            if isinstance(market_snapshot, dict):
                if signal.direction == "LONG":
                    offer = market_snapshot.get("offer") or market_snapshot.get("OFR")
                    if offer is not None:
                        entry_distance = round(signal.entry_price - float(offer), 2)
                else:  # SHORT
                    bid = market_snapshot.get("bid") or market_snapshot.get("BID")
                    if bid is not None:
                        entry_distance = round(float(bid) - signal.entry_price, 2)
        except Exception as snap_e:
            logger.warning(f"[OrderManager] Could not fetch market price snapshot: {snap_e}")

        # Build direction for IG
        ig_direction = "SELL" if signal.direction == "SHORT" else "BUY"

        attempts = []
        max_retries = 2          # v2: classified retry — at most one re-attempt, transient errors only
        retry_delay_s = 3

        for attempt in range(1, max_retries + 1):
            no_retry = False
            # Re-fetch market snapshot on each attempt (except first, already done)
            if attempt > 1:
                try:
                    market_snapshot = self.ig_client.fetch_current_price("IX.D.DOW.IFS.IP")
                    if isinstance(market_snapshot, dict):
                        if signal.direction == "LONG":
                            offer = market_snapshot.get("offer") or market_snapshot.get("OFR")
                            if offer is not None:
                                entry_distance = round(signal.entry_price - float(offer), 2)
                        else:
                            bid = market_snapshot.get("bid") or market_snapshot.get("BID")
                            if bid is not None:
                                entry_distance = round(float(bid) - signal.entry_price, 2)
                except Exception as snap_e:
                    logger.warning(f"[OrderManager] Snapshot failed on attempt {attempt}: {snap_e}")

                # v3 anti-duplicate: never re-POST without one last broker-state
                # check — attempt 1's order may exist despite the error (the IG
                # confirms endpoint 404s while the order is already live).
                verified = self._verify_order_at_broker(signal, max_checks=2, delay_s=2.0)
                if verified:
                    verified_record = {
                        "attempt": attempt,
                        "market_price_snapshot": market_snapshot,
                        "entry_distance_points": entry_distance,
                        "status": None,
                        "reason": None,
                        "response": None,
                    }
                    self._accept_via_broker_state(
                        signal, result, verified_record, verified,
                        "verified_via_broker_state_before_retry",
                    )
                    attempts.append(verified_record)
                    logger.info(
                        f"[OrderManager] Attempt {attempt}/{max_retries} for {signal.signal_id}: "
                        f"ACCEPTED (verified at broker before re-POST)"
                    )
                    break

            attempt_record = {
                "attempt": attempt,
                "market_price_snapshot": market_snapshot,
                "entry_distance_points": entry_distance,
                "status": None,
                "reason": None,
                "response": None,
            }

            try:
                response = self.ig_client.create_working_order(
                    direction=ig_direction,
                    epic="IX.D.DOW.IFS.IP",
                    size=CONFIG.size,
                    level=signal.entry_price,
                    stop_level=signal.stop_loss,
                    limit_level=signal.kc_lower if signal.direction == "SHORT" else signal.kc_upper,
                    force_open=True,
                    order_type="STOP",  # v2: momentum entries require STOP, not LIMIT
                )

                attempt_record["response"] = response

                if isinstance(response, dict):
                    attempt_record["deal_reference"] = response.get("dealReference")
                    if response.get("dealId"):
                        attempt_record["deal_id"] = response.get("dealId")
                    elif response.get("affectedDeals"):
                        try:
                            attempt_record["deal_id"] = response["affectedDeals"][0].get("dealId")
                        except Exception:
                            pass

                    deal_status = response.get("dealStatus")
                    if deal_status == "ACCEPTED" or response.get("status") == "OPEN":
                        attempt_record["status"] = "ACCEPTED"
                        result["status"] = "ACCEPTED"
                        result["deal_reference"] = attempt_record.get("deal_reference")
                        result["deal_id"] = attempt_record.get("deal_id")
                        result["response"] = response
                        self._mark_processed(signal.signal_id)
                        self._register_pending(signal)

                        # NEW: capture the stable transaction reference for P&L reconciliation
                        if attempt_record.get("deal_id"):
                            try:
                                tx_ref = self.ig_client.fetch_transaction_reference(attempt_record["deal_id"])
                                result["transaction_reference"] = tx_ref
                                attempt_record["transaction_reference"] = tx_ref
                            except Exception as tx_e:
                                logger.warning(f"[OrderManager] Could not fetch transaction_reference: {tx_e}")
                    else:
                        attempt_record["status"] = "REJECTED"
                        attempt_record["reason"] = response.get("reason", "broker_rejected")
                else:
                    attempt_record["status"] = "ATTEMPTED"
                    # v3 anti-duplicate: unclear response — poll broker state with
                    # backoff before any retry (IG indexing lags seconds behind).
                    verified = self._verify_order_at_broker(signal)
                    if verified:
                        self._accept_via_broker_state(
                            signal, result, attempt_record, verified,
                            "verified_via_broker_state",
                        )

                logger.info(
                    f"[OrderManager] Attempt {attempt}/{max_retries} for {signal.signal_id}: "
                    f"{attempt_record['status']} (entry_dist={entry_distance})"
                )

            except Exception as e:
                attempt_record["status"] = "REJECTED"
                attempt_record["reason"] = str(e)
                logger.error(f"[OrderManager] Attempt {attempt} failed for {signal.signal_id}: {e}")

                if _is_validation_error(e):
                    # v2: validation errors fail identically every retry — don't re-attempt
                    logger.info(f"[OrderManager] Validation error — no retry for {signal.signal_id}")
                    no_retry = True
                else:
                    # v3 anti-duplicate: transient failure — poll broker state with
                    # backoff; the order may exist despite the error (e.g. the IG
                    # confirms endpoint 404s right after a successful POST).
                    verified = self._verify_order_at_broker(signal)
                    if verified:
                        self._accept_via_broker_state(
                            signal, result, attempt_record, verified,
                            "verified_via_broker_state_after_exception",
                        )

            attempts.append(attempt_record)

            # Stop on success, or immediately on non-retryable validation error
            if attempt_record["status"] == "ACCEPTED" or no_retry:
                if no_retry and attempt_record["status"] != "ACCEPTED":
                    # validation error: record final status instead of leaving UNKNOWN
                    result["status"] = "REJECTED"
                    result["reason"] = attempt_record.get("reason", "validation_error")
                    result["response"] = attempt_record.get("response")
                break

            # If this was the last attempt, record final status
            if attempt == max_retries:
                result["status"] = "REJECTED"
                result["reason"] = attempt_record.get("reason", "max_retries_exhausted")
                result["response"] = attempt_record.get("response")

            # Wait before next attempt (unless last attempt)
            if attempt < max_retries:
                time.sleep(retry_delay_s)

        # Finalise result with attempt history
        result["attempts"] = attempts
        result["attempt_count"] = len(attempts)
        result["market_price_snapshot"] = attempts[0]["market_price_snapshot"] if attempts else market_snapshot
        result["entry_distance_points"] = attempts[0]["entry_distance_points"] if attempts else entry_distance

        logger.info(
            f"[OrderManager] Final status for {signal.signal_id}: {result['status']} "
            f"after {len(attempts)} attempt(s)"
        )

        return result