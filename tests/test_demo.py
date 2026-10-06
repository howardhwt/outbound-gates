import json
from datetime import date
from pathlib import Path

from demo.report import build_report
from demo.run_demo import ROOT as DEMO, confirm_send, run, run_detailed
from gates.checker import check, load_claims, load_config
from gates.funnel import funnel
from gates.presend import find_company, load_crm

ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_config(ROOT / "gates" / "config.example.json")
CRM = load_crm(DEMO / "mock_crm.json")
CANDIDATES = json.loads((DEMO / "candidates.json").read_text())


def test_each_account_ends_where_the_readme_says():
    _, _, outcomes = run(CRM, CANDIDATES, CONFIG)
    expected = {
        "northwind-audio.example": "DRAFTED",
        "lumen-notes.example": "DRAFTED",
        "harborvoice.example": "BLOCKED by pre-send",
        "pinestreet.example": "BLOCKED by pre-send",
        "kestrel-labs.example": "BLOCKED by the gate",
        "orchard-ai.example": "parked",
        "quill.example": "human queue",
    }
    assert set(outcomes) == set(expected)
    for domain, start in expected.items():
        assert outcomes[domain].startswith(start), (domain, outcomes[domain])


def test_source_skips_a_duplicate_under_an_alternate_domain():
    after, lines, _ = run(CRM, CANDIDATES, CONFIG)
    assert any("skipped" in l and "getharbor.example" in l for l in lines)
    assert len(after["companies"]) == len(CRM["companies"]) + 2


def test_the_run_does_not_modify_its_input_and_drafts_do_not_count_as_contacted():
    before = json.dumps(CRM, sort_keys=True)
    after, _, _ = run(CRM, CANDIDATES, CONFIG)
    assert json.dumps(CRM, sort_keys=True) == before
    assert funnel(after)["contacted"] == funnel(CRM)["contacted"]


def test_a_confirmed_send_moves_status_and_sets_the_date_once():
    after, _, _ = run(CRM, CANDIDATES, CONFIG)
    confirm_send(after, "northwind-audio.example", after["today"])
    company = find_company(after, "northwind-audio.example")
    assert company["status"] == "ATTEMPTED"
    assert company["date_contacted"] == after["today"]
    assert funnel(after)["contacted"]["reseller"] == funnel(CRM)["contacted"]["reseller"] + 1
    assert not any(t["subject"].startswith("SEND") and not t["done"] for t in company["tasks"])


def test_walkthrough_files_match_the_demo_fixtures_and_the_real_gate_output():
    walk = ROOT / "examples" / "walkthrough"
    acct = DEMO / "accounts" / "northwind-audio"
    assert (walk / "03-claims.json").read_text() == (acct / "claims.json").read_text()
    assert (walk / "04-draft.txt").read_text() == (acct / "draft.txt").read_text()
    findings = check(
        (acct / "draft.txt").read_text(), CONFIG, "cold",
        load_claims(acct / "claims.json"), today=date(2026, 10, 3),
    )
    recorded = (walk / "05-gate-output.txt").read_text()
    for finding in findings:
        assert str(finding) in recorded
    assert recorded.rstrip().endswith("PASS")


def test_the_report_shows_every_account_and_escapes_its_content():
    after, _, records, source_lines = run_detailed(CRM, CANDIDATES, CONFIG)
    html = build_report(CRM, after, records, source_lines, [])
    for record in records:
        assert record["name"] in html
    assert html.count('<details class="acct">') == len(records)
    assert "Blocked by pre-send" in html and "Blocked by the gate" in html
    assert "http://" not in html.replace("https://", "")  # no outside resources
    assert "<script" not in html
    hostile = [dict(records[0], name="<img src=x onerror=alert(1)>")]
    assert "<img src=x" not in build_report(CRM, after, hostile, source_lines, [])
