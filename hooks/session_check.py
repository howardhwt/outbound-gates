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
- ``draft-guard``: a user-level PreToolUse hook. In a project with .gates/ it
  refuses any send tool by name, from any server (the backstop for servers no
  deny rule names), and refuses Gmail create_draft and update_draft unless this
  session has a heartbeat, so a gate that didn't load fails closed.
- ``selftest``: run by a person (and by setup). Checks what setup installed:
  hooks, copied files, project deny rules, the generated matcher, and sample
  tool calls through the guard. Prints a report; exits 1 if anything fails.

The user-level copy lives in ~/.config/outbound-gates/ (gates/setup.py puts it
there) and runs through gate-net.sh, which turns a failure into a deny (guard)
or a warning (watchdog). Stdlib only. The hook modes always exit 0.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ID = "outbound-gates@outbound-gates"
VERSION = "dev"  # setup stamps the plugin version into the copy
NET_DIR = Path(__file__).resolve().parent
# Where setup keeps the manifest and log; the plugin's own copy of this file reads them here too.
CONFIG_DIR = Path.home() / ".config" / "outbound-gates"
# Used when blocked.json is missing. setup writes the full list from gates/hook.config.json.
DEFAULT_SEND_NAMES = ["send_message", "send_email", "send_mail", "reply", "forward",
                      "schedule_message", "send_now"]
MANAGED_SETTINGS = [Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
                    Path("/etc/claude-code/managed-settings.json")]


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


def covering_versions(project: Path, home: Path) -> list[str]:
    """Versions of the plugin installs that cover this project."""
    record = _read_json(home / ".claude" / "plugins" / "installed_plugins.json")
    installs = (record.get("plugins") or {}).get(PLUGIN_ID) or []
    return [str(i.get("version")) for i in installs
            if isinstance(i, dict) and i.get("version")
            and (i.get("scope") == "user" or _same_dir(i.get("projectPath", ""), project))]


def _version_key(version: str) -> tuple:
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.+-]", version))


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


def set_up_by_setup(project: Path) -> bool:
    """True when setup recorded this project in installed.json next to this copy."""
    projects = _read_json(NET_DIR / "installed.json").get("projects") or {}
    try:
        return str(project.resolve()) in projects
    except (OSError, RuntimeError):
        return False


def gated(project: Path) -> bool:
    """A project the gate covers: it has .gates/, or setup installed the net here."""
    return (project / ".gates").is_dir() or set_up_by_setup(project)


def watchdog(payload: dict, env: dict, home: Path) -> str:
    project = Path(env.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    if not (project / ".gates").is_dir():
        if set_up_by_setup(project):
            return json.dumps({
                "systemMessage": (f"WARNING send gate: setup installed the safety net in {project}, but its "
                                  ".gates/ folder is missing, so the plugin gate has no settings here. The guard "
                                  "still refuses send tools. Restore .gates/ or run the uninstall."),
                "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": (
                    "The outbound-gates .gates/ folder is missing in a project set up for the send gate. "
                    "Do not create drafts or call any send tool; tell the user.")},
            })
        return ""
    problems = []
    for ok, reason in (install_state(project, home), enabled_state(project, home)):
        if not ok:
            problems.append(reason)
    if not problems:
        notes = [n for n in [stale_copy(project, home)] + net_warnings(project, home) + style_notes(project) if n]
        return json.dumps({"systemMessage": " | ".join(notes)}) if notes else ""
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


