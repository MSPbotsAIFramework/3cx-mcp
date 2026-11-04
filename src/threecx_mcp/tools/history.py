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
"""Historical call records and PBX event logs (3CX Configuration XAPI)."""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..client import ThreeCXClient
from ._common import Offset, Page, PageSize, READ_ONLY, ClientFactory, list_page, run_tool

DateStr = Annotated[str, Field(description="ISO 8601 date/time, e.g. '2026-11-01T00:00:00Z'.", min_length=1)]
Caller = Annotated[str | None, Field(description="Exact caller number.")]
Callee = Annotated[str | None, Field(description="Exact callee number.")]


def _date_range_filter(start_date: str, end_date: str, **eq: str | None) -> str:
    clauses = [f"SegmentStartTime ge {start_date}", f"SegmentStartTime le {end_date}"]
    clauses += [f"{field} eq '{value}'" for field, value in eq.items() if value]
    return " and ".join(clauses)


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def threecx_list_call_history(
        start_date: DateStr,
        end_date: DateStr,
        caller: Caller = None,
        callee: Callee = None,
        page: Page = None,
        page_size: PageSize = None,
        offset: Offset = None,
    ) -> str:
        """List historical calls in a date range; filter by caller or callee number."""

        async def action(client: ThreeCXClient) -> dict:
            filter_clause = _date_range_filter(start_date, end_date, CallerID=caller, Callee=callee)
            return await list_page(client, "CallHistoryView", {"$filter": filter_clause}, page, page_size, offset)

        return await run_tool("threecx_list_call_history", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def threecx_list_event_logs(
        start_date: DateStr,
        end_date: DateStr,
        page: Page = None,
        page_size: PageSize = None,
        offset: Offset = None,
    ) -> str:
        """List PBX event-log entries in a date range."""

        async def action(client: ThreeCXClient) -> dict:
            filter_clause = _date_range_filter(start_date, end_date)
            return await list_page(client, "EventLogs", {"$filter": filter_clause}, page, page_size, offset)

        return await run_tool("threecx_list_event_logs", client_factory, action)
