from datetime import date

from gates.checker import FAIL, WARN, passed
from gates.presend import find_company, main, presend_check

TODAY = date(2026, 10, 3)


def company(**over):
    base = {
        "domain": "acme.example", "alt_domains": [], "name": "Acme", "status": "NEW",
        "date_contacted": None, "date_replied": None,
        "notes": [], "tasks": [], "deals": [],
    }
    return {"companies": [{**base, **over}]}


def rules(crm, contact="a@acme.example", severity=FAIL):
    found = presend_check(crm, "acme.example", contact, today=TODAY)
    return {f.rule for f in found if f.severity == severity}


def test_clean_account_passes():
    assert passed(presend_check(company(), "acme.example", "a@acme.example", today=TODAY))


def test_unknown_company_is_blocked():
    assert rules({"companies": []}) == {"unknown_company"}


def test_alternate_domain_matches_the_same_company():
    crm = company(alt_domains=["getacme.example"])
    assert find_company(crm, "GetAcme.example")["domain"] == "acme.example"


def test_disqualified_status_blocks():
    assert "disqualified" in rules(company(status="UNQUALIFIED"))


def test_disqualification_on_a_deal_blocks_even_when_status_is_new():
    crm = company(deals=[{"stage": "closed_lost", "dq_reason": "builds in house"}])
    assert "disqualified" in rules(crm)


def test_dq_and_do_not_email_notes_block():
    for text in ("DQ: wrong fit", "DO NOT EMAIL: asked us to stop"):
        assert "disqualified" in rules(company(notes=[{"date": "2026-09-01", "text": text}]))


def test_same_contact_already_emailed_blocks():
    crm = company(notes=[{"date": "2026-09-20", "text": "SENT: first email", "to": "a@acme.example"}])
    assert "contacted" in rules(crm)


def test_open_send_task_blocks_and_a_finished_one_does_not():
    subject = "SEND: Acme: draft waiting for a@acme.example"
    assert "draft_waiting" in rules(company(tasks=[{"subject": subject, "done": False}]))
    assert "draft_waiting" not in rules(company(tasks=[{"subject": subject, "done": True}]))


def test_promise_and_reply_block():
    assert "promise" in rules(company(notes=[{"date": "2026-08-01", "text": "PROMISE: I won't write again"}]))
    assert "replied" in rules(company(date_replied="2026-09-12"))


def test_recent_colleague_send_warns_but_does_not_block():
    crm = company(notes=[{"date": "2026-10-01", "text": "SENT: first email", "to": "b@acme.example"}])
    found = presend_check(crm, "acme.example", "a@acme.example", today=TODAY)
    assert passed(found)
    assert {f.rule for f in found if f.severity == WARN} == {"colleague_contacted"}


def test_old_colleague_send_is_ignored():
    crm = company(notes=[{"date": "2026-08-01", "text": "SENT: first email", "to": "b@acme.example"}])
    assert presend_check(crm, "acme.example", "a@acme.example", today=TODAY) == []


def test_cli_exit_codes(tmp_path, capsys):
    import json
    path = tmp_path / "crm.json"
    path.write_text(json.dumps(company(status="UNQUALIFIED")))
    args = ["--crm", str(path), "--domain", "acme.example", "--contact", "a@acme.example", "--today", "2026-10-03"]
    assert main(args) == 1
    assert "BLOCKED" in capsys.readouterr().out
    path.write_text(json.dumps(company()))
    assert main(args) == 0


# Owner claim and contact-level blocks (optional fields; absent means no check)

def test_account_owned_by_someone_else_is_blocked():
    crm = company(owner="rep-2")
    found = presend_check(crm, "acme.example", "a@acme.example", today=TODAY, sender_owner="rep-1")
    assert "owned_by_other" in {f.rule for f in found if f.severity == FAIL}


def test_own_or_unowned_account_passes_owner_check():
    for crm in (company(owner="rep-1"), company(owner=""), company()):
        assert passed(presend_check(crm, "acme.example", "a@acme.example", today=TODAY, sender_owner="rep-1"))


def test_owner_check_is_off_without_sender_owner():
    assert passed(presend_check(company(owner="rep-2"), "acme.example", "a@acme.example", today=TODAY))


def test_contact_blocks():
    person = {"email": "A@acme.example", "blocked": "Asked to stop", "in_sequence": True, "unanswered_touches": 4}
    found = presend_check(company(contacts=[person]), "acme.example", "a@acme.example", today=TODAY, max_unanswered=4)
    assert {"contact_blocked", "in_sequence", "touch_limit"} <= {f.rule for f in found if f.severity == FAIL}


def test_touch_limit_needs_a_threshold_and_other_contacts_do_not_block():
    person = {"email": "b@acme.example", "blocked": "Bounced", "unanswered_touches": 9}
    assert passed(presend_check(company(contacts=[person]), "acme.example", "a@acme.example", today=TODAY, max_unanswered=4))
    mine = {"email": "a@acme.example", "unanswered_touches": 9}
    assert passed(presend_check(company(contacts=[mine]), "acme.example", "a@acme.example", today=TODAY))
