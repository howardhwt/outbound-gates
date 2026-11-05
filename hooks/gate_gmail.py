"""Claude Code PreToolUse hook: the send gate.

Claude Code runs this (through hooks/gate.sh) before every MCP tool call.

- Any other tool: exit with no output, without importing the gate modules.
- Gmail send_message, reply, forward and update_draft, and any tool on the
  block list: denied. Sends happen from Gmail, by a person.
- Gmail create_draft: allowed only when evaluate() passes.

A denial is one JSON object on stdout (permissionDecision "deny") with the
reason, written once at the very end. Everything else goes to stderr. If this
script crashes, hooks/gate.sh turns the crash into a denial.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
# Claude Code lets a call through when a hook passes its timeout (60 s in
# hooks/hooks.json). The CRM read has a 40 s budget; this watchdog refuses at
# 45 s whatever is still running (DNS, a slow-drip response, a sleep).
WATCHDOG_S = 45.0
_out_lock = threading.Lock()
_out_done = False
GMAIL_ALWAYS_BLOCKED = {
    "send_message": "sends go out from Gmail by a person, not from the agent",
    "reply": "replies are outside the gate; answer the thread yourself in Gmail",
    "forward": "forwards are outside the gate; forward it yourself in Gmail",
    "update_draft": "edit by creating a new draft, so the whole email is checked again",
}
DRAFT_FIELDS = {"to", "cc", "bcc", "subject", "body", "htmlBody", "includeSignature"}
REFUSED_FIELDS = {
    "attachments": "attachments are not gated",
    "replyToMessageId": "replies are outside wave 1; create a new draft instead",
}


def deny(reason: str) -> str:
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"send gate: {reason}",
        }
    })


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def blocked_tools(plugin_root: Path, project_dir: Path) -> list[str]:
    """Plugin defaults plus anything the project adds (it can't remove any).

    Read with plain json so unrelated tool calls never import the gate.
    tests/test_hook.py checks this matches gates.evidence.load_settings.
    """
    tools = list(_read_json(plugin_root / "gates" / "hook.config.json").get("blocked_tools", []))
    for tool in _read_json(project_dir / ".gates" / "config.json").get("blocked_tools", []):
        if tool not in tools:
            tools.append(tool)
    return tools


def classify(payload: dict, plugin_root: Path, project_dir: Path) -> tuple[str, str]:
    """("pass" | "deny" | "gate", reason or tool short name)."""
    tool = str(payload.get("tool_name", ""))
    short = tool.rsplit("__", 1)[-1]
    server = str((payload.get("mcp_server") or {}).get("name", ""))
    server_part = tool.split("__")[1] if tool.count("__") >= 2 else ""
    is_gmail = "gmail" in server.lower() or "gmail" in server_part.lower()
    if is_gmail and short in GMAIL_ALWAYS_BLOCKED:
        return "deny", f"Gmail {short} is blocked: {GMAIL_ALWAYS_BLOCKED[short]}"
    if is_gmail and short == "create_draft":
        return "gate", short
    if short in blocked_tools(plugin_root, project_dir):
        return "deny", f"{short} is blocked in this project: agents don't send here; a person does, and email goes through a gated Gmail draft"
    return "pass", short


def _draft_from_input(tool_input: dict):
    from email.utils import getaddresses

    from gates.checker import html_to_text
    from gates.evaluate import Draft

    for key in tool_input:
        if key in REFUSED_FIELDS and tool_input[key]:
            raise ValueError(f"field {key} refused: {REFUSED_FIELDS[key]}")
        if key not in DRAFT_FIELDS and key not in REFUSED_FIELDS:
            raise ValueError(f"unexpected field {key}; the gate only checks known draft fields")
    recipients = []
    for key in ("to", "cc", "bcc"):
        value = tool_input.get(key) or []
        values = [value] if isinstance(value, str) else list(value)
        recipients += [addr for _, addr in getaddresses([str(v) for v in values]) if addr]
    if len(recipients) != 1:
        raise ValueError(f"one recipient per gated draft (found {len(recipients)} across to, cc and bcc)")
    bodies = []
    if tool_input.get("body"):
        bodies.append(("body", str(tool_input["body"])))
    if tool_input.get("htmlBody"):
        bodies.append(("htmlBody", html_to_text(str(tool_input["htmlBody"]))))
    return Draft(recipients[0], str(tool_input.get("subject") or ""), tuple(bodies))


def _deny_reason(verdict, gates_home: Path, contact: str) -> str:
    from gates.evidence import calc_command, draft_dir, record_command

    fails = [f for f in verdict.findings if f.severity == "fail"]
    lines = [f"{len(fails)} problem(s) with this draft:"]
    lines += [f"- {f.rule}: {f.message}" for f in fails]
    if any(f.rule in ("claims", "claims_unverified") for f in fails):
        lines.append(
            "To source a number, record the exact sentence from the company's own site "
            "(replace URL, QUOTE and VALUE):"
        )
        lines.append(record_command(gates_home, contact))
        lines.append("For a number you worked out from sourced numbers (a saving, a ratio), record the "
                     "calculation; the gate recomputes it from the verified sources (unit constants only, "
                     "within 2%):")
        lines.append(calc_command(gates_home, contact))
        lines.append(f"Claims folder read: {draft_dir(gates_home, contact) / 'claims'}")
    warns = [f for f in verdict.findings if f.rule == "crm_coverage"]
    if warns:
        lines.append("CRM warnings:")
        lines += [f"- {f.message}" for f in warns]
    return "\n".join(lines)


def _log(gates_home: Path, entry: dict) -> None:
    try:
        path = gates_home / "log" / "gate.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as err:
        print(f"send gate: could not write the decision log: {err}", file=sys.stderr)


def _emit(text: str) -> bool:
    """Write the hook's answer once. The watchdog and main() race for it."""
    global _out_done
    with _out_lock:
        if _out_done:
            return False
        _out_done = True
        sys.stdout.write(text)
        sys.stdout.flush()
        return True


def _watchdog_fire(seconds: float) -> None:
    if _emit(deny(f"gate deadline exceeded: no verdict within {seconds:g} s, so the draft is refused")):
        os._exit(0)


def start_watchdog(seconds: float = WATCHDOG_S) -> threading.Timer:
    timer = threading.Timer(seconds, _watchdog_fire, args=(seconds,))
    timer.daemon = True
    timer.start()
    return timer


def decide(raw: str, env: dict, today=None, now=None, *, deadline=None, transport=None) -> str:
    """The hook's whole decision: returns the stdout text ("" means allow)."""
    from datetime import date, datetime, timezone

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
    except ValueError as err:
        return deny(f"cannot read tool input: {err}")

    plugin_root = Path(env.get("CLAUDE_PLUGIN_ROOT") or PLUGIN_ROOT)
    project_dir = Path(env.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    gates_home = project_dir / ".gates"
    try:
        action, detail = classify(payload, plugin_root, project_dir)
    except (OSError, ValueError) as err:
        return deny(f"cannot read gate settings: {err}")
    if action == "pass":
        return ""

    import hashlib

    stamp = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    tool = str(payload.get("tool_name", ""))
    entry = {"v": 1, "ts": stamp, "tool": tool, "decision": "deny"}
    if action == "deny":
        entry["rules"] = ["blocked_tool"]
        _log(gates_home, entry)
        return deny(detail)

    from gates.checker import UsageError
    from gates.evaluate import evaluate
    from gates.evidence import EvidenceError, contact_parts, load_settings, read_crm

    try:
        draft = _draft_from_input(payload.get("tool_input") or {})
        domain, _ = contact_parts(draft.recipient)
        entry["recipient_domain"] = domain
        entry["contact_sha256"] = hashlib.sha256(draft.recipient.lower().encode()).hexdigest()
        settings = load_settings(plugin_root, project_dir)
        entry["style"] = settings["style_source"]
        entry["crm_path"] = "hubspot" if settings["crm_source"] == "hubspot" else str(settings["crm_path"])
        crm, warnings = read_crm(settings, draft.recipient, deadline=deadline, transport=transport)
        verdict = evaluate(draft, crm, settings, gates_home=gates_home, today=today or date.today(),
                           crm_warnings=warnings)
    except (ValueError, UsageError, EvidenceError, OSError) as err:
        entry["rules"] = ["input"]
        _log(gates_home, entry)
        return deny(str(err))

    entry["decision"] = "allow" if verdict.passed else "deny"
    entry["rules"] = sorted({f.rule for f in verdict.findings if f.severity == "fail"})
    entry["inputs_sha256"] = verdict.inputs_sha256
    entry["warnings"] = len(warnings)
    _log(gates_home, entry)
    if verdict.passed:
        if not warnings:
            return ""
        # No permissionDecision: the call goes ahead as normal, and you see what wasn't checked.
        return json.dumps({"systemMessage": "send gate: draft passed, with warnings: " + " | ".join(warnings)})
    return deny(_deny_reason(verdict, gates_home, draft.recipient))


def main() -> int:
    start_watchdog()
    raw = sys.stdin.read()
    try:
        out = decide(raw, dict(os.environ))
    except Exception as err:  # the gate fails closed on anything unexpected
        out = deny(f"gate internal error: {type(err).__name__}: {err}")
    _emit(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
