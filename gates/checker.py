"""Gate checker for outbound email drafts.

A draft written by a person or a language model is plain text. This module
runs a fixed set of rules over that text and returns findings. A finding
with severity "fail" blocks the draft. A finding with severity "warn" is
shown to the reviewer and does not block.

The rules are deliberately mechanical. Anything that needs judgment belongs
in the review step, not here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

FAIL = "fail"
WARN = "warn"

NUMBER_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
CONTRACTION_RE = re.compile(r"\b\w+'(?:s|t|re|ve|ll|d|m)\b", re.IGNORECASE)
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.rule}: {self.message}"


def load_config(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_claims(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _strip_markup(text: str) -> str:
    return text.replace("**", "")


def _normalise_number(token: str) -> str:
    return token.replace(",", "").rstrip(".")


def check_dashes(text: str, config: dict) -> Iterable[Finding]:
    if "—" in text or "–" in text:
        yield Finding("dashes", FAIL, "contains an em dash or en dash")
    limit = config.get("max_spaced_hyphens", 1)
    count = text.count(" - ")
    if count > limit:
        yield Finding(
            "dashes", FAIL, f"{count} spaced hyphens, the limit is {limit}"
        )


def check_length(text: str, config: dict, register: str) -> Iterable[Finding]:
    bounds = config["registers"][register].get("words")
    if not bounds:
        return
    low, high = bounds
    words = len(_strip_markup(text).split())
    if not low <= words <= high:
        yield Finding(
            "length", FAIL, f"{words} words, {register} allows {low} to {high}"
        )


def check_sign_off(text: str, config: dict, register: str) -> Iterable[Finding]:
    expected = config["registers"][register].get("sign_off")
    if expected and not text.rstrip().endswith(expected):
        yield Finding("sign_off", FAIL, f"does not end with {expected!r}")


def check_required_close(
    text: str, config: dict, register: str
) -> Iterable[Finding]:
    close = config["registers"][register].get("required_close")
    if close and close not in text:
        yield Finding("required_close", FAIL, "the fixed closing line is missing")


def check_banned_phrases(text: str, config: dict) -> Iterable[Finding]:
    lowered = text.lower()
    for phrase in config.get("banned_phrases", []):
        if phrase.lower() in lowered:
            yield Finding("banned_phrase", FAIL, f"contains {phrase!r}")


def check_bold(text: str, config: dict) -> Iterable[Finding]:
    patterns = [re.compile(p) for p in config.get("bold_allowed_patterns", [])]
    for span in BOLD_RE.findall(text):
        if not any(p.fullmatch(span) for p in patterns):
            yield Finding("bold", FAIL, f"bold text {span!r} is not on the allow list")


def check_cost_assertions(text: str, config: dict) -> Iterable[Finding]:
    """Reject sentences that state what the reader pays.

    A stranger can verify what a company publishes. They cannot know what it
    pays. A draft may quote a listed price and may ask about cost. It may not
    assert cost.
    """
    for pattern in config.get("cost_assertion_patterns", []):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            sentence_end = text.find("?", match.start())
            next_stop = min(
                (i for i in (text.find(".", match.start()), text.find("!", match.start())) if i != -1),
                default=len(text),
            )
            is_question = sentence_end != -1 and sentence_end < next_stop
            if not is_question:
                yield Finding(
                    "cost_assertion",
                    FAIL,
                    f"asserts what the reader pays: {match.group(0)!r}",
                )


def check_claims(
    text: str, config: dict, claims: list[dict] | None, today: date | None = None
) -> Iterable[Finding]:
    """Every number in the draft needs a sourced claim behind it."""
    if claims is None:
        return
    today = today or date.today()
    max_age = config.get("claim_max_age_days")
    sourced: set[str] = set()
    for claim in claims:
        value = _normalise_number(str(claim.get("value", "")))
        if not claim.get("url"):
            yield Finding(
                "claims", FAIL, f"claim {claim.get('claim', value)!r} has no source URL"
            )
            continue
        sourced.add(value)
        checked = claim.get("checked")
        if max_age and checked:
            age = (today - datetime.strptime(checked, "%Y-%m-%d").date()).days
            if age > max_age:
                yield Finding(
                    "claims",
                    WARN,
                    f"claim {claim.get('claim', value)!r} was checked {age} days ago",
                )
    own_facts = {_normalise_number(v) for v in config.get("own_facts", [])}
    derived = {_normalise_number(v) for c in claims for v in c.get("derived", [])}
    allowed = sourced | own_facts | derived
    seen: set[str] = set()
    for token in NUMBER_RE.findall(_strip_markup(text)):
        value = _normalise_number(token)
        if value in seen or not any(ch.isdigit() for ch in value):
            continue
        seen.add(value)
        if value not in allowed:
            yield Finding("claims", FAIL, f"the number {token!r} has no sourced claim")


def check_rhythm(text: str, config: dict) -> Iterable[Finding]:
    body = _strip_markup(text)
    if len(CONTRACTION_RE.findall(body)) < config.get("min_contractions", 0):
        yield Finding("rhythm", WARN, "few contractions, the draft may read stiffly")
    lengths = [len(s.split()) for s in SENTENCE_RE.findall(body)]
    for a, b, c in zip(lengths, lengths[1:], lengths[2:]):
        if max(a, b, c) - min(a, b, c) <= 2 and min(a, b, c) > 3:
            yield Finding(
                "rhythm", WARN, "three sentences in a row have almost the same length"
            )
            break


def check(
    text: str,
    config: dict,
    register: str,
    claims: list[dict] | None = None,
    today: date | None = None,
) -> list[Finding]:
    if register not in config["registers"]:
        raise ValueError(f"unknown register {register!r}")
    findings: list[Finding] = []
    findings += check_dashes(text, config)
    findings += check_length(text, config, register)
    findings += check_sign_off(text, config, register)
    findings += check_required_close(text, config, register)
    findings += check_banned_phrases(text, config)
    findings += check_bold(text, config)
    findings += check_cost_assertions(text, config)
    findings += check_claims(text, config, claims, today)
    findings += check_rhythm(text, config)
    return findings


def passed(findings: Iterable[Finding]) -> bool:
    return not any(f.severity == FAIL for f in findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check an outbound email draft.")
    parser.add_argument("draft", help="path to the draft text file")
    parser.add_argument("--config", required=True, help="path to the gate config JSON")
    parser.add_argument("--register", required=True, help="register name from the config")
    parser.add_argument("--claims", help="path to the claims JSON for this draft")
    args = parser.parse_args(argv)

    text = Path(args.draft).read_text(encoding="utf-8")
    config = load_config(args.config)
    claims = load_claims(args.claims) if args.claims else None
    findings = check(text, config, args.register, claims)
    for finding in findings:
        print(finding)
    if passed(findings):
        print("PASS")
        return 0
    print("BLOCKED")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
