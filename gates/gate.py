"""og: one command for the send gate, installed by setup as ~/.config/outbound-gates/gate.

    gate status [--project DIR]            what is on, what is set, what happened today, what to do next
    gate check DRAFT --to ADDR [...]       the whole gate on a draft file (gates.evaluate)
    gate record ... / gate calc ...          record a verified source or a calculation (gates.evidence)
    gate propose CHANGE [--project DIR]    stage a person-only change and print its approval code
    gate yes CODE                          apply the staged changes (the person types this, with !)
    gate no                                drop the staged changes

Person-only changes are the ones that loosen what the agent may do or say:
own facts, names, the logging BCC, the allow list, a project's style, and a
safety-net refresh. The agent may propose them. Only the person applies them,
by typing `! ~/.config/outbound-gates/gate yes CODE`: commands typed with ! skip
the hooks, and in strict mode the guard refuses the agent's own `gate yes`. The
code is a hash of exactly what was staged, so a change slipped in after the
person read the proposal makes the code fail.

A hook's "ask" decision can't do this job: in bypass-permissions mode Claude
Code approves it without a prompt (see docs/adr/0001-approval-by-code.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import setup as net_setup
from .checker import UsageError, validate_style
from .evidence import NAME_RE

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 1
TOOL_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
STYLE_CHOICES = {"neutral": {}, "example": {"preset": "example"}}


class ProposalError(Exception):
    """A proposal that can't be staged or applied. Exit 1."""


def net_dir(home: Path) -> Path:
    return home.resolve() / ".config" / "outbound-gates"


def og_command(home: Path) -> str:
    """The gate path to type, with ~ for the home folder: shorter, and the same on every machine."""
    path = net_dir(home) / "gate"
    try:
        return "~/" + str(path.relative_to(home.resolve()))
    except ValueError:
        return str(path)


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    net_setup.atomic_write(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"), 0o600)


# --- proposals ------------------------------------------------------------------------

def approval_code(changes: list[dict]) -> str:
    return hashlib.sha256(json.dumps(changes, sort_keys=True).encode("utf-8")).hexdigest()[:4]


def describe(change: dict) -> str:
    op, value = change["op"], change.get("value")
    if op == "add-fact":
        return f"add own fact {value!r} (it will pass in any draft without a source)"
    if op == "add-name":
        return f"add name {value!r} (it stops counting as a number)"
    if op == "add-bcc":
        return f"add logging BCC {value!r} (drafts may copy it in bcc)"
    if op == "allow":
        return f"allow tool {value!r} in strict projects"
    if op == "style":
        return f"set {change['project']}/.gates/style.json to {json.dumps(value)}"
    if op == "refresh":
        return f"refresh the safety net for {change['project']} from plugin {change['version']}"
    return op


def _version_key(version: str) -> tuple:
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.+-]", version or "0"))


def _session_check():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_og_sc", PLUGIN_ROOT / "hooks" / "session_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_send_tool(short: str, home: Path) -> bool:
    sc = _session_check()
    names = [str(n) for n in _read(net_dir(home) / "blocked.json").get("guard_names") or sc.DEFAULT_SEND_NAMES]
    return sc.name_matches(short, names)


