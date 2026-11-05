"""hooks/session_check.py: the plugin heartbeat and the user-level watchdog."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hooks import session_check as sc


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Never read the real ~/.config/outbound-gates of the machine running the tests."""
    config = tmp_path / "config-outbound-gates"
    monkeypatch.setattr(sc, "CONFIG_DIR", config)
    return config

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    home, project = tmp_path / "home", tmp_path / "proj"
    (project / ".gates").mkdir(parents=True)
    (project / ".gates" / "style.json").write_text("{}\n")  # a project set up with a style
    install = home / ".claude" / "plugins" / "cache" / "og"
    write(install / "hooks" / "hooks.json", {"hooks": {"PreToolUse": [{"matcher": "mcp__.*"}]}})
    return home, project, install


def install(home, install_path, scope="local", project=None):
    entry = {"scope": scope, "installPath": str(install_path), "version": "0.1.0"}
    if project is not None:
        entry["projectPath"] = str(project)
    write(home / ".claude" / "plugins" / "installed_plugins.json", {"version": 2, "plugins": {sc.PLUGIN_ID: [entry]}})


def enable(path: Path, value=True):
    write(path, {"enabledPlugins": {sc.PLUGIN_ID: value}})


def run_watchdog(home, project):
    return sc.watchdog({"cwd": str(project)}, {}, home)


def test_silent_when_installed_and_enabled(env):
    home, project, path = env
    install(home, path, project=project)
    enable(project / ".claude" / "settings.local.json")
    assert run_watchdog(home, project) == ""


def test_silent_in_projects_without_gates(env, tmp_path):
    home, _, _ = env
    other = tmp_path / "other"
    other.mkdir()
    assert run_watchdog(home, other) == ""


def test_warns_when_not_installed(env):
    home, project, _ = env
    enable(project / ".claude" / "settings.local.json")
    out = json.loads(run_watchdog(home, project))
    assert "send gate OFF" in out["systemMessage"]
    assert "not installed for this project" in out["systemMessage"]
    assert "Do not create" in out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_warns_when_installed_for_another_project(env, tmp_path):
    home, project, path = env
    install(home, path, project=tmp_path / "elsewhere")
    enable(home / ".claude" / "settings.json")
    assert "not installed for this project" in run_watchdog(home, project)


def test_user_scope_install_covers_every_project(env):
    home, project, path = env
    install(home, path, scope="user")
    enable(home / ".claude" / "settings.json")
    assert run_watchdog(home, project) == ""


def test_warns_when_not_enabled(env):
    home, project, path = env
    install(home, path, project=project)
    assert "not enabled in any settings file" in run_watchdog(home, project)


def test_local_disable_beats_user_enable(env):
    home, project, path = env
    install(home, path, project=project)
    enable(home / ".claude" / "settings.json", True)
    enable(project / ".claude" / "settings.local.json", False)
    assert "disabled in local settings" in run_watchdog(home, project)


def test_warns_when_installed_copy_has_no_hook(env):
    home, project, path = env
    (path / "hooks" / "hooks.json").unlink()
    install(home, path, project=project)
    enable(project / ".claude" / "settings.local.json")
    assert "no PreToolUse hook" in run_watchdog(home, project)


def test_plugin_mode_logs_heartbeat_and_says_on(env):
    _, project, _ = env
    now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
    out = json.loads(sc.plugin({"session_id": "s1", "source": "startup", "cwd": str(project)}, {}, now))
    assert out["systemMessage"].startswith("send gate: on")
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert out["hookSpecificOutput"]["additionalContext"].startswith("send gate: on")
    assert "s1" in out["hookSpecificOutput"]["additionalContext"]
    line =json.loads((project / ".gates" / "log" / "sessions.jsonl").read_text().strip())
    assert line == {"v": 1, "ts": "2026-11-03T12:00:00+00:00", "event": "gate_loaded",
                    "session_id": "s1", "source": "startup"}


