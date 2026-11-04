# 3CX MCP Server

An [MCP](https://modelcontextprotocol.io) server for [3CX](https://www.3cx.com), a PBX phone
system. It lets an AI agent look up the extension and queue directory, see currently
in-progress calls and their participants, read call history and PBX event logs, and (on
explicit request) place and end calls.

- Transport: MCP Streamable HTTP at `POST /mcp`, health probe at `GET /health`
- Stateless and multi-tenant: credentials arrive on every request as HTTP headers and are
  never stored; a fresh 3CX OAuth token is exchanged per request, never cached or shared
  across requests
- Python 3.12, [uv](https://docs.astral.sh/uv/), FastMCP, httpx
- License: Apache-2.0, Copyright 2026 MSPbots.ai

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Authentication headers

| Header | Required | Type | Default | Allowed values | Description | Example |
|---|---|---|---|---|---|---|
| `X-3CX-Base-URL` | Yes | string | none | https URL | Root URL of the 3CX PBX (a bare host gets `https://`) | `https://pbx.example.com` |
| `X-3CX-Client-Id` | Yes | string | none | - | API Client ID created in the PBX | `your-3cx-client-id` |
| `X-3CX-Client-Secret` | Yes | string | none | - | API Client Secret for the same client | `your-3cx-client-secret` |

Where to get them: in the 3CX Management Console open **Settings → Integrations → API** and
create an API Client. Some read endpoints (call history reporting) need the client to hold the
PBX's System Owner role, not just System Admin. If the PBX restricts access by IP, allow the
public egress IP of the environment that runs this server.

How credentials are used: on first use per request, the Client ID/Secret pair is exchanged for
a bearer token via `POST {X-3CX-Base-URL}/connect/token` (OAuth2 `client_credentials` grant).
The resulting token is used for the rest of that request's upstream calls and is never cached
or reused across requests.

A request to `/mcp` without all three headers gets HTTP 401 naming the missing one(s); a
non-https base URL gets HTTP 400.

## Tools

All tools return a JSON string. List tools take `page` and `page_size` (default 25, max 200)
and return `{count, page, has_more, next_page, offset, next_offset, items}`. They also take
`offset` (a 0-based record position that overrides `page`): pass `next_offset` to continue, in
case a page was cut to fit the response size limit. **write** marks tools that change state on
the PBX; **confirm** marks the one tool that also needs `confirm: true`.

| Area | Tools |
|---|---|
| Connection | `threecx_test_connection` |
| Directory | `threecx_list_extensions` (number, name, department), `threecx_get_extension` (**id**), `threecx_list_queues` (number, name), `threecx_get_queue` (**id**) |
| Live calls | `threecx_list_active_calls` (dn), `threecx_get_call_participants` (**dn**); **write**: `threecx_make_call` (**dn**, **destination**, timeout); **confirm**: `threecx_drop_call` (**dn**, **participant_id**) |
| History | `threecx_list_call_history` (**start_date**, **end_date**, caller, callee), `threecx_list_event_logs` (**start_date**, **end_date**) |

Errors come back as a JSON envelope instead of an exception:

```json
{"error": {"code": "not_found", "message": "3CX returned HTTP 404 for /xapi/v1/Defs(999)", "retryable": false}}
```

`code` is one of `not_configured`, `unauthorized`, `not_found`, `invalid_argument`,
`rate_limited`, `upstream_error`. Reads are retried with backoff on 429, 5xx and network
errors; writes (`threecx_make_call`, `threecx_drop_call`) are never auto-retried, so a retried
request can never double-dial or double-drop a live call.

## Run locally

```bash
uv sync
uv run python -m threecx_mcp          # listens on 0.0.0.0:8080
```

or with Docker:

```bash
docker build --platform linux/amd64 -t threecx-mcp:dev .
docker run --rm -p 8080:8080 threecx-mcp:dev
```

Configuration (environment variables, all optional): `MCP_HTTP_HOST` (default `0.0.0.0`),
`MCP_HTTP_PORT` (default `8080`), `LOG_LEVEL` (default `INFO`). Credentials are never read
from the environment.

## Testing example

```bash
# list tools
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-3CX-Base-URL: https://pbx.example.com" \
  -H "X-3CX-Client-Id: your-3cx-client-id" \
  -H "X-3CX-Client-Secret: your-3cx-client-secret" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## Tests

```bash
uv sync
uv run pytest
```

`tests/test_middleware.py` covers the credential header contract and request isolation (no
network access, no real PBX). `tests/test_tools.py` covers every tool against a mocked 3CX HTTP
layer: `tools/list` snapshot, pagination, error-code mapping, the `threecx_drop_call` confirm
gate, and the read-retries/write-does-not-retry contract. Tools that need a live PBX
(`threecx_list_active_calls` / `threecx_get_call_participants` / `threecx_make_call` /
`threecx_drop_call` against real call state) are listed in `docs/tickets/PRD-21277.ac.json` as
needing a real vendor account; they do not block delivery (no test account is available).
