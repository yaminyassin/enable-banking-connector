# enablebanking-mcp (read-only)
Tools: list_accounts, get_balances(account_id), get_transactions(account_id, date_from?, date_to?). No payment endpoints exist in the code.

Env: ENABLEBANKING_APP_ID (JWT kid), ENABLEBANKING_KEY_PATH (default /workspace/enablebanking/private.pem),
ENABLEBANKING_SESSION_ID or ENABLEBANKING_SESSION_FILE (default ~/.enablebanking_session, mode 600), MCP_AUTH_TOKEN (>=32 chars), PORT (8000).

Consent (once, on the box):
  .venv/bin/python -m ebmcp.consent start --aspsp "<Bank name>" --country <CC> --days 90
  # open URL, approve, copy the https://localhost:8443/callback?...code=... URL
  .venv/bin/python -m ebmcp.consent finish --code '<callback URL or code>'

Run: MCP_AUTH_TOKEN=... .venv/bin/python -m ebmcp.server   -> http://host:8000/mcp (streamable HTTP), /healthz unauthenticated.
Offline check: .venv/bin/python verify.py

Azure Container Apps: build the Dockerfile, mount private.pem as a secret volume at /secrets/private.pem,
set MCP_AUTH_TOKEN, ENABLEBANKING_APP_ID, ENABLEBANKING_SESSION_ID as secrets, ingress target port 8000.
