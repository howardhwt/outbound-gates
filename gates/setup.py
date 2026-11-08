"""Install, check or remove the outbound-gates safety net.

The plugin's gate only runs while the plugin is loaded. This installs the
layers that hold when it isn't:

  ~/.config/outbound-gates/            session_check.py (version-stamped), gate-net.sh,
                                       blocked.json, setup.py (a copy of this file),
                                       installed.json (what setup added, for uninstall)
  ~/.claude/settings.json              SessionStart -> gate-net.sh watchdog
                                       PreToolUse (generated matcher) -> gate-net.sh draft-guard
  <project>/.claude/settings.local.json  permissions.deny for known send tools
  <project>/.gates/                    created if missing
  <project>/.gates/style.json          the project's writing style: written for a new
                                       project ({} = neutral) or when --style is given

A person runs it, in two steps (the agent may run only the first):

  python3 <plugin>/gates/setup.py --project DIR --plugin-root <plugin>               # preview
  python3 <plugin>/gates/setup.py --project DIR --plugin-root <plugin> --yes HASH     # apply
  python3 ~/.config/outbound-gates/setup.py --project DIR --uninstall [--all] [--yes HASH]

The preview prints a PLANHASH; --yes needs it and refuses (exit 3) if the plan
changed since. Optional at install: --strict/--no-strict, --check-names FILE
(advisory), --style neutral|example|FILE, and a guided first run: --crm-source file|hubspot, --crm-path,
--sender-owner. Every applied run appends a line to setup.log.

Uninstall runs from the copy, so it works after the plugin is removed. Setup
only changes entries it adds; everything else in each settings file is re-read
just before writing and kept. Each changed settings file gets a backup.

Standalone and stdlib only (it is copied out of the plugin), so it does not
import the gates package. Exit codes: 0 done, 1 refused, selftest failed or the
first-run config or style step was skipped, 2 bad input, 3 plan changed since preview.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
STRICT_MCP_MATCHER = "^mcp__.*"
STRICT_FILE_MATCHER = "^(Bash|Edit|Write|MultiEdit|NotebookEdit)$"
NET_FILES = ("session_check.py", "gate-net.sh", "blocked.json", "setup.py", "gate")
GATE_LAUNCHER = """#!/usr/bin/env python3
# outbound-gates: the gate command, written by setup. It runs gates.gate from the newest
# installed copy of the plugin, so commands in your notes never name a version.
import re
import sys
from pathlib import Path

BAKED = Path(__PLUGIN_ROOT__)
VERSION = re.compile(r"\\d+\\.\\d+\\.\\d+")


def key(path):
    return tuple(int(x) for x in path.name.split("."))


roots = [BAKED]
if VERSION.fullmatch(BAKED.name) and BAKED.parent.is_dir():
    roots = [p for p in BAKED.parent.iterdir() if VERSION.fullmatch(p.name)]
roots = [p for p in roots if (p / "gates" / "gate.py").is_file()]
if not roots:
    sys.exit(f"gate: no outbound-gates plugin with gate found at {BAKED}; update the plugin")
root = max(roots, key=key) if VERSION.fullmatch(roots[0].name) else roots[0]
sys.path.insert(0, str(root))
from gates.gate import main  # noqa: E402

sys.exit(main())
"""
OLD_LAUNCHER_MARKER = "# outbound-gates: the og command, written by setup."


def remove_old_launcher(net: Path) -> bool:
    """Before 0.10.0 the command was called og. Remove that launcher, but only if setup wrote it."""
    old = net / "og"
    try:
        if old.is_file() and OLD_LAUNCHER_MARKER in old.read_text(encoding="utf-8"):
            old.unlink()
            return True
    except (OSError, UnicodeDecodeError):
        pass
    return False


LEGACY_MARKERS = ("session_check.py watchdog", "session_check.py draft-guard")
DRAFT_NAMES = ["create_draft", "update_draft"]


class SetupError(Exception):
    """Bad input or a file setup must not touch. Nothing has been written."""


class Refused(Exception):
    """Setup can't go ahead (another run holds the lock). Exit 1."""


