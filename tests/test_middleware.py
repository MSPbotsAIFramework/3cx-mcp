# Copyright 2026 MSPbots.ai
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Credential middleware: 401 on missing headers, URL normalisation, request isolation."""

import asyncio

import pytest
from starlette.testclient import TestClient

from threecx_mcp.__main__ import build_http_app
from threecx_mcp.auth import GatewayCredentialsMiddleware, _credentials_var, normalize_base_url
from threecx_mcp.server import create_mcp_server

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
CREDS = {
    "X-3CX-Base-URL": "https://pbx.example.com",
    "X-3CX-Client-Id": "client-id",
    "X-3CX-Client-Secret": "client-secret",
}


def _client() -> TestClient:
    return TestClient(build_http_app(create_mcp_server()))


def test_health_is_local():
    with _client() as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-3CX-Base-URL": "https://pbx.example.com"},
        {"X-3CX-Client-Id": "client-id"},
        {"X-3CX-Client-Secret": "client-secret"},
        CREDS | {"X-3CX-Client-Secret": "${THREECX_SECRET}"},
    ],
)
def test_missing_credential_header_returns_401(headers):
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | headers)
    assert resp.status_code == 401
    assert resp.json()["required_headers"] == list(CREDS)


def test_plain_http_base_url_is_rejected():
    with _client() as client:
        resp = client.post(
            "/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | CREDS | {"X-3CX-Base-URL": "http://pbx.example.com"}
        )
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://pbx.example.com", "https://pbx.example.com"),
        ("pbx.example.com", "https://pbx.example.com"),
        ("pbx.example.com/", "https://pbx.example.com"),
        ("https://pbx.example.com/", "https://pbx.example.com"),
        ("http://pbx.example.com", None),
        ("https://", None),
    ],
)
def test_normalize_base_url(value, expected):
    assert normalize_base_url(value) == expected


def test_tools_list_over_http():
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | CREDS)
    assert resp.status_code == 200
    for name in ("threecx_test_connection", "threecx_make_call", "threecx_drop_call"):
        assert name in resp.text


def _run_middleware(headers: list[tuple[bytes, bytes]]) -> dict:
    seen: dict = {}

    async def app(scope, receive, send):
        seen["credentials"] = _credentials_var.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    asyncio.run(GatewayCredentialsMiddleware(app)({"type": "http", "path": "/mcp", "headers": headers}, receive, send))
    return seen


def test_credentials_reach_request_context_and_are_reset():
    seen = _run_middleware(
        [
            (b"x-3cx-base-url", b"https://pbx.example.com/"),
            (b"x-3cx-client-id", b" client-id "),
            (b"x-3cx-client-secret", b"client-secret"),
        ]
    )
    creds = seen["credentials"]
    assert creds.base_url == "https://pbx.example.com"
    assert creds.client_id == "client-id"
    assert creds.client_secret == "client-secret"
    assert _credentials_var.get() is None


def test_concurrent_requests_do_not_leak_credentials_or_tokens():
    """Two interleaved requests with different X-3CX-* headers never see each other's credentials.

    This is the isolation guarantee AC11 depends on: the token exchanged for one
    tenant's secret must never be visible to, or reused by, a concurrent request
    carrying a different tenant's secret.
    """
    observed: list[tuple[str, str]] = []

    async def app(scope, receive, send):
        before = _credentials_var.get().client_secret
        await asyncio.sleep(0.01)
        observed.append((before, _credentials_var.get().client_secret))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    middleware = GatewayCredentialsMiddleware(app)

    def scope(secret: bytes) -> dict:
        return {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"x-3cx-base-url", b"https://pbx.example.com"),
                (b"x-3cx-client-id", b"tenant"),
                (b"x-3cx-client-secret", secret),
            ],
        }

    async def both():
        await asyncio.gather(
            middleware(scope(b"tenant-a-secret"), receive, send),
            middleware(scope(b"tenant-b-secret"), receive, send),
        )

    asyncio.run(both())
    assert sorted(observed) == [("tenant-a-secret", "tenant-a-secret"), ("tenant-b-secret", "tenant-b-secret")]


def test_credentials_repr_hides_secret():
    seen = _run_middleware(
        [
            (b"x-3cx-base-url", b"https://pbx.example.com"),
            (b"x-3cx-client-id", b"client-id"),
            (b"x-3cx-client-secret", b"very-secret-value"),
        ]
    )
    assert "very-secret-value" not in repr(seen["credentials"])
