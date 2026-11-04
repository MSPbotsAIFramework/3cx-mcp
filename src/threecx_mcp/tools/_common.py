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
"""Shared argument types, OData pagination, the confirm gate and the error-handling wrapper."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..client import ThreeCXClient, ThreeCXError
from ..config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from ..errors import error_envelope

logger = logging.getLogger("threecx_mcp.tools")

ClientFactory = Callable[[], ThreeCXClient | None]

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)

NOT_CONFIGURED = error_envelope(
    "not_configured",
    "3CX credentials are missing for this request (X-3CX-Base-URL, X-3CX-Client-Id, X-3CX-Client-Secret).",
    False,
)

Page = Annotated[int | None, Field(description="Page number, from 1 (default 1).", ge=1)]
PageSize = Annotated[
    int | None, Field(description=f"Records per page (default {DEFAULT_PAGE_SIZE}, max {MAX_PAGE_SIZE}).", ge=1, le=MAX_PAGE_SIZE)
]
Offset = Annotated[
    int | None,
    Field(description="Record position to start from (0-based, overrides page); pass next_offset from the previous result.", ge=0),
]
Confirm = Annotated[bool | None, Field(description="Must be true, set only after the user approved ending this call.")]


def compact(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


def odata_filter(**eq: str | None) -> str | None:
    """`field eq 'value'` clauses joined with `and`, skipping unset fields."""
    clauses = [f"{field} eq '{value}'" for field, value in eq.items() if value]
    return " and ".join(clauses) or None


def not_confirmed(action: str) -> dict:
    return {"done": False, "message": f"Not done: pass confirm: true to {action}."}


async def list_page(
    client: ThreeCXClient,
    entity: str,
    params: dict[str, Any],
    page: int | None,
    page_size: int | None,
    offset: int | None = None,
) -> dict:
    """GET an OData v4 entity and return the org's page/page_size/offset pagination envelope.

    Unlike page-number-only APIs, OData's $skip/$top take any start position
    directly, so (unlike hudu-mcp's list_page) no extra re-slicing is needed
    once the page is fetched.
    """
    size = page_size or DEFAULT_PAGE_SIZE
    start = offset if offset is not None else ((page or 1) - 1) * size
    number = start // size + 1
    payload = await client.get_xapi(entity, compact(params) | {"$top": size, "$skip": start})
    items = payload.get("value") if isinstance(payload, dict) else payload
    items = items if isinstance(items, list) else []
    more = len(items) >= size
    return compact(
        {
            "items": items,
            "count": len(items),
            "page": number,
            "has_more": more,
            "next_page": number + 1 if more else None,
            "offset": start,
            "next_offset": start + len(items) if more else None,
        }
    )


async def run_tool(
    name: str,
    client_factory: ClientFactory,
    action: Callable[[ThreeCXClient], Awaitable[Any]],
) -> str:
    """Resolve the caller's client, run ``action``, and always return a string.

    Errors never escape as exceptions: they become the standard error envelope.
    """
    client = client_factory()
    if client is None:
        return NOT_CONFIGURED
    started = time.monotonic()
    try:
        result = await action(client)
    except ThreeCXError as e:
        logger.info("tool=%s outcome=error status=%s ms=%d", name, e.status_code, _ms(started))
        return e.to_envelope()
    except ValueError as e:
        logger.info("tool=%s outcome=invalid_argument ms=%d", name, _ms(started))
        return error_envelope("invalid_argument", str(e), False)
    except Exception as e:  # noqa: BLE001 - tools must never raise
        logger.warning("tool=%s outcome=unexpected error=%s ms=%d", name, type(e).__name__, _ms(started))
        return error_envelope("upstream_error", f"Unexpected error: {type(e).__name__}", True)
    logger.info("tool=%s outcome=ok ms=%d", name, _ms(started))
    if result is None:
        result = {"done": True}
    return result if isinstance(result, str) else dump_json_capped(result)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
