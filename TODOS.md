# TODOS

Deferred from the CEO review of `docs/designs/mcp-send-gate.md` (2026-11-03). Wave 1 ships the evidence store, `claims_unverified`, `evaluate()` and the Claude Code hook on a file-based CRM.

## Now: trust before features (decided 2026-11-03, after the live check)

Context: a live check sent a real email because the hook wasn't loaded (docs/send-gate.md, "Live check"). Howard wants a gate he'd trust, and agents will send in future versions. So enforcement must fail closed: no gate, no send.

- ~~**Lock sending in Claude Code permissions (P1).**~~ Done 2026-11-03. User-level `permissions.deny` in `~/.claude/settings.json` covers Gmail `send_message`, `reply`, `forward`, the Apollo send and sequence tools, `apollo_emailer_campaigns_step_content`, `apollo_agent_workflow_automation` and `apollo_agent_optimize_sequences`. Verified in a bypass-permissions session: the three Apollo tools were in the agent's tool list, and adding them to the deny list removed them mid-session. No send tool was called. Caveat: a denied name and a name that doesn't exist look the same, so absence alone proves nothing. The Gmail names, the `apollo_sequences_*` entries and the `list_labels` canary are unconfirmed; to confirm one, remove it from the deny list, start a new session and check it appears in the tool list (list only, never call it).
- ~~**Make "gate not loaded" visible (P1).**~~ Done 2026-11-03. The plugin prints "send gate: on" at session start and logs a heartbeat to `.gates/log/sessions.jsonl`. A user-level watchdog (`hooks/session_check.py watchdog`) warns and tells the agent not to draft when a project has `.gates/` but the plugin isn't installed for it, isn't enabled, or has no hook. Setup and limits are in docs/send-gate.md, "Is the gate on?". Not done: blocking drafts when no heartbeat exists for the session. That would make it fail closed, not just visible.
- **The gate becomes the only way to send (P1, wave 2).** A small service that holds the email credentials and exposes a `send` endpoint running the same `evaluate()`; agents call it through the MCP server, and Clay through HTTP. The agent never holds Gmail or Apollo send access. It runs where the agent can't read the credentials (hosted, or a separate OS account).
- **Agent sending (later).** Switched on only through that service.

## Wave 2: send gate

- **MCP server** (`gates/mcp_server.py`, `.mcp.json`). Expose `record_claim` and `evaluate` as MCP tools for Claude Desktop and other MCP clients. Include `notifications/initialized`, `ping`, protocol-version negotiation and unknown-method errors (reviewer concern R3-21). Confirm Claude Code expands `${CLAUDE_PROJECT_DIR}` in a plugin `.mcp.json` env block (R3-4). Deferred in CEO review D2 to keep the first PR small; in wave 1 the agent calls `python -m gates.evidence record` via Bash.
- **Live HubSpot read in the hook.** `crm.source: hubspot` currently exits 2 with "live HubSpot read ships in wave 2". Needs the 40 s budget, a 45 s watchdog that calls `os._exit(2)`, Retry-After ignored and backoff capped at 1 s in hook mode, `HubSpot(retries=1)` plus a bound transport timeout (R3-2, R3-3), the real request count (R3-8) and correct coverage warnings (R3-9). Also replace `hubspot._plain` with the shared `checker.html_to_text` (eng review D6). Deferred in D3: the demo runs on the mock CRM, and this is the riskiest fail-open path.
- **Gate `update_draft`** instead of blocking it. First define how the hook gets the full recipient, subject and body when the tool input holds only a draft id (R3-11). Deferred in D4.
- **Packaging.** `pyproject.toml` and `uvx outbound-gates-mcp`, shipped with the MCP server. Deferred in D5.

## Later

- Clay HTTP wrapper (`python -m gates.serve`) and an HTTP API column recipe.
- Public receipts page per email, built from stored evidence snapshots.
- **Checked maths in gated drafts (P3).** What: allow a computed number (margin, savings) in a gated draft only when the gate re-runs a declared formula over verified claims and own_facts. Why: price-comparison maths is the repo's strongest cold-email pattern, and wave 1 blocks it (eng D12, CEO D15). Context: `derived` values are agent-declared and unchecked, so they are ignored under require_evidence; the gate demo uses a draft with sourced numbers only. Start from `checker.check_claims` and the claim schema. Effort: human L / CC M. Depends on: wave 1 shipped. (eng review D17)
