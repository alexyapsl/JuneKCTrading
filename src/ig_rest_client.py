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
            # Use positional args (trading-ig versions differ on kwarg names)
            self.ig_service = IGService(
                self.creds["username"],
                self.creds["password"],
                self.creds["api_key"],
                self.creds["acc_type"],
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
        expiry: str = "-",
        currency_code: str = "GBP",
        force_open: bool = True,
        guaranteed_stop: bool = False,
        time_in_force: str = "GOOD_TILL_CANCELLED",
        good_till_date: Optional[str] = None,
        stop_distance: Optional[float] = None,
        limit_distance: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Create a working order (limit order) on IG.

        Matches the working pattern:
            ig_service.create_working_order(
                currency_code=...,
                direction=...,
                epic=...,
                time_in_force=...,
                good_till_date=...,
                expiry=...,
                force_open=...,
                order_type='LIMIT',
                guaranteed_stop=...,
                size=...,
                stop_distance=None,
                stop_level=...,
                level=...,
                limit_distance=None,
                limit_level=...,
            )

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
            Stop loss level (absolute price)
        limit_level : float
            Take profit level (absolute price)
        expiry : str
            "-" for daily funded bet (default, matches working example)
        currency_code : str
            Account currency (default GBP)
        force_open : bool
            Allow position to be opened even if opposite position exists
        guaranteed_stop : bool
            Use guaranteed stop (costs extra premium)
        time_in_force : str
            "GOOD_TILL_CANCELLED" (default) or "GOOD_TILL_DATE"
        good_till_date : str or None
            Required when time_in_force="GOOD_TILL_DATE"
        stop_distance : float or None
            Stop distance (alternative to stop_level)
        limit_distance : float or None
            Limit distance (alternative to limit_level)

        Returns
        -------
        dict
            IG response dictionary (contains dealReference, etc.)
        """
        self.ensure_session()

        direction = direction.upper()
        if direction not in ("BUY", "SELL"):
            raise ValueError("direction must be 'BUY' or 'SELL'")

        try:
            logger.info(
                f"[IG] Creating working order: {direction} {size} {epic} "
                f"@ {level} | SL={stop_level} | TP={limit_level}"
            )
            response = self.ig_service.create_working_order(
                currency_code=currency_code,
                direction=direction,
                epic=epic,
                time_in_force=time_in_force,
                good_till_date=good_till_date,
                expiry=expiry,
                force_open=force_open,
                order_type="LIMIT",
                guaranteed_stop=guaranteed_stop,
                size=str(size),  # library often expects string
                stop_distance=stop_distance,
                stop_level=stop_level,
                level=level,
                limit_distance=limit_distance,
                limit_level=limit_level,
            )
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

    def fetch_current_price(self, epic: str) -> Dict[str, Any]:
        """
        Fetch a lightweight current-price snapshot for a single epic.

        Useful for measuring how far a planned working-order level is from the
        current market before submission. Captures bid/ask/offer prices so we
        can analyse distance-to-entry requirements.

        Returns a dict with keys such as:
          bid, offer, high, low, mid, snapshotTime
        (exact keys depend on IG response; the method passes through the raw
        'snapshot' payload when available).
        """
        self.ensure_session()
        try:
            logger.info(f"[IG] Fetching current price snapshot for {epic}")
            response = self.ig_service.fetch_current_prices([epic])
            logger.debug(f"[IG] fetch_current_prices raw response: {response}")
            # Typical trading-ig response shape: {'prices': {...}, 'snapshot': {...}}
            if isinstance(response, dict):
                # Prefer 'snapshot' if the library provides a clean flattened view
                snapshot = response.get("snapshot") or response.get("prices") or response
                return snapshot if isinstance(snapshot, dict) else {"raw": snapshot}
            return {"raw": response}
        except Exception as e:
            logger.error(f"[IG] fetch_current_price failed for {epic}: {e}")
            raise

    # ------------------------------------------------------------------ #
    #                     CONFIRM / OUTCOME (Phase 1.5)                  #
    # ------------------------------------------------------------------ #

    def confirm_order(self, deal_reference: str) -> Dict[str, Any]:
        """
        Retrieve the confirmation / final status for a deal reference.

        Uses trading_ig's fetch_deal_by_deal_reference under the hood.
        Returns the canonical IG record for that deal (status, affected deals,
        P&L when closed, etc).

        Use this to reconcile outcomes for both ACCEPTED and REJECTED orders.

        Parameters
        ----------
        deal_reference : str
            The dealReference returned when the working order was submitted.

        Returns
        -------
        dict
            IG deal confirmation payload.
        """
        self.ensure_session()
        try:
            logger.info(f"[IG] Confirming deal_reference={deal_reference}")
            response = self.ig_service.fetch_deal_by_deal_reference(deal_reference)
            logger.info(f"[IG] Confirmation response: {response}")
            return response
        except Exception as e:
            logger.error(f"[IG] confirm_order failed for {deal_reference}: {e}")
            raise

    # ------------------------------------------------------------------
    # Activity / History lookup (preferred for rejected working orders)
    # ------------------------------------------------------------------

    def fetch_account_activity(
        self,
        from_date: str = None,
        to_date: str = None,
        deal_id: str = None,
        epic: str = None,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Query the account activity history.

        This is the most reliable way to retrieve historical working-order
        outcomes (including rejections) after the dealReference has expired.

        Corresponds to:
            GET /history/activity?from=...&to=...&dealId=...&epic=...

        Parameters
        ----------
        from_date : str
            Start date (YYYY-MM-DD) or datetime string.
        to_date : str
            End date (YYYY-MM-DD) or datetime string.
        deal_id : str, optional
            Filter by a specific dealId returned by IG.
        epic : str, optional
            Filter by market epic.
        limit : int
            Maximum number of activities to return.

        Returns
        -------
        dict
            {"activities": [...], "metadata": {...}}
        """
        self.ensure_session()
        try:
            params = {}
            if from_date:
                params["from"] = from_date
            if to_date:
                params["to"] = to_date
            if deal_id:
                params["dealId"] = deal_id
            if epic:
                params["epic"] = epic

            logger.info(f"[IG] Fetching account activity deal_id={deal_id} from={from_date} to={to_date}")
            # trading-ig exposes this as fetch_account_activity
            response = self.ig_service.fetch_account_activity(
                from_date=from_date,
                to_date=to_date,
                deal_id=deal_id,
                epic=epic,
            )
            # Some versions return a DataFrame or a dict; normalise to dict
            if hasattr(response, "to_dict"):
                response = response.to_dict()
            logger.info(f"[IG] Activity response: {response}")
            return response
        except Exception as e:
            logger.error(f"[IG] fetch_account_activity failed: {e}")
            raise

    def fetch_account_transactions(
        self,
        from_date: str = None,
        to_date: str = None,
        type: str = None,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Query the account transaction history (P&L, deposits, withdrawals, etc).

        Corresponds to:
            GET /history/transactions?from=...&to=...&type=...

        Useful for reconciling closed positions and realised P&L.
        """
        self.ensure_session()
        try:
            logger.info(f"[IG] Fetching transactions from={from_date} to={to_date}")
            response = self.ig_service.fetch_account_transactions(
                from_date=from_date,
                to_date=to_date,
                type=type,
            )
            if hasattr(response, "to_dict"):
                response = response.to_dict()
            return response
        except Exception as e:
            logger.error(f"[IG] fetch_account_transactions failed: {e}")
            raise


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