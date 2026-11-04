"""hooks/gate_gmail.py and hooks/gate.sh: routing, refusals, verdicts, logging, crashes."""
import copy
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from gates import evidence
from hooks import gate_gmail

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "hook_input" / "gmail_create_draft.json").read_text())
DRAFT_TEXT = (ROOT / "demo" / "gate" / "northwind-draft.txt").read_text(encoding="utf-8")
CONTACT = "dana@northwind-audio.example"
QUOTE = "Transcription is billed at $0.006 per audio minute"
TODAY = date(2026, 11, 3)
NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".gates").mkdir(parents=True)
    shutil.copytree(ROOT / "demo" / "pages", proj / "demo" / "pages")
    shutil.copy(ROOT / "demo" / "gate_crm.json", proj / "demo" / "gate_crm.json")
    shutil.copy(ROOT / "demo" / "gate" / "config.json", proj / ".gates" / "config.json")
    return proj


def payload(tool="mcp__claude_ai_Gmail__create_draft", server="claude.ai Gmail", **tool_input):
    data = copy.deepcopy(FIXTURE)
    data["tool_name"] = tool
    data["mcp_server"] = {"name": server, "source": "claudeai"}
    data["tool_input"] = tool_input or {"to": [CONTACT], "subject": "Northwind supply", "body": DRAFT_TEXT}
    return data


def run(project, data):
    env = {"CLAUDE_PLUGIN_ROOT": str(ROOT), "CLAUDE_PROJECT_DIR": str(project)}
    return gate_gmail.decide(json.dumps(data), env, today=TODAY, now=NOW)