def test_plugin_mode_writes_nothing_without_gates(tmp_path):
    out = json.loads(sc.plugin({"cwd": str(tmp_path)}, {}))
    assert "on" in out["systemMessage"]
    assert not (tmp_path / ".gates").exists()


def test_real_hook_file_registers_both_hooks():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    assert "gate.sh" in hooks["PreToolUse"][0]["hooks"][0]["command"]
    assert hooks["SessionStart"][0]["hooks"][0]["command"].endswith("session_check.py\" plugin")


@pytest.mark.parametrize("stdin", ["", "not json", "[]"])
def test_script_never_fails_a_session(tmp_path, stdin):
    for mode in ("plugin", "watchdog", "bogus"):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "session_check.py"), mode],
            input=stdin, capture_output=True, text=True, cwd=tmp_path,
            env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(tmp_path)},
        )
        assert proc.returncode == 0
        if proc.stdout:
            json.loads(proc.stdout)


# draft-guard: no heartbeat for this session, no Gmail draft.

DRAFT = "mcp__claude_ai_Gmail__create_draft"


def heartbeat(project: Path, session_id: str) -> None:
    sc.plugin({"session_id": session_id, "source": "startup", "cwd": str(project)}, {})


def guard(project: Path, tool=DRAFT, session_id="s1") -> str:
    return sc.draft_guard({"tool_name": tool, "session_id": session_id, "cwd": str(project)}, {})


def is_deny(out: str) -> bool:
    return bool(out) and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("tool", [DRAFT, "mcp__claude_ai_Gmail__update_draft", "mcp__gmail__create_draft"])
def test_guard_denies_drafts_without_heartbeat(env, tool):
    _, project, _ = env
    out = guard(project, tool)
    assert is_deny(out)
    assert "did not load in this session" in out


def test_guard_allows_when_this_session_has_a_heartbeat(env):
    _, project, _ = env
    heartbeat(project, "s1")
    assert guard(project, session_id="s1") == ""


def test_guard_ignores_another_sessions_heartbeat(env):
    _, project, _ = env
    heartbeat(project, "old-session")
    assert is_deny(guard(project, session_id="s1"))


def test_guard_denies_without_session_id(env):
    _, project, _ = env
    heartbeat(project, "")
    assert is_deny(guard(project, session_id=""))


def test_guard_skips_bad_log_lines(env):
    _, project, _ = env
    log = project / ".gates" / "log" / "sessions.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("not json\n[]\n" + json.dumps({"event": "gate_loaded", "session_id": "s1"}) + "\n")
    assert guard(project) == ""


@pytest.mark.parametrize("tool", [
    "mcp__claude_ai_Gmail__list_drafts", "mcp__claude_ai_Gmail__get_thread",
    "mcp__claude_ai_Slack__create_draft", "Bash", "mcp__claude_ai_Gmail",
])
def test_guard_ignores_other_tools(env, tool):
    _, project, _ = env
    assert guard(project, tool) == ""


def test_guard_ignores_projects_without_gates(tmp_path):
    assert guard(tmp_path) == ""


def test_guard_script_fails_closed_on_crash(env, monkeypatch):
    _, project, _ = env
    monkeypatch.setattr(sc, "draft_guard", lambda *a: 1 / 0)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps({"tool_name": DRAFT})))
    out = __import__("io").StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert sc.main(["x", "draft-guard"]) == 0
    assert "guard crashed" in out.getvalue() and is_deny(out.getvalue())


def test_guard_script_end_to_end(env):
    _, project, _ = env
    script = str(ROOT / "hooks" / "session_check.py")
    payload = json.dumps({"tool_name": DRAFT, "session_id": "e2e", "cwd": str(project)})

    def run(mode, data):
        return subprocess.run([sys.executable, script, mode], input=data, capture_output=True,
                              text=True, env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(project)})

    assert is_deny(run("draft-guard", payload).stdout)
    run("plugin", json.dumps({"session_id": "e2e", "source": "startup"}))
    assert run("draft-guard", payload).stdout == ""


