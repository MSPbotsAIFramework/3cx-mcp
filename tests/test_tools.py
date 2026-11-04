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
"""tools/list snapshot and tool behaviour against a mocked 3CX HTTP layer. No network access."""

import asyncio
import json

import httpx
import pytest

from threecx_mcp import client as client_module
from threecx_mcp.auth import _credentials_var
from threecx_mcp.client import ThreeCXCredentials
from threecx_mcp.server import INSTRUCTIONS, create_mcp_server

CREDS = ThreeCXCredentials("https://pbx.example.com", "client-id", "client-secret")
TOKEN_RESPONSE = {"access_token": "token-abc", "token_type": "Bearer", "expires_in": 3600}

# Tool names are a public contract: existing agents and tenant connections
# depend on them. Changing this snapshot is a breaking change (SOP §13).
EXPECTED_TOOLS: dict[str, set[str]] = {
    "threecx_test_connection": set(),
    "threecx_list_extensions": set(),
    "threecx_get_extension": {"id"},
    "threecx_list_queues": set(),
    "threecx_get_queue": {"id"},
    "threecx_list_active_calls": set(),
    "threecx_get_call_participants": {"dn"},
    "threecx_make_call": {"dn", "destination"},
    "threecx_drop_call": {"dn", "participant_id"},
    "threecx_list_call_history": {"start_date", "end_date"},
    "threecx_list_event_logs": {"start_date", "end_date"},
}


@pytest.fixture(scope="module")
def tools():
    return {t.name: t for t in asyncio.run(create_mcp_server().list_tools())}


def test_tool_names_are_stable(tools):
    assert set(tools) == set(EXPECTED_TOOLS)


def test_instructions_are_reasonably_sized():
    assert 0 < len(INSTRUCTIONS) <= 1500


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(client_module.asyncio, "sleep", instant)


class Recorder:
    """Mock transport: answers /connect/token, then routes by method + path."""

    def __init__(self, routes=None, default=None):
        self.requests: list[httpx.Request] = []
        self.routes = routes or {}
        self.default = default if default is not None else {"value": []}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/connect/token":
            return httpx.Response(200, json=TOKEN_RESPONSE)
        reply = self.routes.get(f"{request.method} {path}", self.routes.get(path, self.default))
        if callable(reply):
            return reply(request)
        return httpx.Response(200, json=reply)

    def calls(self, path: str, method: str | None = None) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path and (method is None or r.method == method)]


def body(request: httpx.Request):
    return json.loads(request.content) if request.content else None


async def _call(monkeypatch, recorder: Recorder, tool: str, args: dict):
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    monkeypatch.setattr(client_module, "_get_shared_http_client", lambda: http)
    ctx = _credentials_var.set(CREDS)
    try:
        content, _ = await create_mcp_server().call_tool(tool, args)
    finally:
        _credentials_var.reset(ctx)
    return json.loads(content[0].text)


# ---- connection -------------------------------------------------------------


async def test_test_connection_tool_reports_ok(monkeypatch):
    rec = Recorder({"GET /xapi/v1/Defs": {"value": [{"Number": "100"}]}})
    result = await _call(monkeypatch, rec, "threecx_test_connection", {})
    assert result == {"status": "ok", "pbx_fqdn": "https://pbx.example.com", "checked_endpoint": "/xapi/v1/Defs"}
    assert rec.calls("/connect/token", "POST")
    token_request = rec.calls("/connect/token", "POST")[0]
    assert "client-secret" in token_request.content.decode()
    # the token is sent back on the follow-up XAPI call, not the raw secret
    xapi_request = rec.calls("/xapi/v1/Defs", "GET")[0]
    assert xapi_request.headers["authorization"] == "Bearer token-abc"


async def test_token_exchanged_once_per_client_instance(monkeypatch):
    """Within one tool call (one client instance), repeated upstream calls reuse the same token."""
    rec = Recorder({"/xapi/v1/Defs": {"value": [{"Number": "100"}]}})
    await _call(monkeypatch, rec, "threecx_list_extensions", {"page_size": 5})
    assert len(rec.calls("/connect/token", "POST")) == 1


# ---- directory ---------------------------------------------------------------


async def test_list_extensions_filters_and_paginates(monkeypatch):
    rows = [{"Number": str(i), "FirstName": "Example"} for i in range(5)]
    rec = Recorder({"/xapi/v1/Defs": {"value": rows}})
    result = await _call(monkeypatch, rec, "threecx_list_extensions", {"name": "Example", "page_size": 5})
    params = rec.calls("/xapi/v1/Defs")[0].url.params
    assert params["$filter"] == "FirstName eq 'Example'"
    assert (params["$top"], params["$skip"]) == ("5", "0")
    assert (result["count"], result["has_more"], result["next_offset"]) == (5, True, 5)
    assert result["items"] == rows


async def test_get_extension_not_found_envelope(monkeypatch):
    rec = Recorder({"/xapi/v1/Defs(999)": lambda r: httpx.Response(404, json={"error": "not found"})})
    result = await _call(monkeypatch, rec, "threecx_get_extension", {"id": "999"})
    assert result["error"]["code"] == "not_found"


async def test_list_and_get_queue(monkeypatch):
    rec = Recorder(
        {
            "/xapi/v1/Queues": {"value": [{"Number": "800", "Name": "Support"}]},
            "/xapi/v1/Queues(800)": {"Number": "800", "Name": "Support"},
        }
    )
    listed = await _call(monkeypatch, rec, "threecx_list_queues", {})
    assert listed["items"] == [{"Number": "800", "Name": "Support"}]
    fetched = await _call(monkeypatch, rec, "threecx_get_queue", {"id": "800"})
    assert fetched == {"Number": "800", "Name": "Support"}