def reason(out):
    parsed = json.loads(out)  # every deny is exactly one JSON object
    hso = parsed["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny" and hso["hookEventName"] == "PreToolUse"
    return hso["permissionDecisionReason"]


def record_price(project):
    settings = evidence.load_settings(ROOT, project, user_config=project / "none.json")
    evidence.record_claim(project / ".gates", CONTACT, "https://northwind-audio.example/pricing",
                          QUOTE, "$0.006", settings=settings, crm=evidence.load_crm(settings), now=NOW)


def log_lines(project):
    path = project / ".gates" / "log" / "gate.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# --- routing ---

def test_unrelated_tools_pass_without_importing_the_gate(project):
    code = (
        "import json, sys; sys.path.insert(0, %r)\n"
        "from hooks import gate_gmail\n"
        "out = gate_gmail.decide(json.dumps({'tool_name': 'mcp__claude_ai_Slack__slack_read_channel'}),"
        " {'CLAUDE_PLUGIN_ROOT': %r, 'CLAUDE_PROJECT_DIR': %r})\n"
        "assert out == ''\n"
        "print(sorted(m for m in sys.modules if m == 'gates' or m.startswith('gates.')))\n"
    ) % (str(ROOT), str(ROOT), str(project))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"
    assert log_lines(project) == []


def test_hooks_json_routes_every_mcp_tool():
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    matcher = config["hooks"]["PreToolUse"][0]["matcher"]
    names = [f"mcp__claude_ai_Gmail__{t}" for t in
             ("create_draft", "update_draft", "send_message", "reply", "forward")]
    names += [f"mcp__claude_ai_Apollo_io__{t}" for t in
              json.loads((ROOT / "gates" / "hook.config.json").read_text())["blocked_tools"]]
    names += ["mcp__someone_smartlead__smartlead_send", "mcp__claude_ai_Slack__slack_send_message"]
    assert all(re.fullmatch(matcher, n) for n in names)


@pytest.mark.parametrize("tool", ["send_message", "reply", "forward", "update_draft"])
def test_gmail_sends_replies_forwards_and_updates_are_always_denied(project, tool):
    out = run(project, payload(tool=f"mcp__claude_ai_Gmail__{tool}"))
    assert f"Gmail {tool} is blocked" in reason(out)
    assert log_lines(project)[-1]["decision"] == "deny"


def test_gmail_from_another_server_is_recognised(project):
    out = run(project, payload(tool="mcp__gmail_local__send_message", server="local gmail"))
    assert "blocked" in reason(out)


@pytest.mark.parametrize("tool", json.loads((ROOT / "gates" / "hook.config.json").read_text())["blocked_tools"])
def test_default_blocked_tools_are_denied_by_name_suffix(project, tool):
    out = run(project, payload(tool=f"mcp__claude_ai_Apollo_io__{tool}", server="claude.ai Apollo.io"))
    assert "email goes through a gated Gmail draft" in reason(out)


@pytest.mark.parametrize("tool", ["mcp__claude_ai_Slack__slack_send_message",
                                  "mcp__claude_ai_Slack__slack_schedule_message",
                                  "mcp__slack_local__slack_send_message"])
def test_slack_sends_are_denied(project, tool):
    out = run(project, payload(tool=tool, server="claude.ai Slack", text="hi", channel_id="C1"))
    assert "is blocked in this project" in reason(out)
    assert log_lines(project)[-1]["rules"] == ["blocked_tool"]


@pytest.mark.parametrize("tool", ["mcp__claude_ai_Slack__slack_send_message_draft",
                                  "mcp__claude_ai_Slack__slack_read_channel",
                                  "mcp__claude_ai_Apollo_io__apollo_contacts_search"])
def test_reads_and_slack_drafts_pass(project, tool):
    assert run(project, payload(tool=tool, server="x", q="y")) == ""


def test_project_can_add_a_blocked_tool_but_not_remove_one(project):
    cfg = json.loads((project / ".gates" / "config.json").read_text())
    cfg["blocked_tools"] = ["smartlead_send"]
    (project / ".gates" / "config.json").write_text(json.dumps(cfg))
    assert "blocked" in reason(run(project, payload(tool="mcp__x__smartlead_send", server="x")))
    assert "blocked" in reason(run(project, payload(
        tool="mcp__claude_ai_Apollo_io__apollo_emailer_messages_send_now", server="Apollo")))


def test_hook_block_list_matches_load_settings(project):
    cfg = json.loads((project / ".gates" / "config.json").read_text())
    cfg["blocked_tools"] = ["smartlead_send", "apollo_sequences_create"]
    (project / ".gates" / "config.json").write_text(json.dumps(cfg))
    settings = evidence.load_settings(ROOT, project, user_config=project / "none.json")
    assert gate_gmail.blocked_tools(ROOT, project) == settings["blocked_tools"]


# --- draft fields and recipients (eng D9) ---

@pytest.mark.parametrize("extra,message", [
    ({"attachments": [{"content": "eA=="}]}, "attachments are not gated"),
    ({"replyToMessageId": "abc"}, "replies are outside wave 1"),
    ({"scheduleSend": "tomorrow"}, "unexpected field scheduleSend"),
])
def test_unchecked_fields_are_refused(project, extra, message):
    data = payload()
    data["tool_input"].update(extra)
    assert message in reason(run(project, data))


@pytest.mark.parametrize("to,cc", [([], []), ([CONTACT, "ops@northwind-audio.example"], []),
                                   ([CONTACT], ["Boss <boss@northwind-audio.example>"])])
def test_exactly_one_recipient(project, to, cc):
    data = payload()
    data["tool_input"].update(to=to, cc=cc)
    assert "one recipient per gated draft" in reason(run(project, data))


def test_named_address_counts_as_one_recipient(project):
    record_price(project)
    data = payload()
    data["tool_input"]["to"] = [f"Dana Smith <{CONTACT}>"]
    assert run(project, data) == ""


# --- verdicts ---

def test_unsourced_number_is_denied_with_the_record_command(project):
    text = reason(run(project, payload()))
    assert "claims: the number '$0.006' has no sourced claim" in text
    assert "python3 -m gates.evidence record" in text and "--gates-home" in text
    assert "Claims folder read:" in text


def test_recorded_source_lets_the_draft_through_and_logs_it(project):
    record_price(project)
    assert run(project, payload()) == ""
    entry = log_lines(project)[-1]
    assert entry["decision"] == "allow" and entry["recipient_domain"] == "northwind-audio.example"
    assert entry["crm_path"].endswith("demo/gate_crm.json") and len(entry["inputs_sha256"]) == 64


def test_html_body_with_an_invented_number_is_denied(project):
    record_price(project)
    data = payload()
    data["tool_input"]["htmlBody"] = "<p>" + DRAFT_TEXT.replace(
        "$0.006 per audio minute", "$0.006 per audio minute, 80% less").replace("\n\n", "</p><p>") + "</p>"
    assert "htmlBody:" in reason(run(project, data))


def test_log_never_holds_the_email_text(project):
    run(project, payload())
    line = json.dumps(log_lines(project))
    assert "Northwind's pricing page" not in line and CONTACT not in line


def test_missing_crm_path_is_denied(project):
    (project / ".gates" / "config.json").write_text(json.dumps({"example_pages": "demo/pages"}))
    assert "set crm_path in .gates/config.json" in reason(run(project, payload()))


def test_project_cannot_switch_off_evidence(project):
    cfg = json.loads((project / ".gates" / "config.json").read_text())
    cfg["require_evidence"] = False
    (project / ".gates" / "config.json").write_text(json.dumps(cfg))
    folder = evidence.draft_dir(project / ".gates", CONTACT) / "claims"
    folder.mkdir(parents=True)
    (folder / "x.json").write_text(json.dumps({"claim": "x", "value": "$0.006",
                                               "url": "https://northwind-audio.example/pricing"}))
    assert "claims_unverified" in reason(run(project, payload()))


def test_unreadable_input_is_denied(project):
    out = gate_gmail.decide("not json", {"CLAUDE_PROJECT_DIR": str(project)})
    assert "cannot read tool input" in reason(out)


def test_log_write_failure_does_not_change_the_verdict(project):
    record_price(project)
    (project / ".gates" / "log").write_text("a file where the log folder should be")
    assert run(project, payload()) == ""


# --- the wrapper (eng D10) ---

def wrapper(project, stdin, **env):
    full = {"PATH": "/usr/bin:/bin", "CLAUDE_PLUGIN_ROOT": str(ROOT), "CLAUDE_PROJECT_DIR": str(project),
            "GATE_PYTHON": sys.executable, **env}
    return subprocess.run(["sh", str(ROOT / "hooks" / "gate.sh")], input=stdin, text=True,
                          capture_output=True, env=full)


def test_wrapper_passes_the_gate_answer_through(project):
    result = wrapper(project, json.dumps(payload()))
    assert result.returncode == 0 and "has no sourced claim" in reason(result.stdout)
    assert wrapper(project, json.dumps({"tool_name": "mcp__a__b"})).stdout == ""


def test_wrapper_turns_a_missing_interpreter_into_a_deny(project):
    result = wrapper(project, "{}", GATE_PYTHON="/nonexistent/python3")
    assert result.returncode == 0 and "gate crashed: exit 127" in reason(result.stdout)


def test_wrapper_turns_a_crash_into_a_deny_and_drops_partial_output(project, tmp_path):
    fake = tmp_path / "fakepython"
    fake.write_text("#!/bin/sh\nprintf 'partial'\nexit 1\n")
    fake.chmod(0o755)
    result = wrapper(project, "{}", GATE_PYTHON=str(fake))
    assert "gate crashed: exit 1" in reason(result.stdout) and "partial" not in result.stdout
