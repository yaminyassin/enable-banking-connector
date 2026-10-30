"""One-time consent flow (CLI).

  python -m ebmcp.consent start --aspsp "Revolut" --country LT [--days 180] [--psu-type personal]
      -> prints the bank URL. Open it, approve, then copy the `code` from the
         https://localhost:8443/callback?...&code=... address bar (page will not load; that's fine).
  python -m ebmcp.consent finish --code <code>   (or paste the whole callback URL)
      -> exchanges code for a session, saves session_id to ~/.enablebanking_session.<bank-slug>
         (mode 600; does not overwrite other banks), prints account names only (never the session id).
"""
import argparse
import secrets
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from . import client

REDIRECT = "https://localhost:8443/callback"


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--aspsp", required=True)
    s.add_argument("--country", required=True)
    s.add_argument("--days", type=int, default=90)
    s.add_argument("--psu-type", default="personal", choices=["personal", "business"])
    s.add_argument("--redirect", default=REDIRECT)
    f = sub.add_parser("finish")
    f.add_argument("--code", required=True, help="code value or full callback URL")
    a = p.parse_args()

    if a.cmd == "start":
        until = (datetime.now(timezone.utc) + timedelta(days=a.days)).isoformat()
        r = client.start_auth(a.aspsp, a.country, a.redirect, until, secrets.token_urlsafe(16), a.psu_type)
        print("Open this URL and approve access:\n" + r["url"])
    else:
        code = a.code
        if code.startswith("http"):
            q = parse_qs(urlparse(code).query)
            if "error" in q:
                sys.exit(f"Bank returned error: {q.get('error')} {q.get('error_description')}")
            code = q["code"][0]
        r = client.create_session(code)
        bank = (r.get("aspsp") or {}).get("name") or "unknown"
        path = f"{client.SESSION_FILE}.{client.bank_slug(bank)}"
        saved = client.save_session(r["session_id"], path)
        print(f"Session saved to {saved} (mode 600). Bank={bank}. Valid until {r['access'].get('valid_until')}.")
        for acc in r.get("accounts", []) + r.get("accounts_data", []):
            if isinstance(acc, dict):
                print(f"- {acc.get('name') or ''} {acc.get('currency')} {acc.get('cash_account_type')}")


if __name__ == "__main__":
    main()
