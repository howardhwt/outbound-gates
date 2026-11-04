"""Live HubSpot reads in the gate: read_crm, the time limits and the hook's watchdog.

A fake HubSpot (a transport function) stands in for the API. Nothing here
touches the network.
"""
import json
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

from gates import evidence, hubspot
from gates.checker import UsageError
from hooks import gate_gmail

ROOT = Path(__file__).resolve().parents[1]
CONTACT = "dana@northwind-audio.example"
DRAFT_TEXT = (ROOT / "demo" / "gate" / "northwind-draft.txt").read_text(encoding="utf-8")
QUOTE = "Transcription is billed at $0.006 per audio minute"
TODAY = date(2026, 11, 4)


class FakeHubSpot:
    """Answers the requests build_company and read_contact make."""

    def __init__(self, company=None, contact=None, deals=(), status_for=None):
        self.company = company if company is not None else {
            "id": "101", "properties": {"domain": "northwind-audio.example", "name": "Northwind Audio",
                                        "hs_lead_status": "NEW", "hubspot_owner_id": "42"}}
        self.contact = contact
        self.deals = list(deals)
        self.status_for = status_for or (lambda method, url: None)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        forced = self.status_for(method, url)
        if forced:
            return forced
        data = json.loads(body) if body else {}
        if url.endswith("/crm/v3/objects/companies/search"):
            return 200, {}, json.dumps({"results": [self.company] if self.company else []}).encode()
        if "/associations/deals" in url:
            return 200, {}, json.dumps({"results": [{"toObjectId": d["id"]} for d in self.deals]}).encode()
        if "/associations/" in url:
            return 200, {}, b'{"results": []}'
        if url.endswith("/crm/v3/objects/deals/batch/read"):
            wanted = {i["id"] for i in data["inputs"]}
            return 200, {}, json.dumps({"results": [d for d in self.deals if d["id"] in wanted]}).encode()
        if url.endswith("/crm/v3/objects/contacts/search"):
            return 200, {}, json.dumps({"results": [self.contact] if self.contact else []}).encode()
        return 404, {}, b"{}"


CLEAN_CONTACT = {"properties": {"email": CONTACT, "hs_email_optout": "false", "hs_sequences_is_enrolled": "false"}}


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".gates").mkdir(parents=True)
    shutil.copytree(ROOT / "demo" / "pages", proj / "demo" / "pages")
    (proj / ".gates" / "config.json").write_text(json.dumps({
        "crm_source": "hubspot", "sender_owner": "42", "example_pages": "demo/pages"}))
    return proj


@pytest.fixture
def key(tmp_path, monkeypatch):
    path = tmp_path / "hubspot.key"
    path.write_text("test-key\n")
    monkeypatch.setattr(evidence, "HUBSPOT_KEY_FILE", path)
    return path


def settings_for(project):
    return evidence.load_settings(ROOT, project)


# --- settings -----------------------------------------------------------------

def test_file_is_the_default_source(tmp_path):
    (tmp_path / ".gates").mkdir()
    assert evidence.load_settings(ROOT, tmp_path)["crm_source"] == "file"


def test_unknown_source_is_refused(tmp_path):
    (tmp_path / ".gates").mkdir()
    (tmp_path / ".gates" / "config.json").write_text('{"crm_source": "salesforce"}')
    with pytest.raises(UsageError, match="crm_source must be one of"):
        evidence.load_settings(ROOT, tmp_path)


def test_one_touch_limit_key(tmp_path):
    (tmp_path / ".gates").mkdir()
    (tmp_path / ".gates" / "config.json").write_text('{"hubspot": {"max_unanswered_touches": 3}}')
    with pytest.raises(UsageError, match="max_unanswered"):
        evidence.load_settings(ROOT, tmp_path)


# --- read_crm -----------------------------------------------------------------

def test_missing_key_refuses(project, tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "HUBSPOT_KEY_FILE", tmp_path / "nope.key")
    with pytest.raises(UsageError, match="no HubSpot key"):
        evidence.read_crm(settings_for(project), CONTACT, transport=FakeHubSpot())


def test_live_read_builds_the_crm(project, key):
    fake = FakeHubSpot(contact=CLEAN_CONTACT)
    crm, warnings = evidence.read_crm(settings_for(project), CONTACT, transport=fake)
    company = crm["companies"][0]
    assert company["domain"] == "northwind-audio.example" and company["owner"] == "42"
    assert company["contacts"][0]["email"] == CONTACT
    assert warnings[-1].startswith("read live from HubSpot in ")
    assert sum(w.startswith("HubSpot mapping gaps: ") for w in warnings) == 1  # collapsed to one line
    assert not any("sender_owner" in w for w in warnings)


