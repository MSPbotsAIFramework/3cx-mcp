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
"""Extension and queue directory lookups (3CX Configuration XAPI)."""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..client import ThreeCXClient
from ._common import Offset, Page, PageSize, READ_ONLY, ClientFactory, list_page, odata_filter, run_tool

Number = Annotated[str | None, Field(description="Exact extension/queue number.")]
Name = Annotated[str | None, Field(description="Exact display name.")]
Department = Annotated[str | None, Field(description="Exact department.")]
RecordId = Annotated[str, Field(description="3CX id of the extension or queue.", min_length=1)]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def threecx_list_extensions(
        number: Number = None,
        name: Name = None,
        department: Department = None,
        page: Page = None,
        page_size: PageSize = None,
        offset: Offset = None,
    ) -> str:
        """List PBX extensions (number, name, department, mobile); filter by number, name or department."""

        async def action(client: ThreeCXClient) -> dict:
            filter_clause = odata_filter(Number=number, FirstName=name, Department=department)
            return await list_page(client, "Defs", {"$filter": filter_clause}, page, page_size, offset)

        return await run_tool("threecx_list_extensions", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def threecx_get_extension(id: RecordId) -> str:
        """Get one extension by id."""

        async def action(client: ThreeCXClient) -> Any:
            return await client.get_xapi(f"Defs({id})")

        return await run_tool("threecx_get_extension", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def threecx_list_queues(
        number: Number = None,
        name: Name = None,
        page: Page = None,
        page_size: PageSize = None,
        offset: Offset = None,
    ) -> str:
        """List call queues (number, name); filter by number or name."""

        async def action(client: ThreeCXClient) -> dict:
            filter_clause = odata_filter(Number=number, Name=name)
            return await list_page(client, "Queues", {"$filter": filter_clause}, page, page_size, offset)

        return await run_tool("threecx_list_queues", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def threecx_get_queue(id: RecordId) -> str:
        """Get one call queue by id."""

        async def action(client: ThreeCXClient) -> Any:
            return await client.get_xapi(f"Queues({id})")

        return await run_tool("threecx_get_queue", client_factory, action)
