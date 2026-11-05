"""The style layer: truth rules stay fixed, a project's style.json shapes the rest."""
import json
from pathlib import Path

import pytest

from gates import checker, evidence
from gates.checker import UsageError

ROOT = Path(__file__).resolve().parents[1]
STYLES = ROOT / "gates" / "styles"
NEUTRAL = json.loads((ROOT / "gates" / "config.example.json").read_text())


def merged(style):
    return checker.merge_style(NEUTRAL, style, STYLES)


def project_with(tmp_path, style=None):
    proj = tmp_path / "proj"
    (proj / ".gates").mkdir(parents=True)
    if style is not None:
        (proj / ".gates" / "style.json").write_text(json.dumps(style))
    return proj


# --- the snapshot -------------------------------------------------------------------

def test_demo_rules_are_neutral_plus_example_style_plus_demo_facts():
    """demo/rules.json is what the demo always ran with. If the neutral rules,
    the example style or the demo's own facts change, this says so."""
    rules = merged({"preset": "example"})
    user = json.loads((ROOT / "demo" / "gate" / "user-config.json").read_text())
    rules = evidence.apply_user_facts(rules, user, "demo user config")
    snapshot = json.loads((ROOT / "demo" / "rules.json").read_text())
    strip = lambda d: {k: v for k, v in d.items() if k != "_comment"}
    assert strip(rules) == strip(snapshot)


# --- validation ---------------------------------------------------------------------

@pytest.mark.parametrize("key", ["own_facts", "claims", "cost_assertion_patterns", "claim_max_age_days", "hubspot"])
def test_style_cannot_set_truth_rules(key):
    with pytest.raises(UsageError, match="truth rule"):
        checker.validate_style({key: []})


@pytest.mark.parametrize("style,error", [
    ({"colour": "blue"}, "unknown key"),
    ({"preset": "loud"}, "unknown preset"),
    ({"registers": {"cold": {"tone": "warm"}}}, "may only set"),
    ({"registers": {"cold": {"words": [100, 50]}}}, "low <= high"),
    ({"registers": {"cold": {"words": [-1, 50]}}}, "low <= high"),
    ({"registers": {"cold": {"sign_off": 3}}}, "must be text"),
    ({"min_contractions": -1}, "whole number"),
    ({"max_spaced_hyphens": 1.5}, "whole number"),
    ({"banned_phrases": "synergy"}, "list of text"),
    ({"bold_allowed_patterns": ["("]}, "not a valid regex"),
    ({"bold_allowed_patterns": ["a" * 201]}, "longer than 200"),
    ([], "JSON object"),
])
def test_bad_styles_are_refused(style, error):
    with pytest.raises(UsageError, match=error):
        checker.validate_style(style)


def test_example_preset_is_a_valid_style():
    checker.load_preset("example", STYLES)


# --- merging ------------------------------------------------------------------------

def test_registers_merge_key_by_key():
    rules = merged({"registers": {"cold": {"sign_off": "Cheers,"}}})
    assert rules["registers"]["cold"] == {**NEUTRAL["registers"]["cold"], "sign_off": "Cheers,"}


def test_style_on_top_of_preset():
    rules = merged({"preset": "example", "registers": {"cold": {"words": [50, 120]}}, "min_contractions": 0})
    assert rules["registers"]["cold"]["words"] == [50, 120]
    assert rules["registers"]["cold"]["sign_off"] == "Thanks,"  # from the preset
    assert rules["min_contractions"] == 0


def test_banned_phrases_and_bold_patterns_are_only_added():
    rules = merged({"banned_phrases": ["move the needle", NEUTRAL["banned_phrases"][0]]})
    assert rules["banned_phrases"] == NEUTRAL["banned_phrases"] + ["move the needle"]
    rules = merged({"preset": "example", "bold_allowed_patterns": ["Acme"]})
    assert rules["bold_allowed_patterns"][-1] == "Acme" and len(rules["bold_allowed_patterns"]) == 3


def test_merge_does_not_change_the_base():
    before = json.dumps(NEUTRAL, sort_keys=True)
    merged({"preset": "example", "banned_phrases": ["x"]})
    assert json.dumps(NEUTRAL, sort_keys=True) == before


# --- defaults when a key is absent --------------------------------------------------

def test_no_hyphen_limit_unless_a_style_sets_one():
    text = "a - b - c"
    assert list(checker.check_dashes(text, NEUTRAL)) == []
    assert list(checker.check_dashes(text, merged({"max_spaced_hyphens": 1})))


def test_bold_is_free_without_patterns_and_none_with_an_empty_list():
    text = "This is **bold** text."
    assert list(checker.check_bold(text, NEUTRAL)) == []
    assert list(checker.check_bold(text, {"bold_allowed_patterns": []}))


# --- where a project's style comes from ---------------------------------------------

