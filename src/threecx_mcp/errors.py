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
"""Shared error envelope and the error-code vocabulary tools return instead of raising."""

from ._json import compact_json

CODES = frozenset(
    {"not_configured", "unauthorized", "not_found", "invalid_argument", "rate_limited", "upstream_error"}
)


def error_envelope(code: str, message: str, retryable: bool) -> str:
    assert code in CODES, f"unknown error code {code!r}"
    return compact_json({"error": {"code": code, "message": message, "retryable": retryable}})
