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

A person runs it, in two steps (the agent may run only the first):

  python3 <plugin>/gates/setup.py --project DIR --plugin-root <plugin>          # preview
  python3 <plugin>/gates/setup.py --project DIR --plugin-root <plugin> --yes    # apply
  python3 ~/.config/outbound-gates/setup.py --project DIR --uninstall [--all] [--yes]

Uninstall runs from the copy, so it works after the plugin is removed. Setup
only changes entries it adds; everything else in each settings file is re-read
just before writing and kept. Each changed settings file gets a backup.

Standalone and stdlib only (it is copied out of the plugin), so it does not
import the gates package. Exit codes: 0 done, 1 refused or selftest failed,
2 bad input.
"""
from __future__ import annotations

import argparse
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
NET_FILES = ("session_check.py", "gate-net.sh", "blocked.json", "setup.py")
LEGACY_MARKERS = ("session_check.py watchdog", "session_check.py draft-guard")
DRAFT_NAMES = ["create_draft", "update_draft"]


class SetupError(Exception):
    """Bad input or a file setup must not touch. Nothing has been written."""


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


def install_user_hooks(settings: dict, net_dir: Path, matcher: str) -> dict:
    """Replace our hooks and any manual (legacy) session_check hooks with the current ones."""
    settings = _drop_hooks(settings, lambda c: not _is_ours(c, net_dir) and not _is_legacy(c, net_dir))
    commands = _hook_commands(net_dir)
    hooks = settings.setdefault("hooks", {})
    hooks.setdefault("SessionStart", []).append(
        {"hooks": [{"type": "command", "command": commands["watchdog"], "timeout": 10}]})
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


def plan_install(project: Path, home: Path, plugin_root: Path, managed: list[Path] | None = None) -> dict:
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
    warnings += [w for w in (_gitignore_warning(project), _user_scope_warning(home)) if w]
    return {"project": project, "home": home, "plugin_root": plugin_root, "net_dir": net_dir,
            "version": plugin_version(plugin_root), "names": names, "deny_new": deny_new,
            "legacy": legacy, "warnings": warnings, "user_path": user_path, "local_path": local_path}


def describe_install(plan: dict) -> list[str]:
    net = plan["net_dir"]
    lines = [f"Setup plan for {plan['project']} (plugin {plan['version']}):",
             f"  copy session_check.py, gate-net.sh, blocked.json, setup.py into {net}",
             f"  {plan['user_path']}: SessionStart -> gate-net.sh watchdog; PreToolUse -> gate-net.sh draft-guard",
             f"    guard matcher covers {len(plan['names']['guard_names'])} send names plus Gmail drafts, on any server"]
    for command in plan["legacy"]:
        lines.append(f"    replace manual hook: {command}")
    if plan["deny_new"]:
        lines.append(f"  {plan['local_path']}: add {len(plan['deny_new'])} deny rules:")
        lines += [f"    {n}" for n in plan["deny_new"]]
    else:
        lines.append(f"  {plan['local_path']}: deny rules already present")
    if not (plan["project"] / ".gates").is_dir():
        lines.append(f"  create {plan['project'] / '.gates'}")
    lines += [f"  warning: {w}" for w in plan["warnings"]]
    return lines


def apply_install(plan: dict, stamp: str) -> list[str]:
    net, root = plan["net_dir"], plan["plugin_root"]
    net.mkdir(parents=True, exist_ok=True)
    os.chmod(net, 0o700)
    source = (root / "hooks" / "session_check.py").read_text(encoding="utf-8")
    stamped, count = re.subn(r'^VERSION = "dev".*$', f'VERSION = "{plan["version"]}"', source, count=1, flags=re.M)
    if count != 1:
        raise SetupError("hooks/session_check.py has no VERSION line to stamp")
    atomic_write(net / "session_check.py", stamped.encode("utf-8"), 0o600)
    atomic_write(net / "gate-net.sh", (root / "hooks" / "gate-net.sh").read_bytes(), 0o700)
    atomic_write(net / "setup.py", Path(__file__).read_bytes(), 0o600)
    blocked = {"schema": SCHEMA, "version": plan["version"], **plan["names"]}
    atomic_write(net / "blocked.json", (json.dumps(blocked, indent=2) + "\n").encode("utf-8"), 0o600)

    changed = []
    if write_settings(plan["user_path"], lambda s: install_user_hooks(s, net, plan["names"]["matcher"]), stamp):
        changed.append(str(plan["user_path"]))
    if write_settings(plan["local_path"], lambda s: add_deny(s, plan["names"]["deny_rules"]), stamp):
        changed.append(str(plan["local_path"]))
    (plan["project"] / ".gates").mkdir(exist_ok=True)

    manifest_path = net / "installed.json"
    manifest = read_settings(manifest_path) or {"schema": SCHEMA, "projects": {}}
    entry = manifest.setdefault("projects", {}).setdefault(str(plan["project"]), {"deny_added": []})
    entry["deny_added"] += [n for n in plan["deny_new"] if n not in entry["deny_added"]]
    manifest.update({"schema": SCHEMA, "version": plan["version"], "user_hooks": True})
    atomic_write(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode("utf-8"), 0o600)
    return changed


def plan_uninstall(project: Path, home: Path, remove_all: bool) -> dict:
    net = home.resolve() / ".config" / "outbound-gates"
    manifest = read_settings(net / "installed.json")
    projects = manifest.get("projects") or {}
    targets = list(projects) if remove_all else [p for p in projects if p == str(project)]
    remaining = [p for p in projects if p not in targets]
    return {"home": home, "net_dir": net, "manifest": manifest, "targets": targets,
            "user_too": bool(manifest) and not remaining, "user_path": home / ".claude" / "settings.json"}


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
    return changed


# --- command line --------------------------------------------------------------

def main(argv: list[str] | None = None, home: Path | None = None, managed: list[Path] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or remove the outbound-gates safety net.")
    parser.add_argument("--project", required=True, help="the outbound project folder")
    parser.add_argument("--plugin-root", help="the installed plugin folder (needed to install)")
    parser.add_argument("--uninstall", action="store_true", help="remove what setup added for this project")
    parser.add_argument("--all", action="store_true", help="with --uninstall: every project and the user-level hooks")
    parser.add_argument("--yes", action="store_true", help="apply the plan (without it, only print it)")
    args = parser.parse_args(argv)
    home = home or Path.home()
    project = Path(args.project).expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        if args.uninstall:
            plan = plan_uninstall(project, home, args.all)
            print("\n".join(describe_uninstall(plan)))
            if not plan["manifest"]:
                return 0
            if not args.yes:
                print(f"\nTo apply, run: python3 \"{plan['net_dir'] / 'setup.py'}\" --project \"{project}\" --uninstall"
                      + (" --all" if args.all else "") + " --yes")
                return 0
            for path in apply_uninstall(plan, stamp):
                print(f"changed {path} (backup kept next to it)")
            print("Uninstalled. Start a new session for Claude Code to drop the hooks.")
            return 0

        if not args.plugin_root:
            raise SetupError("--plugin-root is required to install")
        plugin_root = Path(args.plugin_root).expanduser().resolve()
        if not project.is_dir():
            raise SetupError(f"project folder not found: {project}")
        plan = plan_install(project, home, plugin_root, managed)
        print("\n".join(describe_install(plan)))
        if not args.yes:
            print(f"\nTo apply, run: python3 \"{plugin_root / 'gates' / 'setup.py'}\" --project \"{project}\" "
                  f"--plugin-root \"{plugin_root}\" --yes")
            return 0
        for path in apply_install(plan, stamp):
            print(f"changed {path} (backup kept next to it)")
    except SetupError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    test = subprocess.run([sys.executable, str(plan["net_dir"] / "session_check.py"), "selftest", str(project)],
                          capture_output=True, text=True, env={**os.environ, "HOME": str(home)})
    print(test.stdout, end="")
    print("Start a new Claude Code session in this folder for the hooks to take effect.")
    return 0 if test.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