class PlanChanged(Exception):
    """The plan differs from the preview the person approved. Exit 3."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan_hash(payload: dict) -> str:
    return _sha(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))[:12]


class Lock:
    """One setup run at a time: an O_EXCL lock file holding our pid; a dead owner's lock is taken over."""

    def __init__(self, net_dir: Path):
        self.path = net_dir / ".lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    owner = int(self.path.read_text().strip() or "0")
                    os.kill(owner, 0)
                except (ValueError, ProcessLookupError, OSError):
                    self.path.unlink(missing_ok=True)  # stale lock
                    continue
                raise Refused(f"another setup run (pid {owner}) holds {self.path}; wait for it, "
                              "or delete the file if that run is gone") from None
        raise Refused(f"could not take {self.path}")

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def append_log(net_dir: Path, entry: dict) -> None:
    net_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"schema": SCHEMA, "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                       **entry}, sort_keys=True)
    with open(net_dir / "setup.log", "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


# --- names -------------------------------------------------------------------

def _words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [w.lower() for w in re.split(r"[_\-\s]+", spaced) if w]


def word_pattern(name: str) -> str:
    """send_message -> [Ss]end[_-]?[Mm]essage. Same syntax in JavaScript and Python."""
    parts = []
    for word in _words(name):
        head = word[0]
        parts.append((f"[{head.upper()}{head}]" if head.isalpha() else re.escape(head)) + re.escape(word[1:]))
    return "[_-]?".join(parts)


def load_names(plugin_root: Path) -> dict:
    """Guard names, full deny-rule names and the hook matcher, all from hook.config.json."""
    path = plugin_root / "gates" / "hook.config.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise SetupError(f"cannot read {path}: {err}") from None
    blocked = [str(n) for n in cfg.get("blocked_tools", [])]
    guard = []
    for name in list(cfg.get("send_words", [])) + blocked:
        if name not in guard:
            guard.append(name)
    deny = []
    for server, tools in (cfg.get("deny_rules") or {}).items():
        deny += [f"mcp__{server}__{t}" for t in tools]
    deny += [f"mcp__claude_ai_Apollo_io__{n}" for n in blocked if n.startswith("apollo_")]
    routed = guard + DRAFT_NAMES
    matcher = "^mcp__.*__.*(?:" + "|".join(word_pattern(n) for n in routed) + ")$"
    return {"guard_names": guard, "deny_rules": deny, "matcher": matcher}


def plugin_version(plugin_root: Path) -> str:
    try:
        return str(json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text())["version"])
    except (OSError, ValueError, KeyError):
        return "dev"


# --- files -------------------------------------------------------------------

