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