def _sha_file(path: Path) -> str | None:
    import hashlib
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def tamper_findings(project: Path, home: Path) -> list[str]:
    """What setup installed that is now missing or changed (the manifest is the record).

    Detect-only: an agent that edits installed.json along with the files is not caught.
    """
    manifest = _read_json(CONFIG_DIR / "installed.json")
    if not manifest:
        return []
    found = []
    for name, sha in (manifest.get("files") or {}).items():
        now = _sha_file(CONFIG_DIR / name)
        if now is None:
            found.append(f"{name} is missing from {CONFIG_DIR}")
        elif now != sha:
            found.append(f"{name} in {CONFIG_DIR} was changed after setup")
    hooks = _read_json(home / ".claude" / "settings.json").get("hooks") or {}
    commands = [str(h.get("command", "")) for groups in hooks.values() if isinstance(groups, list)
                for g in groups if isinstance(g, dict) for h in g.get("hooks") or [] if isinstance(h, dict)]
    wrapper = str(CONFIG_DIR.resolve() / "gate-net.sh") if CONFIG_DIR.exists() else str(CONFIG_DIR / "gate-net.sh")
    if manifest.get("user_hooks"):
        for mode in ("watchdog", "draft-guard"):
            if not any(wrapper in c and mode in c for c in commands):
                found.append(f"the {mode} hook was removed from {home / '.claude' / 'settings.json'}")
    try:
        entry = (manifest.get("projects") or {}).get(str(project.resolve()))
    except (OSError, RuntimeError):
        entry = None
    if entry:
        deny = (_read_json(project / ".claude" / "settings.local.json").get("permissions") or {}).get("deny") or []
        missing = [n for n in entry.get("deny_rules") or [] if n not in deny]
        if missing:
            found.append(f"{len(missing)} deny rules were removed from {project / '.claude' / 'settings.local.json'}")
    return found


def uninstalled_note(project: Path) -> str:
    """'uninstalled on <date>' when setup.log's last word on this project is an uninstall."""
    try:
        key = str(project.resolve())
        lines = (CONFIG_DIR / "setup.log").read_text(encoding="utf-8").splitlines()
    except (OSError, RuntimeError):
        return ""
    last = None
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("project") == key:
            last = entry
    if last and last.get("action") == "uninstall":
        return (f"send gate: the safety net was uninstalled for this project on {str(last.get('ts', ''))[:10]} "
                "(setup.log). Run setup again to restore it.")
    return ""


def style_notes(project: Path) -> list[str]:
    """For the person: which style the gate uses here, and whether it changed since setup.

    Style is how the email reads (length, sign-off, closing line), not what it may
    claim, so a change is a notice rather than a warning; the person decides.
    """
    path = project / ".gates" / "style.json"
    if not path.is_file():
        return ["send gate: this project has no .gates/style.json, so the example style applies "
                "(80-150 word cold emails, a fixed sign-off and closing line). "
                "Run setup with --style neutral|example|FILE to choose one."]
    try:
        entry = (_read_json(CONFIG_DIR / "installed.json").get("projects") or {}).get(str(project.resolve())) or {}
    except (OSError, RuntimeError):
        entry = {}
    recorded = entry.get("style_sha256")
    if recorded and _sha_file(path) != recorded:
        return [f"send gate: {path} changed since setup recorded it. If you made the change, run setup "
                "again to record it; if you didn't, check it (style sets length, sign-off and banned phrases)."]
    return []


def net_warnings(project: Path, home: Path) -> list[str]:
    """Session-start warnings about the safety net itself."""
    warnings = [f"WARNING send gate safety net changed: {f}" for f in tamper_findings(project, home)]
    note = uninstalled_note(project)
    if note:
        warnings.append(note)
    return warnings


def stale_copy(project: Path, home: Path) -> str:
    """A warning when this user-level copy is older than the plugin, else ""."""
    if VERSION == "dev":
        return ""
    versions = covering_versions(project, home)
    if not versions:
        return ""
    newest = max(versions, key=_version_key)
    if _version_key(newest) <= _version_key(VERSION):
        return ""
    return (f"send gate: the safety-net copy in {NET_DIR} is version {VERSION}, older than the "
            f"plugin ({newest}). Re-run setup (/outbound-gates:setup) to update it.")


