"""
IG REST Client – Minimal wrapper for trading-ig

This client is designed to be instantiated per experiment/account.
It supports both demo and live environments via explicit credential
injection (never reads .env itself – that is handled by AccountResolver).

Phase 1 scope:
- Login (demo / live)
- Create working order (limit order with entry, stop, limit)
- Basic error handling and logging

Future phases will add:
- amend_working_order
- cancel_working_order
- close_position
- get_working_orders / get_open_positions
"""

import logging
from typing import Dict, Any, Optional
from trading_ig import IGService

logger = logging.getLogger(__name__)


class IGRestClient:
    """
    Thin wrapper around trading_ig.IGService.

    Responsibilities:
    - Authenticate using provided credentials
    - Provide high-level methods for order operations
    - Keep the underlying IGService instance encapsulated

    Usage
    -----
    creds = resolve_credentials("account1", paper_trading=True)
    client = IGRestClient(creds)
    client.login()

    order = client.create_working_order(
        direction="SELL",
        epic="IX.D.DOW.IFS.IP",
        size=1,
        level=52623.2,           # entry
        stop_level=52657.3,
        limit_level=52644.8,
        expiry="DFB",
        currency_code="GBP",
        force_open=True
    )
    """

    def __init__(self, credentials: Dict[str, str]):
        """
        Parameters
        ----------
        credentials : dict
            Must contain: username, password, api_key, acc_type
            (as returned by account_resolver.resolve_credentials)
        """
        self.creds = credentials
        self.ig_service: Optional[IGService] = None
        self._is_logged_in = False

    def login(self) -> bool:
        """
        Authenticate to IG REST API.

        Returns
        -------
        bool
            True if login succeeded.
        """
        if self._is_logged_in and self.ig_service is not None:
            return True

        try:
            self.ig_service = IGService(
                ig_username=self.creds["username"],
                ig_password=self.creds["password"],
                ig_api_key=self.creds["api_key"],
                acc_type=self.creds["acc_type"],
            )
            self.ig_service.create_session()
            self._is_logged_in = True
            logger.info(
                f"[IG] Logged in successfully as {self.creds['username']} "
                f"({self.creds['acc_type']}) using {self.creds.get('credential_file', 'unknown file')}"
            )
            return True
        except Exception as e:
            logger.error(f"[IG] Login failed: {e}")
            self._is_logged_in = False
            raise

    def ensure_session(self):
        """Make sure we have an active session. Re-login if needed."""
        if not self._is_logged_in or self.ig_service is None:
            self.login()

    # ------------------------------------------------------------------ #
    #                           PHASE 1 METHODS                          #
    # ------------------------------------------------------------------ #

    def create_working_order(
        self,
        direction: str,
        epic: str,
        size: float,
        level: float,
        stop_level: float,
        limit_level: float,
        expiry: str = "DFB",
        currency_code: str = "GBP",
        force_open: bool = True,
        guaranteed_stop: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a working order (limit order) on IG.

        Parameters
        ----------
        direction : str
            "BUY" or "SELL"
        epic : str
            Market epic (e.g. "IX.D.DOW.IFS.IP")
        size : float
            Position size in points (£1 per point for mini Dow)
        level : float
            Limit price for the working order (entry)
        stop_level : float
            Stop loss level
        limit_level : float
            Take profit level (target)
        expiry : str
            "DFB" for daily funded bet (default)
        currency_code : str
            Account currency (default GBP)
        force_open : bool
            Allow position to be opened even if opposite position exists
        guaranteed_stop : bool
            Use guaranteed stop (costs extra premium)

        Returns
        -------
        dict
            IG response dictionary (contains dealReference, etc.)
        """
        self.ensure_session()

        direction = direction.upper()
        if direction not in ("BUY", "SELL"):
            raise ValueError("direction must be 'BUY' or 'SELL'")

        payload = {
            "direction": direction,
            "epic": epic,
            "size": size,
            "level": level,
            "stopLevel": stop_level,
            "limitLevel": limit_level,
            "type": "LIMIT",           # working order type
            "expiry": expiry,
            "currencyCode": currency_code,
            "forceOpen": force_open,
            "guaranteedStop": guaranteed_stop,
        }

        try:
            logger.info(
                f"[IG] Creating working order: {direction} {size} {epic} "
                f"@ {level} | SL={stop_level} | TP={limit_level}"
            )
            response = self.ig_service.create_working_order(**payload)
            logger.info(f"[IG] Working order accepted: {response}")
            return response
        except Exception as e:
            logger.error(f"[IG] create_working_order failed: {e}")
            raise

    # ------------------------------------------------------------------ #
    #                     FUTURE PHASE 2+ METHODS (STUBS)                #
    # ------------------------------------------------------------------ #

    def amend_working_order(self, deal_reference: str, **kwargs) -> Dict[str, Any]:
        """Placeholder for Phase 2."""
        raise NotImplementedError("amend_working_order will be implemented in Phase 2")

    def cancel_working_order(self, deal_reference: str) -> Dict[str, Any]:
        """Placeholder for Phase 2."""
        raise NotImplementedError("cancel_working_order will be implemented in Phase 2")

    def close_position(self, deal_id: str, direction: str, size: float, **kwargs) -> Dict[str, Any]:
        """Placeholder for Phase 2."""
        raise NotImplementedError("close_position will be implemented in Phase 2")

    def get_working_orders(self) -> Dict[str, Any]:
        """Placeholder for Phase 2."""
        raise NotImplementedError("get_working_orders will be implemented in Phase 2")

    def get_open_positions(self) -> Dict[str, Any]:
        """Placeholder for Phase 2."""
        raise NotImplementedError("get_open_positions will be implemented in Phase 2")


# Quick smoke test (requires valid credentials file)
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))

    from src.account_resolver import resolve_credentials

    logging.basicConfig(level=logging.INFO)

    try:
        creds = resolve_credentials("account1", paper_trading=True)
        client = IGRestClient(creds)
        client.login()
        print("✓ Login successful (demo account)")
    except Exception as e:
        print(f"✗ Error: {e}")