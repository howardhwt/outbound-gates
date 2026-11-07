"""/outbound-gates:start: a try folder for one real prospect, and the first catch in it, offline."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from gates import evidence, quickstart
from gates.evidence import EvidenceError

ROOT = Path(__file__).resolve().parents[1]
ADDRESS = "dana@prospect-co.test"
DOMAIN = "prospect-co.test"
PAGE = b"<html><body><h1>Pricing</h1><p>Our API costs $0.04 per minute of audio.</p></body></html>"
QUOTE = "Our API costs $0.04 per minute of audio."
DRAFT = ("Hi Dana,\n\nYour pricing page lists the API at $0.04 per minute of audio. We run a speech API for "
         "resellers, and our rate sits below that. Worth comparing on a sample of your audio?\n\nThanks,\nSam\n")


def env_for(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home)}  # never the real user config


def fake_fetch(url, check):
    return url, "text/html", PAGE


def public(host):
    return ["93.184.216.34"]


def record(dest, url, quote=QUOTE, value="$0.04"):
    settings = evidence.load_settings(ROOT, dest, user_config=dest / "no-user-config.json")
    return evidence.record_claim(dest / ".gates", ADDRESS, url, quote, value, settings=settings,
                                 crm=evidence.load_crm(settings), fetch=fake_fetch, resolve=public)


def check(tmp_path, dest):
    return subprocess.run([sys.executable, "-m", "gates.evaluate", str(dest / "draft.txt"), "--project", str(dest),
                           "--to", ADDRESS], cwd=ROOT, env=env_for(tmp_path), capture_output=True, text=True)


# --- the try folder ------------------------------------------------------------------

def test_try_folder_holds_a_generated_one_company_crm(tmp_path):
    dest = quickstart.make_try_folder(ADDRESS, tmp_path / "try")
    crm = json.loads((dest / "crm.json").read_text())
    assert [c["domain"] for c in crm["companies"]] == [DOMAIN]
    company = crm["companies"][0]
    assert company["status"] == "NEW" and company["notes"][0]["text"] == quickstart.TRY_NOTE
    assert quickstart.TRY_NOTE in crm["_comment"]
    assert json.loads((dest / ".gates" / "config.json").read_text())["crm_path"] == "crm.json"
    assert json.loads((dest / ".gates" / "style.json").read_text()) == {}  # neutral: fails only on truth


@pytest.mark.parametrize("address", ["sam@gmail.com", "Sam@Outlook.com", "x@icloud.com"])
def test_free_mail_is_refused_and_nothing_is_written(tmp_path, address):
    with pytest.raises(quickstart.ProspectError, match="use a work address at the company"):
        quickstart.make_try_folder(address, tmp_path / "try")
    assert not (tmp_path / "try").exists()


@pytest.mark.parametrize("address", ["not-an-address", "a@b", "two@at@x.com", "a b@x.com", "@x.com"])
def test_bad_addresses_are_refused(tmp_path, address):
    with pytest.raises(quickstart.ProspectError, match="not one email address"):
        quickstart.make_try_folder(address, tmp_path / "try")


def test_never_writes_into_an_existing_folder(tmp_path):
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "keep.txt").write_text("mine")
    with pytest.raises(FileExistsError):
        quickstart.make_try_folder(ADDRESS, tmp_path / "taken")
    assert sorted(p.name for p in (tmp_path / "taken").iterdir()) == ["keep.txt"]


def test_command_line_refuses_free_mail(tmp_path, capsys):
    assert quickstart.main(["--prospect", "a@gmail.com", str(tmp_path / "try")]) == 1
    assert "free-mail" in capsys.readouterr().err


# --- the first catch, offline ----------------------------------------------------------

def test_first_catch_refused_then_passes_once_sourced(tmp_path):
    dest = quickstart.make_try_folder(ADDRESS, tmp_path / "try")
    (dest / "draft.txt").write_text(DRAFT)
    first = check(tmp_path, dest)
    assert first.returncode == 1 and "'$0.04' has no sourced claim" in first.stdout
    record(dest, f"https://{DOMAIN}/pricing")
    second = check(tmp_path, dest)
    assert second.returncode == 0 and second.stdout.rstrip().endswith("PASS"), second.stdout


def test_printed_commands_check_the_try_folder(tmp_path):
    dest = quickstart.make_try_folder(ADDRESS, tmp_path / "try")
    (dest / "draft.txt").write_text(DRAFT)
    check_cmd, record_cmd, again = quickstart.prospect_commands(dest, ADDRESS)
    assert check_cmd == again and "--gates-home" in record_cmd and "--url URL" in record_cmd
    out = subprocess.run(["sh", "-c", check_cmd], env=env_for(tmp_path), capture_output=True, text=True)
    assert out.returncode == 1 and "BLOCKED" in out.stdout


def test_a_subdomain_page_counts_as_the_company(tmp_path):
    dest = quickstart.make_try_folder(ADDRESS, tmp_path / "try")
    assert record(dest, f"https://www.{DOMAIN}/pricing").exists()


def test_a_page_on_another_domain_is_refused(tmp_path):
    dest = quickstart.make_try_folder(ADDRESS, tmp_path / "try")
    with pytest.raises(EvidenceError, match="source host not allowed: prospect-co.com"):
        record(dest, "https://prospect-co.com/pricing")


# --- the skill and strict mode -----------------------------------------------------------

def test_start_skill_follows_the_reviewed_plan():
    skill = (ROOT / "skills" / "start" / "SKILL.md").read_text()
    assert skill.startswith("---\nname: start\n")
    assert 'PYTHONPATH="${CLAUDE_SKILL_DIR}/../.." python3 -m gates.quickstart --prospect ADDRESS' in skill
    assert "the number finding first" in skill                       # D6
    assert "Don't add a number just for the demo" in skill            # D12
    assert "try **one** other page" in skill and "outbound-gates-demo" in skill   # D5
    assert "isn't trusted for this company without a CRM" in skill   # D8
    assert "`First catch on DOMAIN`" in skill and "`Finished on the demo company because REASON`" in skill  # D13
    assert "run /outbound-gates:setup in your outbound folder" in skill  # D14


def test_strict_mode_allows_making_a_try_folder():
    from hooks import session_check as sc
    command = ('PYTHONPATH="/Users/u/.claude/plugins/cache/outbound-gates/outbound-gates/0.9.0" '
               "python3 -m gates.quickstart --prospect dana@prospect-co.test outbound-gates-try")
    assert sc.bash_refusal(command, Path("/w")) == ""
    assert sc.bash_refusal(command + " && rm -rf /w/.gates", Path("/w"))
