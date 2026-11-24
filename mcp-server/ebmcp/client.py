"""Read-only Enable Banking (PSD2 AIS) client.

Deliberately has NO payment (PIS) endpoints. Only these calls exist:
  POST /auth, POST /sessions (one-time consent, CLI only)
  GET /sessions/{id}, GET /accounts/{id}/details|balances|transactions
Never log the JWT, the key, or the session id.
"""
import logging
import os
import stat
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import jwt

logging.getLogger("httpx").setLevel(logging.WARNING)  # URLs carry session ids
API = os.environ.get("ENABLEBANKING_API", "https://api.enablebanking.com")
KEY_PATH = os.environ.get("ENABLEBANKING_KEY_PATH", "/workspace/enablebanking/private.pem")
SESSION_FILE = os.environ.get("ENABLEBANKING_SESSION_FILE", str(Path.home() / ".enablebanking_session"))
JWT_TTL = 3600  # docs: max 86400
TIMEOUT = 60
MAX_ATTEMPTS = 3
MAX_RETRY_WAIT = 30.0

_CONSENT_CODES = frozenset({
    "CLOSED_SESSION",
    "EXPIRED_AUTHORIZATION_CODE",
    "EXPIRED_SESSION",
    "REVOKED_SESSION",
    "SESSION_DOES_NOT_EXIST",
    "WRONG_AUTHORIZATION_CODE",
    "WRONG_SESSION_STATUS",
})
_RATE_CODES = frozenset({"ASPSP_RATE_LIMIT_EXCEEDED"})
_UNAVAILABLE_CODES = frozenset({"ASPSP_ERROR", "ASPSP_TIMEOUT"})
_KIND_CONSENT, _KIND_RATE, _KIND_UNAVAILABLE, _KIND_CLIENT = (
    "consent", "rate_limited", "unavailable", "client",
)
_MESSAGES = {
    _KIND_CONSENT: (
        "Bank consent expired, revoked, or unauthorized. "
        "Re-run consent (`python -m ebmcp.consent`)."
    ),
    _KIND_RATE: "Rate limited by the bank or Enable Banking. Wait and retry later.",
    _KIND_UNAVAILABLE: "Bank or Enable Banking is temporarily unavailable. Try again later.",
    _KIND_CLIENT: "Enable Banking rejected the request.",
}


class EnableBankingError(RuntimeError):
    """Classified Enable Banking failure. str(error) is safe to show callers."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def make_jwt(app_id: str | None = None, key_path: str | None = None) -> str:
    app_id = app_id or os.environ.get("ENABLEBANKING_APP_ID")
    if not app_id:
        raise RuntimeError("ENABLEBANKING_APP_ID is not set (app not registered yet)")
    key = Path(key_path or KEY_PATH).read_bytes()
    now = int(time.time())
    claims = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + JWT_TTL}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": app_id, "typ": "JWT"})


def _parse_ids(raw: str) -> list[str]:
    return [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()]


def _bank_slug(name: str, country: str = "") -> str:
    return "".join(c for c in f"{name}{country}".lower() if c.isalnum()) or "unknown"


def _session_path(bank: str, country: str = "") -> Path:
    base = Path(SESSION_FILE)
    return base.with_name(f"{base.name}.{_bank_slug(bank, country)}")


def _session_files() -> list[Path]:
    base = Path(SESSION_FILE)
    files = []
    if base.is_file():
        files.append(base)
    parent = base.parent
    prefix = base.name + "."
    if parent.is_dir():
        files.extend(sorted(p for p in parent.iterdir() if p.name.startswith(prefix) and p.is_file()))
    return files


def session_ids() -> list[str]:
    ids = _parse_ids(os.environ.get("ENABLEBANKING_SESSION_ID") or "")
    if not ids:
        seen: set[str] = set()
        for path in _session_files():
            for sid in _parse_ids(path.read_text()):
                if sid not in seen:
                    seen.add(sid)
                    ids.append(sid)
    if ids:
        return ids
    raise RuntimeError("No session: set ENABLEBANKING_SESSION_ID or run `python -m ebmcp.consent`")


def save_session(sid: str, bank: str, country: str = "") -> Path:
    p = _session_path(bank, country)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(sid)
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    return p


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _eb_error_code(response: httpx.Response) -> str | None:
    try:
        data = response.json()
    except ValueError:
        return None
    code = data.get("error") if isinstance(data, dict) else None
    return code if isinstance(code, str) else None


def _classify(status: int, eb_code: str | None) -> str:
    if eb_code in _RATE_CODES or status == 429:
        return _KIND_RATE
    if eb_code in _CONSENT_CODES:
        return _KIND_CONSENT
    if eb_code in _UNAVAILABLE_CODES or status >= 500 or status == 408:
        return _KIND_UNAVAILABLE
    return _KIND_CLIENT


def _retryable(status: int, eb_code: str | None) -> bool:
    if eb_code in _RATE_CODES or eb_code in _UNAVAILABLE_CODES:
        return False
    return status == 429 or status == 408 or status >= 500


def _backoff(attempt: int) -> float:
    return min(MAX_RETRY_WAIT, 0.5 * (2 ** attempt))


def _retry_wait(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return min(MAX_RETRY_WAIT, max(0.0, float(raw)))
        except ValueError:
            try:
                dt = parsedate_to_datetime(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return min(MAX_RETRY_WAIT, max(0.0, dt.timestamp() - time.time()))
            except (TypeError, ValueError, OverflowError):
                pass
    return _backoff(attempt)


def _error(kind: str) -> EnableBankingError:
    return EnableBankingError(kind, _MESSAGES[kind])


def _req(method: str, path: str, **kw) -> dict:
    headers = {"Authorization": f"Bearer {make_jwt()}", "Accept": "application/json"}
    url = API + path
    with _client() as http:
        for attempt in range(MAX_ATTEMPTS):
            try:
                r = http.request(method, url, headers=headers, **kw)
            except httpx.RequestError:
                if attempt + 1 >= MAX_ATTEMPTS:
                    raise _error(_KIND_UNAVAILABLE) from None
                _sleep(_backoff(attempt))
                continue
            if r.status_code < 400:
                return r.json()
            eb_code = _eb_error_code(r)
            kind = _classify(r.status_code, eb_code)
            if not _retryable(r.status_code, eb_code) or attempt + 1 >= MAX_ATTEMPTS:
                raise _error(kind)
            _sleep(_retry_wait(r, attempt))
    raise _error(_KIND_UNAVAILABLE)


def get(path: str, params: dict | None = None) -> dict:
    return _req("GET", path, params={k: v for k, v in (params or {}).items() if v})


# --- consent (used only by the CLI) ---
def start_auth(aspsp_name: str, country: str, redirect_url: str, valid_until: str, state: str, psu_type: str) -> dict:
    body = {"access": {"valid_until": valid_until, "balances": True, "transactions": True},
            "aspsp": {"name": aspsp_name, "country": country},
            "state": state, "redirect_url": redirect_url, "psu_type": psu_type}
    return _req("POST", "/auth", json=body)


def create_session(code: str) -> dict:
    return _req("POST", "/sessions", json={"code": code})
