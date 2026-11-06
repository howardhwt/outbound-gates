"""og: proposals the person approves with a code, status, the launcher, and the pass-through commands."""
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from gates import og, quickstart, setup

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    return h


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    (p / ".gates").mkdir(parents=True)
    return p.resolve()


def net(home):
    return og.net_dir(home)


def run(home, *argv):
    return og.main(list(argv), home=home)


def staged_code(home, capsys, *argv):
    assert run(home, "propose", *argv) == 0
    out = capsys.readouterr().out
    return out.split(" yes ")[1].split()[0]


# --- proposals --------------------------------------------------------------------------

def test_nothing_changes_until_the_person_approves(home, project, capsys):
    code = staged_code(home, capsys, "add-fact", "$0.01", "--project", str(project))
    assert not (net(home) / "config.json").exists()
    assert run(home, "yes", code) == 0
    assert json.loads((net(home) / "config.json").read_text())["own_facts"] == ["$0.01"]
    assert not (net(home) / "pending.json").exists()
    log = [json.loads(line) for line in (net(home) / "setup.log").read_text().splitlines()]
    assert log[-1]["action"] == "approve" and log[-1]["code"] == code


def test_a_change_slipped_in_after_reading_breaks_the_code(home, project, capsys):
    code = staged_code(home, capsys, "add-name", "H3", "--project", str(project))
    staged_code(home, capsys, "add-bcc", "log@crm.example", "--project", str(project))  # after the person read it
    assert run(home, "yes", code) == 1
    assert "doesn't match" in capsys.readouterr().err
    assert not (net(home) / "config.json").exists()


def test_proposals_add_up_and_apply_together(home, project, capsys):
    staged_code(home, capsys, "add-fact", "$0.01", "--project", str(project))
    staged_code(home, capsys, "add-name", "1080p", "--project", str(project))
    code = staged_code(home, capsys, "allow", "mcp__claude_ai_HubSpot__manage_crm_objects", "--project", str(project))
    assert run(home, "yes", code) == 0
    config = json.loads((net(home) / "config.json").read_text())
    assert config == {"own_facts": ["$0.01"], "names": ["1080p"]}
    assert json.loads((net(home) / "allow.json").read_text())["allow"] == ["manage_crm_objects"]


def test_existing_config_is_kept(home, project, capsys):
    net(home).mkdir(parents=True)
    (net(home) / "config.json").write_text(json.dumps({"own_facts": ["1"], "source_allowlist": ["x.com"]}))
    code = staged_code(home, capsys, "add-fact", "$0.01", "--project", str(project))
    run(home, "yes", code)
    assert json.loads((net(home) / "config.json").read_text()) == {"own_facts": ["1", "$0.01"],
                                                                   "source_allowlist": ["x.com"]}


@pytest.mark.parametrize("argv,error", [
    (["add-fact", "cheap"], "own fact is a number"),
    (["add-name", "3"], "one word with a letter and a digit"),
    (["add-bcc", "two words@x.y"], "one email address"),
    (["allow", "send_message"], "send tool"),
    (["allow", "gmail_send_email"], "send tool"),
    (["style", "loud"], "neutral, example or a JSON file"),
])
def test_bad_proposals_are_refused_when_proposed(home, project, capsys, argv, error):
    assert run(home, "propose", *argv, "--project", str(project)) == 1
    assert error in capsys.readouterr().err
    assert not (net(home) / "pending.json").exists()


def test_style_proposal_writes_and_records_it(home, project, capsys, tmp_path):
    net(home).mkdir(parents=True)
    (net(home) / "installed.json").write_text(json.dumps({"projects": {str(project): {}}}))
    style = tmp_path / "mine.json"
    style.write_text(json.dumps({"registers": {"cold": {"words": [60, 110]}}}))
    code = staged_code(home, capsys, "style", str(style), "--project", str(project))
    style.write_text(json.dumps({"min_contractions": 9}))  # the proposal holds the content, not the path
    run(home, "yes", code)
    assert json.loads((project / ".gates" / "style.json").read_text()) == {"registers": {"cold": {"words": [60, 110]}}}
    entry = json.loads((net(home) / "installed.json").read_text())["projects"][str(project)]
    assert entry["style_sha256"] == setup.style_sha(project)


def test_style_that_sets_a_truth_rule_is_refused(home, project, capsys, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"own_facts": ["70%"]}))
    assert run(home, "propose", "style", str(bad), "--project", str(project)) == 1
    assert "truth rule" in capsys.readouterr().err


def test_no_drops_the_proposal(home, project, capsys):
    staged_code(home, capsys, "add-fact", "$0.01", "--project", str(project))
    assert run(home, "no") == 0
    assert not (net(home) / "pending.json").exists()
    assert run(home, "yes", "abcd") == 1