def test_key_is_sent_as_bearer_token(project, key):
    seen = []

    def transport(method, url, headers, body):
        seen.append(headers.get("Authorization"))
        return FakeHubSpot(contact=CLEAN_CONTACT)(method, url, headers, body)

    evidence.read_crm(settings_for(project), CONTACT, transport=transport)
    assert set(seen) == {"Bearer test-key"}


def test_contact_missing_from_hubspot_is_a_warning(project, key):
    _, warnings = evidence.read_crm(settings_for(project), CONTACT, transport=FakeHubSpot(contact=None))
    assert any("is not a HubSpot contact" in w for w in warnings)


def test_no_sender_owner_is_a_warning(project, key):
    (project / ".gates" / "config.json").write_text('{"crm_source": "hubspot"}')
    _, warnings = evidence.read_crm(settings_for(project), CONTACT, transport=FakeHubSpot(contact=CLEAN_CONTACT))
    assert any("owner check is off" in w for w in warnings)


def test_unknown_company_gives_an_empty_crm(project, key):
    crm, _ = evidence.read_crm(settings_for(project), CONTACT, transport=FakeHubSpot(company={}))
    assert crm == {"companies": []}


def test_hubspot_error_refuses(project, key):
    fake = FakeHubSpot(status_for=lambda m, u: (401, {}, b"{}"))
    with pytest.raises(UsageError, match="refused the request"):
        evidence.read_crm(settings_for(project), CONTACT, transport=fake)


def test_unreachable_hubspot_refuses(project, key):
    def transport(*_):
        raise OSError("connection refused")

    with pytest.raises(UsageError, match="HubSpot unreachable"):
        evidence.read_crm(settings_for(project), CONTACT, transport=transport)


# --- time limits ---------------------------------------------------------------

def test_retry_after_is_ignored_and_backoff_capped():
    sleeps = []
    responses = iter([(429, {"Retry-After": "30"}, b""), (200, {}, b'{"results": []}')])
    client = hubspot.HubSpot("k", transport=lambda *a: next(responses), sleep=sleeps.append,
                             retries=1, max_backoff=1.0, honour_retry_after=False)
    client.find_companies("x.example", [])
    assert sleeps == [1] and client.requests == 2


def test_only_one_retry():
    client = hubspot.HubSpot("k", transport=lambda *a: (503, {}, b""), sleep=lambda s: None,
                             retries=1, max_backoff=1.0, honour_retry_after=False)
    with pytest.raises(UsageError, match="503"):
        client.find_companies("x.example", [])
    assert client.requests == 2


def test_budget_checked_before_each_request():
    clock = iter([0.0, 50.0])
    client = hubspot.HubSpot("k", transport=lambda *a: (200, {}, b'{"results": [], "paging": {"next": {"after": "2"}}}'),
                             deadline=40.0, clock=lambda: next(clock))
    with pytest.raises(hubspot.BudgetExceeded, match="ran out of time after 1"):
        client.associated_ids("1", "notes")


def test_budget_checked_before_a_sleep():
    sleeps = []
    client = hubspot.HubSpot("k", transport=lambda *a: (429, {}, b""), sleep=sleeps.append, retries=1,
                             max_backoff=1.0, honour_retry_after=False, deadline=40.5, clock=lambda: 40.0)
    with pytest.raises(hubspot.BudgetExceeded):
        client.find_companies("x.example", [])
    assert sleeps == []


def test_default_client_is_unchanged():
    sleeps = []
    responses = iter([(429, {"Retry-After": "7"}, b""), (200, {}, b'{"results": []}')])
    hubspot.HubSpot("k", transport=lambda *a: next(responses), sleep=sleeps.append).find_companies("x.example", [])
    assert sleeps == [7.0]


def test_read_crm_passes_budget_through(project, key):
    with pytest.raises(UsageError, match="ran out of time"):
        evidence.read_crm(settings_for(project), CONTACT, transport=FakeHubSpot(), deadline=time.monotonic() - 1)


# --- the hook --------------------------------------------------------------------

def hook_payload(project):
    return json.dumps({
        "session_id": "s", "cwd": str(project), "hook_event_name": "PreToolUse",
        "tool_name": "mcp__claude_ai_Gmail__create_draft",
        "tool_input": {"to": [CONTACT], "subject": "Northwind supply", "body": DRAFT_TEXT},
    })