def read_settings(path: Path) -> dict:
    """A settings file as a dict; missing means {}. Invalid JSON stops setup."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError as err:
        raise SetupError(f"{path} is not valid JSON ({err}); fix it first, nothing was changed") from None
    if not isinstance(data, dict):
        raise SetupError(f"{path} must hold a JSON object; nothing was changed")
    return data


def atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_settings(path: Path, edit, stamp: str) -> bool:
    """Re-read the file, apply edit(dict) -> dict, back up and write if it changed."""
    before = read_settings(path)
    after = edit(json.loads(json.dumps(before)))
    if after == before:
        return False
    if path.exists():
        shutil.copy2(path.resolve(), path.resolve().with_name(path.name + f".bak-outbound-gates-{stamp}"))
    atomic_write(path, (json.dumps(after, indent=2) + "\n").encode("utf-8"))
    return True


# --- settings edits ----------------------------------------------------------

def _hook_commands(net_dir: Path) -> dict:
    wrapper = net_dir / "gate-net.sh"
    return {"watchdog": f'sh "{wrapper}" watchdog', "guard": f'sh "{wrapper}" draft-guard'}


def _is_ours(command: str, net_dir: Path) -> bool:
    return str(net_dir / "gate-net.sh") in command


def _is_legacy(command: str, net_dir: Path) -> bool:
    return any(m in command for m in LEGACY_MARKERS) and not _is_ours(command, net_dir)


def _drop_hooks(settings: dict, keep) -> dict:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for event in list(hooks):
        groups = []
        for group in hooks[event] if isinstance(hooks[event], list) else []:
            if not isinstance(group, dict):
                groups.append(group)
                continue
            items = [h for h in group.get("hooks") or [] if not isinstance(h, dict) or keep(str(h.get("command", "")))]
            if items:
                groups.append({**group, "hooks": items})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    return settings


def guard_matchers(matcher: str, strict: bool) -> list[str]:
    """PreToolUse matchers for the guard: send names only, or (strict) every MCP tool plus Bash and file edits."""
    return [STRICT_MCP_MATCHER, STRICT_FILE_MATCHER] if strict else [matcher]


def install_user_hooks(settings: dict, net_dir: Path, matchers: list[str]) -> dict:
    """Replace our hooks and any manual (legacy) session_check hooks with the current ones."""
    settings = _drop_hooks(settings, lambda c: not _is_ours(c, net_dir) and not _is_legacy(c, net_dir))
    commands = _hook_commands(net_dir)
    hooks = settings.setdefault("hooks", {})
    hooks.setdefault("SessionStart", []).append(
        {"hooks": [{"type": "command", "command": commands["watchdog"], "timeout": 10}]})
    for matcher in matchers:
        hooks.setdefault("PreToolUse", []).append(
            {"matcher": matcher, "hooks": [{"type": "command", "command": commands["guard"], "timeout": 10}]})
    return settings


def remove_user_hooks(settings: dict, net_dir: Path) -> dict:
    return _drop_hooks(settings, lambda c: not _is_ours(c, net_dir))


def add_deny(settings: dict, names: list[str]) -> dict:
    deny = settings.setdefault("permissions", {}).setdefault("deny", [])
    deny += [n for n in names if n not in deny]
    return settings


def remove_deny(settings: dict, names: list[str]) -> dict:
    perms = settings.get("permissions")
    if isinstance(perms, dict) and isinstance(perms.get("deny"), list):
        perms["deny"] = [n for n in perms["deny"] if n not in names]
        if not perms["deny"]:
            del perms["deny"]
        if not perms:
            del settings["permissions"]
    return settings


# --- plan --------------------------------------------------------------------

def _load_session_check(plugin_root: Path):
    spec = importlib.util.spec_from_file_location("_og_session_check", plugin_root / "hooks" / "session_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gitignore_warning(project: Path) -> str | None:
    try:
        inside = subprocess.run(["git", "-C", str(project), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True, timeout=10)
        if inside.returncode != 0:
            return None
        ignored = subprocess.run(["git", "-C", str(project), "check-ignore", "-q", ".gates/"],
                                 capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if ignored.returncode == 0:
        return None
    return f"{project}/.gates/ is not git-ignored; add the line `.gates/` to {project}/.gitignore"


def _user_scope_warning(home: Path) -> str | None:
    record = read_settings(home / ".claude" / "plugins" / "installed_plugins.json")
    for install in (record.get("plugins") or {}).get("outbound-gates@outbound-gates") or []:
        if isinstance(install, dict) and install.get("scope") == "user":
            return ("the outbound-gates plugin is installed at user scope, so its gate blocks Gmail "
                    "sends in every project; reinstall it with --scope local in your outbound folder")
    return None


def _copies(plugin_root: Path, version: str, names: dict) -> dict:
    source = (plugin_root / "hooks" / "session_check.py").read_text(encoding="utf-8")
    stamped, count = re.subn(r'^VERSION = "dev".*$', f'VERSION = "{version}"', source, count=1, flags=re.M)
    if count != 1:
        raise SetupError("hooks/session_check.py has no VERSION line to stamp")
    blocked = {"schema": SCHEMA, "version": version, **names}
    return {"session_check.py": stamped.encode("utf-8"),
            "gate-net.sh": (plugin_root / "hooks" / "gate-net.sh").read_bytes(),
            "setup.py": Path(__file__).read_bytes(),
            "gate": GATE_LAUNCHER.replace("__PLUGIN_ROOT__", repr(str(plugin_root))).encode("utf-8"),
            "blocked.json": (json.dumps(blocked, indent=2) + "\n").encode("utf-8")}


def plan_config(project: Path, home: Path, crm_source: str | None, crm_path: str | None,
                sender_owner: str | None) -> dict:
    """Guided first run: what to add to .gates/config.json, or why the step is skipped."""
    if not crm_source:
        if crm_path or sender_owner:
            return {"add": {}, "error": "--crm-path and --sender-owner need --crm-source"}
        return {"add": {}, "error": None}
    wanted = {"crm_source": crm_source}
    problems = []
    if crm_source == "file":
        if not crm_path:
            problems.append("file mode needs --crm-path")
        else:
            target = Path(crm_path).expanduser()
            target = target if target.is_absolute() else project / target
            if not target.is_file():
                problems.append(f"CRM file not found: {target}")
            wanted["crm_path"] = crm_path
    else:  # hubspot
        if not sender_owner:
            problems.append("HubSpot mode needs --sender-owner (your HubSpot owner id)")
        key = home / ".config" / "outbound-gates" / "hubspot.key"
        if not key.is_file():
            problems.append(f"no HubSpot key at {key}: save a read-only Service Key there, then chmod 600 {key}")
        else:
            lines = key.read_text(encoding="utf-8").splitlines()
            if len(lines) != 1 or not lines[0] or re.search(r"\s", lines[0]):
                problems.append(f"{key} must hold exactly one token on one line (it is never printed)")
            if os.stat(key).st_mode & 0o077:
                problems.append(f"{key} is readable by others: run chmod 600 {key}")
    if sender_owner:
        wanted["sender_owner"] = sender_owner
    current = read_settings(project / ".gates" / "config.json")
    conflicts = [f"{k} is already {current[k]!r}" for k, v in wanted.items() if k in current and current[k] != v]
    if conflicts:
        problems.append("existing .gates/config.json differs (" + "; ".join(conflicts) + "); edit it by hand")
    add = {k: v for k, v in wanted.items() if k not in current}
    return {"add": add if not problems else {}, "error": "; ".join(problems) or None}


STYLES = {"neutral": {}, "example": {"preset": "example"}}


def _load_checker(plugin_root: Path):
    spec = importlib.util.spec_from_file_location("_og_checker", plugin_root / "gates" / "checker.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while it loads
    spec.loader.exec_module(module)
    return module


def style_sha(project: Path) -> str | None:
    """The sha256 of the project's style.json, or None when it has none."""
    path = project / ".gates" / "style.json"
    return _sha(path.read_bytes()) if path.is_file() else None


