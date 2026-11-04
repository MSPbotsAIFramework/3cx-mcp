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
"""Connectivity check."""

from mcp.server.fastmcp import FastMCP

from ..client import ThreeCXClient
from ..config import XAPI_PATH
from ._common import READ_ONLY, ClientFactory, run_tool

CHECKED_ENDPOINT = f"{XAPI_PATH}/Defs"


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def threecx_test_connection() -> str:
        """Check the 3CX connection: exchange the client credentials for a token and confirm the PBX answers."""

        async def action(client: ThreeCXClient) -> dict:
            await client.get_xapi("Defs", {"$top": 1})
            return {"status": "ok", "pbx_fqdn": client.instance_url, "checked_endpoint": CHECKED_ENDPOINT}

        return await run_tool("threecx_test_connection", client_factory, action)
