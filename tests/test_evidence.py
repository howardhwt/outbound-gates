"""gates.evidence: the source rule, the fetch guard, claim storage and the record command."""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gates import evidence
from gates.checker import UsageError
from gates.evidence import EvidenceError, claims_for, record_claim, verify_claim

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "evidence"
PRICING = (FIXTURES / "pricing.html").read_bytes()
OTHER = (FIXTURES / "other.html").read_bytes()
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
CONTACT = "dana@northwind-audio.example"
CRM = {"companies": [{"domain": "northwind.com", "alt_domains": ["northwind-audio.com"]}]}
SETTINGS = {"source_allowlist": [], "example_pages": None}
QUOTE = "Transcription is billed at $0.006 per audio minute"
REAL = "dana@northwind-audio.com"


def public(host):
    return ["93.184.216.34"]


def serve(pages):
    """A fake fetch that serves pages by URL and runs the guard like the real one."""
    def fetch(url, check):
        check(url)
        body = pages[url]
        return url, "text/html; charset=utf-8", body
    return fetch


def record(tmp_path, url="https://northwind.com/pricing", quote=QUOTE, value="$0.006",
           contact=REAL, pages=None, settings=SETTINGS, crm=CRM):
    pages = pages or {url: PRICING}
    return record_claim(tmp_path, contact, url, quote, value, settings=settings, crm=crm,
                        fetch=serve(pages), resolve=public, now=NOW)


# --- recipient paths (eng D8) ---

@pytest.mark.parametrize("bad", ["x@..", "..", "", "a@b@c.com", "a@.", "@acme.com", "a@acme..com"])
def test_bad_recipients_are_refused(tmp_path, bad):
    with pytest.raises(EvidenceError, match="invalid recipient path"):
        evidence.draft_dir(tmp_path, bad)


def test_same_folder_whatever_the_case(tmp_path):
    assert evidence.draft_dir(tmp_path, "Dana@Acme.COM") == evidence.draft_dir(tmp_path, "dana@acme.com")


# --- recording a claim ---

def test_a_supported_quote_is_recorded_and_verifies(tmp_path):
    path = record(tmp_path)
    claim = json.loads(path.read_text())
    assert claim["v"] == 1 and claim["value"] == "$0.006" and claim["checked"] == "2026-11-03"
    assert claims_for(tmp_path, REAL) == [claim]
    assert verify_claim(claim, tmp_path)


def test_alternate_domain_counts_as_the_company_site(tmp_path):
    url = "https://www.northwind-audio.com/pricing"
    assert record(tmp_path, url=url).exists()


def test_quote_must_be_on_the_page(tmp_path):
    with pytest.raises(EvidenceError, match="quote not found at URL"):
        record(tmp_path, quote="Transcription is billed at $0.004 per audio minute")
    assert not (tmp_path / "evidence").exists()


def test_value_must_be_a_number_in_the_quote_not_a_substring(tmp_path):
    with pytest.raises(EvidenceError, match="value not in quote"):
        record(tmp_path, quote="Plans start at 1,200 minutes a month", value="200")
    assert record(tmp_path, quote="Plans start at 1,200 minutes a month", value="1200").exists()


def test_text_hidden_in_page_script_does_not_count(tmp_path):
    with pytest.raises(EvidenceError, match="quote not found at URL"):
        record(tmp_path, quote="Save 40% this week", value="40%")


def test_off_domain_source_is_refused(tmp_path):
    url = "https://gist.example.org/fake"
    with pytest.raises(EvidenceError, match="source host not allowed"):
        record(tmp_path, url=url, pages={url: OTHER})


def test_user_allowlist_admits_a_third_party_host(tmp_path):
    url = "https://news.example.org/story"
    settings = dict(SETTINGS, source_allowlist=["example.org"])
    assert record(tmp_path, url=url, quote="Our team grew 40% last year", value="40%",
                  pages={url: OTHER}, settings=settings).exists()


def test_company_not_in_crm_is_refused(tmp_path):
    with pytest.raises(EvidenceError, match="company not in CRM"):
        record(tmp_path, contact="jo@unknown.com")


@pytest.mark.parametrize("url", ["file:///etc/hosts", "ftp://northwind.com/x"])
def test_non_http_schemes_are_refused(tmp_path, url):
    with pytest.raises(EvidenceError, match="scheme not allowed"):
        record(tmp_path, url=url, pages={url: PRICING})


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "100.64.0.1", "169.254.1.1", "::1", "::ffff:127.0.0.1", "fd00::1"])
def test_private_addresses_are_refused(address):
    with pytest.raises(EvidenceError, match="private address not allowed"):
        evidence.check_url("https://northwind.com/x", ["northwind.com"], lambda h: [address])