def plan_style(project: Path, plugin_root: Path, choice: str | None) -> dict:
    """What to write to .gates/style.json, or why the step is skipped.

    A new project (no .gates/ yet) gets the neutral style unless --style says
    otherwise. An existing project is left alone unless --style is given, and an
    existing style.json that differs is never overwritten.
    """
    path = project / ".gates" / "style.json"
    if choice is None and (project / ".gates").exists():
        return {"write": None, "label": None, "error": None}
    choice = choice or "neutral"
    if choice in STYLES:
        style, label = STYLES[choice], choice
    else:
        source = Path(choice).expanduser()
        try:
            style = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            raise SetupError(f"cannot read --style file {source}: {err}") from None
        label = f"from {source}"
    checker = _load_checker(plugin_root)
    try:
        checker.validate_style(style, f"--style {choice}")
    except checker.UsageError as err:
        raise SetupError(str(err)) from None
    data = (json.dumps(style, indent=2) + "\n").encode("utf-8")
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            current = None
        if current == style:
            return {"write": None, "label": label, "error": None}
        return {"write": None, "label": label,
                "error": f"{path} already holds a different style; edit it by hand or move it away first"}
    return {"write": data, "label": label, "error": None}


def check_names(names_file: str | None, deny_rules: list[str], project: Path, home: Path) -> list[str]:
    """Advisory: does each deny rule name a tool the agent listed, one already denied, or neither?"""
    if not names_file:
        return []
    try:
        loaded = {line.strip() for line in Path(names_file).read_text(encoding="utf-8").splitlines() if line.strip()}
    except OSError as err:
        raise SetupError(f"cannot read --check-names file: {err}") from None
    denied = set()
    for path in (home / ".claude" / "settings.json", project / ".claude" / "settings.json",
                 project / ".claude" / "settings.local.json"):
        denied |= set((read_settings(path).get("permissions") or {}).get("deny") or [])
    lines = ["Name check (advisory: the agent supplied the tool list):"]
    for rule in deny_rules:
        state = ("matches a loaded tool" if rule in loaded else
                 "already denied (hidden from the agent, so it can't be listed)" if rule in denied else
                 "possibly dead: no loaded tool has this name")
        lines.append(f"    {rule}: {state}")
    return lines