def decide(project, fake, **kw):
    return gate_gmail.decide(hook_payload(project), {"CLAUDE_PLUGIN_ROOT": str(ROOT), "CLAUDE_PROJECT_DIR": str(project)},
                             today=TODAY, transport=fake, **kw)


def record_price(project, fake):
    settings = settings_for(project)
    crm, _ = evidence.read_crm(settings, CONTACT, transport=fake)
    evidence.record_claim(project / ".gates", CONTACT, "https://northwind-audio.example/pricing", QUOTE, "$0.006",
                          settings=settings, crm=crm)


def log_lines(project):
    return [json.loads(l) for l in (project / ".gates" / "log" / "gate.jsonl").read_text().splitlines()]


def test_hook_allows_with_warnings_shown(project, key):
    fake = FakeHubSpot(contact=CLEAN_CONTACT)
    record_price(project, fake)
    out = decide(project, fake)
    assert "permissionDecision" not in out
    assert json.loads(out)["systemMessage"].startswith("send gate: draft passed, with warnings: ")
    entry = log_lines(project)[-1]
    assert entry["decision"] == "allow" and entry["crm_path"] == "hubspot" and entry["warnings"] >= 1


def test_hook_refuses_a_disqualified_account(project, key):
    fake = FakeHubSpot(contact=CLEAN_CONTACT, deals=[{"id": "9", "properties": {"dealstage": "closedlost",
                                                                                   "closed_lost_reason": "no budget"}}])
    record_price(project, fake)
    reason = json.loads(decide(project, fake))["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no budget" in reason and "CRM warnings:" in reason


def test_hook_refuses_an_unsubscribed_contact(project, key):
    fake = FakeHubSpot(contact={"properties": {"email": CONTACT, "hs_email_optout": "true"}})
    record_price(project, fake)
    assert "unsubscribed" in json.loads(decide(project, fake))["hookSpecificOutput"]["permissionDecisionReason"]


def test_hook_refuses_without_a_key(project, tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "HUBSPOT_KEY_FILE", tmp_path / "nope.key")
    reason = json.loads(decide(project, FakeHubSpot()))["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no HubSpot key" in reason
    assert log_lines(project)[-1]["rules"] == ["input"]


def test_hook_refuses_when_out_of_time(project, key):
    out = decide(project, FakeHubSpot(contact=CLEAN_CONTACT), deadline=time.monotonic() - 1)
    assert "ran out of time" in json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def test_hook_never_falls_back_to_a_file(project, key):
    (project / ".gates" / "config.json").write_text(json.dumps({
        "crm_source": "hubspot", "crm_path": str(ROOT / "demo" / "gate_crm.json")}))
    out = decide(project, FakeHubSpot(status_for=lambda m, u: (500, {}, b"down")))
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_record_command_reads_hubspot(project, key, monkeypatch):
    monkeypatch.setattr(evidence, "read_crm", lambda settings, contact, **kw: (
        {"companies": [{"domain": "northwind-audio.example", "alt_domains": []}]}, []))
    calls = []
    monkeypatch.setattr(evidence, "record_claim", lambda *a, **kw: calls.append(kw["crm"]) or Path("x"))
    assert evidence.main(["record", "--gates-home", str(project / ".gates"), "--contact", CONTACT,
                          "--url", "https://northwind-audio.example/pricing", "--quote", QUOTE,
                          "--value", "$0.006"]) == 0
    assert calls[0]["companies"][0]["domain"] == "northwind-audio.example"


# --- the watchdog ------------------------------------------------------------------

def test_watchdog_refuses_a_hung_hook_in_time():
    script = (
        "import sys, time; sys.path.insert(0, %r)\n"
        "from hooks import gate_gmail\n"
        "gate_gmail.start_watchdog(0.5)\n"
        "time.sleep(10)\n"
        "gate_gmail._emit('too late')\n"
    ) % str(ROOT)
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
    assert time.monotonic() - started < 5
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "gate deadline exceeded" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_watchdog_stays_quiet_after_an_answer():
    script = (
        "import sys, time; sys.path.insert(0, %r)\n"
        "from hooks import gate_gmail\n"
        "gate_gmail.start_watchdog(0.3)\n"
        "gate_gmail._emit('')\n"
        "time.sleep(1)\n"
    ) % str(ROOT)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0 and proc.stdout == ""


def test_watchdog_inside_hook_budget():
    assert evidence.CRM_BUDGET_S < gate_gmail.WATCHDOG_S < 60
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
    assert gate_gmail.WATCHDOG_S + 10 <= hooks["timeout"]
