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
"""Token-economical JSON serialization for tool results.

Compact (no indent), non-ASCII preserving, and capped at MAX_CHARS so one tool
call can never flood the agent's context.

This is a single-step-truncation variant of the capping algorithm used by
other MSPbotsAIFramework connectors (e.g. hudu-mcp): it binary-searches how
many records of the largest list field fit, without also shortening long text
fields first. 3CX directory/call records are small (ids, numbers, names), so
the extra per-field shrinking step those connectors need for long article
bodies does not apply here (PRD-21277 tech design, Reuse & cleanup).
"""

import json
from typing import Any

MAX_CHARS = 20_000

# Fields that point to the next page; a cut page would make them skip records.
_CONTINUATION_KEYS = frozenset({"next_page", "next_offset"})


def compact_json(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str)


def dump_json_capped(data: Any, max_chars: int = MAX_CHARS) -> str:
    """Serialize ``data`` within ``max_chars``, truncating the largest list field if needed.

    When the full result is too large, the biggest list-valued field is cut down
    (via binary search) to the most records that still fit, and the response is
    marked ``truncated``/``retry_page_size`` so the caller can narrow the page size
    instead of silently losing records off the end.
    """
    text = compact_json(data)
    if len(text) <= max_chars:
        return text

    if isinstance(data, dict):
        list_keys = [k for k, v in data.items() if isinstance(v, list)]
        if list_keys:
            key = max(list_keys, key=lambda k: len(compact_json(data[k])))
            items = data[key]

            def build(n: int) -> dict:
                candidate = {k: v for k, v in data.items() if k not in _CONTINUATION_KEYS}
                candidate[key] = items[:n]
                candidate["count"] = n
                candidate["original_count"] = len(items)
                candidate["has_more"] = True
                candidate["truncated"] = True
                candidate["truncated_field"] = key
                candidate["retry_page_size"] = max(n, 1)
                candidate["note"] = (
                    f"Only {n} of {len(items)} records fit the response size limit; "
                    f"retry with a page size of {max(n, 1)} or narrower filters."
                )
                return candidate

            lo, hi = 0, len(items)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if len(compact_json(build(mid))) <= max_chars:
                    lo = mid
                else:
                    hi = mid - 1
            return compact_json(build(lo))

    return compact_json(
        {
            "truncated": True,
            "note": (
                f"Result too large to return ({len(text)} chars); narrow the query "
                "with filters or a smaller page size."
            ),
        }
    )