# --- backstop, stale copy, policy warnings, selftest, wrapper (installable net) ---

@pytest.mark.parametrize("tool,names,hit", [
    ("slack_send_message", ["send_message"], True),
    ("sendEmail", ["send_email"], True),
    ("send-mail", ["send_mail"], True),
    ("apollo_emailer_messages_send_now", ["send_now"], True),
    ("port_forward_list", ["forward"], False),
    ("resend_message", ["send_message"], False),
    ("list_drafts", ["send_message", "create_draft"], False),
])
def test_name_matches_by_whole_trailing_words(tool, names, hit):
    assert sc.name_matches(tool, names) is hit


@pytest.mark.parametrize("tool", ["mcp__self_hosted__sendEmail", "mcp__claude_ai_Slack__slack_send_message",
                                  "mcp__other__apollo_sequences_create", "mcp__x__reply"])
def test_guard_denies_send_tools_from_any_server_in_gated_projects(env, tool, monkeypatch, tmp_path):
    _, project, _ = env
    monkeypatch.setattr(sc, "NET_DIR", tmp_path / "net")  # no blocked.json: built-in names
    names = ["apollo_sequences_create"]
    (project / ".gates" / "config.json").write_text(json.dumps({"blocked_tools": names}))
    assert is_deny(sc.draft_guard({"tool_name": tool, "cwd": str(project)}, {}))


def test_guard_ignores_send_tools_outside_gated_projects(tmp_path):
    assert sc.draft_guard({"tool_name": "mcp__x__send_email", "cwd": str(tmp_path)}, {}) == ""


def test_guard_reads_names_from_blocked_json(env, monkeypatch, tmp_path):
    _, project, _ = env
    netdir = tmp_path / "net"
    netdir.mkdir()
    (netdir / "blocked.json").write_text(json.dumps({"guard_names": ["blast_campaign"]}))
    monkeypatch.setattr(sc, "NET_DIR", netdir)
    assert is_deny(sc.draft_guard({"tool_name": "mcp__x__blast_campaign", "cwd": str(project)}, {}))
    assert sc.draft_guard({"tool_name": "mcp__x__send_email", "cwd": str(project)}, {}) == ""


def test_stale_copy_warns_when_plugin_is_newer(env, monkeypatch):
    home, project, path = env
    install(home, path, project=project)  # version 0.1.0
    monkeypatch.setattr(sc, "VERSION", "0.0.9")
    assert "older than the plugin (0.1.0)" in sc.stale_copy(project, home)
    monkeypatch.setattr(sc, "VERSION", "0.1.0")
    assert sc.stale_copy(project, home) == ""
    monkeypatch.setattr(sc, "VERSION", "dev")
    assert sc.stale_copy(project, home) == ""


def test_watchdog_reports_a_stale_copy(env, monkeypatch):
    home, project, path = env
    install(home, path, project=project)
    enable(project / ".claude" / "settings.local.json")
    monkeypatch.setattr(sc, "VERSION", "0.0.1")
    assert "older than the plugin" in json.loads(run_watchdog(home, project))["systemMessage"]


def test_policy_warnings(env, tmp_path):
    home, project, _ = env
    managed = tmp_path / "managed.json"
    managed.write_text(json.dumps({"allowManagedHooksOnly": True, "allowManagedPermissionRulesOnly": True}))
    write(project / ".claude" / "settings.local.json", {"disableAllHooks": True})
    warnings = " ".join(sc.policy_warnings(project, home, [managed]))
    assert "allowManagedHooksOnly" in warnings and "allowManagedPermissionRulesOnly" in warnings
    assert "local settings" in warnings and "disableAllHooks" in warnings
    assert sc.policy_warnings(tmp_path / "nowhere", tmp_path / "nohome", []) == []