# ---- calls --------------------------------------------------------------------


async def test_list_active_calls_filters_by_dn(monkeypatch):
    rec = Recorder({"/xapi/v1/ActiveCalls": {"value": [{"Id": 1, "Dn": "100"}]}})
    result = await _call(monkeypatch, rec, "threecx_list_active_calls", {"dn": "100"})
    assert result["items"] == [{"Id": 1, "Dn": "100"}]
    assert rec.calls("/xapi/v1/ActiveCalls")[0].url.params["$filter"] == "Dn eq '100'"


async def test_get_call_participants(monkeypatch):
    rec = Recorder({"/callcontrol/100/participants": {"value": [{"id": 7, "status": "Connected"}]}})
    result = await _call(monkeypatch, rec, "threecx_get_call_participants", {"dn": "100"})
    assert result["items"] == [{"id": 7, "status": "Connected"}]


async def test_make_call_sends_destination_and_timeout(monkeypatch):
    rec = Recorder({"POST /callcontrol/100/makecall": {"id": "p1", "status": "ringing"}})
    result = await _call(monkeypatch, rec, "threecx_make_call", {"dn": "100", "destination": "555", "timeout": 45})
    assert result == {"id": "p1", "status": "ringing"}
    assert body(rec.calls("/callcontrol/100/makecall", "POST")[0]) == {"destination": "555", "timeout": 45}


async def test_drop_call_requires_confirm(monkeypatch):
    rec = Recorder()
    result = await _call(monkeypatch, rec, "threecx_drop_call", {"dn": "100", "participant_id": "5"})
    assert result["done"] is False
    assert not rec.calls("/callcontrol/100/participants/5/drop")
    result = await _call(monkeypatch, rec, "threecx_drop_call", {"dn": "100", "participant_id": "5", "confirm": True})
    assert result["done"] is True
    assert rec.calls("/callcontrol/100/participants/5/drop", "POST")


# ---- history --------------------------------------------------------------------


async def test_call_history_and_event_logs_date_range(monkeypatch):
    rec = Recorder(
        {
            "/xapi/v1/CallHistoryView": {"value": [{"CallerID": "100", "Callee": "200"}]},
            "/xapi/v1/EventLogs": {"value": [{"EventType": "Login"}]},
        }
    )
    history = await _call(
        monkeypatch,
        rec,
        "threecx_list_call_history",
        {"start_date": "2026-11-01T00:00:00Z", "end_date": "2026-11-02T00:00:00Z", "caller": "100"},
    )
    assert history["items"] == [{"CallerID": "100", "Callee": "200"}]
    params = rec.calls("/xapi/v1/CallHistoryView")[0].url.params
    assert "SegmentStartTime ge 2026-11-01T00:00:00Z" in params["$filter"]
    assert "CallerID eq '100'" in params["$filter"]

    logs = await _call(
        monkeypatch,
        rec,
        "threecx_list_event_logs",
        {"start_date": "2026-11-01T00:00:00Z", "end_date": "2026-11-02T00:00:00Z"},
    )
    assert logs["items"] == [{"EventType": "Login"}]


# ---- retries and error mapping -------------------------------------------------


async def test_read_retries_on_429_and_5xx_write_does_not(monkeypatch):
    replies = iter([httpx.Response(502), httpx.Response(200, json={"value": []})])
    rec = Recorder(
        {
            "GET /xapi/v1/ActiveCalls": lambda r: next(replies),
            "POST /callcontrol/100/makecall": lambda r: httpx.Response(500, json={"error": "pbx busy"}),
        }
    )
    await _call(monkeypatch, rec, "threecx_list_active_calls", {})
    assert len(rec.calls("/xapi/v1/ActiveCalls", "GET")) == 2

    result = await _call(monkeypatch, rec, "threecx_make_call", {"dn": "100", "destination": "555"})
    assert result["error"]["code"] == "upstream_error"
    assert len(rec.calls("/callcontrol/100/makecall", "POST")) == 1


async def test_write_is_not_retried_on_429_either(monkeypatch):
    rec = Recorder({"POST /callcontrol/100/participants/5/drop": lambda r: httpx.Response(429, json={})})
    result = await _call(monkeypatch, rec, "threecx_drop_call", {"dn": "100", "participant_id": "5", "confirm": True})
    assert result["error"]["code"] == "rate_limited"
    assert len(rec.calls("/callcontrol/100/participants/5/drop", "POST")) == 1


async def test_missing_credentials_return_not_configured_envelope(monkeypatch):
    http = httpx.AsyncClient(transport=httpx.MockTransport(Recorder()))
    monkeypatch.setattr(client_module, "_get_shared_http_client", lambda: http)
    content, _ = await create_mcp_server().call_tool("threecx_test_connection", {})
    result = json.loads(content[0].text)
    assert result["error"]["code"] == "not_configured"


async def test_invalid_argument_from_pydantic_uses_error_envelope(monkeypatch):
    rec = Recorder()
    ctx = _credentials_var.set(CREDS)
    try:
        content, _ = await create_mcp_server().call_tool("threecx_make_call", {"dn": "", "destination": "555"})
    finally:
        _credentials_var.reset(ctx)
    result = json.loads(content[0].text)
    assert result["error"]["code"] == "invalid_argument"