def make_change(op: str, value: str | None, project: Path, home: Path) -> dict:
    """One change, checked now so a bad one is refused when proposed, not when approved."""
    if op == "add-fact":
        if not value or not any(ch.isdigit() for ch in value):
            raise ProposalError("an own fact is a number as written in the email, e.g. '$0.01'")
        return {"op": op, "value": value}
    if op == "add-name":
        if not value or not NAME_RE.match(value):
            raise ProposalError("a name is one word with a letter and a digit, e.g. H3 or 1080p")
        return {"op": op, "value": value}
    if op == "add-bcc":
        if not value or value.count("@") != 1 or re.search(r"\s", value):
            raise ProposalError("a logging BCC is one email address")
        return {"op": op, "value": value.strip().lower()}
    if op == "allow":
        if not value or not TOOL_RE.match(value.split("__")[-1]):
            raise ProposalError("give the tool's name, e.g. manage_crm_objects")
        short = value.split("__")[-1]
        if _is_send_tool(short, home):
            raise ProposalError(f"{short} is a send tool; send tools stay blocked and can't be allowed")
        return {"op": op, "value": short}
    if op == "style":
        if value in STYLE_CHOICES:
            style = STYLE_CHOICES[value]
        else:
            try:
                style = json.loads(Path(str(value)).expanduser().read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as err:
                raise ProposalError(f"style must be neutral, example or a JSON file: {err}") from None
        try:
            validate_style(style, "proposed style")
        except UsageError as err:
            raise ProposalError(str(err)) from None
        if not (project / ".gates").is_dir():
            raise ProposalError(f"no .gates folder in {project}")
        return {"op": op, "value": style, "project": str(project)}
    if op == "refresh":
        if not (project / ".gates").is_dir():
            raise ProposalError(f"no .gates folder in {project}: run setup there first")
        return {"op": op, "project": str(project), "version": net_setup.plugin_version(PLUGIN_ROOT),
                "plugin_root": str(PLUGIN_ROOT)}
    raise ProposalError(f"unknown change {op!r}")


def propose(change: dict, home: Path) -> tuple[list[dict], str]:
    path = net_dir(home) / "pending.json"
    with net_setup.Lock(net_dir(home)):
        pending = _read(path)
        changes = [c for c in pending.get("changes") or [] if isinstance(c, dict)]
        if change not in changes:
            changes.append(change)
        _write(path, {"schema": SCHEMA, "changes": changes})
    return changes, approval_code(changes)


def _add_to_list(path: Path, key: str, value: str) -> None:
    data = _read(path)
    items = [v for v in data.get(key) or [] if isinstance(v, str)]
    if value not in items:
        items.append(value)
    data[key] = items
    _write(path, data)


def apply(change: dict, home: Path) -> str:
    net = net_dir(home)
    op = change["op"]
    if op in ("add-fact", "add-name", "add-bcc"):
        key = {"add-fact": "own_facts", "add-name": "names", "add-bcc": "crm_bcc"}[op]
        _add_to_list(net / "config.json", key, change["value"])
    elif op == "allow":
        data = _read(net / "allow.json") or {"schema": SCHEMA}
        data.setdefault("allow", [])
        _add_to_list(net / "allow.json", "allow", change["value"])
    elif op == "style":
        project = Path(change["project"])
        net_setup.atomic_write(project / ".gates" / "style.json",
                               (json.dumps(change["value"], indent=2) + "\n").encode("utf-8"))
        manifest = _read(net / "installed.json")
        entry = (manifest.get("projects") or {}).get(str(project))
        if entry is not None:  # record it, so the session start doesn't report it as an unexpected change
            entry["style_sha256"] = net_setup.style_sha(project)
            _write(net / "installed.json", manifest)
    elif op == "refresh":
        project, root = Path(change["project"]), Path(change["plugin_root"])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with net_setup.Lock(net):
            plan = net_setup.plan_install(project, home, root)
            net_setup.apply_install(plan, stamp)
    return describe(change)


def approve(code: str, home: Path) -> list[str]:
    net = net_dir(home)
    with net_setup.Lock(net):
        changes = [c for c in _read(net / "pending.json").get("changes") or [] if isinstance(c, dict)]
        if not changes:
            raise ProposalError("nothing is waiting for approval")
        if code != approval_code(changes):
            raise ProposalError(f"code {code!r} doesn't match what is staged now (the proposal changed "
                                "since it was printed). Run gate status to see it, then use its code")
        done = []
        for change in changes:
            if change["op"] == "refresh":
                continue  # needs the lock itself; run after
            done.append(apply(change, home))
        (net / "pending.json").unlink()
        net_setup.append_log(net, {"action": "approve", "code": code, "changes": changes})
    for change in changes:
        if change["op"] == "refresh":
            done.append(apply(change, home))
    return done


def drop(home: Path) -> int:
    path = net_dir(home) / "pending.json"
    count = len(_read(path).get("changes") or [])
    path.unlink(missing_ok=True)
    return count


# --- status ---------------------------------------------------------------------------

def status_lines(project: Path, home: Path, today: date | None = None) -> list[str]:
    from .evidence import load_settings

    net = net_dir(home)
    manifest = _read(net / "installed.json")
    entry = (manifest.get("projects") or {}).get(str(project)) or {}
    plugin = net_setup.plugin_version(PLUGIN_ROOT)
    copy = manifest.get("version")
    lines = [f"Project {project}"]
    nexts = []
    if not (project / ".gates").is_dir():
        lines.append("  gate: no .gates folder here, so drafts aren't checked in this folder")
        nexts.append("run /outbound-gates:setup in your outbound folder")
        return lines + ["Next:"] + [f"  - {n}" for n in nexts]
    lines.append(f"  plugin {plugin}; safety net {copy or 'not installed'}"
                 + ("; strict mode on" if entry.get("strict") else ""))
    if not copy:
        nexts.append("install the safety net: /outbound-gates:setup")
    elif _version_key(copy) < _version_key(plugin):
        nexts.append("the safety net is older than the plugin: gate propose refresh, then approve it")
    user_file = net / "config.json"
    try:
        settings = load_settings(PLUGIN_ROOT, project, user_config=user_file)
        rules = settings["rules"]
        lines.append(f"  style: {settings['style_source']}; CRM: {settings['crm_source']}")
        lines.append(f"  own facts: {', '.join(rules.get('own_facts') or []) or 'none'}")
        lines.append(f"  names: {', '.join(rules.get('names') or []) or 'none'}")
        lines.append(f"  logging BCC: {len(settings.get('crm_bcc') or [])} address(es)")
    except (UsageError, OSError, ValueError) as err:
        lines.append(f"  settings: can't load ({err})")
        nexts.append("fix the settings error above; the gate refuses every draft until then")
    allowed = _read(net / "allow.json").get("allow") or []
    lines.append(f"  allowed tools (strict): {', '.join(allowed) or 'only reads and Gmail drafts'}")
    day = (today or date.today()).isoformat()
    passed, refused, rules_hit = 0, 0, {}
    try:
        for raw in (project / ".gates" / "log" / "gate.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                entry_ = json.loads(raw)
            except ValueError:
                continue
            if not str(entry_.get("ts", "")).startswith(day):
                continue
            if entry_.get("decision") == "allow":
                passed += 1
            else:
                refused += 1
                for rule in entry_.get("rules") or []:
                    rules_hit[rule] = rules_hit.get(rule, 0) + 1
    except OSError:
        pass
    top = ", ".join(f"{r} x{n}" for r, n in sorted(rules_hit.items(), key=lambda kv: -kv[1])[:3])
    lines.append(f"  today: {passed} draft(s) passed, {refused} refused" + (f" ({top})" if top else ""))
    pending = [c for c in _read(net / "pending.json").get("changes") or [] if isinstance(c, dict)]
    if pending:
        lines.append(f"  waiting for your approval (code {approval_code(pending)}):")
        lines += [f"    - {describe(c)}" for c in pending]
        nexts.append(f"approve with: ! {og_command(home)} yes {approval_code(pending)}   "
                     f"(or drop: ! {og_command(home)} no)")
    if not nexts:
        nexts.append("draft: ask for one account, e.g. 'qualify and draft <company>, stop at the Gmail draft'")
    return lines + ["Next:"] + [f"  - {n}" for n in nexts]


# --- command line ---------------------------------------------------------------------

def main(argv: list[str] | None = None, home: Path | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    home = home or Path.home()
    if argv and argv[0] == "check":
        from .evaluate import main as evaluate_main
        return evaluate_main(argv[1:])
    if argv and argv[0] in ("record", "calc"):
        from .evidence import main as evidence_main
        rest = argv[1:]
        if "--gates-home" not in rest:
            rest += ["--gates-home", str(Path.cwd().resolve() / ".gates")]
        return evidence_main([argv[0]] + rest)

    parser = argparse.ArgumentParser(prog="gate", description="The send gate's one command.")
    sub = parser.add_subparsers(dest="command", required=True)
    st = sub.add_parser("status", help="what is on, what is set, today, and what to do next")
    st.add_argument("--project", default=".")
    pr = sub.add_parser("propose", help="stage a person-only change")
    pr.add_argument("change", choices=["add-fact", "add-name", "add-bcc", "allow", "style", "refresh"])
    pr.add_argument("value", nargs="?")
    pr.add_argument("--project", default=".")
    ys = sub.add_parser("yes", help="apply the staged changes (type this yourself, with !)")
    ys.add_argument("code")
    sub.add_parser("no", help="drop the staged changes")
    sub.add_parser("check", help="the whole gate on a draft file (see gate check --help)")
    sub.add_parser("record", help="record a verified source (see gate record --help)")
    sub.add_parser("calc", help="record a calculation (see gate calc --help)")
    args = parser.parse_args(argv)
    og_path = og_command(home)
    try:
        if args.command == "status":
            print("\n".join(status_lines(Path(args.project).expanduser().resolve(), home)))
            return 0
        if args.command == "propose":
            change = make_change(args.change, args.value, Path(args.project).expanduser().resolve(), home)
            changes, code = propose(change, home)
            print("Staged. Nothing changes until you approve:")
            print("\n".join(f"  - {describe(c)}" for c in changes))
            print(f"\nTo approve, type:  ! {og_path} yes {code}")
            print(f"To drop it:        ! {og_path} no")
            return 0
        if args.command == "yes":
            done = approve(args.code, home)
            for line in done:
                print(f"applied: {line}")
            if any(line.startswith("refresh") for line in done):
                print("Start a new Claude Code session for the refreshed safety net to take effect.")
            return 0
        if args.command == "no":
            print(f"dropped {drop(home)} staged change(s)")
            return 0
    except (ProposalError, net_setup.Refused) as err:
        print(f"refused: {err}", file=sys.stderr)
        return 1
    except (net_setup.SetupError, UsageError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