def plan_install(project: Path, home: Path, plugin_root: Path, managed: list[Path] | None = None,
                 strict: bool | None = None, config: dict | None = None,
                 names_file: str | None = None, style: dict | None = None) -> dict:
    names = load_names(plugin_root)
    net_dir = home.resolve() / ".config" / "outbound-gates"
    user_path = home / ".claude" / "settings.json"
    local_path = project / ".claude" / "settings.local.json"
    user = read_settings(user_path)
    local = read_settings(local_path)
    deny_now = (local.get("permissions") or {}).get("deny") or []
    deny_new = [n for n in names["deny_rules"] if n not in deny_now]
    legacy = []
    for groups in (user.get("hooks") or {}).values():
        for group in groups if isinstance(groups, list) else []:
            for hook in (group.get("hooks") or []) if isinstance(group, dict) else []:
                command = str(hook.get("command", "")) if isinstance(hook, dict) else ""
                if _is_legacy(command, net_dir):
                    legacy.append(command)
    check = _load_session_check(plugin_root)
    warnings = check.policy_warnings(project, home, managed)
    manifest = read_settings(net_dir / "installed.json")
    projects = manifest.get("projects") or {}
    if strict is None:  # sticky: a re-run keeps the project's current mode
        strict = bool((projects.get(str(project)) or {}).get("strict"))
    others_strict = any((v or {}).get("strict") for k, v in projects.items() if k != str(project))
    warnings += [w for w in (_gitignore_warning(project), _user_scope_warning(home)) if w]
    version = plugin_version(plugin_root)
    copies = _copies(plugin_root, version, names)
    config = config or {"add": {}, "error": None}
    style = style or {"write": None, "label": None, "error": None}
    plan = {"project": project, "home": home, "plugin_root": plugin_root, "net_dir": net_dir,
            "version": version, "names": names, "deny_new": deny_new,
            "legacy": legacy, "warnings": warnings, "user_path": user_path, "local_path": local_path,
            "strict": strict, "any_strict": strict or others_strict, "copies": copies, "config": config,
            "style": style,
            "name_check": check_names(names_file, names["deny_rules"], project, home)}
    plan["hash"] = plan_hash({"action": "install", "project": str(project), "version": version,
                              "deny_new": deny_new, "legacy": legacy, "strict": strict,
                              "matchers": guard_matchers(names["matcher"], plan["any_strict"]),
                              "copies": {k: _sha(v) for k, v in copies.items()}, "config": config["add"],
                              "style": _sha(style["write"]) if style["write"] else None})
    return plan


