"""hooks/session_check.py: the plugin heartbeat and the user-level watchdog."""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hooks import session_check as sc

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    home, project = tmp_path / "home", tmp_path / "proj"
    (project / ".gates").mkdir(parents=True)
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
    line = json.loads((project / ".gates" / "log" / "sessions.jsonl").read_text().strip())
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