def test_selftest_fails_when_nothing_is_installed(env, monkeypatch, tmp_path):
    home, project, _ = env
    monkeypatch.setattr(sc, "NET_DIR", tmp_path / "net")
    passed, lines = sc.selftest(project, home, [])
    assert not passed
    assert any(l.startswith("FAIL") and "PreToolUse" in l for l in lines)


@pytest.mark.parametrize("mode,expect", [("draft-guard", "permissionDecision"), ("watchdog", "systemMessage")])
def test_wrapper_fails_safe_when_python_is_missing(tmp_path, mode, expect):
    (tmp_path / ".gates").mkdir()
    proc = subprocess.run(["sh", str(ROOT / "hooks" / "gate-net.sh"), mode], input="{}", capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(tmp_path), "GATE_PYTHON": "/nonexistent"})
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert expect in json.dumps(out)
    if mode == "draft-guard":
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_wrapper_passes_outside_gated_projects_even_when_broken(tmp_path):
    proc = subprocess.run(["sh", str(ROOT / "hooks" / "gate-net.sh"), "draft-guard"], input="{}", capture_output=True,
                          text=True, env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(tmp_path),
                                          "GATE_PYTHON": "/nonexistent"})
    assert proc.returncode == 0 and proc.stdout == ""


def test_wrapper_passes_the_guard_answer_through(tmp_path):
    (tmp_path / ".gates").mkdir()
    proc = subprocess.run(["sh", str(ROOT / "hooks" / "gate-net.sh"), "draft-guard"],
                          input=json.dumps({"tool_name": "mcp__x__send_email"}), capture_output=True, text=True,
                          env={"PATH": os.environ["PATH"], "CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert "is a send tool" in proc.stdout


# --- red-team quick wins ---

def set_up_project(tmp_path, monkeypatch, project):
    netdir = tmp_path / "net"
    netdir.mkdir(exist_ok=True)
    (netdir / "installed.json").write_text(json.dumps({"schema": 1, "projects": {str(project.resolve()): {}}}))
    monkeypatch.setattr(sc, "NET_DIR", netdir)


def test_guard_stays_on_when_gates_folder_is_deleted(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()  # set up by setup, but no .gates/
    set_up_project(tmp_path, monkeypatch, project)
    assert is_deny(sc.draft_guard({"tool_name": "mcp__x__send_email", "cwd": str(project)}, {}))
    assert is_deny(sc.draft_guard({"tool_name": "mcp__claude_ai_Gmail__create_draft", "session_id": "s",
                                   "cwd": str(project)}, {}))


def test_watchdog_warns_when_gates_folder_is_deleted(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    set_up_project(tmp_path, monkeypatch, project)
    out = json.loads(sc.watchdog({"cwd": str(project)}, {}, tmp_path / "home"))
    assert ".gates/ folder is missing" in out["systemMessage"]


def test_projects_never_set_up_stay_untouched(tmp_path, monkeypatch):
    set_up_project(tmp_path, monkeypatch, tmp_path / "elsewhere")
    other = tmp_path / "other"
    other.mkdir()
    assert sc.watchdog({"cwd": str(other)}, {}, tmp_path / "home") == ""
    assert sc.draft_guard({"tool_name": "mcp__x__send_email", "cwd": str(other)}, {}) == ""


# --- strict mode: allow-list, Bash guard, file guard ---

@pytest.fixture
def strict(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / ".gates").mkdir(parents=True)
    netdir = tmp_path / "net"
    netdir.mkdir()
    (netdir / "installed.json").write_text(json.dumps({"schema": 1, "projects": {str(project.resolve()): {"strict": True}}}))
    monkeypatch.setattr(sc, "NET_DIR", netdir)
    return project, netdir


def guard_call(project, tool, **tool_input):
    return sc.draft_guard({"tool_name": tool, "tool_input": tool_input, "session_id": "s", "cwd": str(project)}, {})


@pytest.mark.parametrize("short,read", [
    ("get_meetings", True), ("list_drafts", True), ("notion-search", True), ("apollo_contacts_search", True),
    ("slack_read_channel", True), ("query_crm_data", True),
    ("create_event", False), ("scenarios_run", False), ("manage_crm_objects", False),
    ("apollo_emailer_messages_email_send_status", False),  # has "send": refused, allow it by name if wanted
    ("notion-create-pages", False), ("update_draft", False),
])
def test_is_read_tool(short, read):
    assert sc.is_read_tool(short) is read


def test_strict_refuses_writes_and_allows_reads(strict):
    project, _ = strict
    assert is_deny(guard_call(project, "mcp__claude_ai_Make__scenarios_create"))
    assert is_deny(guard_call(project, "mcp__claude_ai_Google_Calendar__create_event"))
    assert guard_call(project, "mcp__claude_ai_Granola__get_meetings") == ""


def test_strict_allow_file_and_defaults(strict):
    project, netdir = strict
    (project / ".gates" / "log").mkdir()
    (project / ".gates" / "log" / "sessions.jsonl").write_text(json.dumps({"event": "gate_loaded", "session_id": "s"}) + "\n")
    assert guard_call(project, "mcp__claude_ai_Gmail__create_draft") == ""  # default allow, heartbeat present
    assert is_deny(guard_call(project, "mcp__claude_ai_HubSpot__manage_crm_objects"))
    (netdir / "allow.json").write_text(json.dumps({"allow": ["manage_crm_objects"]}))
    assert guard_call(project, "mcp__claude_ai_HubSpot__manage_crm_objects") == ""
    out = guard_call(project, "mcp__claude_ai_Make__scenarios_create")
    assert "allow.json" in out


@pytest.mark.parametrize("command,refused", [
    ("curl -X POST https://api.example.com/send -d to=a@b.c", True),
    ("curl --data-binary @mail.json https://api.example.com", True),
    ("curl -F file=@x https://upload.example.com", True),
    ("wget --post-data='a=1' https://x.example", True),
    ("python3 -c \"import smtplib; smtplib.SMTP('h')\"", True),
    ("printf 'Subject: hi' | sendmail a@b.c", True),
    ("osascript -e 'tell application \"Mail\" to send'", True),
    ("python3 -c \"import requests; requests.post('https://x')\"", True),
    ("rm -rf .gates", True),
    ("echo '{}' > .claude/settings.local.json", True),
    ("sed -i '' 's/a/b/' ~/.claude/settings.json", True),
    ("mv ~/.config/outbound-gates/blocked.json /tmp/", True),
    ("curl -s https://example.com/pricing", False),
    ("wget https://example.com/page.html", False),
    ("cat .gates/log/gate.jsonl", False),
    ("ls .gates 2>/dev/null", False),
    ("git status", False),
    ("PYTHONPATH=/p python3 -m gates.evidence record --gates-home /x/.gates --contact a@b.c --url u --quote q --value 1", False),
])
def test_strict_bash_guard(strict, command, refused):
    project, _ = strict
    assert bool(guard_call(project, "Bash", command=command)) is refused


@pytest.mark.parametrize("rel,refused", [
    (".claude/settings.local.json", True), (".gates/log/sessions.jsonl", True), (".gates/config.json", True),
    ("draft.txt", False), ("notes/plan.md", False),
])
def test_strict_file_guard(strict, rel, refused):
    project, _ = strict
    assert bool(guard_call(project, "Write", file_path=rel)) is refused
    assert bool(guard_call(project, "Edit", file_path=str(project / rel))) is refused


def test_strict_file_guard_protects_the_net_copy(strict):
    project, netdir = strict
    assert is_deny(guard_call(project, "Edit", file_path=str(netdir / "allow.json")))


def test_non_strict_projects_ignore_bash_and_files(env, monkeypatch, tmp_path):
    _, project, _ = env
    monkeypatch.setattr(sc, "NET_DIR", tmp_path / "net")
    assert guard_call(project, "Bash", command="curl -X POST https://x -d a=1") == ""
    assert guard_call(project, "Write", file_path=".claude/settings.local.json") == ""
    assert guard_call(project, "mcp__claude_ai_Make__scenarios_create") == ""


def test_strict_off_outside_gated_projects(strict, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    assert guard_call(other, "Bash", command="curl -X POST https://x -d a=1") == ""


# --- tamper tripwire and uninstalled note ---

def write_manifest(config, project, files=None, deny=None, user_hooks=True):
    config.mkdir(parents=True, exist_ok=True)
    (config / "installed.json").write_text(json.dumps({
        "schema": 1, "user_hooks": user_hooks, "files": files or {},
        "projects": {str(project.resolve()): {"deny_rules": deny or []}}}))


def test_tamper_reports_changed_and_missing_files(isolated_config, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (isolated_config).mkdir(parents=True)
    (isolated_config / "gate-net.sh").write_text("original")
    import hashlib
    sha = hashlib.sha256(b"original").hexdigest()
    write_manifest(isolated_config, project, files={"gate-net.sh": sha, "blocked.json": sha}, user_hooks=False)
    (isolated_config / "gate-net.sh").write_text("edited to always allow")
    found = " ".join(sc.tamper_findings(project, tmp_path / "home"))
    assert "gate-net.sh" in found and "changed after setup" in found
    assert "blocked.json is missing" in found


def test_tamper_reports_removed_hooks_and_deny_rules(isolated_config, tmp_path):
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"deny": ["a"]}}))
    write_manifest(isolated_config, project, deny=["a", "b"])
    found = " ".join(sc.tamper_findings(project, tmp_path / "home"))
    assert "watchdog hook was removed" in found and "draft-guard hook was removed" in found
    assert "1 deny rules were removed" in found


def test_no_manifest_no_findings(tmp_path):
    assert sc.tamper_findings(tmp_path, tmp_path) == []


def test_uninstalled_note(isolated_config, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    isolated_config.mkdir(parents=True)
    key = str(project.resolve())
    (isolated_config / "setup.log").write_text(
        json.dumps({"action": "install", "project": key, "ts": "2026-11-01T00:00:00+00:00"}) + "\n"
        + json.dumps({"action": "uninstall", "project": key, "ts": "2026-11-03T00:00:00+00:00"}) + "\n")
    assert "uninstalled for this project on 2026-11-03" in sc.uninstalled_note(project)
    with (isolated_config / "setup.log").open("a") as handle:
        handle.write(json.dumps({"action": "install", "project": key, "ts": "2026-11-04T00:00:00+00:00"}) + "\n")
    assert sc.uninstalled_note(project) == ""


def test_plugin_mode_shows_tamper_warnings(isolated_config, tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / ".gates").mkdir(parents=True)
    write_manifest(isolated_config, project, deny=["a"])
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    out = json.loads(sc.plugin({"session_id": "s", "cwd": str(project)}, {}))
    assert out["systemMessage"].startswith("send gate: on") and "safety net changed" in out["systemMessage"]


# --- style notices (for the person, not the agent) -------------------------------------

def test_notice_names_the_example_style_when_there_is_no_style_file(env):
    home, project, path = env
    (project / ".gates" / "style.json").unlink()
    install(home, path, project=project)
    enable(project / ".claude" / "settings.local.json")
    out = json.loads(run_watchdog(home, project))
    assert "no .gates/style.json, so the example style applies" in out["systemMessage"]
    assert "hookSpecificOutput" not in out  # the agent is not told to act on it


def test_notice_when_style_changed_since_setup(env, monkeypatch):
    home, project, path = env
    config = home / ".config" / "outbound-gates"
    monkeypatch.setattr(sc, "CONFIG_DIR", config)
    write(config / "installed.json", {"projects": {str(project.resolve()): {
        "style_sha256": sc._sha_file(project / ".gates" / "style.json")}}})
    assert sc.style_notes(project) == []
    (project / ".gates" / "style.json").write_text('{"min_contractions": 0}\n')
    assert "changed since setup recorded it" in sc.style_notes(project)[0]
