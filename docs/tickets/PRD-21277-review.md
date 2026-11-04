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