def test_project_without_style_keeps_the_example_style(tmp_path):
    rules, source = evidence.project_rules(ROOT, project_with(tmp_path))
    assert source == "example (no style.json)"
    assert rules["registers"]["cold"]["words"] == [80, 150]


@pytest.mark.parametrize("style,source", [
    ({}, "default"),
    ({"_comment": "neutral"}, "default"),
    ({"preset": "example"}, "example"),
    ({"min_contractions": 1}, "custom"),
])
def test_style_source(tmp_path, style, source):
    assert evidence.project_rules(ROOT, project_with(tmp_path, style))[1] == source


def test_project_style_errors_name_the_file(tmp_path):
    with pytest.raises(UsageError, match="style.json.*truth rule"):
        evidence.project_rules(ROOT, project_with(tmp_path, {"own_facts": ["70%"]}))


def test_style_source_is_in_settings(tmp_path):
    settings = evidence.load_settings(ROOT, project_with(tmp_path, {}), user_config=tmp_path / "none.json")
    assert settings["style_source"] == "default"


# --- checker --project: the CLI uses the gate's own rules for a project ---------------

def run_checker(*args):
    import os, subprocess, sys
    env = {**os.environ, "HOME": str(ROOT / "tests" / "no-such-home")}  # never the real user config
    return subprocess.run([sys.executable, "-m", "gates.checker", *args], cwd=ROOT, capture_output=True,
                          text=True, env=env)


def test_checker_project_uses_the_project_style(tmp_path):
    draft = tmp_path / "draft.txt"
    draft.write_text("Hi Dana,\n\nShort note about your audio pipeline. Worth a chat?\n\nBest,\nSam\n")
    neutral = project_with(tmp_path / "a", {})
    example = project_with(tmp_path / "b", {"preset": "example"})
    assert run_checker(str(draft), "--project", str(neutral), "--register", "cold").returncode == 1  # under 20 words
    out = run_checker(str(draft), "--project", str(example), "--register", "cold")
    assert out.returncode == 1 and "Thanks," in out.stdout  # the example sign-off


def test_checker_config_and_project_are_exclusive(tmp_path):
    out = run_checker("x.txt", "--config", "demo/rules.json", "--project", str(tmp_path))
    assert out.returncode == 2 and "not allowed with" in out.stderr


def test_checker_config_needs_a_register():
    out = run_checker("demo/gate/northwind-draft.txt", "--config", "demo/rules.json")
    assert out.returncode == 2 and "--register is needed" in out.stderr


def test_strict_mode_allows_the_checker_but_not_writes_into_gates():
    from hooks import session_check as sc
    # the real command names the installed plugin under ~/.claude/plugins, a protected folder
    command = ('PYTHONPATH="/Users/u/.claude/plugins/cache/outbound-gates/outbound-gates/0.6.0" '
               "python3 -m gates.checker /w/draft.txt --project /w")
    assert sc.bash_refusal(command, Path("/w")) == ""
    assert sc.bash_refusal(command + " > /w/.gates/style.json", Path("/w"))
    assert sc.bash_refusal(command + " && rm -rf /w/.gates", Path("/w"))
    assert sc.bash_refusal(command + "; python3 -c 'open(\".gates/style.json\",\"w\")'", Path("/w"))


def test_readme_style_table_lists_every_style_key():
    import re
    readme = (ROOT / "README.md").read_text()
    table = readme.split("| Style key |", 1)[1].split("\n\n", 1)[0]
    assert set(re.findall(r"^\| `(\w+)` \|", table, re.M)) == checker.STYLE_KEYS


# --- per-register hyphen limit and the follow-up register --------------------------

def test_register_hyphen_limit_wins_over_the_global_one():
    rules = merged({"max_spaced_hyphens": 0, "registers": {"follow_up": {"max_spaced_hyphens": 1}}})
    text = "Let me know - no worries."
    assert list(checker.check_dashes(text, rules, "cold"))
    assert list(checker.check_dashes(text, rules, "follow_up")) == []


@pytest.mark.parametrize("bad", [-1, True, 1.5])
def test_register_hyphen_limit_is_a_whole_number(bad):
    with pytest.raises(UsageError, match="max_spaced_hyphens"):
        checker.validate_style({"registers": {"cold": {"max_spaced_hyphens": bad}}})


@pytest.mark.parametrize("subject,register", [
    ("Re: pricing", "follow_up"), ("RE : pricing", "follow_up"), ("  re:pricing", "follow_up"),
    ("Regarding pricing", "cold"), ("Pricing, re: your page", "cold"), ("", "cold"),
])
def test_reply_subjects_get_the_follow_up_register(subject, register):
    from gates.evaluate import register_for
    assert register_for(subject, NEUTRAL, "cold") == register


def test_no_follow_up_register_keeps_the_default():
    from gates.evaluate import register_for
    assert register_for("Re: x", {"registers": {"cold": {}}}, "cold") == "cold"
