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
"""FastMCP factory: builds the server and registers every tool module."""

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ._arguments import EnvelopeFastMCP
from .auth import get_credentials_from_context
from .client import ThreeCXClient
from .config import Settings

INSTRUCTIONS = (
    "3CX is a PBX phone system. This connects to one 3CX instance per request "
    "(identified by the three X-3CX-* credential headers) and covers directory "
    "lookups, live call visibility and the two most common call-control "
    "actions.\n"
    "Usage:\n"
    "- threecx_test_connection first confirms the credentials and PBX are reachable.\n"
    "- Directory: threecx_list_extensions / threecx_get_extension, "
    "threecx_list_queues / threecx_get_queue.\n"
    "- Live calls: threecx_list_active_calls (optionally filtered by dn), "
    "threecx_get_call_participants(dn) for one call's participants.\n"
    "- History: threecx_list_call_history / threecx_list_event_logs, both take "
    "a start_date/end_date range.\n"
    "- Lists page with page + page_size (or offset); has_more says whether to "
    "page on.\n"
    "threecx_make_call places a call and threecx_drop_call ends one; call them "
    "only when the user explicitly asks. threecx_drop_call also needs "
    "confirm: true, since ending a live call is irreversible from here."
)


def get_client_from_context() -> ThreeCXClient | None:
    """The client for the current request, or None outside a request."""
    credentials = get_credentials_from_context()
    if credentials is None:
        return None
    return ThreeCXClient(credentials)


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    """Build the FastMCP server and register every tool."""
    mcp = EnvelopeFastMCP(
        name="3cx-mcp",
        instructions=INSTRUCTIONS,
        # The service sits behind the gateway, so Host is never localhost;
        # DNS-rebinding protection would reject those requests with 421.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
    )

    client_factory: Callable[[], ThreeCXClient | None] = get_client_from_context

    from .tools import calls, connection, directory, history

    for module in (connection, directory, calls, history):
        module.register(mcp, client_factory)

    return mcp
