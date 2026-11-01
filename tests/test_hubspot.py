"""The HubSpot adapter, against a fake HubSpot. No network and no key needed.

Property names and enum values in the fixtures follow a live portal's schema,
read on 2026-11-01: hs_lead_status uses ATTEMPTED_TO_CONTACT, hs_task_status has
five values, emails carry hs_email_direction and hs_email_status.
"""
import json
import re
from datetime import date

import pytest

from gates.checker import FAIL, UsageError, passed
from gates.hubspot import HubSpot, build_company, export, load_mapping, main
from gates.presend import presend_check

TODAY = date(2026, 10, 3)


class FakeHubSpot:
    """Answers the four endpoints the adapter uses, from in-memory records."""

    def __init__(self, companies=None, notes=None, tasks=None, deals=None, emails=None,
                 links=None, page_size=500, fail_first=0):
        self.companies = companies or []
        self.records = {"notes": notes or {}, "tasks": tasks or {}, "deals": deals or {}, "emails": emails or {}}
        self.links = links or {}  # (company_id, type) -> [ids]
        self.page_size = page_size
        self.fail_first = fail_first
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers))
        if self.fail_first:
            self.fail_first -= 1
            return 429, {"Retry-After": "0"}, b"{}"
        path = url.split("api.hubapi.com", 1)[1]
        data = json.loads(body) if body else None
        if path == "/crm/v3/objects/companies/search":
            wanted = {f["filters"][0]["value"] for f in data["filterGroups"]}
            hits = [c for c in self.companies if c["properties"].get("domain") in wanted]
            return 200, {}, json.dumps({"results": hits}).encode()
        m = re.match(r"/crm/v4/objects/companies/(\w+)/associations/(\w+)\?limit=\d+(?:&after=(\d+))?", path)
        if m:
            ids = self.links.get((m.group(1), m.group(2)), [])
            start = int(m.group(3) or 0)
            page = ids[start:start + self.page_size]
            out = {"results": [{"toObjectId": int(i)} for i in page]}
            if start + self.page_size < len(ids):
                out["paging"] = {"next": {"after": str(start + self.page_size)}}
            return 200, {}, json.dumps(out).encode()
        m = re.match(r"/crm/v3/objects/(\w+)/batch/read", path)
        if m:
            assert len(data["inputs"]) <= 100
            store = self.records[m.group(1)]
            hits = [{"id": i["id"], "properties": store[i["id"]]} for i in data["inputs"] if i["id"] in store]
            return 200, {}, json.dumps({"results": hits}).encode()
        return 404, {}, b'{"message":"not found"}'


def acme(**props):
    base = {"domain": "acme.example", "name": "Acme", "hs_lead_status": "NEW"}
    return {"id": "1", "properties": {**base, **props}}


def client(fake):
    return HubSpot("pat-test", transport=fake, sleep=lambda s: None)


def build(fake, mapping=None):
    company, _ = build_company(client(fake), "acme.example", mapping or load_mapping(None))
    return company


def rules(company, contact="a@acme.example"):
    found = presend_check({"companies": [company]}, "acme.example", contact, today=TODAY)
    return {f.rule for f in found if f.severity == FAIL}


def test_clean_company_passes():
    assert passed(presend_check({"companies": [build(FakeHubSpot([acme()]))]}, "acme.example", "a@acme.example", today=TODAY))


def test_status_map_translates_hubspot_lead_status():
    assert build(FakeHubSpot([acme(hs_lead_status="ATTEMPTED_TO_CONTACT")]))["status"] == "ATTEMPTED"


def test_unqualified_lead_status_blocks():
    assert "disqualified" in rules(build(FakeHubSpot([acme(hs_lead_status="UNQUALIFIED")])))


def test_closed_lost_reason_blocks_regardless_of_stage_id():
    fake = FakeHubSpot([acme()], deals={"9": {"dealstage": "3940243419", "closed_lost_reason": "builds in house"}},
                       links={("1", "deals"): ["9"]})
    assert "disqualified" in rules(build(fake))


def test_sent_note_html_body_is_read_and_blocks_same_contact():
    fake = FakeHubSpot([acme()], notes={"5": {"hs_note_body": "<p>SENT: first email to <b>A@acme.example</b></p>",
                                              "hs_timestamp": "2026-09-20T10:00:00Z"}},
                       links={("1", "notes"): ["5"]})
    company = build(fake)
    assert company["notes"][0]["to"] == "a@acme.example"
    assert "contacted" in rules(company)


def test_logged_outgoing_email_blocks_even_without_a_sent_note():
    fake = FakeHubSpot([acme()], emails={"7": {"hs_email_direction": "EMAIL", "hs_email_status": None,
                                               "hs_email_to_email": "a@acme.example", "hs_email_subject": "Hi",
                                               "hs_timestamp": "1758000000000"}},
                       links={("1", "emails"): ["7"]})
    company = build(fake)
    assert company["date_contacted"] == "2025-09-16"
    assert "contacted" in rules(company)


