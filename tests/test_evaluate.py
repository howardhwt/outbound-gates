"""gates.evaluate: one deterministic verdict per draft, with evidence and pre-send."""
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from gates import evidence
from gates.evaluate import Draft, evaluate

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 3)
CONTACT = "dana@northwind-audio.example"
DRAFT_TEXT = (ROOT / "demo" / "gate" / "northwind-draft.txt").read_text(encoding="utf-8")
CRM = json.loads((ROOT / "demo" / "gate_crm.json").read_text(encoding="utf-8"))
QUOTE = "Transcription is billed at $0.006 per audio minute"


@pytest.fixture
def project(tmp_path):
    """A project set up like the demo: .gates/config.json, fixture pages, CRM file."""
    proj = tmp_path / "proj"
    (proj / ".gates").mkdir(parents=True)
    shutil.copytree(ROOT / "demo" / "pages", proj / "demo" / "pages")
    shutil.copy(ROOT / "demo" / "gate_crm.json", proj / "demo" / "gate_crm.json")
    shutil.copy(ROOT / "demo" / "gate" / "config.json", proj / ".gates" / "config.json")
    return proj


def settings_for(proj):
    return evidence.load_settings(ROOT, proj, user_config=proj / "no-user-config.json")


def record_price(proj):
    s = settings_for(proj)
    return evidence.record_claim(
        proj / ".gates", CONTACT, "https://northwind-audio.example/pricing", QUOTE, "$0.006",
        settings=s, crm=CRM, now=datetime(2026, 11, 3, tzinfo=timezone.utc))


def run(proj, subject="Northwind transcription supply", bodies=None):
    draft = Draft(CONTACT, subject, bodies or (("body", DRAFT_TEXT),))
    return evaluate(draft, CRM, settings_for(proj), gates_home=proj / ".gates", today=TODAY)


def rules(verdict, severity="fail"):
    return {f.rule for f in verdict.findings if f.severity == severity}


def test_unsourced_price_is_blocked_then_passes_once_recorded(project):
    blocked = run(project)
    assert not blocked.passed and "claims" in rules(blocked)
    assert any("'$0.006'" in f.message for f in blocked.findings)
    record_price(project)
    allowed = run(project)
    assert allowed.passed, [str(f) for f in allowed.findings]
    assert allowed.verified_claims == 1


def test_forged_claim_without_stored_page_is_unverified(project):
    folder = evidence.draft_dir(project / ".gates", CONTACT) / "claims"
    folder.mkdir(parents=True)
    (folder / "forged.json").write_text(json.dumps({
        "claim": "made up", "value": "$0.006", "url": "https://northwind-audio.example/pricing",
        "quote": QUOTE, "evidence_sha256": "0" * 64, "checked": "2026-11-03"}))
    verdict = run(project)
    assert not verdict.passed and "claims_unverified" in rules(verdict)


def test_verdict_is_deterministic(project):
    record_price(project)
    assert run(project).to_json() == run(project).to_json()


def test_fingerprint_changes_when_a_stored_page_changes(project):
    claim_path = record_price(project)
    before = run(project).inputs_sha256
    sha = json.loads(claim_path.read_text())["evidence_sha256"]
    page = project / ".gates" / "evidence" / f"{sha}.txt"
    page.write_text(page.read_text() + " ")
    assert run(project).inputs_sha256 != before


def test_subject_gets_its_own_subset_of_rules(project):
    record_price(project)
    verdict = run(project, subject="I wanted to reach out about 40% savings")
    messages = [f.message for f in verdict.findings if f.severity == "fail"]
    assert any(m.startswith("subject:") and "reach out" in m for m in messages)
    assert any(m.startswith("subject:") and "'40%'" in m for m in messages)
    assert not any(f.rule in {"length", "sign_off", "required_close"} and f.message.startswith("subject:")
                   for f in verdict.findings)


def test_every_body_version_is_checked(project):
    record_price(project)
    html_text = DRAFT_TEXT.replace("$0.006 per audio minute", "$0.006 per audio minute, 80% less")
    verdict = run(project, bodies=(("body", DRAFT_TEXT), ("htmlBody", html_text)))
    assert not verdict.passed
    assert any(f.message.startswith("htmlBody:") and "'80%'" in f.message for f in verdict.findings)


def test_presend_history_still_blocks(project):
    record_price(project)
    crm = json.loads(json.dumps(CRM))
    crm["companies"][0]["deals"] = [{"stage": "closed_lost", "dq_reason": "not a fit"}]
    draft = Draft(CONTACT, "Northwind", (("body", DRAFT_TEXT),))
    verdict = evaluate(draft, crm, settings_for(project), gates_home=project / ".gates", today=TODAY)
    assert "disqualified" in rules(verdict)


def test_verdict_records_the_crm_path(project):
    assert run(project).to_dict()["crm_path"].endswith("demo/gate_crm.json")
