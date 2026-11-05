"""One verdict for one gated draft.

The Claude Code hook calls this now; an HTTP endpoint for Clay can call it
later. Every input is explicit, ``today`` included, so the same draft, CRM,
settings, claims and stored pages always give the same verdict, byte for byte.

    verdict = evaluate(Draft(recipient, subject, bodies), crm, settings,
                       gates_home=project / ".gates", today=date.today())
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Sequence

from . import evidence
from .checker import (
    FAIL,
    WARN,
    Finding,
    check,
    check_banned_phrases,
    check_bold,
    check_claims,
    check_cost_assertions,
    check_dashes,
    normalise_text,
    passed,
)
from .presend import presend_check

FORMAT_VERSION = 1


@dataclass(frozen=True)
class Draft:
    """What a gated draft would put in front of the recipient.

    ``bodies`` holds every version of the body the recipient could see, as
    (label, plain text) pairs, for example ("body", ...) and ("htmlBody", ...)
    with HTML already reduced to text.
    """
    recipient: str
    subject: str
    bodies: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Verdict:
    passed: bool
    findings: tuple[Finding, ...]
    inputs_sha256: str
    crm_path: str | None = None
    claims_used: int = 0
    verified_claims: int = 0

    def to_dict(self) -> dict:
        return {
            "v": FORMAT_VERSION,
            "pass": self.passed,
            "findings": [
                {"rule": f.rule, "severity": f.severity, "message": f.message}
                for f in self.findings
            ],
            "inputs_sha256": self.inputs_sha256,
            "crm_path": self.crm_path,
            "claims": self.claims_used,
            "verified_claims": self.verified_claims,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def _subject_findings(subject: str, rules: dict, claims: list, today: date, verified: list) -> list[Finding]:
    """The rules that make sense for a subject line, reported with a subject: prefix.

    Length, sign-off, closing line and rhythm are body rules. Claim entries are
    validated once, in the body check; here only the numbers are looked at.
    """
    text = normalise_text(subject)
    found: list[Finding] = []
    found += check_dashes(text, rules)
    found += check_banned_phrases(text, rules)
    found += check_bold(text, rules)
    found += check_cost_assertions(text, rules)
    found += [
        f for f in check_claims(text, rules, claims, today, verified)
        if f.message.startswith("the number")
    ]
    return [Finding(f.rule, f.severity, f"subject: {f.message}") for f in found]


def _fingerprint(
    draft: Draft, crm: dict, settings: dict, today: date, claims: list, gates_home: Path
) -> str:
    def plain(value):
        return str(value) if isinstance(value, Path) else value

    payload = {
        "draft": [draft.recipient, draft.subject, list(map(list, draft.bodies))],
        "crm": crm,
        "settings": {k: plain(v) for k, v in sorted(settings.items()) if k != "style_source"},
        "today": today.isoformat(),
        "claims": claims,
        "snapshots": evidence.snapshot_digests(claims, gates_home),
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _with_checked_maths(contact: str, rules: dict, claims: list, verified: list, gates_home: Path):
    """Recompute each stored calculation from the verified claims and own facts.

    A calculation that still holds becomes one more verified claim for its shown
    number, so the claims check treats it like a sourced number. One that no
    longer holds is a warning with the reason; its number then has no source and
    the claims check fails the draft if the number is used.
    """
    calcs = evidence.calcs_for(gates_home, contact)
    if not calcs:
        return [], claims, verified
    from .checker import normalise_number

    values = {normalise_number(str(claims[i].get("value", ""))) for i in verified}
    own = {normalise_number(v) for v in rules.get("own_facts", [])}
    claims, verified, findings = list(claims), list(verified), []
    for calc in calcs:
        reason = evidence.check_calc(calc, values, own)
        label = f"{calc.get('show')} = {calc.get('formula')}"
        if reason:
            findings.append(Finding("calc", WARN, f"calculation {label!r} doesn't hold: {reason}"))
            continue
        verified.append(len(claims))
        claims.append({"claim": f"calculation: {label}", "value": str(calc.get("show")),
                       "url": "calc:" + str(calc.get("formula")), "calc": calc})
    return findings, claims, verified


def evaluate(draft: Draft, crm: dict, settings: dict, *, gates_home: Path, today: date,
             crm_warnings: Sequence[str] = ()) -> Verdict:
    rules, register = settings["rules"], settings["register"]
    claims = evidence.claims_for(gates_home, draft.recipient)
    verified = [i for i, claim in enumerate(claims) if evidence.verify_claim(claim, gates_home)]
    sourced_claims = len(claims)
    calc_findings, claims, verified = _with_checked_maths(draft.recipient, rules, claims, verified, gates_home)

    findings: list[Finding] = list(calc_findings)
    if not draft.bodies:
        findings.append(Finding("body", FAIL, "the draft has no body"))
    for label, text in draft.bodies:
        body_findings = check(text, rules, register, claims, today, verified)
        if len(draft.bodies) > 1:
            body_findings = [Finding(f.rule, f.severity, f"{label}: {f.message}") for f in body_findings]
        findings += body_findings
    findings += _subject_findings(draft.subject, rules, claims, today, verified)

    domain, _ = evidence.contact_parts(draft.recipient)
    findings += presend_check(
        crm, domain, draft.recipient.strip().lower(), today,
        settings.get("sender_owner"), settings.get("max_unanswered"),
    )
    findings += [Finding("crm_coverage", WARN, w) for w in crm_warnings]
    crm_path = "hubspot" if settings.get("crm_source") == "hubspot" else settings.get("crm_path")
    return Verdict(
        passed=passed(findings),
        findings=tuple(findings),
        inputs_sha256=_fingerprint(draft, crm, settings, today, claims, gates_home),
        crm_path=str(crm_path) if crm_path else None,
        claims_used=sourced_claims,
        verified_claims=len(verified),
    )
