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
"""Live call visibility and call-control actions (3CX Call Control API)."""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..client import ThreeCXClient
from ._common import DESTRUCTIVE, READ_ONLY, WRITES, ClientFactory, Confirm, compact, not_confirmed, run_tool

Dn = Annotated[str, Field(description="Extension number (DN), e.g. '100'.", min_length=1)]
DnFilter = Annotated[str | None, Field(description="Only calls on this extension number (DN).")]
Destination = Annotated[str, Field(description="Number or extension to dial.", min_length=1)]
Timeout = Annotated[int | None, Field(description="Seconds to let the destination ring before giving up (default 30).", ge=1, le=120)]
ParticipantId = Annotated[
    str, Field(description="Participant id returned by threecx_make_call or threecx_get_call_participants.", min_length=1)
]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def threecx_list_active_calls(dn: DnFilter = None) -> str:
        """List currently in-progress calls across the PBX, optionally filtered to one extension (dn)."""

        async def action(client: ThreeCXClient) -> dict:
            filter_clause = f"Dn eq '{dn}'" if dn else None
            payload = await client.get_xapi("ActiveCalls", compact({"$filter": filter_clause}))
            items = payload.get("value") if isinstance(payload, dict) else payload
            items = items if isinstance(items, list) else []
            return {"items": items, "count": len(items)}

        return await run_tool("threecx_list_active_calls", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def threecx_get_call_participants(dn: Dn) -> str:
        """List the participants of the active call on one extension (dn)."""

        async def action(client: ThreeCXClient) -> Any:
            payload = await client.get_callcontrol(dn, "participants")
            items = payload.get("value") if isinstance(payload, dict) else payload
            return {"items": items if isinstance(items, list) else []}

        return await run_tool("threecx_get_call_participants", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def threecx_make_call(dn: Dn, destination: Destination, timeout: Timeout = None) -> str:
        """Place a call from one extension (dn) to a destination number. Only when the user explicitly asks."""

        async def action(client: ThreeCXClient) -> Any:
            body = compact({"destination": destination, "timeout": timeout or 30})
            return await client.post_callcontrol(dn, "makecall", body)

        return await run_tool("threecx_make_call", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def threecx_drop_call(dn: Dn, participant_id: ParticipantId, confirm: Confirm = None) -> str:
        """End a call's participant on one extension (dn). Irreversible from here; needs confirm: true.

        Only when the user explicitly asks.
        """

        async def action(client: ThreeCXClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"end participant {participant_id} on extension {dn}")
            await client.post_callcontrol(dn, f"participants/{participant_id}/drop")
            return {"done": True}

        return await run_tool("threecx_drop_call", client_factory, action)
