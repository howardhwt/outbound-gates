from datetime import date
from pathlib import Path

import pytest

from gates.checker import FAIL, WARN, check, load_claims, load_config, main, passed

ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_config(ROOT / "gates" / "config.example.json")
CLAIMS = load_claims(ROOT / "examples" / "claims.json")
GOOD = (ROOT / "examples" / "draft_pass.txt").read_text(encoding="utf-8")
BAD = (ROOT / "examples" / "draft_fail.txt").read_text(encoding="utf-8")
TODAY = date(2026, 10, 2)


def rules(findings, severity=FAIL):
    return {f.rule for f in findings if f.severity == severity}


def test_good_draft_passes():
    findings = check(GOOD, CONFIG, "cold", CLAIMS, today=TODAY)
    assert passed(findings), [str(f) for f in findings]


def test_bad_draft_is_blocked_for_each_reason():
    found = rules(check(BAD, CONFIG, "cold", CLAIMS, today=TODAY))
    assert {"dashes", "length", "required_close", "banned_phrase", "bold",
            "cost_assertion", "claims"} <= found


def test_em_dash_fails():
    text = GOOD.replace("billed by the second.", "billed by the second — always.")
    assert "dashes" in rules(check(text, CONFIG, "cold", CLAIMS, today=TODAY))


def test_spaced_hyphen_over_limit_fails():
    text = GOOD.replace("billed by the second.", "billed by the second - always.")
    assert "dashes" in rules(check(text, CONFIG, "cold", CLAIMS, today=TODAY))


def test_number_without_claim_fails():
    text = GOOD.replace("speaker labels included", "speaker labels in 40 languages")
    findings = check(text, CONFIG, "cold", CLAIMS, today=TODAY)
    assert any("'40'" in f.message for f in findings if f.rule == "claims")


def test_claim_without_url_fails():
    claims = [dict(CLAIMS[0], url="")]
    assert "claims" in rules(check(GOOD, CONFIG, "cold", claims, today=TODAY))


def test_stale_claim_warns_but_does_not_block():
    findings = check(GOOD, CONFIG, "cold", CLAIMS, today=date(2026, 12, 1))
    assert "claims" in rules(findings, WARN)
    assert passed(findings)


def test_asking_about_cost_is_allowed():
    text = GOOD.replace(
        "Is model supply yours,", "What do you pay per minute today? Is model supply yours,"
    )
    assert "cost_assertion" not in rules(check(text, CONFIG, "cold", CLAIMS, today=TODAY))


def test_asserting_cost_fails():
    text = GOOD.replace("billed by the second.", "and you pay that on every call.")
    assert "cost_assertion" in rules(check(text, CONFIG, "cold", CLAIMS, today=TODAY))


def test_bold_outside_allow_list_fails():
    text = GOOD.replace("word-level timestamps", "**word-level timestamps**")
    assert "bold" in rules(check(text, CONFIG, "cold", CLAIMS, today=TODAY))


def test_follow_up_has_its_own_length_and_sign_off():
    text = (
        "Hi Dana,\n\nA short follow-up with one change since my last note: "
        "we've added streaming, and it's billed at the same rate as file uploads. "
        "I'd rather send you a key than describe it, so tell me where it should go "
        "and I'll set one up today.\n\nThanks,\nSam"
    )
    assert passed(check(text, CONFIG, "follow_up", [], today=TODAY))


def test_unknown_register_raises():
    with pytest.raises(ValueError):
        check(GOOD, CONFIG, "newsletter")


def test_cli_exit_codes(capsys):
    base = ["--config", str(ROOT / "gates" / "config.example.json"), "--register", "cold"]
    assert main([str(ROOT / "examples" / "draft_fail.txt"), *base]) == 1
    assert "BLOCKED" in capsys.readouterr().out


# --- send gate helpers and evidence mode (eng D4, D6, D14; CEO D15) ---

from gates.checker import extract_numbers, html_to_text, normalise_number


def test_draft_and_quote_numbers_normalise_the_same_way():
    assert normalise_number("$1,200") == normalise_number("1,200") == "1200"
    assert [v for _, v in extract_numbers("costs $1,200 a year")] == ["1200"]
    assert [v for _, v in extract_numbers("1,200 per year")] == ["1200"]


def test_extract_numbers_keeps_percent_and_never_matches_substrings():
    values = [v for _, v in extract_numbers("40% of 1,400 minutes")]
    assert values == ["40%", "1400"]
    assert "4" not in values and "40" not in values


def test_extract_numbers_skips_urls_times_and_dates():
    assert extract_numbers("see https://acme.example/plan-2 at 3pm on Jan 5") == []


def test_html_to_text_drops_hidden_page_code():
    page = (
        "<p>Transcription: <b>$0.006</b>&nbsp;per minute</p>"
        "<script>var promo = '40% off';</script><style>.x{}</style>"
        "<!-- 99% hidden -->"
    )
    text = html_to_text(page)
    assert "$0.006 per minute" in text
    assert "40%" not in text and "99%" not in text


def test_evidence_mode_counts_only_verified_claims():
    claims = [dict(CLAIMS[0])]
    unverified = check(GOOD, CONFIG, "cold", claims, today=TODAY, verified=[])
    assert "claims_unverified" in rules(unverified)
    assert passed(check(GOOD.replace("0.4 cents", "a fraction of a cent")
                        .replace("roughly 67% gross margin", "a healthy margin"),
                        CONFIG, "cold", claims, today=TODAY, verified=[0]))


def test_evidence_mode_ignores_derived_values():
    # examples/claims.json derives 0.4 and 67% from the $0.006 claim
    findings = check(GOOD, CONFIG, "cold", CLAIMS, today=TODAY, verified=[0])
    blocked = {f.message for f in findings if f.rule == "claims" and f.severity == FAIL}
    assert any("'0.4'" in m for m in blocked) and any("'67%'" in m for m in blocked)


def test_unverified_claim_not_used_in_draft_only_warns():
    other = {"claim": "Docs say 90 minutes", "value": "90", "url": "https://x.example/d",
             "checked": "2026-10-01"}
    text = GOOD.replace("0.4 cents", "a fraction of a cent").replace(
        "roughly 67% gross margin", "a healthy margin")
    findings = check(text, CONFIG, "cold", [CLAIMS[0], other], today=TODAY, verified=[0])
    assert passed(findings), [str(f) for f in findings]
    assert "claims_unverified" in rules(findings, WARN)