def describe_install(plan: dict) -> list[str]:
    net = plan["net_dir"]
    lines = [f"Setup plan for {plan['project']} (plugin {plan['version']}):",
             f"  copy session_check.py, gate-net.sh, blocked.json, setup.py and the gate command into {net}",
             f"  {plan['user_path']}: SessionStart -> gate-net.sh watchdog; PreToolUse -> gate-net.sh draft-guard",
             f"    guard matcher covers {len(plan['names']['guard_names'])} send names plus Gmail drafts, on any server"
             if not plan["any_strict"] else
             "    guard runs on every MCP tool and on Bash, Edit and Write (a strict project is set up)"]
    if plan["strict"]:
        lines += [f"  strict mode for {plan['project']}: only read tools and {net / 'allow.json'} entries run;",
                  "    Bash mail commands, data-sending curl/wget and edits to the gate's own files are refused"]
    for command in plan["legacy"]:
        lines.append(f"    replace manual hook: {command}")
    if plan["deny_new"]:
        lines.append(f"  {plan['local_path']}: add {len(plan['deny_new'])} deny rules:")
        lines += [f"    {n}" for n in plan["deny_new"]]
    else:
        lines.append(f"  {plan['local_path']}: deny rules already present")
    if not (plan["project"] / ".gates").is_dir():
        lines.append(f"  create {plan['project'] / '.gates'}")
    if plan["config"]["add"]:
        lines.append(f"  {plan['project'] / '.gates' / 'config.json'}: add " +
                     ", ".join(f"{k}={v!r}" for k, v in plan["config"]["add"].items()))
    if plan["config"]["error"]:
        lines.append(f"  first-run config step skipped: {plan['config']['error']}")
    style_path = plan["project"] / ".gates" / "style.json"
    if plan["style"]["write"]:
        label = plan["style"]["label"]
        lines.append(f"  {style_path}: write " + (f"the {label} style" if label in STYLES else f"the style {label}"))
    elif plan["style"]["error"]:
        lines.append(f"  style step skipped: {plan['style']['error']}")
    elif not style_path.exists():
        lines.append(f"  {style_path}: none, so the gate keeps the example style "
                     "(word counts, sign-offs, closing line); --style neutral|example|FILE sets one")
    lines += [f"  warning: {w}" for w in plan["warnings"]]
    lines += plan["name_check"]
    lines.append(f"PLANHASH {plan['hash']}")
    return lines


def apply_install(plan: dict, stamp: str) -> list[str]:
    net, root = plan["net_dir"], plan["plugin_root"]
    net.mkdir(parents=True, exist_ok=True)
    os.chmod(net, 0o700)
    for name, data in plan["copies"].items():
        atomic_write(net / name, data, 0o700 if name in ("gate-net.sh", "gate") else 0o600)
    remove_old_launcher(net)

    changed = []
    matchers = guard_matchers(plan["names"]["matcher"], plan["any_strict"])
    if write_settings(plan["user_path"], lambda s: install_user_hooks(s, net, matchers), stamp):
        changed.append(str(plan["user_path"]))
    if write_settings(plan["local_path"], lambda s: add_deny(s, plan["names"]["deny_rules"]), stamp):
        changed.append(str(plan["local_path"]))
    (plan["project"] / ".gates").mkdir(exist_ok=True)
    if plan["config"]["add"]:
        config_path = plan["project"] / ".gates" / "config.json"
        merged = {**read_settings(config_path), **plan["config"]["add"]}
        atomic_write(config_path, (json.dumps(merged, indent=2) + "\n").encode("utf-8"))
        changed.append(str(config_path))
    if plan["style"]["write"]:
        style_path = plan["project"] / ".gates" / "style.json"
        atomic_write(style_path, plan["style"]["write"])
        changed.append(str(style_path))

    manifest_path = net / "installed.json"
    manifest = read_settings(manifest_path) or {"schema": SCHEMA, "projects": {}}
    entry = manifest.setdefault("projects", {}).setdefault(str(plan["project"]), {"deny_added": []})
    entry["deny_added"] += [n for n in plan["deny_new"] if n not in entry["deny_added"]]
    entry["strict"] = bool(plan["strict"])
    entry["deny_rules"] = list(plan["names"]["deny_rules"])
    entry["style_sha256"] = style_sha(plan["project"])  # the session check reports a change to the person
    if plan["strict"] and not (net / "allow.json").exists():
        atomic_write(net / "allow.json", (json.dumps({"schema": SCHEMA, "allow": []}, indent=2) + "\n").encode(), 0o600)
    manifest.update({"schema": SCHEMA, "version": plan["version"], "user_hooks": True,
                     "files": {k: _sha(v) for k, v in plan["copies"].items()},
                     "matchers": guard_matchers(plan["names"]["matcher"], plan["any_strict"])})
    atomic_write(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode("utf-8"), 0o600)
    append_log(net, {"action": "install", "project": str(plan["project"]), "version": plan["version"],
                     "planhash": plan["hash"], "strict": bool(plan["strict"])})
    return changed