def policy_warnings(project: Path, home: Path, managed: list[Path] | None = None) -> list[str]:
    """Settings that silently switch the gate's layers off."""
    warnings = []
    for path in (managed if managed is not None else MANAGED_SETTINGS):
        data = _read_json(path)
        if data.get("allowManagedHooksOnly") is True:
            warnings.append(f"{path} sets allowManagedHooksOnly: user and plugin hooks never run, "
                            "so the gate, watchdog and guard are off")
        if data.get("allowManagedPermissionRulesOnly") is True:
            warnings.append(f"{path} sets allowManagedPermissionRulesOnly: Claude Code ignores the "
                            "project's deny rules")
    for label, path in (("user", home / ".claude" / "settings.json"),
                        ("project", project / ".claude" / "settings.json"),
                        ("local", project / ".claude" / "settings.local.json")):
        if _read_json(path).get("disableAllHooks") is True:
            warnings.append(f"{label} settings ({path}) set disableAllHooks: every hook is off, "
                            "including the gate")
    return warnings


GUARDED_DRAFT_TOOLS = {"create_draft", "update_draft"}


def words(name: str) -> list[str]:
    """Split a tool name into lowercase words at _, - and camelCase boundaries."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [w.lower() for w in re.split(r"[_\-\s]+", spaced) if w]


def name_matches(tool_short: str, names: list[str]) -> bool:
    """True when the tool name ends with one of the names, word for word."""
    tool_words = words(tool_short)
    for name in names:
        target = words(name)
        if target and tool_words[-len(target):] == target:
            return True
    return False


def guard_names(project: Path) -> list[str]:
    """Send names from blocked.json next to this copy, plus the project's additions."""
    data = _read_json(NET_DIR / "blocked.json")
    names = list(data.get("guard_names") or DEFAULT_SEND_NAMES)
    extra = _read_json(project / ".gates" / "config.json").get("blocked_tools") or []
    return names + [str(n) for n in extra if isinstance(n, str)]


def _deny(reason: str) -> str:
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"send gate guard: {reason}",
        }
    })


