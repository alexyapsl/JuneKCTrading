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

    @staticmethod
    def _is_auth_error(exc: Exception) -> bool:
        """Detect IG session/token failures that are safe to fix via fresh login."""
        msg = str(exc).lower()
        return (
            "error.security.client-token-invalid" in msg
            or "client-token-invalid" in msg
            or "invalid session token" in msg
            or ("401" in msg and "token" in msg)
        )

    def _force_relogin(self) -> None:
        """Discard the stale REST session and create a fresh one."""
        logger.warning("[IG] Forcing fresh login after stale/invalid session token")
        self._is_logged_in = False
        self.ig_service = None
        self.login()

    def _call_with_reauth(self, operation_name: str, operation):
        """
        Run an authenticated IG call; on a stale-token 401, force a fresh login
        and retry the same operation exactly once.
        """
        self.ensure_session()
        try:
            return operation()
        except Exception as e:
            if not self._is_auth_error(e):
                raise
            logger.warning(
                f"[IG] {operation_name} failed with stale/invalid session token: {e}. "
                "Retrying once with a fresh login."
            )
            self._force_relogin()
            return operation()

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
        order_type: str = "STOP",
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
            response = self._call_with_reauth(
                "create_working_order",
                lambda: self.ig_service.create_working_order(
                    currency_code=currency_code,
                    direction=direction,
                    epic=epic,
                    time_in_force=time_in_force,
                    good_till_date=good_till_date,
                    expiry=expiry,
                    force_open=force_open,
                    order_type=order_type,
                    guaranteed_stop=guaranteed_stop,
                    size=str(size),  # library often expects string
                    stop_distance=stop_distance,
                    stop_level=stop_level,
                    level=level,
                    limit_distance=limit_distance,
                    limit_level=limit_level,
                ),
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

    def update_open_position(
        self,
        deal_id: str,
        limit_level: Optional[float] = None,
        stop_level: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Amend an OPEN position's stop/limit (PUT /positions/otc/{dealId}).

        Used by the per-bar dynamic management (2026-08-25): target follows the
        current KC band; stop ratchets to break-even then to the KC mid.
        Pass the current value for any leg you are NOT changing — IG requires
        both fields to be consistent with each other on every amend.
        """
        self.ensure_session()
        try:
            logger.info(
                f"[IG] Amending open position dealId={deal_id}: "
                f"limit={limit_level} stop={stop_level}"
            )
            response = self._call_with_reauth(
                "update_open_position",
                lambda: self.ig_service.update_open_position(
                    deal_id=deal_id,
                    limit_level=limit_level,
                    stop_level=stop_level,
                ),
            )
            if hasattr(response, "to_dict"):
                response = response.to_dict()
            logger.info(f"[IG] Amend response: {response}")
            return response if isinstance(response, dict) else {"raw": str(response)}
        except Exception as e:
            logger.error(f"[IG] update_open_position failed for {deal_id}: {e}")
            raise

    def cancel_working_order(self, deal_id: str) -> Dict[str, Any]:
        """
        Cancel a working (untriggered) order by its dealId.

        NOTE: IG working-order endpoints key off dealId (not dealReference).
        Use get_working_orders() to resolve a level/direction match to a dealId.
        """
        self.ensure_session()
        try:
            logger.info(f"[IG] Cancelling working order dealId={deal_id}")
            response = self._call_with_reauth(
                "cancel_working_order",
                lambda: self.ig_service.delete_working_order(deal_id),
            )
            if hasattr(response, "to_dict"):
                response = response.to_dict()
            return response if isinstance(response, dict) else {"raw": str(response)}
        except Exception as e:
            logger.error(f"[IG] cancel_working_order failed for {deal_id}: {e}")
            raise

    def close_position(
        self,
        deal_id: str,
        direction: str,
        size: float,
        epic: Optional[str] = None,
        expiry: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Close an open position at market (OTC delete).

        IMPORTANT (bug fix 2026-09-04): IG's DELETE /positions/otc treats
        `dealId` as mutually exclusive with `epic`/`expiry`, and its validator
        counts the KEYS as supplied even when the values are JSON null.
        trading_ig.close_open_position always includes epic/expiry/level/quoteId
        in the body, so routing through it returns 400
        `validation.mutual-exclusive-value.request` on every market close
        (first seen 2026-08-29 on the control runner's stuck long; nulling the
        values on 2026-09-03 did NOT help). We therefore bypass trading_ig and
        POST a minimal body with ONLY dealId/direction/size/orderType.

        Parameters
        ----------
        deal_id : str
            The position's dealId (e.g. "DIAAAAYB636BYBE") — NOT the working-order
            dealId. Fetch open positions and match on the order's level/direction.
        direction : str
            "BUY" or "SELL" — the direction of the OPEN position (the close
            request inverts it internally; we pass the open direction and swap).
        size : float
            Position size (must match the open size for a full close).
        epic : str, optional
            Ignored — kept for backwards compatibility. IG rejects close
            requests that combine dealId with epic/expiry (even as nulls).
        expiry : str, optional
            Ignored — see `epic`.

        Returns
        -------
        dict
            IG confirmation dict (dealStatus, dealId, level, ...).
        """
        self.ensure_session()
        open_dir = direction.upper()
        if open_dir not in ("BUY", "SELL"):
            raise ValueError("direction must be 'BUY' or 'SELL'")
        close_dir = "SELL" if open_dir == "BUY" else "BUY"
        try:
            logger.info(
                f"[IG] Closing position deal_id={deal_id} ({open_dir} {size}) at market"
            )
            response = self._call_with_reauth(
                "close_position",
                lambda: self._close_position_minimal(deal_id, close_dir, size),
            )
            if hasattr(response, "to_dict"):
                response = response.to_dict()
            logger.info(f"[IG] Close response: {response}")
            return response if isinstance(response, dict) else {"raw": str(response)}
        except Exception as e:
            logger.error(f"[IG] close_position failed for {deal_id}: {e}")
            raise

    def _close_position_minimal(
        self, deal_id: str, close_dir: str, size: float
    ) -> Dict[str, Any]:
        """
        Close via POST /positions/otc (_method=DELETE) with a minimal body.

        Only the four required keys are sent — dealId, direction, size,
        orderType — because IG's mutual-exclusion validator rejects the request
        if epic/expiry/level/quoteId keys are present AT ALL, even as null.
        trading_ig's close_open_position always includes them, so we call the
        CRUD layer directly. Raises on non-200 (same contract as trading_ig).
        """
        import json

        params = {
            "dealId": deal_id,
            "direction": close_dir,
            "size": str(size),
            "orderType": "MARKET",
        }
        response = self.ig_service._req(
            "delete", "/positions/otc", params, None, "1"
        )
        if response.status_code != 200:
            raise Exception(response.text)
        deal_reference = json.loads(response.text).get("dealReference")
        if not deal_reference:
            return {"raw": response.text}
        return self.ig_service.fetch_deal_by_deal_reference(deal_reference)

    def get_open_positions(self) -> Dict[str, Any]:
        """
        Fetch all currently open positions.

        Returns dict with key 'positions' (list). Each item typically has
        'position' (dealId, direction, size, level, ...) and 'market' (epic, ...).
        """
        self.ensure_session()
        try:
            response = self._call_with_reauth(
                "get_open_positions",
                lambda: self.ig_service.fetch_open_positions(),
            )
            if hasattr(response, "to_dict"):
                records = response.to_dict("records")
                return {"positions": records}
            if isinstance(response, list):
                return {"positions": response}
            return response if isinstance(response, dict) else {"positions": [], "raw": str(response)}
        except Exception as e:
            logger.error(f"[IG] get_open_positions failed: {e}")
            raise

    # Flat DataFrame record keys -> nested raw-API shape, so consumers can rely
    # on workingOrderData / marketData regardless of trading-ig version.
    _WO_DATA_KEYS = (
        "dealId", "direction", "epic", "orderSize", "orderLevel", "timeInForce",
        "goodTillDate", "goodTillDateISO", "createdDate", "createdDateUTC",
        "guaranteedStop", "orderType", "stopDistance", "limitDistance",
        "currencyCode", "dma", "limitedRiskPremium",
    )
    _WO_MARKET_KEYS = (
        "instrumentName", "exchangeId", "expiry", "marketStatus", "epic",
        "instrumentType", "lotSize", "high", "low", "percentageChange",
        "netChange", "bid", "offer", "updateTime", "updateTimeUTC",
        "delayTime", "streamingPricesAvailable", "scalingFactor",
    )

    @classmethod
    def _normalize_working_order(cls, rec: Dict[str, Any]) -> Dict[str, Any]:
        """Accept a flat DataFrame record or a nested raw-API item; return nested."""
        if isinstance(rec, dict) and "workingOrderData" in rec:
            return rec
        if not isinstance(rec, dict):
            return {"workingOrderData": {}, "marketData": {}}
        wod = {k: rec.get(k) for k in cls._WO_DATA_KEYS if k in rec}
        md = {k: rec.get(k) for k in cls._WO_MARKET_KEYS if k in rec}
        return {"workingOrderData": wod, "marketData": md}

    def get_working_orders(self) -> Dict[str, Any]:
        """
        Fetch all currently working (untriggered) orders.

        Returns dict with key 'workingOrders' (list). Each item typically has
        'workingOrderData' (dealId, direction, orderLevel, orderSize, ...)
        and 'marketData' (epic, bid, offer, ...).
        """
        self.ensure_session()
        try:
            response = self._call_with_reauth(
                "get_working_orders",
                lambda: self.ig_service.fetch_working_orders(),
            )
            if hasattr(response, "to_dict"):
                # trading-ig returns a DataFrame; records orientation preserves rows.
                # Plain to_dict() gives a column-oriented dict, which silently
                # drops every order downstream (bug found 2026-08-24).
                records = response.to_dict("records")
                return {"workingOrders": [self._normalize_working_order(r) for r in records]}
            if isinstance(response, list):
                return {"workingOrders": [self._normalize_working_order(r) for r in response]}
            if isinstance(response, dict) and "workingOrders" in response:
                return {
                    **response,
                    "workingOrders": [
                        self._normalize_working_order(r)
                        for r in (response.get("workingOrders") or [])
                    ],
                }
            return response if isinstance(response, dict) else {"workingOrders": [], "raw": str(response)}
        except Exception as e:
            logger.error(f"[IG] get_working_orders failed: {e}")
            raise

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
            response = None
            # trading_ig.IGService uses lazy __getattr__, so hasattr is unreliable.
            # Try the preferred method first, then fall back safely.
            try:
                if hasattr(self.ig_service, "fetch_market_by_epic"):
                    response = self.ig_service.fetch_market_by_epic(epic)
            except Exception:
                response = None

            if response is None:
                try:
                    if hasattr(self.ig_service, "fetch_current_prices"):
                        response = self.ig_service.fetch_current_prices([epic])
                except Exception:
                    response = None

            if response is None:
                logger.warning("[IG] No working price-fetch method on IGService; skipping snapshot")
                return None

            logger.debug(f"[IG] price snapshot raw response: {response}")
            if isinstance(response, dict):
                snapshot = response.get("snapshot") or response.get("prices") or response
                return snapshot if isinstance(snapshot, dict) else {"raw": snapshot}
            return {"raw": response}
        except Exception as e:
            logger.warning(f"[IG] fetch_current_price failed for {epic}: {e} (snapshot skipped)")
            return None  # non-fatal for Phase 1.5"

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
            response = self._call_with_reauth(
                "confirm_order",
                lambda: self.ig_service.fetch_deal_by_deal_reference(deal_reference),
            )
            logger.info(f"[IG] Confirmation response: {response}")
            return response
        except Exception as e:
            logger.error(f"[IG] confirm_order failed for {deal_reference}: {e}")
            raise

    def fetch_transaction_reference(self, deal_id: str) -> Optional[str]:
        """
        Derive the transaction/position reference from an IG dealId.

        IG deal ids embed the reference directly: dealId = "DIAAAAY" + ref
        (verified 2026-08-20 against activity + transaction history: close
        dealId DIAAAAYBYYME3AD <-> txn reference BYYME3AD, same timestamp).

        NB: GET /history/transactions?dealId=... does NOT work — the dealId
        param is silently ignored and the endpoint returns the LATEST
        transactions, which would attach the wrong reference. Do not use it.
        """
        if deal_id and deal_id.startswith("DIAAAAY") and len(deal_id) == 15:
            ref = deal_id[7:]
            logger.info(f"[IG] Derived transaction reference from dealId: {ref}")
            return ref
        logger.warning(
            f"[IG] Unrecognized dealId format, cannot derive reference: {deal_id}"
        )
        return None

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
            response = self._call_with_reauth(
                "fetch_account_activity",
                lambda: self.ig_service.fetch_account_activity(
                    from_date=from_date,
                    to_date=to_date,
                    deal_id=deal_id,
                    epic=epic,
                ),
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
            response = self._call_with_reauth(
                "fetch_account_transactions",
                lambda: self.ig_service.fetch_account_transactions(
                    from_date=from_date,
                    to_date=to_date,
                    type=type,
                ),
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