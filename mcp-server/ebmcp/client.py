"""Read-only Enable Banking (PSD2 AIS) client.

Deliberately has NO payment (PIS) endpoints. Only these calls exist:
  POST /auth, POST /sessions (one-time consent, CLI only)
  GET /sessions/{id}, GET /accounts/{id}/details|balances|transactions
Never log the JWT, the key, or the session id.
"""
import os
import stat
import time
from pathlib import Path

import httpx
import jwt

API = os.environ.get("ENABLEBANKING_API", "https://api.enablebanking.com")
KEY_PATH = os.environ.get("ENABLEBANKING_KEY_PATH", "/workspace/enablebanking/private.pem")
SESSION_FILE = os.environ.get("ENABLEBANKING_SESSION_FILE", str(Path.home() / ".enablebanking_session"))
JWT_TTL = 3600  # docs: max 86400


def make_jwt(app_id: str | None = None, key_path: str | None = None) -> str:
    app_id = app_id or os.environ.get("ENABLEBANKING_APP_ID")
    if not app_id:
        raise RuntimeError("ENABLEBANKING_APP_ID is not set (app not registered yet)")
    key = Path(key_path or KEY_PATH).read_bytes()
    now = int(time.time())
    claims = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + JWT_TTL}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": app_id, "typ": "JWT"})


def session_id() -> str:
    sid = os.environ.get("ENABLEBANKING_SESSION_ID")
    if sid:
        return sid.strip()
    p = Path(SESSION_FILE)
    if p.exists():
        return p.read_text().strip()
    raise RuntimeError("No session: set ENABLEBANKING_SESSION_ID or run `python -m ebmcp.consent`")


def save_session(sid: str) -> None:
    p = Path(SESSION_FILE)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(sid)
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


def _req(method: str, path: str, **kw) -> dict:
    headers = {"Authorization": f"Bearer {make_jwt()}", "Accept": "application/json"}
    r = httpx.request(method, API + path, headers=headers, timeout=60, **kw)
    if r.status_code >= 400:
        # Error bodies are Enable Banking ErrorResponse (no secrets); never echo request headers.
        raise RuntimeError(f"Enable Banking {method} {path.split('?')[0]} -> {r.status_code}: {r.text[:500]}")
    return r.json()


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
