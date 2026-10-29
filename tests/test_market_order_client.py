"""Tests for IGRestClient.create_market_order + snapshot re-auth (2026-10-29)."""

from src.ig_rest_client import IGRestClient


CREDS = {
    "username": "demo-user",
    "password": "demo-pass",
    "api_key": "demo-key",
    "acc_type": "DEMO",
}


class _FakeResponse:
    status_code = 200
    text = '{"dealReference": "MKTREF1"}'


class _FakeIGService:
    def __init__(self):
        self.reqs = []
        self.confirms = []

    def create_session(self):
        pass

    def _req(self, action, endpoint, params, session, version):
        self.reqs.append({"action": action, "endpoint": endpoint, "params": params, "version": version})
        return _FakeResponse()

    def fetch_deal_by_deal_reference(self, ref):
        self.confirms.append(ref)
        return {"dealStatus": "ACCEPTED", "dealId": "DIAMKT1", "level": 97.0, "status": "OPEN"}


def test_create_market_order_minimal_body(monkeypatch):
    service = _FakeIGService()
    monkeypatch.setattr("src.ig_rest_client.IGService", lambda *args: service)

    client = IGRestClient(CREDS)
    client.login()
    result = client.create_market_order(
        direction="SELL",
        epic="IX.D.DOW.IFS.IP",
        size=1.0,
        stop_level=110.0,
        limit_level=70.0,
    )

    assert result["dealStatus"] == "ACCEPTED"
    assert result["dealId"] == "DIAMKT1"
    assert len(service.reqs) == 1
    req = service.reqs[0]
    assert req["action"] == "create"
    assert req["endpoint"] == "/positions/otc"
    assert req["version"] == "2"
    params = req["params"]
    assert params["orderType"] == "MARKET"
    assert params["direction"] == "SELL"
    assert params["stopLevel"] == 110.0
    assert params["limitLevel"] == 70.0
    # IG's mutual-exclusion validators can reject a request merely for
    # containing these keys, even as null (lesson from close_position 2026-09-04)
    assert "level" not in params
    assert "quoteId" not in params
    assert service.confirms == ["MKTREF1"]


def test_fetch_current_price_reauths_on_stale_token(monkeypatch):
    """Stale token on the snapshot call must force ONE fresh login, not skip.

    Previously the snapshot silently returned None and the order POST paid the
    re-login delay — the root cause of the missed 2026-10-29 09:45 short.
    """

    class StaleService:
        def create_session(self):
            pass

        def fetch_market_by_epic(self, epic):
            raise Exception('HTTP error: 401 {"errorCode":"error.security.client-token-invalid"}')

    class FreshService:
        def create_session(self):
            pass

        def fetch_market_by_epic(self, epic):
            return {"snapshot": {"bid": 52844.4, "offer": 52848.0, "marketStatus": "TRADEABLE"}}

    services = [StaleService(), FreshService()]
    monkeypatch.setattr("src.ig_rest_client.IGService", lambda *args: services.pop(0))

    client = IGRestClient(CREDS)
    client.login()
    stale = client.ig_service

    snap = client.fetch_current_price("IX.D.DOW.IFS.IP")

    assert snap is not None
    assert snap["bid"] == 52844.4
    assert client.ig_service is not stale


def test_fetch_current_price_still_none_when_no_method(monkeypatch):
    """Non-auth failure on both fetch methods -> None (non-fatal), no relogin."""

    class NoMethodService:
        def __init__(self):
            self.sessions = 0

        def create_session(self):
            self.sessions += 1

        def fetch_market_by_epic(self, epic):
            raise Exception("403 forbidden")

        def fetch_current_prices(self, epics):
            raise Exception("403 forbidden")

    service = NoMethodService()
    monkeypatch.setattr("src.ig_rest_client.IGService", lambda *args: service)

    client = IGRestClient(CREDS)
    client.login()

    assert client.fetch_current_price("IX.D.DOW.IFS.IP") is None
    assert service.sessions == 1  # no forced relogin for non-auth errors
