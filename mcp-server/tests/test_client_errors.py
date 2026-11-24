"""Behavioural tests for Enable Banking request classification, retries, and safe errors."""
import time

import httpx
import pytest

from ebmcp import client

JWT = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.e30.testdeadbeefjwt"
SESSION_ID = "11111111-2222-4333-8444-555555555555"
OTHER_ACCOUNT_ID = "acc-from-bank-payload-999"
CALLER_ACCOUNT_ID = "acc-passed-by-caller"


class Script:
    """MockTransport handler that returns scripted responses or raises."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = 0
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.requests.append(request)
        step = self.steps[min(self.calls - 1, len(self.steps) - 1)]
        if isinstance(step, BaseException):
            raise step
        return step


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    monkeypatch.setattr(client, "make_jwt", lambda: JWT)
    monkeypatch.setattr(client, "API", "https://api.enablebanking.test")
    recorded: list[float] = []
    monkeypatch.setattr(client, "_sleep", lambda s: recorded.append(s))
    return recorded


def _install(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        client, "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), timeout=60),
    )


def _json(status: int, error: str | None = None, extra: dict | None = None,
          headers: dict | None = None) -> httpx.Response:
    body = {"message": "bank said something secret", "code": status}
    if error:
        body["error"] = error
    if extra:
        body.update(extra)
    return httpx.Response(status, json=body, headers=headers or {})


def test_expired_session_tells_caller_to_reconsent(monkeypatch, sleeps):
    script = Script(_json(401, "EXPIRED_SESSION", extra={
        "detail": f"session {SESSION_ID} jwt={JWT}",
    }))
    _install(monkeypatch, script)
    with pytest.raises(client.EnableBankingError) as ei:
        client.get(f"/sessions/{SESSION_ID}")
    err = ei.value
    assert err.kind == "consent"
    assert "consent" in str(err).lower()
    assert "re-run consent" in str(err).lower()
    assert script.calls == 1
    assert sleeps == []


def test_revoked_session_is_consent(monkeypatch):
    _install(monkeypatch, Script(_json(401, "REVOKED_SESSION")))
    with pytest.raises(client.EnableBankingError) as ei:
        client.get(f"/sessions/{SESSION_ID}")
    assert ei.value.kind == "consent"


def test_missing_session_is_consent_not_generic_client_error(monkeypatch, sleeps):
    script = Script(_json(404, "SESSION_DOES_NOT_EXIST"))
    _install(monkeypatch, script)
    with pytest.raises(client.EnableBankingError) as ei:
        client.get(f"/sessions/{SESSION_ID}")
    assert ei.value.kind == "consent"
    assert "re-run consent" in str(ei.value).lower()
    assert script.calls == 1
    assert sleeps == []


def test_unauthorized_status_is_consent_without_retry(monkeypatch, sleeps):
    script = Script(_json(403, "ACCESS_DENIED"), _json(403, "ACCESS_DENIED"))
    _install(monkeypatch, script)
    with pytest.raises(client.EnableBankingError) as ei:
        client.get(f"/accounts/{CALLER_ACCOUNT_ID}/balances")
    assert ei.value.kind == "consent"
    assert script.calls == 1
    assert sleeps == []


def test_rate_limit_retries_then_rate_limited(monkeypatch, sleeps):
    script = Script(_json(429, "ASPSP_RATE_LIMIT_EXCEEDED"))
    _install(monkeypatch, script)
    with pytest.raises(client.EnableBankingError) as ei:
        client.get(f"/accounts/{CALLER_ACCOUNT_ID}/transactions")
    assert ei.value.kind == "rate_limited"
    assert "rate limited" in str(ei.value).lower()
    assert script.calls == client.MAX_ATTEMPTS
    assert sleeps == [0.5, 1.0]


def test_rate_limit_respects_retry_after_and_succeeds(monkeypatch, sleeps):
    script = Script(
        _json(429, "ASPSP_RATE_LIMIT_EXCEEDED", headers={"Retry-After": "7"}),
        httpx.Response(200, json={"balances": []}),
    )
    _install(monkeypatch, script)
    assert client.get(f"/accounts/{CALLER_ACCOUNT_ID}/balances") == {"balances": []}
    assert script.calls == 2
    assert sleeps == [7.0]


def test_retry_after_is_capped(monkeypatch, sleeps):
    script = Script(_json(503, headers={"Retry-After": "3600"}))
    _install(monkeypatch, script)
    with pytest.raises(client.EnableBankingError) as ei:
        client.get("/sessions/x")
    assert ei.value.kind == "unavailable"
    assert sleeps == [client.MAX_RETRY_WAIT, client.MAX_RETRY_WAIT]


def test_5xx_retries_then_unavailable(monkeypatch, sleeps):
    iban = "FI2112345600000785"
    script = Script(_json(502, "ASPSP_ERROR", extra={"detail": {"iban": iban}}))
    _install(monkeypatch, script)
    with pytest.raises(client.EnableBankingError) as ei:
        client.get(f"/accounts/{CALLER_ACCOUNT_ID}/details")
    assert ei.value.kind == "unavailable"
    assert "unavailable" in str(ei.value).lower()
    assert iban not in str(ei.value)
    assert script.calls == client.MAX_ATTEMPTS
    assert sleeps == [0.5, 1.0]


def test_aspsp_error_on_4xx_is_unavailable_without_retry(monkeypatch, sleeps):
    script = Script(_json(400, "ASPSP_ERROR"))
    _install(monkeypatch, script)
    with pytest.raises(client.EnableBankingError) as ei:
        client.get(f"/accounts/{CALLER_ACCOUNT_ID}/balances")
    assert ei.value.kind == "unavailable"
    assert script.calls == 1
    assert sleeps == []


def test_timeout_retries_then_unavailable(monkeypatch, sleeps):
    script = Script(httpx.ReadTimeout("Read timed out"))
    _install(monkeypatch, script)
    with pytest.raises(client.EnableBankingError) as ei:
        client.get(f"/sessions/{SESSION_ID}")
    assert ei.value.kind == "unavailable"
    assert script.calls == client.MAX_ATTEMPTS
    assert sleeps == [0.5, 1.0]


def test_client_error_does_not_retry(monkeypatch, sleeps):
    script = Script(_json(400, "WRONG_REQUEST_PARAMETERS"))
    _install(monkeypatch, script)
    with pytest.raises(client.EnableBankingError) as ei:
        client.get(f"/accounts/{CALLER_ACCOUNT_ID}/transactions")
    assert ei.value.kind == "client"
    assert "rejected" in str(ei.value).lower()
    assert script.calls == 1
    assert sleeps == []


def test_error_text_never_contains_session_id_jwt_or_bank_payload(monkeypatch):
    payload = {
        "message": f"session {SESSION_ID} died",
        "detail": {"session_id": SESSION_ID, "account_id": OTHER_ACCOUNT_ID, "token": JWT},
        "error": "EXPIRED_SESSION",
    }
    script = Script(httpx.Response(401, json=payload))
    _install(monkeypatch, script)
    with pytest.raises(client.EnableBankingError) as ei:
        client.get(f"/sessions/{SESSION_ID}")
    msg = str(ei.value)
    assert SESSION_ID in str(script.requests[0].url)
    assert SESSION_ID not in msg
    assert JWT not in msg
    assert OTHER_ACCOUNT_ID not in msg
    assert CALLER_ACCOUNT_ID not in msg
    assert "died" not in msg.lower()
    assert "/sessions/" not in msg
    assert "Bearer" not in msg


def test_non_json_upstream_error_does_not_leak_body(monkeypatch):
    script = Script(httpx.Response(502, text=f"fatal {SESSION_ID} {JWT} {OTHER_ACCOUNT_ID}"))
    _install(monkeypatch, script)
    with pytest.raises(client.EnableBankingError) as ei:
        client.get(f"/sessions/{SESSION_ID}")
    msg = str(ei.value)
    assert ei.value.kind == "unavailable"
    assert SESSION_ID not in msg
    assert JWT not in msg
    assert OTHER_ACCOUNT_ID not in msg


def test_retry_after_http_date(monkeypatch, sleeps):
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)
    later = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(1_700_000_004.0))
    script = Script(
        _json(429, "ASPSP_RATE_LIMIT_EXCEEDED", headers={"Retry-After": later}),
        httpx.Response(200, json={"ok": True}),
    )
    _install(monkeypatch, script)
    assert client.get("/accounts/x/balances") == {"ok": True}
    assert script.calls == 2
    assert sleeps == [4.0]


def test_5xx_then_success_returns_body(monkeypatch, sleeps):
    script = Script(
        _json(500, "ASPSP_TIMEOUT"),
        httpx.Response(200, json={"accounts": ["ok"]}),
    )
    _install(monkeypatch, script)
    assert client.get(f"/sessions/{SESSION_ID}") == {"accounts": ["ok"]}
    assert script.calls == 2
    assert sleeps == [0.5]
