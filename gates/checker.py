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
import html
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
URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)+/\S*")
TYPOGRAPHY = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"'})

# Numbers that are not facts about anyone: clock times and calendar dates.
DEFAULT_NUMBER_IGNORE = [
    r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b",
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\b",
]


NAME_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)\S{1,40}$")


def name_patterns(names: Iterable[str]) -> list[str]:
    """Patterns that hide names with digits in them (a model "H3", "1080p") from
    the number check. Each name must contain a letter, so a bare number can't be
    listed as a name, and it matches only as a whole token."""
    return [rf"(?<![\w.$]){re.escape(n)}(?!\w)" for n in names]


class UsageError(ValueError):
    """A problem with the inputs, as opposed to a problem with the draft."""


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.rule}: {self.message}"


def _load_json(path: str | Path, what: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UsageError(f"{what} file not found: {path}") from None
    except json.JSONDecodeError as err:
        raise UsageError(f"{what} file is not valid JSON: {path} ({err})") from None


def load_config(path: str | Path) -> dict:
    config = _load_json(path, "config")
    if not isinstance(config, dict) or "registers" not in config:
        raise UsageError(f"config file has no 'registers' section: {path}")
    return config


# --- style -------------------------------------------------------------------
#
# A style is the house-style part of the rules. It can loosen or tighten length,
# sign-off, the fixed closing line, contractions and hyphens, and add banned
# phrases and bold patterns. It can never touch the truth rules (claims, own
# facts, cost assertions, claim age), so those keys are refused in a style.

STYLE_KEYS = {"registers", "max_spaced_hyphens", "min_contractions", "banned_phrases",
              "bold_allowed_patterns"}
REGISTER_KEYS = {"words", "sign_off", "required_close", "max_spaced_hyphens"}
PRESETS = {"example"}
MAX_PATTERN_LENGTH = 200


def validate_style(style, where: str = "style") -> dict:
    """A style dict, checked. Raises UsageError naming the bad key and where it belongs."""
    if not isinstance(style, dict):
        raise UsageError(f"{where} must be a JSON object")
    for key in style:
        if key in ("_comment", "preset"):
            continue
        if key in ("own_facts", "claims", "cost_assertion_patterns", "claim_max_age_days", "hubspot"):
            raise UsageError(f"{where} can't set {key!r}: it is a truth rule, not style"
                             + (" (own facts go in ~/.config/outbound-gates/config.json)" if key == "own_facts" else ""))
        if key not in STYLE_KEYS:
            raise UsageError(f"{where} has an unknown key {key!r}; style keys are {sorted(STYLE_KEYS)}")
    preset = style.get("preset")
    if preset is not None and preset not in PRESETS:
        raise UsageError(f"{where} names an unknown preset {preset!r}; presets are {sorted(PRESETS)}")
    for name, register in (style.get("registers") or {}).items():
        if not isinstance(register, dict) or set(register) - REGISTER_KEYS:
            raise UsageError(f"{where}: register {name!r} may only set {sorted(REGISTER_KEYS)}")
        words = register.get("words")
        if words is not None and not (isinstance(words, list) and len(words) == 2
                                      and all(isinstance(w, int) and w >= 0 for w in words) and words[0] <= words[1]):
            raise UsageError(f"{where}: register {name!r} words must be [low, high] with 0 <= low <= high")
        limit = register.get("max_spaced_hyphens")
        if limit is not None and not (isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0):
            raise UsageError(f"{where}: register {name!r} max_spaced_hyphens must be a whole number, 0 or more")
        for text_key in ("sign_off", "required_close"):
            if text_key in register and not isinstance(register[text_key], str):
                raise UsageError(f"{where}: register {name!r} {text_key} must be text")
    for key in ("max_spaced_hyphens", "min_contractions"):
        if key in style and not (isinstance(style[key], int) and style[key] >= 0):
            raise UsageError(f"{where}: {key} must be a whole number, 0 or more")
    for key in ("banned_phrases", "bold_allowed_patterns"):
        if key in style and not (isinstance(style[key], list) and all(isinstance(v, str) for v in style[key])):
            raise UsageError(f"{where}: {key} must be a list of text")
    for pattern in style.get("bold_allowed_patterns", []):
        if len(pattern) > MAX_PATTERN_LENGTH:
            raise UsageError(f"{where}: a bold pattern is longer than {MAX_PATTERN_LENGTH} characters")
        try:
            re.compile(pattern)
        except re.error as err:
            raise UsageError(f"{where}: bold pattern {pattern!r} is not a valid regex ({err})") from None
    return style


def load_preset(name: str, styles_dir: Path) -> dict:
    preset = validate_style(_load_json(styles_dir / f"{name}.json", f"{name} style"), f"{name} style")
    if "preset" in preset:
        raise UsageError(f"the {name} style can't name another preset")
    return preset


def merge_style(base: dict, style: dict, styles_dir: Path, where: str = "style") -> dict:
    """The rules with a style applied. Registers merge key by key; banned phrases
    and bold patterns are only added; contractions and hyphen limits are replaced.
    A "preset" is applied first, then the style's own keys on top."""
    validate_style(style, where)
    rules = json.loads(json.dumps(base))
    layers = []
    if style.get("preset"):
        layers.append(load_preset(style["preset"], Path(styles_dir)))
    layers.append(style)
    for layer in layers:
        for name, register in (layer.get("registers") or {}).items():
            rules.setdefault("registers", {}).setdefault(name, {}).update(register)
        for key in ("max_spaced_hyphens", "min_contractions"):
            if key in layer:
                rules[key] = layer[key]
        for key in ("banned_phrases", "bold_allowed_patterns"):
            if key in layer:
                current = list(rules.get(key, []))
                rules[key] = current + [v for v in layer[key] if v not in current]
    return rules


def load_claims(path: str | Path) -> list[dict]:
    claims = _load_json(path, "claims")
    if not isinstance(claims, list):
        raise UsageError(f"claims file must hold a list of claims: {path}")
    return claims


def normalise_text(text: str) -> str:
    """Mail clients and language models produce curly quotes and Windows line
    endings. The rules compare against plain text, so both are flattened first."""
    return text.replace("\r\n", "\n").replace("\r", "\n").translate(TYPOGRAPHY)


def _strip_markup(text: str) -> str:
    return text.replace("**", "")


def normalise_number(token: str) -> str:
    """Compare numbers by value: "$1,200" and "1200" are the same number.
    A percent sign is kept, because 67 and 67% are different claims."""
    return token.replace(",", "").replace("$", "").rstrip(".")


_normalise_number = normalise_number  # older name, kept for existing callers


def extract_numbers(
    text: str, ignore_patterns: Iterable[str] = DEFAULT_NUMBER_IGNORE
) -> list[tuple[str, str]]:
    """Every number in the text as (token as written, normalised value).

    The draft check and the source check both read numbers through this one
    function, so "$1,200" in a draft and "1,200" in a quoted page always
    compare as the same number. URLs, clock times and dates are skipped.
    """
    body = URL_RE.sub(" ", _strip_markup(text))
    for pattern in ignore_patterns:
        body = re.sub(pattern, " ", body, flags=re.IGNORECASE)
    numbers = []
    for token in NUMBER_RE.findall(body):
        value = normalise_number(token)
        if any(ch.isdigit() for ch in value):
            numbers.append((token, value))
    return numbers


_HIDDEN_BLOCK_RE = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>", re.I | re.S)
_BREAK_RE = re.compile(r"<br\s*/?>|</(?:p|div|li|tr|h[1-6]|section|article|td|th)\s*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def html_to_text(markup: str) -> str:
    """Reduce HTML to the text a reader sees.

    Script, style and template blocks are dropped entirely, so text hidden in
    page code can never count as something the page says.
    """
    text = _COMMENT_RE.sub(" ", markup or "")
    text = _HIDDEN_BLOCK_RE.sub(" ", text)
    text = _BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text).replace("\u00a0", " ")
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def check_dashes(text: str, config: dict, register: str | None = None) -> Iterable[Finding]:
    if "—" in text or "–" in text:
        yield Finding("dashes", FAIL, "contains an em dash or en dash")
    limit = config.get("max_spaced_hyphens")  # absent: no limit (style, not truth)
    if register is not None:  # a register's own limit wins
        limit = (config.get("registers", {}).get(register) or {}).get("max_spaced_hyphens", limit)
    count = text.count(" - ")
    if limit is not None and count > limit:
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
    if "bold_allowed_patterns" not in config:
        return  # absent: no bold rule. An empty list still means "no bold at all".
    patterns = [re.compile(p) for p in config["bold_allowed_patterns"]]
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
    text: str,
    config: dict,
    claims: list[dict] | None,
    today: date | None = None,
    verified: Iterable[int] | None = None,
) -> Iterable[Finding]:
    """Every number in the draft needs a sourced claim behind it.

    ``verified`` is None for the plain checker: a claim with a value and a URL
    counts as sourced, and its ``derived`` values are allowed. The send gate
    passes the indices of the claims whose stored evidence verified. Then only
    those claims count, ``derived`` values are ignored, and an unverified claim
    blocks only a draft that uses its number.
    """
    if claims is None:
        return
    evidence_mode = verified is not None
    verified_ids = set(verified or ())
    today = today or date.today()
    max_age = config.get("claim_max_age_days")
    sourced: set[str] = set()
    unverified: dict[str, str] = {}
    for index, claim in enumerate(claims):
        value = normalise_number(str(claim.get("value", "")))
        name = claim.get("claim", value or "?")
        if not value:
            yield Finding("claims", FAIL, f"claim {name!r} has no value")
            continue
        if not claim.get("url"):
            yield Finding("claims", FAIL, f"claim {name!r} has no source URL")
            continue
        if evidence_mode and index not in verified_ids:
            unverified.setdefault(value, name)
            continue
        sourced.add(value)
        checked = claim.get("checked")
        if max_age and checked:
            try:
                age = (today - datetime.strptime(checked, "%Y-%m-%d").date()).days
            except ValueError:
                yield Finding(
                    "claims",
                    FAIL,
                    f"claim {name!r} has a checked date that is not YYYY-MM-DD: {checked!r}",
                )
                continue
            if age > max_age:
                yield Finding("claims", WARN, f"claim {name!r} was checked {age} days ago")
    own_facts = {normalise_number(v) for v in config.get("own_facts", [])}
    derived = (
        set()
        if evidence_mode
        else {normalise_number(v) for c in claims for v in c.get("derived", [])}
    )
    allowed = sourced | own_facts | derived
    seen: set[str] = set()
    ignore = list(config.get("number_ignore_patterns", DEFAULT_NUMBER_IGNORE)) + name_patterns(config.get("names", []))
    for token, value in extract_numbers(text, ignore):
        if value in seen:
            continue
        seen.add(value)
        if value in allowed:
            continue
        if value in unverified:
            yield Finding(
                "claims_unverified",
                FAIL,
                f"the number {token!r} rests on claim {unverified[value]!r}, "
                "which has no verified stored source",
            )
        else:
            yield Finding("claims", FAIL, f"the number {token!r} has no sourced claim")
    for value, name in unverified.items():
        if value not in seen and value not in allowed:
            yield Finding(
                "claims_unverified",
                WARN,
                f"claim {name!r} has no verified stored source (its number is not in this draft)",
            )


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
    verified: Iterable[int] | None = None,
) -> list[Finding]:
    if register not in config["registers"]:
        known = ", ".join(sorted(config["registers"]))
        raise UsageError(f"unknown register {register!r}. The config defines: {known}")
    text = normalise_text(text)
    findings: list[Finding] = []
    findings += check_dashes(text, config, register)
    findings += check_length(text, config, register)
    findings += check_sign_off(text, config, register)
    findings += check_required_close(text, config, register)
    findings += check_banned_phrases(text, config)
    findings += check_bold(text, config)
    findings += check_cost_assertions(text, config)
    findings += check_claims(text, config, claims, today, verified)
    findings += check_rhythm(text, config)
    return findings


