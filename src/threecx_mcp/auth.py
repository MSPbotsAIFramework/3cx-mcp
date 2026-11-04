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
"""Per-request credential parsing and the request-level isolation middleware.

Reads X-3CX-Base-URL / X-3CX-Client-Id / X-3CX-Client-Secret on every /mcp
request; 401 names any missing header, 400 rejects a non-https base URL. The
actual OAuth token exchange happens lazily in ``client.ThreeCXClient`` on first
upstream call, not here -- that keeps a plain ``tools/list`` request (which
never touches the PBX) free of an extra upstream dependency, matching the
shape of every other header-auth connector in the org (e.g. hudu-mcp).
"""

import contextvars
import re
import uuid
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .client import ThreeCXCredentials

HEADER_BASE_URL = "X-3CX-Base-URL"
HEADER_CLIENT_ID = "X-3CX-Client-Id"
HEADER_CLIENT_SECRET = "X-3CX-Client-Secret"
REQUIRED_HEADERS = [HEADER_BASE_URL, HEADER_CLIENT_ID, HEADER_CLIENT_SECRET]

_PLACEHOLDER = re.compile(r"^\$\{.*\}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1"}

# Per-request isolation: the middleware sets these for the duration of one
# HTTP request and resets them in `finally`. Nothing credential-related is
# ever stored at module level (AC11).
_credentials_var: contextvars.ContextVar[ThreeCXCredentials | None] = contextvars.ContextVar(
    "threecx_credentials", default=None
)
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("threecx_request_id", default="-")


def get_credentials_from_context() -> ThreeCXCredentials | None:
    """The caller's credentials for the current request, or None outside a request."""
    return _credentials_var.get()


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or _PLACEHOLDER.match(text):
        return None
    return text


def normalize_base_url(value: str) -> str | None:
    """https origin of the PBX (no path, no trailing slash); None when unusable."""
    text = value.strip()
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    if not parts.hostname:
        return None
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS):
        return None
    return f"{parts.scheme}://{parts.netloc}".rstrip("/")


class GatewayCredentialsMiddleware:
    """Reads the three X-3CX-* headers on /mcp requests; 401 when missing, 400 when not https."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        base = _clean(request.headers.get(HEADER_BASE_URL))
        client_id = _clean(request.headers.get(HEADER_CLIENT_ID))
        client_secret = _clean(request.headers.get(HEADER_CLIENT_SECRET))
        if not (base and client_id and client_secret):
            missing = [
                h
                for h, v in ((HEADER_BASE_URL, base), (HEADER_CLIENT_ID, client_id), (HEADER_CLIENT_SECRET, client_secret))
                if not v
            ]
            await JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": f"Missing header(s): {', '.join(missing)}",
                    "required_headers": REQUIRED_HEADERS,
                },
                status_code=401,
            )(scope, receive, send)
            return
        base_url = normalize_base_url(base)
        if base_url is None:
            await JSONResponse(
                {
                    "error": "Invalid base URL",
                    "message": f"{HEADER_BASE_URL} must be the https URL (or hostname) of the 3CX PBX",
                },
                status_code=400,
            )(scope, receive, send)
            return

        request_id = (request.headers.get("x-request-id") or "").strip()[:64] or uuid.uuid4().hex[:12]
        creds_token = _credentials_var.set(ThreeCXCredentials(base_url, client_id, client_secret))
        rid_token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _credentials_var.reset(creds_token)
            request_id_var.reset(rid_token)