def plan_uninstall(project: Path, home: Path, remove_all: bool) -> dict:
    net = home.resolve() / ".config" / "outbound-gates"
    manifest = read_settings(net / "installed.json")
    projects = manifest.get("projects") or {}
    targets = list(projects) if remove_all else [p for p in projects if p == str(project)]
    remaining = [p for p in projects if p not in targets]
    plan = {"home": home, "net_dir": net, "manifest": manifest, "targets": targets,
            "user_too": bool(manifest) and not remaining, "user_path": home / ".claude" / "settings.json"}
    plan["hash"] = plan_hash({"action": "uninstall", "targets": targets, "user_too": plan["user_too"],
                              "deny": {t: projects[t].get("deny_added") for t in targets}})
    return plan


def describe_uninstall(plan: dict) -> list[str]:
    if not plan["manifest"]:
        return ["Nothing to uninstall: no installed.json in " + str(plan["net_dir"])]
    lines = ["Uninstall plan:"]
    for project in plan["targets"]:
        added = plan["manifest"]["projects"][project].get("deny_added") or []
        lines.append(f"  {project}/.claude/settings.local.json: remove {len(added)} deny rules setup added")
    if not plan["targets"]:
        lines.append("  this project has no entries from setup")
    if plan["user_too"]:
        lines.append(f"  {plan['user_path']}: remove the gate-net.sh hooks")
        lines.append(f"  delete {', '.join(NET_FILES + ('installed.json',))} from {plan['net_dir']}")
    else:
        lines.append("  user-level hooks stay: other projects still use them (use --all to remove everything)")
    lines.append(f"PLANHASH {plan['hash']}")
    return lines


def apply_uninstall(plan: dict, stamp: str) -> list[str]:
    changed = []
    manifest = plan["manifest"]
    for project in plan["targets"]:
        added = manifest["projects"][project].get("deny_added") or []
        path = Path(project) / ".claude" / "settings.local.json"
        if path.exists() and write_settings(path, lambda s: remove_deny(s, added), stamp):
            changed.append(str(path))
        del manifest["projects"][project]
    net = plan["net_dir"]
    if plan["user_too"]:
        if write_settings(plan["user_path"], lambda s: remove_user_hooks(s, net), stamp):
            changed.append(str(plan["user_path"]))
        for name in NET_FILES + ("installed.json",):
            (net / name).unlink(missing_ok=True)
    elif manifest:
        atomic_write(net / "installed.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"), 0o600)
    for project in plan["targets"]:
        append_log(net, {"action": "uninstall", "project": project, "planhash": plan["hash"],
                         "all": bool(plan["user_too"])})
    return changed


# --- command line --------------------------------------------------------------

def _confirm(given: str | None, plan: dict) -> bool:
    """None: preview only. A hash: apply if it matches. Empty: refuse (bad input)."""
    if given is None:
        return False
    if not given:
        raise SetupError("--yes needs the PLANHASH printed by the preview")
    if given != plan["hash"]:
        raise PlanChanged(f"the plan changed since the preview (PLANHASH {given} is now {plan['hash']}); "
                          "run the preview again and review it")
    return True


