# PRD-21277 · Code review

## Round 1 · 2026-11-04 · reviewed dev/PRD-21277 @ 1a8920a (base a2141a5)

Verdict: changes requested

### Findings

1. [must-fix] `src/threecx_mcp/tools/_common.py:60` — `odata_filter()` builds OData v4
   `$filter` clauses (`field eq 'value'`) by naive string interpolation of caller-supplied
   strings (`name`/`department`/`number` from `threecx_list_extensions`/`threecx_list_queues`
   in `tools/directory.py`), and the same unescaped pattern is duplicated in
   `tools/calls.py:39` (`dn` filter for `threecx_list_active_calls`) and
   `tools/history.py:30-32` (`_date_range_filter`'s `caller`/`callee` clauses). OData string
   literals require doubling an embedded `'` to `''`; this code does none of that. A value
   containing a quote (e.g. a legitimate name like `O'Brien`) breaks the filter clause and
   returns a 3CX API error instead of the AC3-required match — a correctness bug — and a
   deliberately crafted value (e.g. `x' or Number ne '`) rewrites the filter's boolean logic
   sent to the live PBX, which is OData filter injection, not just a formatting edge case.
   `tests/test_tools.py` has no case with a quote in any filter value, so the gap is untested.
   Fix: escape embedded `'` (double it) before interpolating any caller-supplied value in
   `odata_filter()` and `_date_range_filter()`, ideally via one shared escaping helper so every
   call site is covered instead of relying on each tool remembering to do it.

2. [must-fix] `docs/tickets/PRD-21277.md:457,462` — the repository is not Chinese-free at
   `1a8920a`: the Self-test section quotes Chinese skill-doc phrases
   ("没有测试账号也照常发 INT", "三份产物") inside `docs/tickets/PRD-21277.md`, which lives in
   `MSPbotsAIFramework/3cx-mcp` and must be English-only (SOP §0.4; `mcp-track.md` §2.4 "只写
   英文的 docs/tickets/PRD-XXXXX.md"; review checklist §4.3 "仓库里没有中文"). This also means
   the Self-test section's own claim — "No Chinese anywhere: ... — no output" — does not hold
   against the committed HEAD; the grep was evidently run before the doc commit that added
   these quotes. Fix: rephrase both lines in English only (describe or paraphrase the SOP rule
   instead of quoting the Chinese original), re-run the grep across the full worktree, and
   correct the Self-test section's evidence line to match what the re-run actually shows.

Verified independently: `uv sync && uv run pytest -v` on `1a8920a` → 33/33 pass, matching the
Self-test claim. `README.md`'s "MSPbots Connector" section is byte-identical to
`vendor-mcp-development/reference/readme-connector-section.md` (diff confirmed empty). SOP §7
known pitfalls (DNS-rebinding protection off, MCP app lifespan mounted on the outer Starlette
app, `curl` installed in the production image, `--platform linux/amd64` documented) are all
correctly handled. Credential isolation (`contextvars.ContextVar`, `reset()` in `finally`,
no module-level credential/token state) and the `confirm: true` gate on `threecx_drop_call`
match the tech design and are covered by passing tests. No other must-fix found.

### Suggestions

3. [suggestion] `src/threecx_mcp/server.py:55` — `create_mcp_server(settings: Settings | None
   = None)` accepts `settings` but never reads it in the function body (only `__main__.py`
   reads `settings.log_level`/`mcp_http_*` before calling it). Harmless today; either use it
   or drop the parameter.

4. [suggestion] `docs/tickets/PRD-21277.md` "Reuse & cleanup" says the stack is "fastmcp +
   httpx + pydantic-settings", but `pyproject.toml` depends on `mcp[cli]` (the official MCP
   SDK, whose `mcp.server.fastmcp.FastMCP` is used directly) rather than the standalone
   `fastmcp` package. Functionally fine and a reasonable dev-leg refinement, but the doc text
   should name the package actually used so a future reader isn't misled comparing against
   `hudu-mcp`'s `pyproject.toml`.

### Fixes (dev leg, round 1)

1. OData filter injection (`_common.py:60`, `calls.py:39`, `history.py:30-32`): added a shared
   `escape_odata_literal()` helper in `src/threecx_mcp/tools/_common.py` that doubles embedded
   single quotes per the OData v4 string-literal escaping rule, and routed every caller-supplied
   value through it before interpolation — `odata_filter()` (used by `threecx_list_extensions` /
   `threecx_list_queues`), the inline `Dn eq '...'` clause in `threecx_list_active_calls`
   (`calls.py`), and `_date_range_filter()`'s `caller`/`callee` clauses (`history.py`). Added five
   tests in `tests/test_tools.py`: two direct unit tests of `escape_odata_literal`/`odata_filter`
   (`test_escape_odata_literal_doubles_embedded_quotes`, `test_odata_filter_escapes_values_before_interpolating`),
   and three tool-level tests (`test_list_extensions_escapes_quote_in_name`,
   `test_list_active_calls_escapes_quote_in_dn`, `test_call_history_escapes_quote_in_caller`) that
   each assert the literal `$filter` string sent upstream has the embedded `'` doubled, for a
   value with a plain quote (`O'Brien`) and a crafted injection attempt (`x' or Dn ne '`).
   `uv run pytest -v`: 38/38 pass (33 prior + 5 new).

2. Chinese text in the shipped doc (`docs/tickets/PRD-21277.md:457,462`): rephrased both lines in
   English only — paraphrased the SOP's "a missing vendor test account does not block publishing
   to INT" rule and the `legs/dev.md` "three deliverables" rule instead of quoting their Chinese
   originals. Re-ran `grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`
   across the full worktree: the only remaining match is this review record's own quotation of
   the two Chinese phrases as evidence for this finding (lines above, written by the review leg
   in round 1). I did not edit that quotation: round 1's findings are the review leg's own frozen
   record of what it found at `1a8920a`, and a dev-leg "Fixes" entry is additive, not a rewrite of
   a prior round's evidence — the same "don't touch what's already frozen" principle that keeps
   the dev leg from editing the main ticket doc outside a named must-fix applies to not rewriting
   the review's own findings text. `docs/tickets/PRD-21277.md`, and every other file in the
   worktree, is Chinese-free.

Gates re-run on this round's HEAD: `uv sync && uv run pytest -v` -> 38/38 pass; local smoke
(`uv run python -m threecx_mcp`, port 8080): `GET /health` -> `200 {"status":"ok"}`; `POST /mcp`
with no credential headers -> `401` listing all three `X-3CX-*` headers; `POST /mcp initialize`
with well-shaped placeholder headers (`https://pbx.example.com` / `placeholder-id` /
`placeholder-secret`) -> `200` with server info (tool list itself unchanged by this round, already
covered by `test_tool_names_are_stable`). README's "MSPbots Connector" section is still
byte-identical to `vendor-mcp-development/reference/readme-connector-section.md`. `gitleaks` is
not installed on this machine (same as round 1; not re-attempted). `main` is unchanged at
`a2141a5` since branch creation, so no sync was needed before re-testing.
