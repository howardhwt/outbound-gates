"""The first ten minutes: gates.quickstart, gates.evaluate, the try skill and docs/quickstart.md."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from gates import evaluate as evaluate_mod
from gates import quickstart

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "quickstart.md"
CONTACT = quickstart.CONTACT


def env_for(tmp_path):
    """A clean environment: a HOME of its own (never the real user config), the plugin as OG."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "OG": str(ROOT)}


def doc_blocks():
    """(expected exit code, command) for every ```sh test block in the doc."""
    text = DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"```sh test(?: exit=(\d+))?\n(.*?)```", text, re.S)
    return [(int(code or 0), body.strip()) for code, body in blocks]


def test_the_doc_has_the_four_steps():
    assert len(doc_blocks()) == 4


def test_doc_commands_run_as_written(tmp_path):
    env = env_for(tmp_path)
    outputs = []
    for expected, command in doc_blocks():
        result = subprocess.run(["sh", "-c", command], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert result.returncode == expected, (command, result.stdout, result.stderr)
        outputs.append(result.stdout)
    assert "BLOCKED" in outputs[1] and "'$0.006' has no sourced claim" in outputs[1]
    assert "recorded:" in outputs[2]
    assert outputs[3].rstrip().endswith("PASS")


def test_quickstart_prints_commands_that_work(tmp_path):
    dest = quickstart.copy_to(tmp_path / "demo")
    check, record, again = quickstart.next_commands(dest)
    env = env_for(tmp_path)
    run = lambda c: subprocess.run(["sh", "-c", c], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert run(check).returncode == 1
    assert run(record).returncode == 0
    assert run(again).returncode == 0


def test_quickstart_project_shape(tmp_path):
    dest = quickstart.copy_to(tmp_path / "demo")
    assert (dest / ".gates" / "config.json").is_file() and not (dest / "gates").exists()
    assert json.loads((dest / ".gates" / "style.json").read_text()) == {}  # neutral style
    draft = (dest / "draft.txt").read_text()
    assert re.findall(r"\$?\d[\d.,]*", draft) == ["$0.006"]  # one number, and it has a source on the page
    assert "**" not in draft


def test_quickstart_refuses_an_existing_folder(tmp_path, capsys):
    (tmp_path / "taken").mkdir()
    assert quickstart.main([str(tmp_path / "taken")]) == 2
    assert "already exists" in capsys.readouterr().err


# --- gates.evaluate ------------------------------------------------------------------

def run_evaluate(tmp_path, *args):
    return subprocess.run([sys.executable, "-m", "gates.evaluate", *args], cwd=ROOT, env=env_for(tmp_path),
                          capture_output=True, text=True)


@pytest.mark.parametrize("args,error", [
    (["--to", CONTACT, "--project", "/nonexistent-og"], "no .gates folder"),
    (["--to", "not-an-address"], "not one email address"),
    (["--to", CONTACT, "--today", "05/11/2026"], "--today must be YYYY-MM-DD"),
])
def test_evaluate_bad_input_exits_2(tmp_path, args, error):
    dest = quickstart.copy_to(tmp_path / "demo")
    if "--project" not in args:
        args = args + ["--project", str(dest)]
    result = run_evaluate(tmp_path, str(dest / "draft.txt"), *args)
    assert result.returncode == 2 and error in result.stderr


def test_evaluate_missing_draft_exits_2(tmp_path):
    dest = quickstart.copy_to(tmp_path / "demo")
    result = run_evaluate(tmp_path, str(dest / "nope.txt"), "--project", str(dest), "--to", CONTACT)
    assert result.returncode == 2 and "cannot read the draft" in result.stderr


def test_evaluate_uses_the_given_user_config(tmp_path):
    dest = quickstart.copy_to(tmp_path / "demo")
    user = tmp_path / "user.json"
    user.write_text(json.dumps({"own_facts": ["$0.006"]}))
    result = run_evaluate(tmp_path, str(dest / "draft.txt"), "--project", str(dest), "--to", CONTACT,
                          "--user-config", str(user))
    assert result.returncode == 0 and result.stdout.rstrip().endswith("PASS")


def test_evaluate_reply_subject_uses_follow_up(tmp_path):
    dest = quickstart.copy_to(tmp_path / "demo")
    result = run_evaluate(tmp_path, str(dest / "draft.txt"), "--project", str(dest), "--to", CONTACT,
                          "--subject", "Re: pricing")
    assert "register: follow_up" in result.stdout


def test_evaluate_and_the_hook_agree(tmp_path, monkeypatch):
    """The command line and the Gmail hook give the same verdict on the same draft."""
    from hooks import gate_gmail

    dest = quickstart.copy_to(tmp_path / "demo")
    text = (dest / "draft.txt").read_text()
    payload = {"tool_name": "mcp__claude_ai_Gmail__create_draft", "mcp_server": {"name": "claude.ai Gmail"},
               "tool_input": {"to": [CONTACT], "subject": "", "body": text}}
    env = {"CLAUDE_PLUGIN_ROOT": str(ROOT), "CLAUDE_PROJECT_DIR": str(dest)}
    cli = lambda: run_evaluate(tmp_path, str(dest / "draft.txt"), "--project", str(dest), "--to", CONTACT).returncode
    assert cli() == 1 and gate_gmail.decide(json.dumps(payload), env) != ""
    _, record, _ = quickstart.next_commands(dest)
    assert subprocess.run(["sh", "-c", record], env=env_for(tmp_path), capture_output=True).returncode == 0
    assert cli() == 0 and gate_gmail.decide(json.dumps(payload), env) == ""


# --- the try skill and strict mode -----------------------------------------------------

def test_try_skill_runs_the_quickstart():
    skill = (ROOT / "skills" / "try" / "SKILL.md").read_text()
    assert skill.startswith("---\nname: try\n")
    assert 'PYTHONPATH="${CLAUDE_SKILL_DIR}/../.." python3 -m gates.quickstart' in skill


@pytest.mark.parametrize("module", ["gates.quickstart outbound-gates-try",
                                    "gates.evaluate /w/draft.txt --project /w --to a@b.c"])
def test_strict_mode_allows_the_quickstart_commands(module):
    from hooks import session_check as sc
    command = f'PYTHONPATH="/Users/u/.claude/plugins/cache/outbound-gates/outbound-gates/0.7.0" python3 -m {module}'
    assert sc.bash_refusal(command, Path("/w")) == ""
    assert sc.bash_refusal(command + " > /w/.gates/style.json", Path("/w"))
    assert sc.bash_refusal(command + " && rm -rf /w/.gates", Path("/w"))


def test_issue_template_asks_for_versions_and_scrubbed_output():
    template = (ROOT / ".github" / "ISSUE_TEMPLATE" / "stuck.md").read_text()
    assert "name: Stuck setting up" in template and "version" in template and "Remove any API keys" in template