def main(argv: list[str] | None = None, home: Path | None = None, managed: list[Path] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or remove the outbound-gates safety net.")
    parser.add_argument("--project", required=True, help="the outbound project folder")
    parser.add_argument("--plugin-root", help="the installed plugin folder (needed to install)")
    parser.add_argument("--uninstall", action="store_true", help="remove what setup added for this project")
    parser.add_argument("--all", action="store_true", help="with --uninstall: every project and the user-level hooks")
    parser.add_argument("--yes", nargs="?", const="", metavar="PLANHASH",
                        help="apply the plan; needs the PLANHASH printed by the preview")
    parser.add_argument("--strict", action="store_true",
                        help="strict mode for this project: allow-list for MCP tools, Bash and file-edit guard")
    parser.add_argument("--no-strict", action="store_true", help="turn strict mode off for this project")
    parser.add_argument("--check-names", metavar="FILE",
                        help="preview only: compare deny rules with the tool names in FILE (advisory)")
    parser.add_argument("--crm-source", choices=["file", "hubspot"], help="first run: where the gate reads the CRM")
    parser.add_argument("--crm-path", help="first run, file mode: the CRM export")
    parser.add_argument("--sender-owner", help="first run: your HubSpot owner id")
    parser.add_argument("--style", metavar="neutral|example|FILE",
                        help="the project's writing style (.gates/style.json); new projects get neutral")
    args = parser.parse_args(argv)
    home = home or Path.home()
    project = Path(args.project).expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    net_dir = home.resolve() / ".config" / "outbound-gates"
    try:
        if args.uninstall:
            plan = plan_uninstall(project, home, args.all)
            print("\n".join(describe_uninstall(plan)))
            if not plan["manifest"]:
                return 0
            if not _confirm(args.yes, plan):
                print(f"\nTo apply, run: python3 \"{plan['net_dir'] / 'setup.py'}\" --project \"{project}\" --uninstall"
                      + (" --all" if args.all else "") + f" --yes {plan['hash']}")
                return 0
            with Lock(net_dir):
                plan = plan_uninstall(project, home, args.all)
                _confirm(args.yes, plan)
                for path in apply_uninstall(plan, stamp):
                    print(f"changed {path} (backup kept next to it)")
            print("Uninstalled. Start a new session for Claude Code to drop the hooks.")
            return 0

        if not args.plugin_root:
            raise SetupError("--plugin-root is required to install")
        plugin_root = Path(args.plugin_root).expanduser().resolve()
        if not project.is_dir():
            raise SetupError(f"project folder not found: {project}")
        if args.strict and args.no_strict:
            raise SetupError("use --strict or --no-strict, not both")
        if args.check_names and args.yes is not None:
            raise SetupError("--check-names is for the preview only")
        mode = True if args.strict else False if args.no_strict else None

        def build():
            config = plan_config(project, home, args.crm_source, args.crm_path, args.sender_owner)
            style = plan_style(project, plugin_root, args.style)
            return plan_install(project, home, plugin_root, managed, mode, config, args.check_names, style)

        plan = build()
        print("\n".join(describe_install(plan)))
        if not _confirm(args.yes, plan):
            extra = (" --strict" if args.strict else " --no-strict" if args.no_strict else "")
            for flag, value in (("--crm-source", args.crm_source), ("--crm-path", args.crm_path),
                                ("--sender-owner", args.sender_owner), ("--style", args.style)):
                if value:
                    extra += f' {flag} "{value}"'
            print(f"\nTo apply, run: python3 \"{plugin_root / 'gates' / 'setup.py'}\" --project \"{project}\" "
                  f"--plugin-root \"{plugin_root}\"{extra} --yes {plan['hash']}")
            return 0
        with Lock(net_dir):
            plan = build()  # re-read everything under the lock; the hash must still match
            _confirm(args.yes, plan)
            for path in apply_install(plan, stamp):
                print(f"changed {path} (backup kept next to it)")
    except SetupError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except Refused as err:
        print(f"refused: {err}", file=sys.stderr)
        return 1
    except PlanChanged as err:
        print(f"plan changed: {err}", file=sys.stderr)
        return 3

    test = subprocess.run([sys.executable, str(plan["net_dir"] / "session_check.py"), "selftest", str(project)],
                          capture_output=True, text=True, env={**os.environ, "HOME": str(home)})
    print(test.stdout, end="")
    if plan["config"]["error"]:
        print(f"First-run config step was skipped: {plan['config']['error']}")
    if plan["style"]["error"]:
        print(f"Style step was skipped: {plan['style']['error']}")
    print("Start a new Claude Code session in this folder for the hooks to take effect.")
    return 0 if test.returncode == 0 and not plan["config"]["error"] and not plan["style"]["error"] else 1


if __name__ == "__main__":
    sys.exit(main())
