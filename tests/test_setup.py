"""gates/setup.py: plan, apply, merge, migration, uninstall and the generated names."""
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from gates import setup

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "gates" / "hook.config.json").read_text())


@pytest.fixture
def env(tmp_path):
    home, project = tmp_path / "home", tmp_path / "proj"
    (home / ".claude").mkdir(parents=True)
    project.mkdir()
    managed = tmp_path / "managed-settings.json"
    return home, project, managed


def run(env, *extra):
    home, project, managed = env
    argv = ["--project", str(project), "--plugin-root", str(ROOT), *extra]
    return setup.main(argv, home=home, managed=[managed])


def user_settings(home):
    return json.loads((home / ".claude" / "settings.json").read_text())


def local_settings(project):
    return json.loads((project / ".claude" / "settings.local.json").read_text())


def net(home):
    return home.resolve() / ".config" / "outbound-gates"


# --- names ----------------------------------------------------------------------

def test_names_come_from_hook_config():
    names = setup.load_names(ROOT)
    for word in CONFIG["send_words"] + CONFIG["blocked_tools"]:
        assert word in names["guard_names"]
    apollo = [n for n in CONFIG["blocked_tools"] if n.startswith("apollo_")]
    assert [n.split("__")[-1] for n in names["deny_rules"] if "Apollo_io" in n] == apollo
    assert "mcp__claude_ai_Gmail__send_message" in names["deny_rules"]
    assert "mcp__claude_ai_Slack__slack_send_message" in names["deny_rules"]


def test_word_pattern():
    assert setup.word_pattern("send_message") == "[Ss]end[_-]?[Mm]essage"
    assert setup.word_pattern("sendEmail") == "[Ss]end[_-]?[Ee]mail"


@pytest.mark.parametrize("tool,routed", [
    ("mcp__claude_ai_Slack__slack_send_message", True),
    ("mcp__mail__sendEmail", True),
    ("mcp__mail__send-email", True),
    ("mcp__mail__send_mail", True),
    ("mcp__x__apollo_emailer_messages_send_now", True),
    ("mcp__claude_ai_Gmail__create_draft", True),
    ("mcp__claude_ai_Gmail__update_draft", True),
    ("mcp__claude_ai_Gmail__list_drafts", False),
    ("mcp__claude_ai_Granola__get_meetings", False),
])
def test_generated_matcher_routes_the_right_tools(tool, routed):
    assert bool(re.search(setup.load_names(ROOT)["matcher"], tool)) is routed


def test_matcher_uses_syntax_shared_with_javascript():
    matcher = setup.load_names(ROOT)["matcher"]
    assert "(?i" not in matcher and "(?<" not in matcher and "(?P" not in matcher


def test_every_guard_name_is_routed_by_the_matcher():
    names = setup.load_names(ROOT)
    pattern = re.compile(names["matcher"])
    assert all(pattern.search(f"mcp__srv__{n}") for n in names["guard_names"])


# --- install --------------------------------------------------------------------------

def test_preview_writes_nothing(env, capsys):
    home, project, _ = env
    assert run(env) == 0
    assert "To apply, run:" in capsys.readouterr().out
    assert not (home / ".claude" / "settings.json").exists()
    assert not (project / ".claude").exists() and not (project / ".gates").exists()
    assert not net(home).exists()


def test_install_writes_every_layer(env, capsys):
    home, project, _ = env
    assert run(env, "--yes") == 0
    out = capsys.readouterr().out
    assert "PASSED" in out
    for name in setup.NET_FILES:
        assert (net(home) / name).is_file()
    assert 'VERSION = "' in (net(home) / "session_check.py").read_text()
    assert 'VERSION = "dev"' not in (net(home) / "session_check.py").read_text()
    assert stat.S_IMODE((net(home) / "gate-net.sh").stat().st_mode) == 0o700
    assert stat.S_IMODE(net(home).stat().st_mode) == 0o700
    hooks = user_settings(home)["hooks"]
    assert "gate-net.sh\" watchdog" in hooks["SessionStart"][0]["hooks"][0]["command"]
    assert hooks["PreToolUse"][0]["matcher"] == setup.load_names(ROOT)["matcher"]
    assert set(setup.load_names(ROOT)["deny_rules"]) <= set(local_settings(project)["permissions"]["deny"])
    assert (project / ".gates").is_dir()
    manifest = json.loads((net(home) / "installed.json").read_text())
    assert manifest["schema"] == 1 and str(project.resolve()) in manifest["projects"]


def test_install_merges_and_replaces_manual_hooks(env):
    home, project, _ = env
    (home / ".claude" / "settings.json").write_text(json.dumps({
        "permissions": {"deny": ["mcp__mine__thing"]},
        "tui": "fullscreen",
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "python3 /clone/hooks/session_check.py watchdog"}]}],
            "PreToolUse": [{"matcher": "mcp__.*__(create_draft|update_draft)",
                            "hooks": [{"type": "command", "command": "python3 /clone/hooks/session_check.py draft-guard"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "keep-me"}]}],
        },
    }))
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.local.json").write_text(json.dumps(
        {"enabledPlugins": {"x": True}, "permissions": {"deny": ["mcp__claude_ai_Gmail__reply"]}}))
    assert run(env, "--yes") == 0
    user = user_settings(home)
    assert user["tui"] == "fullscreen" and user["permissions"] == {"deny": ["mcp__mine__thing"]}
    assert user["hooks"]["Stop"][0]["hooks"][0]["command"] == "keep-me"
    commands = [h["command"] for e in ("SessionStart", "PreToolUse") for g in user["hooks"][e] for h in g["hooks"]]
    assert not any("/clone/" in c for c in commands) and len(commands) == 2
    local = local_settings(project)
    assert local["enabledPlugins"] == {"x": True}
    assert local["permissions"]["deny"].count("mcp__claude_ai_Gmail__reply") == 1
    manifest = json.loads((net(home) / "installed.json").read_text())
    assert "mcp__claude_ai_Gmail__reply" not in manifest["projects"][str(project.resolve())]["deny_added"]
    assert list((home / ".claude").glob("settings.json.bak-outbound-gates-*"))


