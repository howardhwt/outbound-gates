"""SessionStart checks that make "gate not loaded" visible.

Two modes, one per hook registration:

- ``plugin``: registered by the plugin itself (hooks/hooks.json). If it runs,
  the plugin is loaded, so it writes a heartbeat line to
  .gates/log/sessions.jsonl and tells the user "send gate: on".
- ``watchdog``: registered in the user's own ~/.claude/settings.json, outside
  the plugin, because a plugin that isn't loaded can't report itself missing.
  In a project with a .gates/ folder it checks that the plugin is installed for
  this project and enabled. If not, it warns the user and tells the agent not
  to create Gmail drafts. Silent in projects without .gates/ and when all is well.

Stdlib only. Both modes always exit 0: a broken check must not stop a session.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ID = "outbound-gates@outbound-gates"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _same_dir(a: str | Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except (OSError, RuntimeError):
        return False


def enabled_state(project: Path, home: Path) -> tuple[bool, str]:
    """Is the plugin enabled here? Local settings beat project settings beat user settings."""
    for label, path in (
        ("local", project / ".claude" / "settings.local.json"),
        ("project", project / ".claude" / "settings.json"),
        ("user", home / ".claude" / "settings.json"),
    ):
        plugins = _read_json(path).get("enabledPlugins")
        if isinstance(plugins, dict) and PLUGIN_ID in plugins:
            if plugins[PLUGIN_ID] is True:
                return True, f"enabled in {label} settings"
            return False, f"disabled in {label} settings ({path})"
    return False, "not enabled in any settings file"


def install_state(project: Path, home: Path) -> tuple[bool, str]:
    """Is there an install that covers this project, with its hook file present?"""
    record = _read_json(home / ".claude" / "plugins" / "installed_plugins.json")
    installs = (record.get("plugins") or {}).get(PLUGIN_ID) or []
    covering = [
        i for i in installs
        if isinstance(i, dict)
        and (i.get("scope") == "user" or _same_dir(i.get("projectPath", ""), project))
    ]
    if not covering:
        return False, "not installed for this project"
    for install in covering:
        hooks = _read_json(Path(install.get("installPath", "")) / "hooks" / "hooks.json")
        if (hooks.get("hooks") or {}).get("PreToolUse"):
            return True, f"installed ({install.get('scope')} scope, version {install.get('version')})"
    return False, "installed, but the installed copy has no PreToolUse hook (reinstall the plugin)"


def watchdog(payload: dict, env: dict, home: Path) -> str:
    project = Path(env.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    if not (project / ".gates").is_dir():
        return ""
    problems = []
    for ok, reason in (install_state(project, home), enabled_state(project, home)):
        if not ok:
            problems.append(reason)
    if not problems:
        return ""
    why = "; ".join(problems)
    return json.dumps({
        "systemMessage": (
            f"WARNING send gate OFF in this project: {why}. Gmail drafts are NOT checked. "
            "Fix: claude plugin install outbound-gates@outbound-gates --scope local, "
            "then start a new session."
        ),
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                f"The outbound-gates send gate is NOT loaded in this session ({why}). "
                "Do not create or update Gmail drafts, and do not call any send, reply, "
                "forward or sequence tool. Tell the user the gate is off before any email work."
            ),
        },
    })


def plugin(payload: dict, env: dict, now: datetime | None = None) -> str:
    project = Path(env.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    stamp = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    entry = {
        "v": 1, "ts": stamp, "event": "gate_loaded",
        "session_id": payload.get("session_id"), "source": payload.get("source"),
    }
    try:
        path = project / ".gates" / "log" / "sessions.jsonl"
        if path.parent.parent.is_dir():  # only log in projects that use the gate
            path.parent.mkdir(exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as err:
        print(f"send gate: could not write the session log: {err}", file=sys.stderr)
    return json.dumps({
        "systemMessage": "send gate: on. Gmail drafts are checked; send, reply and forward are blocked.",
    })


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except ValueError:
        payload = {}
    try:
        if mode == "plugin":
            out = plugin(payload, dict(os.environ))
        elif mode == "watchdog":
            out = watchdog(payload, dict(os.environ), Path.home())
        else:
            print(f"usage: session_check.py plugin|watchdog (got {mode!r})", file=sys.stderr)
            return 0
    except Exception as err:  # never block a session start
        out = json.dumps({"systemMessage": f"send gate check failed: {type(err).__name__}: {err}"})
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