def test_redirect_to_another_host_is_refused(tmp_path):
    def fetch(url, check):
        check(url)
        check("https://elsewhere.org/page")  # what urllib_fetch does on each hop
        return "https://elsewhere.org/page", "text/html", PRICING
    with pytest.raises(EvidenceError, match="source host not allowed"):
        record_claim(tmp_path, REAL, "https://northwind.com/p", QUOTE, "$0.006",
                     settings=SETTINGS, crm=CRM, fetch=fetch, resolve=public, now=NOW)


def test_non_text_pages_and_large_pages_are_refused():
    with pytest.raises(EvidenceError, match="unsupported content type: application/pdf"):
        evidence.page_text("application/pdf", b"%PDF")
    with pytest.raises(EvidenceError, match="page too large"):
        evidence.page_text("text/html", b"x" * (evidence.MAX_BYTES + 1))


def test_example_hosts_are_served_from_fixture_pages_only(tmp_path):
    pages = tmp_path / "pages" / "northwind-audio.example"
    pages.mkdir(parents=True)
    (pages / "pricing.html").write_bytes(PRICING)
    crm = {"companies": [{"domain": "northwind-audio.example"}]}
    settings = dict(SETTINGS, example_pages=tmp_path / "pages")
    def no_network(url, check):
        raise AssertionError("an .example host must never be fetched")
    path = record_claim(tmp_path / ".gates", CONTACT, "https://northwind-audio.example/pricing",
                        QUOTE, "$0.006", settings=settings, crm=crm, fetch=no_network,
                        resolve=public, now=NOW)
    assert verify_claim(json.loads(path.read_text()), tmp_path / ".gates")


def test_recording_twice_is_idempotent_and_parallel_records_all_land(tmp_path):
    first = record(tmp_path)
    assert record(tmp_path) == first
    quotes = [("Plans start at 1,200 minutes a month", "1,200"), (QUOTE, "$0.006")]
    threads = [threading.Thread(target=record, kwargs=dict(tmp_path=tmp_path, quote=q, value=v))
               for q, v in quotes * 3]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert {c["value"] for c in claims_for(tmp_path, REAL)} == {"$0.006", "1,200"}


# --- verification ---

def test_tampered_or_missing_snapshot_fails_verification(tmp_path):
    claim = json.loads(record(tmp_path).read_text())
    snapshot = tmp_path / "evidence" / f"{claim['evidence_sha256']}.txt"
    snapshot.write_text(snapshot.read_text().replace("$0.006", "$0.004"))
    assert not verify_claim(claim, tmp_path)
    snapshot.unlink()
    assert not verify_claim(claim, tmp_path)
    assert not verify_claim(dict(claim, evidence_sha256="../../etc"), tmp_path)


def test_no_claims_folder_means_no_claims(tmp_path):
    assert claims_for(tmp_path, REAL) == []


# --- settings: the project file can only tighten (eng D8) ---

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def test_project_settings_cannot_loosen_safety_keys(tmp_path):
    project = tmp_path / "proj"
    write(project / ".gates" / "config.json", {
        "blocked_tools": [], "banned_phrases": [], "source_allowlist": ["evil.example"],
        "crm_path": "crm.json", "register": "cold", "sender_owner": "howard",
    })
    s = evidence.load_settings(ROOT, project, user_config=tmp_path / "none.json")
    assert "apollo_emailer_messages_send_now" in s["blocked_tools"]
    assert "i wanted to reach out" in [p.lower() for p in s["rules"]["banned_phrases"]]
    assert s["source_allowlist"] == []
    assert s["crm_path"] == project / "crm.json" and s["sender_owner"] == "howard"


def test_project_settings_can_add_and_user_settings_hold_the_allowlist(tmp_path):
    project = tmp_path / "proj"
    write(project / ".gates" / "config.json", {"blocked_tools": ["smartlead_send"],
                                               "banned_phrases": ["circle back"]})
    user = write(tmp_path / "user.json", {"source_allowlist": ["www.G2.com"]})
    s = evidence.load_settings(ROOT, project, user_config=user)
    assert "smartlead_send" in s["blocked_tools"]
    assert "circle back" in s["rules"]["banned_phrases"]
    assert s["source_allowlist"] == ["g2.com"]


def test_missing_crm_path_is_a_clear_error():
    with pytest.raises(UsageError, match="set crm_path"):
        evidence.load_crm({"crm_path": None})


# --- the record command ---

