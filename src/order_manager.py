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
from pathlib import Path
from typing import Optional, Set, Dict, Any
from dataclasses import asdict

from config import CONFIG
from src.signal_detector import Signal
from src.account_resolver import resolve_credentials
from src.ig_rest_client import IGRestClient

logger = logging.getLogger(__name__)


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
            self.processed_file.write_text(json.dumps(payload, indent=2))
        except Exception as e:
            logger.error(f"Failed to save processed_signals.json: {e}")

    def _is_duplicate(self, signal_id: str) -> bool:
        return signal_id in self.processed_signal_ids

    def _mark_processed(self, signal_id: str):
        self.processed_signal_ids.add(signal_id)
        self._save_processed_ids()

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

        try:
            response = self.ig_client.create_working_order(
                direction=ig_direction,
                epic="IX.D.DOW.IFS.IP",
                size=CONFIG.size,
                level=signal.entry_price,
                stop_level=signal.stop_loss,
                limit_level=signal.kc_lower if signal.direction == "SHORT" else signal.kc_upper,
                force_open=True,
            )

            result["response"] = response

            # Extract common fields from IG response
            if isinstance(response, dict):
                result["deal_reference"] = response.get("dealReference")
                # dealId may be nested under affectedDeals for ACCEPTED orders
                if response.get("dealId"):
                    result["deal_id"] = response.get("dealId")
                elif response.get("affectedDeals"):
                    try:
                        result["deal_id"] = response["affectedDeals"][0].get("dealId")
                    except Exception:
                        pass

                deal_status = response.get("dealStatus")
                if deal_status == "ACCEPTED" or response.get("status") == "OPEN":
                    result["status"] = "ACCEPTED"
                    self._mark_processed(signal.signal_id)
                else:
                    result["status"] = "REJECTED"
                    result["reason"] = response.get("reason", "broker_rejected")
            else:
                result["status"] = "ATTEMPTED"
                self._mark_processed(signal.signal_id)

            logger.info(
                f"[OrderManager] Working order {result['status']} for {signal.signal_id} "
                f"(RR={rr}, dealId={result.get('deal_id')})")

            # Attach snapshot/distance data to the result so it appears in the JSONL execution record
            result["market_price_snapshot"] = market_snapshot
            result["entry_distance_points"] = entry_distance
            return result

        except Exception as e:
            logger.error(f"[OrderManager] Failed to place order for {signal.signal_id}: {e}")
            result["status"] = "REJECTED"
            result["reason"] = str(e)
            return result