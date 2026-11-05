"""Checked maths: gates/calc.py, the calc command, and the gate recomputing calculations."""
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from gates import calc, evidence
from gates.evaluate import Draft, evaluate

ROOT = Path(__file__).resolve().parents[1]
CONTACT = "dana@northwind-audio.example"
QUOTE = "Transcription is billed at $0.006 per audio minute"
DRAFT_TEXT = (ROOT / "demo" / "gate" / "northwind-draft.txt").read_text(encoding="utf-8")
SAVING = "(theirs - ours) / theirs * 100"  # (0.006 - 0.002) / 0.006 * 100 = 66.67


def claim_input(value="0.006"):
    return {"value": value, "source": "claim"}


def own_input(value="0.002"):
    return {"value": value, "source": "own"}


# --- the calculator --------------------------------------------------------------

@pytest.mark.parametrize("show", ["67%", "66.7%", "66%", "67.5%", "$66.67"])
def test_shown_number_within_two_percent_passes(show):
    assert calc.check(SAVING, {"theirs": claim_input(), "ours": own_input()}, show) == pytest.approx(66.6667, 1e-3)


@pytest.mark.parametrize("show", ["70%", "60%", "65%"])
def test_shown_number_outside_two_percent_fails(show):
    with pytest.raises(calc.CalcError, match="more than 2% apart"):
        calc.check(SAVING, {"theirs": claim_input(), "ours": own_input()}, show)


def test_unit_constants_convert_units():
    # their per-minute price vs our per-second price: 0.006 / (0.0001 * 60) = 1.0
    assert calc.check("theirs / (ours * 60)", {"theirs": claim_input(), "ours": own_input("0.0001")}, "1") == 1.0


@pytest.mark.parametrize("formula,error", [
    ("theirs * 0 + 70", "constants [0, 70] aren't allowed"),
    ("theirs * 3", "constants [3] aren't allowed"),
    ("theirs ** 2", "only + - * / are allowed"),
    ("__import__('os').system('x')", "not allowed"),
    ("theirs.real", "not allowed"),
    ("abs(theirs)", "not allowed"),
    ("theirs if ours else 1", "not allowed"),
    ("theirs +", "not valid arithmetic"),
    ("'70'", "only numbers may appear"),
])
def test_unsafe_or_made_up_formulas_are_refused(formula, error):
    with pytest.raises(calc.CalcError, match=re.escape(error)):
        calc.check(formula, {"theirs": claim_input(), "ours": own_input()}, "70")


def test_every_input_must_be_used():
    with pytest.raises(calc.CalcError, match="unused: ours"):
        calc.check("theirs * 100", {"theirs": claim_input(), "ours": own_input()}, "0.6")


def test_names_need_inputs():
    with pytest.raises(calc.CalcError, match="no input: other"):
        calc.check("theirs - other", {"theirs": claim_input()}, "0")


def test_own_facts_alone_cannot_prove_a_number():
    with pytest.raises(calc.CalcError, match="at least one input must be a sourced claim"):
        calc.check("ours * 100", {"ours": own_input()}, "0.2")


def test_divide_by_zero():
    with pytest.raises(calc.CalcError, match="divides by zero"):
        calc.check("theirs / (ours - ours)", {"theirs": claim_input(), "ours": own_input()}, "1")


def test_zero_result_needs_zero_shown():
    assert calc.within_margin(0, 0) and not calc.within_margin(0.01, 0)


# --- recording and recomputing ---------------------------------------------------------

@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".gates").mkdir(parents=True)
    shutil.copytree(ROOT / "demo" / "pages", proj / "demo" / "pages")
    shutil.copy(ROOT / "demo" / "gate_crm.json", proj / "demo" / "gate_crm.json")
    shutil.copy(ROOT / "demo" / "gate" / "config.json", proj / ".gates" / "config.json")
    return proj


def settings_for(project):
    return evidence.load_settings(ROOT, project)


def record_price(project):
    settings = settings_for(project)
    evidence.record_claim(project / ".gates", CONTACT, "https://northwind-audio.example/pricing", QUOTE, "$0.006",
                          settings=settings, crm=evidence.load_crm(settings))