def passed(findings: Iterable[Finding]) -> bool:
    return not any(f.severity == FAIL for f in findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check an outbound email draft.")
    parser.add_argument("draft", help="path to the draft text file")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", help="path to a full rules JSON (for example demo/rules.json)")
    source.add_argument("--project", help="a project folder: the same rules the gate uses there "
                        "(plugin truth rules, the project's .gates/style.json, your own facts)")
    parser.add_argument("--register", help="register name (default: the project's register; needed with --config)")
    parser.add_argument("--claims", help="path to the claims JSON for this draft")
    parser.add_argument(
        "--today",
        help="date to measure claim age from, as YYYY-MM-DD (default: the real date)",
    )
    args = parser.parse_args(argv)

    try:
        try:
            text = Path(args.draft).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise UsageError(f"draft file not found: {args.draft}") from None
        if args.project:
            from .evidence import load_settings  # evidence imports this module, so import late

            settings = load_settings(Path(__file__).resolve().parents[1], Path(args.project))
            config, register = settings["rules"], args.register or settings["register"]
        else:
            if not args.register:
                raise UsageError("--register is needed with --config")
            config, register = load_config(args.config), args.register
        claims = load_claims(args.claims) if args.claims else None
        try:
            today = (
                datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else None
            )
        except ValueError:
            raise UsageError(f"--today must be YYYY-MM-DD, got {args.today!r}") from None
        findings = check(text, config, register, claims, today)
    except UsageError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding)
    if passed(findings):
        print("PASS")
        return 0
    print("BLOCKED")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