def test_record_command_exit_codes(tmp_path, capsys):
    project = tmp_path / "proj"
    crm = write(project / "crm.json", CRM)
    write(project / ".gates" / "config.json", {"crm_path": str(crm)})
    home = str(project / ".gates")
    url = "https://northwind.com/pricing"
    args = ["record", "--gates-home", home, "--contact", REAL, "--url", url, "--value", "$0.006"]
    fetch = serve({url: PRICING})
    assert evidence.main(args + ["--quote", QUOTE], fetch=fetch) == 0
    assert evidence.main(args + ["--quote", "not on the page 9"], fetch=fetch) == 1
    assert evidence.main(["record", "--gates-home", "relative/.gates", "--contact", REAL,
                          "--url", url, "--quote", QUOTE, "--value", "1"], fetch=fetch) == 2
    out = capsys.readouterr()
    assert "recorded:" in out.out and "refused: quote not found at URL" in out.err


def test_record_command_has_no_domain_flag():
    with pytest.raises(SystemExit):
        evidence.main(["record", "--domain", "x.com"])


# --- own facts from the user's config ---

def test_own_facts_come_only_from_the_user_config(tmp_path):
    project = tmp_path / "proj"
    (project / ".gates").mkdir(parents=True)
    user = write(tmp_path / "user.json", {"own_facts": ["$0.01", "1"]})
    facts = evidence.load_settings(ROOT, project, user_config=user)["rules"]["own_facts"]
    assert sorted(facts) == ["$0.01", "1"]  # the plugin ships none; the demo price is not a default


def test_project_cannot_add_own_facts(tmp_path):
    project = tmp_path / "proj"
    write(project / ".gates" / "config.json", {"own_facts": ["70%"]})
    facts = evidence.load_settings(ROOT, project, user_config=tmp_path / "none.json")["rules"]["own_facts"]
    assert "70%" not in facts


@pytest.mark.parametrize("bad", ["$0.01", [1], {"a": 1}])
def test_user_own_facts_must_be_a_list_of_strings(tmp_path, bad):
    project = tmp_path / "proj"
    (project / ".gates").mkdir(parents=True)
    user = write(tmp_path / "user.json", {"own_facts": bad})
    with pytest.raises(UsageError, match="own_facts"):
        evidence.load_settings(ROOT, project, user_config=user)


def test_user_own_fact_passes_the_gate_and_feeds_calculations(tmp_path, monkeypatch):
    from datetime import date
    from gates import checker
    project = tmp_path / "proj"
    (project / ".gates").mkdir(parents=True)
    user = write(tmp_path / "user.json", {"own_facts": ["$0.01"]})
    rules = evidence.load_settings(ROOT, project, user_config=user)["rules"]
    findings = list(checker.check_claims("We charge $0.01 per second.", rules, [], date(2026, 11, 4), []))
    assert not [f for f in findings if f.severity == "fail"]
    assert evidence.check_calc({"formula": "theirs / ours", "inputs": {
        "theirs": {"value": "0.02", "source": "claim"}, "ours": {"value": "$0.01", "source": "own"}}, "show": "2"},
        {"0.02"}, {checker.normalise_number(v) for v in rules["own_facts"]}) is None


# --- names with digits (user config) ------------------------------------------------

def test_user_names_stop_counting_as_numbers(tmp_path):
    from gates import checker
    project = tmp_path / "proj"
    (project / ".gates").mkdir(parents=True)
    user = write(tmp_path / "user.json", {"names": ["H3", "1080p"]})
    rules = evidence.load_settings(ROOT, project, user_config=user)["rules"]
    text = "We supply H3 at 1080p. h3 again. But 3 seconds, 1080 frames and H30 are numbers."
    flagged = [f.message for f in checker.check_claims(text, rules, [], None, [])]
    assert any("'3'" in m for m in flagged) and any("'1080'" in m for m in flagged)
    assert any("'30'" in m for m in flagged)
    assert len(flagged) == 3


@pytest.mark.parametrize("bad", ["H3", ["3"], ["$0.16"], ["two words1"], [""], ["x" * 41 + "1"], [3]])
def test_names_must_be_words_with_a_letter_and_a_digit(tmp_path, bad):
    project = tmp_path / "proj"
    (project / ".gates").mkdir(parents=True)
    user = write(tmp_path / "user.json", {"names": bad})
    with pytest.raises(UsageError, match="names"):
        evidence.load_settings(ROOT, project, user_config=user)


def test_project_cannot_add_names(tmp_path):
    project = tmp_path / "proj"
    write(project / ".gates" / "config.json", {"names": ["H3"]})
    rules = evidence.load_settings(ROOT, project, user_config=tmp_path / "none.json")["rules"]
    assert rules["names"] == []
    with pytest.raises(UsageError, match="unknown key"):
        from gates import checker
        checker.validate_style({"names": ["H3"]})
