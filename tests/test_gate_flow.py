"""The demo chain, offline, across real processes (eng D7).

1. The agent drafts with an unsourced price: the hook denies it and prints a
   record command template.
2. The agent records a source on another site: the record command refuses it.
3. The agent fills the printed template with the real quote from Northwind's
   own pricing page: recorded.
4. The same draft goes through the hook again: allowed.
"""
import copy
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "hook_input" / "gmail_create_draft.json").read_text())
DRAFT_TEXT = (ROOT / "demo" / "gate" / "northwind-draft.txt").read_text(encoding="utf-8")
CONTACT = "dana@northwind-audio.example"


def make_project(tmp_path):
    proj = tmp_path / "my project"  # a space, to prove the printed paths are quoted
    (proj / ".gates").mkdir(parents=True)
    shutil.copytree(ROOT / "demo" / "pages", proj / "demo" / "pages")
    shutil.copy(ROOT / "demo" / "gate_crm.json", proj / "demo" / "gate_crm.json")
    shutil.copy(ROOT / "demo" / "gate" / "config.json", proj / ".gates" / "config.json")
    return proj


def hook(proj):
    data = copy.deepcopy(FIXTURE)
    data["cwd"] = str(proj)
    data["tool_input"] = {"to": [CONTACT], "subject": "Northwind supply", "body": DRAFT_TEXT}
    env = {"PATH": "/usr/bin:/bin", "CLAUDE_PLUGIN_ROOT": str(ROOT), "CLAUDE_PROJECT_DIR": str(proj),
           "GATE_PYTHON": sys.executable, "HOME": str(proj)}
    result = subprocess.run(["sh", str(ROOT / "hooks" / "gate.sh")], input=json.dumps(data),
                            text=True, capture_output=True, env=env, cwd=proj)
    assert result.returncode == 0, result.stderr
    if not result.stdout:
        return None
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def shell(command, proj):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(proj)}
    command = command.replace("python3 -m", f"{shlex.quote(sys.executable)} -m", 1)
    return subprocess.run(["sh", "-c", command], text=True, capture_output=True, env=env, cwd="/")


def test_block_refuse_record_allow(tmp_path):
    proj = make_project(tmp_path)

    denied = hook(proj)
    assert denied and "the number '$0.006' has no sourced claim" in denied
    template = next(line for line in denied.splitlines() if "-m gates.evidence record" in line)
    assert str(proj / ".gates" / "drafts") in denied  # the folder it read

    fake = (template.replace("--url URL", "--url https://gist.example.org/northwind")
                    .replace("--quote 'QUOTE'", "--quote 'Northwind charges $0.006 a minute'")
                    .replace("--value VALUE", "--value '$0.006'"))
    refused = shell(fake, proj)
    assert refused.returncode == 1 and "source host not allowed" in refused.stderr

    real = (template.replace("--url URL", "--url https://northwind-audio.example/pricing")
                    .replace("--quote 'QUOTE'",
                             "--quote 'Transcription is billed at $0.006 per audio minute'")
                    .replace("--value VALUE", "--value '$0.006'"))
    recorded = shell(real, proj)
    assert recorded.returncode == 0, recorded.stderr
    assert "recorded:" in recorded.stdout

    assert hook(proj) is None  # allowed
    log = [json.loads(line) for line in (proj / ".gates" / "log" / "gate.jsonl").read_text().splitlines()]
    assert [e["decision"] for e in log] == ["deny", "allow"]