def test_rerun_is_idempotent(env):
    home, project, _ = env
    run(env, "--yes")
    first_user, first_local = user_settings(home), local_settings(project)
    backups = len(list((home / ".claude").glob("*.bak-outbound-gates-*")))
    run(env, "--yes")
    assert user_settings(home) == first_user and local_settings(project) == first_local
    assert len(list((home / ".claude").glob("*.bak-outbound-gates-*"))) == backups


def test_invalid_json_refuses_and_writes_nothing(env, capsys):
    home, project, _ = env
    (home / ".claude" / "settings.json").write_text("{ not json")
    assert run(env, "--yes") == 2
    assert "not valid JSON" in capsys.readouterr().err
    assert (home / ".claude" / "settings.json").read_text() == "{ not json"
    assert not net(home).exists() and not (project / ".gates").exists()


def test_symlinked_settings_keep_the_link(env, tmp_path):
    home, project, _ = env
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text("{}")
    (home / ".claude" / "settings.json").symlink_to(real)
    run(env, "--yes")
    assert (home / ".claude" / "settings.json").is_symlink()
    assert "hooks" in json.loads(real.read_text())


def test_policy_warnings_show_in_the_plan(env, capsys):
    home, project, managed = env
    managed.write_text(json.dumps({"allowManagedHooksOnly": True, "allowManagedPermissionRulesOnly": True}))
    (home / ".claude" / "settings.json").write_text(json.dumps({"disableAllHooks": True}))
    run(env)
    out = capsys.readouterr().out
    assert "allowManagedHooksOnly" in out and "allowManagedPermissionRulesOnly" in out and "disableAllHooks" in out


def test_user_scope_plugin_warning(env, capsys):
    home, _, _ = env
    (home / ".claude" / "plugins").mkdir()
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"plugins": {"outbound-gates@outbound-gates": [{"scope": "user", "version": "0.1.4"}]}}))
    run(env)
    assert "installed at user scope" in capsys.readouterr().out


def test_gitignore_warning_only_warns(env, capsys):
    _, project, _ = env
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    run(env, "--yes")
    assert ".gates/ is not git-ignored" in capsys.readouterr().out
    assert not (project / ".gitignore").exists()


def test_install_needs_plugin_root(env, capsys):
    home, project, managed = env
    assert setup.main(["--project", str(project)], home=home, managed=[managed]) == 2


# --- uninstall ---------------------------------------------------------------------------

def uninstall(env, project=None, *extra):
    home, proj, managed = env
    return setup.main(["--project", str(project or proj), "--uninstall", *extra], home=home, managed=[managed])


def test_uninstall_preview_writes_nothing(env):
    home, project, _ = env
    run(env, "--yes")
    before = user_settings(home), local_settings(project)
    assert uninstall(env) == 0
    assert (user_settings(home), local_settings(project)) == before


def test_uninstall_removes_only_what_setup_added(env):
    home, project, _ = env
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"deny": ["mcp__mine__keep"]}}))
    (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "keep-me"}]}]}}))
    run(env, "--yes")
    assert uninstall(env, None, "--yes") == 0
    assert local_settings(project) == {"permissions": {"deny": ["mcp__mine__keep"]}}
    assert user_settings(home) == {"hooks": {"Stop": [{"hooks": [{"command": "keep-me"}]}]}}
    assert not any((net(home) / n).exists() for n in setup.NET_FILES + ("installed.json",))


def test_uninstall_keeps_user_hooks_while_another_project_uses_them(env, tmp_path):
    home, project, managed = env
    other = tmp_path / "other"
    other.mkdir()
    run(env, "--yes")
    setup.main(["--project", str(other), "--plugin-root", str(ROOT), "--yes"], home=home, managed=[managed])
    uninstall(env, None, "--yes")
    assert "hooks" in user_settings(home)
    assert (net(home) / "session_check.py").exists()
    assert uninstall(env, other, "--all", "--yes") == 0
    assert "hooks" not in user_settings(home)


def test_uninstall_runs_from_the_copy_without_the_plugin(env):
    home, project, _ = env
    run(env, "--yes")
    proc = subprocess.run([sys.executable, str(net(home) / "setup.py"), "--project", str(project), "--uninstall", "--yes"],
                          capture_output=True, text=True, env={**os.environ, "HOME": str(home)}, cwd=str(home))
    assert proc.returncode == 0, proc.stderr
    assert "hooks" not in user_settings(home)


def test_uninstall_with_nothing_installed(env, capsys):
    assert uninstall(env, None, "--yes") == 0
    assert "Nothing to uninstall" in capsys.readouterr().out


# --- the plugin ships what setup needs ------------------------------------------------------

def test_setup_skill_is_person_only():
    text = (ROOT / "skills" / "setup" / "SKILL.md").read_text()
    assert "disable-model-invocation: true" in text
    assert "--yes" in text and "Do not run it yourself" in text
