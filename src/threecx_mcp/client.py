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
"""Async client for 3CX's Configuration XAPI (OData v4) and Call Control REST API.

Authentication is a PBX-local OAuth2 client_credentials exchange
(``POST {base_url}/connect/token``), not a static API key. Each ``ThreeCXClient``
instance exchanges its own token lazily on first use and reuses it for the rest
of its own lifetime; nothing is cached beyond one instance. A fresh instance is
built per incoming MCP request (see ``server.get_client_from_context``), so
this gives "one token exchange per request" without any cross-request or
cross-tenant cache (PRD-21277 tech design: no token caching in v1).
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import CALLCONTROL_PATH, XAPI_PATH
from .errors import error_envelope

logger = logging.getLogger("threecx_mcp.api")

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_MAX_RETRIES = 2
_MAX_BACKOFF_SECONDS = 20.0
# Whole-call budget including retries; the gateway times a tool call out at 120s.
_CALL_BUDGET_SECONDS = 100.0

# One connection pool for the process. It never holds credentials: the
# Authorization header is built per request from the caller's own token, so
# sharing the pool across tenants is safe.
_shared_http_client: httpx.AsyncClient | None = None


def _get_shared_http_client() -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _shared_http_client


# status_code -> (error code, retryable). 0 means no HTTP response at all
# (connection error or timeout).
_STATUS_TO_CODE: dict[int, tuple[str, bool]] = {
    0: ("upstream_error", True),
    400: ("invalid_argument", False),
    401: ("unauthorized", False),
    403: ("unauthorized", False),
    404: ("not_found", False),
    409: ("invalid_argument", False),
    422: ("invalid_argument", False),
    429: ("rate_limited", True),
}


def classify_status(status_code: int) -> tuple[str, bool]:
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code >= 500:
        return "upstream_error", True
    if status_code >= 400:
        return "invalid_argument", False
    return "upstream_error", True


class ThreeCXError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"3CX API error {status_code}: {message}")

    def to_envelope(self) -> str:
        code, retryable = classify_status(self.status_code)
        return error_envelope(code, self.message, retryable)


@dataclass(frozen=True)
class ThreeCXCredentials:
    base_url: str
    client_id: str
    client_secret: str

    def __repr__(self) -> str:  # never leak secrets into logs or tracebacks
        return f"ThreeCXCredentials(base_url={self.base_url!r}, client_id=<redacted>, client_secret=<redacted>)"


class ThreeCXClient:
    """Thin wrapper over 3CX's XAPI + Call Control REST API for one caller's PBX."""

    def __init__(self, credentials: ThreeCXCredentials, http_client: httpx.AsyncClient | None = None):
        self._credentials = credentials
        self._http = http_client
        self.instance_url = credentials.base_url.rstrip("/")
        self._token: str | None = None
        self._token_lock = asyncio.Lock()

    async def _ensure_token(self) -> str:
        if self._token is not None:
            return self._token
        async with self._token_lock:
            if self._token is not None:
                return self._token
            http = self._http or _get_shared_http_client()
            try:
                resp = await http.post(
                    f"{self.instance_url}/connect/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._credentials.client_id,
                        "client_secret": self._credentials.client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.TimeoutException:
                raise ThreeCXError(0, "Timed out exchanging the 3CX client credentials for a token") from None
            except httpx.RequestError as e:
                raise ThreeCXError(
                    0, f"Network error reaching {self.instance_url}/connect/token: {type(e).__name__}"
                ) from None
            if resp.status_code >= 400:
                raise ThreeCXError(resp.status_code, _error_message(resp, "connect/token"))
            try:
                payload = resp.json()
            except ValueError:
                raise ThreeCXError(502, "3CX token endpoint did not return JSON") from None
            token = payload.get("access_token") if isinstance(payload, dict) else None
            if not token:
                raise ThreeCXError(502, "3CX token endpoint response had no access_token")
            self._token = token
            return token

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return (await self.request("GET", path, params=params))[0]

    async def post(self, path: str, body: Any = None) -> Any:
        return (await self.request("POST", path, body=body))[0]

    async def get_xapi(self, entity: str, params: dict[str, Any] | None = None) -> Any:
        return await self.get(f"{XAPI_PATH}/{entity}", params)

    async def get_callcontrol(self, dn: str, suffix: str = "", params: dict[str, Any] | None = None) -> Any:
        path = f"{CALLCONTROL_PATH}/{dn}" + (f"/{suffix}" if suffix else "")
        return await self.get(path, params)

    async def post_callcontrol(self, dn: str, suffix: str, body: Any = None) -> Any:
        return await self.post(f"{CALLCONTROL_PATH}/{dn}/{suffix}", body)

    async def request(
        self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None
    ) -> tuple[Any, httpx.Response]:
        token = await self._ensure_token()
        http = self._http or _get_shared_http_client()
        url = f"{self.instance_url}{path}"
        query = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        started = time.monotonic()
        attempt = 0
        while True:
            t0 = time.monotonic()
            resp: httpx.Response | None = None
            error: ThreeCXError | None = None
            try:
                resp = await http.request(method, url, params=query, json=body, headers=headers)
                status = resp.status_code
            except httpx.TimeoutException:
                status, error = 0, ThreeCXError(0, f"Timed out calling 3CX ({method} {path})")
            except httpx.RequestError as e:
                status, error = 0, ThreeCXError(0, f"Network error calling 3CX ({method} {path}): {type(e).__name__}")

            logger.info(
                "threecx_api method=%s path=%s status=%s ms=%d attempt=%d",
                method, path, status, (time.monotonic() - t0) * 1000, attempt,
            )

            # Writes (POST/PUT/DELETE) are never auto-retried, even on 429 (AC12):
            # a retried write could double-dial or double-drop a live call.
            retryable = method == "GET" and (status == 429 or status == 0 or status >= 500)
            if retryable and attempt < _MAX_RETRIES:
                delay = _retry_delay(resp, attempt)
                if time.monotonic() - started + delay < _CALL_BUDGET_SECONDS:
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
            if error is not None:
                raise error
            assert resp is not None
            if resp.status_code >= 400:
                raise ThreeCXError(resp.status_code, _error_message(resp, path))
            if not resp.content or not resp.content.strip():
                return None, resp
            try:
                return resp.json(), resp
            except ValueError:
                return {"raw_response": resp.text[:2000]}, resp


def _retry_delay(resp: httpx.Response | None, attempt: int) -> float:
    if resp is not None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), _MAX_BACKOFF_SECONDS))
            except ValueError:
                pass
    return min(float(2**attempt), _MAX_BACKOFF_SECONDS)


def _error_message(resp: httpx.Response, path: str) -> str:
    fallback = f"3CX returned HTTP {resp.status_code} for {path}"
    try:
        payload = resp.json()
    except ValueError:
        text = resp.text.strip()
        if text.lower().startswith("<"):
            return fallback  # an HTML error page, not useful to the agent
        return f"{fallback}: {text[:300]}" if text else fallback
    if isinstance(payload, dict):
        message = payload.get("error_description") or payload.get("error") or payload.get("message")
        if isinstance(message, dict):
            message = message.get("message") or str(message)
        if message:
            return f"{fallback}: {str(message)[:500]}"
    return fallback
