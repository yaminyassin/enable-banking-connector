"""Streamable-HTTP MCP server, read-only. Endpoint: POST/GET /mcp. Health: GET /healthz or /health (Cloud Run reserves /healthz).
Every request except the health paths needs `Authorization: Bearer $MCP_AUTH_TOKEN`."""
import hmac
import os

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse, PlainTextResponse

from . import client

mcp = FastMCP(
    "enablebanking-readonly",
    instructions="Read-only bank data via Enable Banking AIS. Cannot initiate payments.",
    stateless_http=True,
    json_response=True,
    # Host header differs behind Azure ingress; access is gated by the bearer token instead.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
def list_accounts() -> list[dict]:
    """List accounts across all authorized bank sessions (uid, bank, IBAN/identifier, name, currency, type)."""
    out = []
    for sid in client.session_ids():
        s = client.get(f"/sessions/{sid}")
        bank = (s.get("aspsp") or {}).get("name")
        for uid in s.get("accounts", []):
            d = client.get(f"/accounts/{uid}/details")
            out.append({"account_id": uid, "bank": bank, "identifier": d.get("account_id"), "name": d.get("name"),
                        "details": d.get("details"), "product": d.get("product"),
                        "currency": d.get("currency"), "cash_account_type": d.get("cash_account_type")})
    return out


@mcp.tool()
def get_balances(account_id: str) -> dict:
    """Balances for an account uid from list_accounts."""
    return client.get(f"/accounts/{account_id}/balances")


@mcp.tool()
def get_transactions(account_id: str, date_from: str | None = None, date_to: str | None = None) -> dict:
    """Transactions for an account uid. Dates are YYYY-MM-DD (UTC, inclusive). Follows continuation_key pages."""
    txs, key = [], None
    for _ in range(50):
        page = client.get(f"/accounts/{account_id}/transactions",
                          {"date_from": date_from, "date_to": date_to, "continuation_key": key})
        txs += page.get("transactions", [])
        key = page.get("continuation_key")
        if not key:
            break
    return {"count": len(txs), "transactions": txs}


class BearerAuth:
    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] not in ("/healthz", "/health"):
            auth = dict(scope["headers"]).get(b"authorization", b"")
            if not (auth.startswith(b"Bearer ") and hmac.compare_digest(auth[7:], self.token)):
                return await JSONResponse({"error": "unauthorized"}, 401)(scope, receive, send)
        if scope["type"] == "http" and scope["path"] in ("/healthz", "/health"):
            return await PlainTextResponse("ok")(scope, receive, send)
        await self.app(scope, receive, send)


def build_app():
    token = os.environ.get("MCP_AUTH_TOKEN")
    if not token or len(token) < 32:
        raise SystemExit("MCP_AUTH_TOKEN must be set (>= 32 chars)")
    return BearerAuth(mcp.streamable_http_app(), token)


def main():
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                access_log=False)  # access log off: avoid logging anything about callers


if __name__ == "__main__":
    main()