def record_saving(project, show="67%"):
    inputs = evidence.parse_calc_inputs(["theirs=0.006", "ours=own:$0.002"])
    return evidence.record_calc(project / ".gates", CONTACT, SAVING, inputs, show, settings=settings_for(project))


def test_parse_calc_inputs():
    assert evidence.parse_calc_inputs(["a=0.006", "b=own:$0.002"]) == {
        "a": {"value": "0.006", "source": "claim"}, "b": {"value": "$0.002", "source": "own"}}
    for bad in (["a"], ["1a=2"], ["a="], ["a=1", "a=2"]):
        with pytest.raises(evidence.UsageError):
            evidence.parse_calc_inputs(bad)


def test_calc_needs_a_verified_source_first(project):
    with pytest.raises(evidence.EvidenceError, match="no verified stored source"):
        record_saving(project)


def test_calc_needs_a_real_own_fact(project):
    record_price(project)
    inputs = evidence.parse_calc_inputs(["theirs=0.006", "ours=own:0.001"])
    with pytest.raises(evidence.EvidenceError, match="not one of your own facts"):
        evidence.record_calc(project / ".gates", CONTACT, SAVING, inputs, "83%", settings=settings_for(project))


def test_calc_is_stored_next_to_the_claims(project):
    record_price(project)
    path = record_saving(project)
    assert path.parent.name == "calcs"
    stored = json.loads(path.read_text())
    assert stored["formula"] == SAVING and stored["show"] == "67%"
    assert evidence.calcs_for(project / ".gates", CONTACT) == [stored]


def gate(project, text):
    return evaluate(Draft(CONTACT, "Northwind supply", (("body", text),)), evidence.load_crm(settings_for(project)),
                    settings_for(project), gates_home=project / ".gates", today=date(2026, 11, 4))


SAVING_DRAFT = DRAFT_TEXT.replace("per audio minute,", "per audio minute, about 67% less,", 1)


def claim_failures(verdict):
    return [f for f in verdict.findings if f.rule in ("claims", "claims_unverified") and f.severity == "fail"]


def test_gate_refuses_a_worked_out_number_without_a_calc(project):
    record_price(project)
    assert any("'67%'" in f.message for f in claim_failures(gate(project, SAVING_DRAFT)))


def test_gate_accepts_a_recorded_calculation(project):
    record_price(project)
    record_saving(project)
    verdict = gate(project, SAVING_DRAFT)
    assert claim_failures(verdict) == []
    assert verdict.claims_used == 1  # the calculation isn't counted as a sourced claim


def test_calc_stops_holding_when_its_source_stops_verifying(project):
    record_price(project)
    record_saving(project)
    claim = evidence.claims_for(project / ".gates", CONTACT)[0]
    page = project / ".gates" / "evidence" / f"{claim['evidence_sha256']}.txt"
    if not page.exists():
        page = next((project / ".gates" / "evidence").rglob(f"{claim['evidence_sha256']}*"))
    page.write_text("edited page")
    verdict = gate(project, SAVING_DRAFT)
    assert any(f.rule == "calc" and "doesn't hold" in f.message for f in verdict.findings)
    assert claim_failures(verdict)  # 67% no longer has a source


def test_calc_command(project):
    record_price(project)
    cmd = [sys.executable, "-m", "gates.evidence", "calc", "--gates-home", str(project / ".gates"),
           "--contact", CONTACT, "--formula", SAVING, "--input", "theirs=0.006", "--input", "ours=own:$0.002",
           "--show", "67%"]
    ok = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(project)})
    assert ok.returncode == 0 and "recorded:" in ok.stdout, ok.stderr
    bad = subprocess.run(cmd[:-1] + ["70%"], cwd=ROOT, capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(project)})
    assert bad.returncode == 1 and "more than 2% apart" in bad.stderr


def test_strict_mode_allows_the_calc_command(tmp_path, monkeypatch):
    from hooks import session_check as sc
    command = ("PYTHONPATH=/p python3 -m gates.evidence calc --gates-home /x/.gates --contact a@b.c "
               "--formula 'a * 100' --input a=0.5 --show 50%")
    assert sc.bash_refusal(command, tmp_path) == ""
    assert sc.bash_refusal(command + " > /x/.gates/fake.json", tmp_path)