def session_has_heartbeat(project: Path, session_id: str) -> bool:
    try:
        lines = (project / ".gates" / "log" / "sessions.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("event") == "gate_loaded" and entry.get("session_id") == session_id:
            return True
    return False


READ_VERBS = {"get", "list", "search", "read", "query", "find", "fetch", "show", "describe",
              "count", "lookup", "view"}
# A tool name with any of these words is never treated as a read, even with a read verb.
WRITE_VERBS = {"send", "create", "update", "delete", "post", "publish", "run", "execute", "approve",
               "add", "remove", "schedule", "trigger", "deploy", "share", "upload", "write", "set",
               "manage", "mark", "trash", "apply", "label", "unlabel", "respond", "invite", "cancel",
               "move", "merge", "complete", "enroll", "forward", "reply", "spawn", "import", "replace",
               "edit", "restore", "activate", "deactivate", "pause", "unpause", "buy", "transfer",
               "purchase", "submit", "register", "assign", "unassign", "start", "stop", "kill", "skip"}
DEFAULT_ALLOW = ["create_draft"]
STRICT_MATCHERS = ["^mcp__.*", "^(Bash|Edit|Write|MultiEdit|NotebookEdit)$"]  # same as gates/setup.py  # Gmail drafts: the plugin gate checks each one
FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
MAIL_COMMAND = re.compile(
    r"\bsendmail\b|\bmailx?\s+-s\b|\bmutt\b|\bswaks\b|\bmsmtp\b|smtplib|"
    r"osascript[^\n]*(?:\bMail\b|\bMessages\b|Outlook)", re.I)
NETWORK_SEND = re.compile(
    r"\bcurl\b[^\n|;&]*(?:\s-d\b|\s-d\S|--data|\s-F\b|\s-F\S|--form|\s-T\b|--upload-file|--json\b|"
    r"(?:-X|--request)\s*['\"]?(?:POST|PUT|PATCH))|"
    r"\bwget\b[^\n|;&]*(?:--post-data|--post-file|--method[= ]['\"]?(?:POST|PUT|PATCH))|"
    r"\bhttp\s+(?:POST|PUT|PATCH)\b|requests\.(?:post|put|patch)\b|"
    r"urlopen\([^)]*data\s*=", re.I)
MUTATES = re.compile(r"\brm\b|\bmv\b|\bcp\b|\bln\b|\bunlink\b|\btruncate\b|\bchmod\b|\btee\b|>|"
                     r"\bsed\b[^\n]*\s-i|\bperl\b[^\n]*\s-i|\bpython3?\b|\bnode\b|\bruby\b|\bdd\b|\binstall\b")


def strict_mode(project: Path) -> bool:
    """True when setup installed this project with --strict."""
    projects = _read_json(NET_DIR / "installed.json").get("projects") or {}
    try:
        return bool((projects.get(str(project.resolve())) or {}).get("strict"))
    except (OSError, RuntimeError):
        return False


def allow_list() -> list[str]:
    """Tools a person allowed in strict projects (short or full names), plus the defaults."""
    extra = _read_json(NET_DIR / "allow.json").get("allow") or []
    return DEFAULT_ALLOW + [str(n) for n in extra if isinstance(n, str)]


def is_read_tool(short: str) -> bool:
    ws = words(short)
    return any(w in READ_VERBS for w in ws) and not any(w in WRITE_VERBS for w in ws)


def protected_paths(project: Path) -> list[Path]:
    home = Path.home()
    return [home / ".claude" / "settings.json", home / ".claude" / "settings.local.json",
            home / ".claude" / "plugins", NET_DIR, project / ".claude" / "settings.json",
            project / ".claude" / "settings.local.json", project / ".gates"]


def _under(path: Path, roots: list[Path]) -> bool:
    try:
        target = path.resolve()
    except (OSError, RuntimeError):
        return True  # can't tell: treat as protected
    for root in roots:
        try:
            r = root.resolve()
        except (OSError, RuntimeError):
            continue
        if target == r or r in target.parents:
            return True
    return False


def _protected_tokens(project: Path) -> list[str]:
    return [".claude/settings", "settings.local.json", ".config/outbound-gates", ".gates",
            ".claude/plugins", str(NET_DIR)]


def bash_refusal(command: str, project: Path) -> str:
    """Why this Bash command is refused in a strict project, or ""."""
    command = re.sub(r"\d?>&\d|\d?>\s*/dev/null", " ", command)  # harmless redirects
    if MAIL_COMMAND.search(command):
        return "it looks like a mail command (sendmail, mail, smtplib, osascript Mail)"
    if NETWORK_SEND.search(command):
        return "it sends data over the network (curl/wget with data or POST/PUT, requests.post)"
    if (re.search(r"gates\.(?:evidence (?:record|calc)|checker)\b", command)
            and not re.search(r"\brm\b|\bmv\b|>|;|&&|\|\|", command)):
        return ""  # the gate's own commands: record a source or a calculation, check a draft
    if any(t in command for t in _protected_tokens(project)) and MUTATES.search(command):
        return "it may change or delete the send gate's own files or settings"
    return ""


def _strict_deny(reason: str) -> str:
    return _deny(f"strict mode: {reason}. Ask the person to do this outside the agent if it is needed.")


def draft_guard(payload: dict, env: dict) -> str:
    """User-level PreToolUse guard for projects the gate covers.

    Every project the gate covers: refuses send tools by name from any server,
    and Gmail drafts in a session with no heartbeat. Strict projects (setup
    --strict) also refuse MCP tools that aren't reads or allowed, Bash commands
    that send mail or post data or touch the gate's own files, and Edit/Write on
    those files.
    """
    tool = str(payload.get("tool_name", ""))
    project = Path(env.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    if not gated(project):
        return ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool == "Bash":
        if not strict_mode(project):
            return ""
        why = bash_refusal(str(tool_input.get("command", "")), project)
        return _strict_deny(f"this Bash command is refused because {why}") if why else ""
    if tool in FILE_TOOLS:
        if not strict_mode(project):
            return ""
        raw = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        path = Path(raw) if Path(raw).is_absolute() else project / raw
        if raw and _under(path, protected_paths(project)):
            return _strict_deny(f"{raw} is one of the send gate's own files or settings")
        return ""

    parts = tool.split("__")
    if len(parts) < 3:
        return ""
    short = parts[-1]
    if name_matches(short, guard_names(project)):
        return _deny(f"{short} is a send tool, and send tools are blocked in this project. "
                     "A person sends; email goes through a gated Gmail draft.")
    if "gmail" in parts[1].lower() and short in GUARDED_DRAFT_TOOLS:
        session_id = str(payload.get("session_id") or "")
        if not (session_id and session_has_heartbeat(project, session_id)):
            return _deny(
                "the send gate did not load in this session (no \"send gate: on\" heartbeat in "
                ".gates/log/sessions.jsonl), so Gmail drafts are refused here. Fix the plugin install, "
                "start a new session and check its first lines say \"send gate: on\"."
            )
    if strict_mode(project):
        allowed = allow_list()
        if not (is_read_tool(short) or short in allowed or tool in allowed):
            return _strict_deny(
                f"{short} is not a read tool or on the allow list, so it is refused in this project. "
                f"A person can allow it by adding \"{short}\" to {NET_DIR / 'allow.json'} "
                '({"allow": ["..."]})')
    return ""


def selftest(project: Path, home: Path, managed: list[Path] | None = None) -> tuple[bool, list[str]]:
    """Check what setup installed. Returns (passed, report lines)."""
    lines: list[str] = []
    failed = False

    def check(ok: bool, what: str) -> None:
        nonlocal failed
        failed |= not ok
        lines.append(f"{'OK  ' if ok else 'FAIL'} {what}")

    blocked = _read_json(NET_DIR / "blocked.json")
    for name in ("session_check.py", "gate-net.sh", "blocked.json", "setup.py"):
        check((NET_DIR / name).is_file(), f"copy present: {NET_DIR / name}")
    wrapper = str(NET_DIR / "gate-net.sh")
    hooks = _read_json(home / ".claude" / "settings.json").get("hooks") or {}

    def commands(event: str) -> list[tuple[str, str]]:
        found = []
        for group in hooks.get(event) or []:
            for hook in (group.get("hooks") or []) if isinstance(group, dict) else []:
                if isinstance(hook, dict):
                    found.append((str(group.get("matcher", "")), str(hook.get("command", ""))))
        return found

    check(any(wrapper in c and "watchdog" in c for _, c in commands("SessionStart")),
          "user SessionStart hook runs the watchdog through gate-net.sh")
    matchers = [m for m, c in commands("PreToolUse") if wrapper in c and "draft-guard" in c]
    check(bool(matchers), "user PreToolUse hook runs the guard through gate-net.sh")
    projects = _read_json(NET_DIR / "installed.json").get("projects") or {}
    any_strict = any((v or {}).get("strict") for v in projects.values())
    expected = STRICT_MATCHERS if any_strict else [blocked.get("matcher")]
    check(bool(matchers) and sorted(matchers) == sorted(m for m in expected if m),
          "guard matchers are the expected ones" + (" (strict: every MCP tool, Bash and file edits)" if any_strict else ""))
    try:
        patterns = [re.compile(m) for m in matchers]
    except re.error as err:
        patterns = []
        check(False, f"guard matchers compile ({err})")
    if patterns:
        routed = lambda name: any(p.search(name) for p in patterns)
        samples = list(blocked.get("guard_names") or []) + ["create_draft", "update_draft", "sendEmail"]
        missed = [n for n in samples if not routed(f"mcp__any_server__{n}")]
        check(not missed, "matcher routes every send and draft name to the guard"
              + (f" (missed: {', '.join(missed)})" if missed else ""))
        if any_strict:
            check(routed("Bash") and routed("Edit") and routed("Write"), "matcher routes Bash, Edit and Write")
        else:
            check(not routed("mcp__claude_ai_Gmail__list_drafts"), "matcher ignores read tools")
    deny = (_read_json(project / ".claude" / "settings.local.json").get("permissions") or {}).get("deny") or []
    missing = [n for n in blocked.get("deny_rules") or [] if n not in deny]
    check(bool(blocked.get("deny_rules")) and not missing,
          f"project deny rules present in {project / '.claude' / 'settings.local.json'}"
          + (f" (missing: {', '.join(missing)})" if missing else ""))
    check((project / ".gates").is_dir(), f"{project / '.gates'} exists")

    env = {"CLAUDE_PROJECT_DIR": str(project)}
    for tool, expect in (("mcp__claude_ai_Slack__slack_send_message", "deny"),
                         ("mcp__self_hosted_mail__sendEmail", "deny"),
                         ("mcp__other_apollo__apollo_emailer_messages_send_now", "deny"),
                         ("mcp__claude_ai_Gmail__create_draft", "deny"),
                         ("mcp__claude_ai_Gmail__list_drafts", "pass")):
        out = draft_guard({"tool_name": tool, "session_id": "selftest-no-heartbeat"}, env)
        got = "deny" if out else "pass"
        check(got == expect, f"guard: {tool} -> {got} (expected {expect})")

    if strict_mode(project):
        lines.append("INFO strict mode is on for this project")
        for tool, tool_input, expect in (
                ("mcp__claude_ai_Make__scenarios_create", {}, "deny"),
                ("mcp__claude_ai_Google_Calendar__create_event", {}, "deny"),
                ("mcp__claude_ai_Granola__get_meetings", {}, "pass"),
                ("Bash", {"command": "curl -X POST https://api.example.com -d x=1"}, "deny"),
                ("Bash", {"command": "python3 -c 'import smtplib'"}, "deny"),
                ("Bash", {"command": f"rm -rf {project / '.gates'}"}, "deny"),
                ("Bash", {"command": "curl -s https://example.com"}, "pass"),
                ("Write", {"file_path": str(project / ".claude" / "settings.local.json")}, "deny"),
                ("Write", {"file_path": str(project / "draft.txt")}, "pass")):
            out = draft_guard({"tool_name": tool, "tool_input": tool_input, "session_id": "selftest"}, env)
            got = "deny" if out else "pass"
            label = tool if not tool_input else f"{tool} {json.dumps(tool_input)[:60]}"
            check(got == expect, f"strict guard: {label} -> {got} (expected {expect})")
    for warning in policy_warnings(project, home, managed):
        lines.append(f"WARN {warning}")
    stale = stale_copy(project, home)
    if stale:
        lines.append(f"WARN {stale}")
    return not failed, lines


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
    message = "send gate: on. Gmail drafts are checked; send, reply and forward are blocked."
    try:
        extra = net_warnings(project, Path.home()) if (project / ".gates").is_dir() else []
    except Exception:  # never block a session start
        extra = []
    if extra:
        message = message + " " + " | ".join(extra)
    return json.dumps({
        "systemMessage": message,  # shown to the user in the terminal
        "hookSpecificOutput": {  # added to the agent's context, so it can confirm the gate too
            "hookEventName": "SessionStart",
            "additionalContext": f"{message} Heartbeat logged for session {payload.get('session_id')}.",
        },
    })


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    if mode == "selftest":
        project = Path(argv[2]) if len(argv) > 2 else Path.cwd()
        passed, lines = selftest(project.resolve(), Path.home())
        print(f"send gate selftest ({project.resolve()}), copy version {VERSION}")
        print("\n".join(lines))
        print("PASSED" if passed else "FAILED")
        return 0 if passed else 1
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
        elif mode == "draft-guard":
            out = draft_guard(payload, dict(os.environ))
        else:
            print(f"usage: session_check.py plugin|watchdog|draft-guard|selftest [project] (got {mode!r})", file=sys.stderr)
            return 0
    except Exception as err:
        if mode == "draft-guard":  # the guard fails closed
            out = _deny(f"guard crashed: {type(err).__name__}: {err}")
        else:  # never block a session start
            out = json.dumps({"systemMessage": f"send gate check failed: {type(err).__name__}: {err}"})
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