@pytest.mark.parametrize("status", ["BOUNCED", "FAILED", "DRAFT"])
def test_unsent_outgoing_email_does_not_count(status):
    fake = FakeHubSpot([acme()], emails={"7": {"hs_email_direction": "EMAIL", "hs_email_status": status,
                                               "hs_email_to_email": "a@acme.example", "hs_timestamp": "2026-09-01T00:00:00Z"}},
                       links={("1", "emails"): ["7"]})
    assert "contacted" not in rules(build(fake))


def test_incoming_email_sets_replied_and_blocks():
    fake = FakeHubSpot([acme()], emails={"8": {"hs_email_direction": "INCOMING_EMAIL", "hs_timestamp": "2026-09-25T08:00:00Z"}},
                       links={("1", "emails"): ["8"]})
    company = build(fake)
    assert company["date_replied"] == "2026-09-25"
    assert "replied" in rules(company)


@pytest.mark.parametrize("status,blocks", [("NOT_STARTED", True), ("WAITING", True), ("DEFERRED", True),
                                           ("IN_PROGRESS", True), ("COMPLETED", False)])
def test_only_completed_send_task_counts_as_done(status, blocks):
    fake = FakeHubSpot([acme()], tasks={"3": {"hs_task_subject": "SEND: Acme: draft waiting for a@acme.example",
                                              "hs_task_status": status}},
                       links={("1", "tasks"): ["3"]})
    assert ("draft_waiting" in rules(build(fake))) is blocks


def test_associations_page_and_batches_stay_under_100():
    ids = [str(i) for i in range(1000, 1250)]
    notes = {i: {"hs_note_body": "SOURCED: research", "hs_timestamp": "2026-09-01T00:00:00Z"} for i in ids}
    fake = FakeHubSpot([acme()], notes=notes, links={("1", "notes"): ids}, page_size=100)
    assert len(build(fake)["notes"]) == 250
    batch_calls = [c for c in fake.calls if "batch/read" in c[1]]
    assert len(batch_calls) == 3


def test_www_domain_and_duplicate_companies_are_combined():
    dup = {"id": "2", "properties": {"domain": "www.acme.example", "name": "Acme (dup)"}}
    fake = FakeHubSpot([acme(), dup], deals={"9": {"closed_lost_reason": "no budget"}}, links={("2", "deals"): ["9"]})
    company, warnings = build_company(client(fake), "https://www.Acme.example/", load_mapping(None))
    assert "disqualified" in rules(company)
    assert any("share acme.example" in w for w in warnings)


def test_unknown_company_returns_empty_export():
    crm, _ = export(client(FakeHubSpot([])), "nobody.example", load_mapping(None))
    assert crm == {"companies": []}
    assert {f.rule for f in presend_check(crm, "nobody.example", "x@nobody.example", today=TODAY)} == {"unknown_company"}


def test_mapped_custom_properties_win_over_derived_dates(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"registers": {}, "hubspot": {"date_replied_property": "first_reply", "segment_property": "seg"}}))
    fake = FakeHubSpot([acme(first_reply="2026-09-30", seg="reseller")])
    company = build(fake, load_mapping(str(cfg)))
    assert company["date_replied"] == "2026-09-30" and company["segment"] == "reseller"


def test_unmapped_fields_are_reported():
    _, warnings = export(client(FakeHubSpot([acme()])), "acme.example", load_mapping(None))
    text = " ".join(warnings)
    assert "auto-replies count as replies" in text and "alternate-domain" in text


def test_rate_limit_is_retried():
    fake = FakeHubSpot([acme()], fail_first=2)
    assert build(fake)["name"] == "Acme"


def test_auth_failure_is_a_usage_error_without_the_token():
    def refuse(method, url, headers, body):
        return 401, {}, b'{"message":"invalid"}'
    with pytest.raises(UsageError) as err:
        build_company(client(refuse), "acme.example", load_mapping(None))
    assert "pat-test" not in str(err.value)


def test_requests_are_read_only():
    fake = FakeHubSpot([acme()], links={("1", "notes"): ["5"]}, notes={"5": {"hs_note_body": "x"}})
    build(fake)
    for method, url, _ in fake.calls:
        assert method == "GET" or url.endswith(("/search", "/batch/read"))


def test_missing_token_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("HUBSPOT_SERVICE_KEY", raising=False)
    monkeypatch.delenv("HUBSPOT_ACCESS_TOKEN", raising=False)
    assert main(["--domain", "acme.example"], transport=FakeHubSpot([acme()])) == 2
    assert "HUBSPOT_SERVICE_KEY" in capsys.readouterr().err


def test_cli_runs_presend_and_blocks(monkeypatch, capsys):
    monkeypatch.setenv("HUBSPOT_SERVICE_KEY", "pat-test")
    fake = FakeHubSpot([acme(hs_lead_status="UNQUALIFIED")])
    assert main(["--domain", "acme.example", "--contact", "a@acme.example", "--today", "2026-10-03"], transport=fake) == 1
    out = capsys.readouterr()
    assert "BLOCKED" in out.out and "pat-test" not in out.out + out.err
