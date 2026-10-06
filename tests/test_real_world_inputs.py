"""Regression tests for inputs a real mail client, model or CRM export produces."""
import json
from datetime import date
from pathlib import Path

import pytest

from gates.checker import FAIL, check, load_claims, load_config, main, passed
from gates.presend import find_company, presend_check
from gates.presend import main as presend_main

ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_config(ROOT / "gates" / "config.example.json")
CLAIMS = load_claims(ROOT / "examples" / "claims.json")
GOOD = (ROOT / "examples" / "draft_pass.txt").read_text(encoding="utf-8")
TODAY = date(2026, 10, 3)


def fails(text, claims=CLAIMS, register="cold"):
    return [str(f) for f in check(text, CONFIG, register, claims, today=TODAY) if f.severity == FAIL]


def test_curly_quotes_from_a_mail_client_do_not_break_the_rules():
    assert fails(GOOD.replace("'", "’")) == []


def test_windows_line_endings_do_not_break_a_two_line_sign_off():
    body = "Hi Dana,\r\n\r\n" + "word " * 45 + "it's and I'd.\r\n\r\nThanks,\r\nSam"
    assert fails(body, claims=[], register="follow_up") == []


@pytest.mark.parametrize("extra", [
    "Free on Oct 6 at 2pm?",
    "Could we talk on 6 October at 10:30 am?",
    "The docs are at https://acme-transcribe.example/v2/start if useful.",
])
def test_dates_times_and_urls_are_not_treated_as_claims(extra):
    assert fails(GOOD.replace("Is model supply", extra + " Is model supply")) == []


def test_a_year_is_still_a_claim():
    assert any("2026" in f for f in fails(GOOD.replace("I saw", "In 2026 I saw")))


def test_a_number_matches_its_claim_with_or_without_the_currency_sign():
    assert fails(GOOD.replace("$0.006", "0.006")) == []


def test_a_claim_with_no_value_or_a_bad_date_is_reported_not_crashed():
    assert any("has no value" in f for f in fails(GOOD, claims=[{"claim": "c", "url": "u"}]))
    bad = [{**CLAIMS[0], "checked": "02/10/2026"}]
    assert any("not YYYY-MM-DD" in f for f in fails(GOOD, claims=bad))


@pytest.mark.parametrize("args, message", [
    (["nofile.txt", "--config", "gates/config.example.json", "--register", "cold"], "draft file not found"),
    (["examples/draft_pass.txt", "--config", "nofile.json", "--register", "cold"], "config file not found"),
    (["examples/draft_pass.txt", "--config", "README.md", "--register", "cold"], "not valid JSON"),
    (["examples/draft_pass.txt", "--config", "gates/config.example.json", "--register", "nope"], "unknown register"),
    (["examples/draft_pass.txt", "--config", "gates/config.example.json", "--register", "cold", "--today", "soon"], "YYYY-MM-DD"),
])
def test_bad_inputs_exit_2_with_one_line_and_no_traceback(args, message, capsys, monkeypatch):
    monkeypatch.chdir(ROOT)
    assert main(args) == 2
    err = capsys.readouterr().err
    assert message in err and "Traceback" not in err


@pytest.mark.parametrize("given", [
    "www.acme.example", "https://acme.example/pricing", "ACME.EXAMPLE ", "a@acme.example",
])
def test_a_domain_is_found_however_it_is_written(given):
    crm = {"companies": [{"domain": "acme.example"}]}
    assert find_company(crm, given) is not None


def test_presend_reads_lowercase_keywords_and_survives_a_bad_date():
    crm = {"companies": [{
        "domain": "acme.example", "status": "unqualified",
        "notes": [
            {"date": "1/10/2026", "text": "dq: wrong fit"},
            {"date": "x", "text": "sent: first email", "to": "b@acme.example"},
        ],
        "tasks": [{"subject": "send: Acme: draft waiting", "done": False}],
    }]}
    found = presend_check(crm, "acme.example", " A@Acme.example ", today=TODAY)
    assert {f.rule for f in found if f.severity == FAIL} == {"disqualified", "draft_waiting"}
    assert not passed(found)


def test_presend_cli_exits_2_on_a_missing_or_malformed_crm(tmp_path, capsys):
    base = ["--domain", "acme.example", "--contact", "a@acme.example"]
    assert presend_main(["--crm", str(tmp_path / "none.json"), *base]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([1, 2]))
    assert presend_main(["--crm", str(bad), *base]) == 2
    assert "Traceback" not in capsys.readouterr().err