def test_refresh_reinstalls_the_safety_net(home, project, capsys):
    managed = home.parent / "managed.json"
    preview = setup.main(["--project", str(project), "--plugin-root", str(ROOT)], home=home, managed=[managed])
    planhash = capsys.readouterr().out.split("PLANHASH ")[1].split()[0]
    assert preview == 0
    setup.main(["--project", str(project), "--plugin-root", str(ROOT), "--yes", planhash], home=home, managed=[managed])
    (net(home) / "og").unlink()
    capsys.readouterr()
    code = staged_code(home, capsys, "refresh", "--project", str(project))
    assert run(home, "yes", code) == 0
    assert (net(home) / "og").is_file()


# --- the agent can't approve -------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "~/.config/outbound-gates/og yes 4f2a",
    '"/Users/u/.config/outbound-gates/og" yes 4f2a',
    "PYTHONPATH=/p python3 -m gates.og yes 4f2a",
])
def test_strict_mode_refuses_the_agents_own_approval(command):
    from hooks import session_check as sc
    assert "only the person approves" in sc.bash_refusal(command, Path("/w"))


def test_strict_mode_lets_the_agent_propose_and_check():
    from hooks import session_check as sc
    for command in ("~/.config/outbound-gates/og propose allow manage_crm_objects",
                    "~/.config/outbound-gates/og status", "~/.config/outbound-gates/og check d.txt --to a@b.c"):
        assert sc.bash_refusal(command, Path("/w")) == "", command


# --- status ------------------------------------------------------------------------------

def test_status_shows_settings_today_and_whats_waiting(home, project, capsys):
    (project / ".gates" / "log").mkdir()
    today = date(2026, 11, 6)
    lines = [{"ts": "2026-11-06T10:00:00+00:00", "decision": "deny", "rules": ["claims"]},
             {"ts": "2026-11-06T10:05:00+00:00", "decision": "allow"},
             {"ts": "2026-11-05T10:05:00+00:00", "decision": "allow"}]
    (project / ".gates" / "log" / "gate.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    code = staged_code(home, capsys, "add-fact", "$0.01", "--project", str(project))
    out = "\n".join(og.status_lines(project, home, today=today))
    assert "today: 1 draft(s) passed, 1 refused (claims x1)" in out
    assert f"waiting for your approval (code {code})" in out and f"og yes {code}" in out
    assert "style: example (no style.json)" in out
    assert "install the safety net" in out


def test_status_outside_a_gated_folder(home, tmp_path):
    out = "\n".join(og.status_lines(tmp_path, home))
    assert "no .gates folder" in out and "/outbound-gates:setup" in out


# --- the launcher and pass-through commands --------------------------------------------

def launcher_for(home, plugin_root):
    path = home / "og"
    path.write_text(setup.OG_LAUNCHER.replace("__PLUGIN_ROOT__", repr(str(plugin_root))))
    path.chmod(0o700)
    return path


def fake_plugin(cache, version):
    root = cache / version
    (root / "gates").mkdir(parents=True)
    (root / "gates" / "__init__.py").write_text("")
    (root / "gates" / "og.py").write_text(f"def main():\n    print('plugin {version}')\n    return 0\n")
    return root


def test_launcher_runs_the_newest_installed_plugin(tmp_path):
    cache = tmp_path / "cache" / "outbound-gates" / "outbound-gates"
    baked = fake_plugin(cache, "0.7.0")
    fake_plugin(cache, "0.10.0")
    (cache / "0.9.0").mkdir()  # an incomplete copy without og is skipped
    out = subprocess.run([sys.executable, str(launcher_for(tmp_path, baked))], capture_output=True, text=True)
    assert out.stdout.strip() == "plugin 0.10.0"


def test_launcher_from_a_clone_runs_status(tmp_path, project):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path / "h")}
    out = subprocess.run([sys.executable, str(launcher_for(tmp_path, ROOT)), "status", "--project", str(project)],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0 and "Next:" in out.stdout


def test_og_check_and_record_pass_through(tmp_path, monkeypatch):
    dest = quickstart.copy_to(tmp_path / "demo")
    monkeypatch.chdir(dest)
    home = tmp_path / "h"
    assert og.main(["check", "draft.txt", "--to", quickstart.CONTACT], home=home) == 1
    assert og.main(["record", "--contact", quickstart.CONTACT, "--url", quickstart.URL,
                    "--quote", quickstart.QUOTE, "--value", "$0.006"], home=home) == 0
    assert og.main(["check", "draft.txt", "--to", quickstart.CONTACT], home=home) == 0


def test_setup_installs_the_launcher_and_uninstall_removes_it():
    assert "og" in setup.NET_FILES


def test_status_skill_runs_og_status():
    skill = (ROOT / "skills" / "status" / "SKILL.md").read_text()
    assert skill.startswith("---\nname: status\n")
    assert 'PYTHONPATH="${CLAUDE_SKILL_DIR}/../.." python3 -m gates.og status --project .' in skill
    assert "Never run `og yes` yourself" in skill


def test_adr_and_glossary_name_the_approval():
    assert "bypass" in (ROOT / "docs" / "adr" / "0001-approval-by-code.md").read_text()
    glossary = (ROOT / "GLOSSARY.md").read_text()
    assert "**Proposal**" in glossary and "**Approval code**" in glossary
