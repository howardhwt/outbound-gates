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
import re
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
    register: str | None = None

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


def register_for(subject: str, rules: dict, default: str) -> str:
    """A reply ("Re: ...") is a follow-up, so it gets the follow_up register when
    the rules have one. Anything else gets the project's register."""
    if re.match(r"\s*re\s*:", subject or "", re.I) and "follow_up" in rules.get("registers", {}):
        return "follow_up"
    return default


def evaluate(draft: Draft, crm: dict, settings: dict, *, gates_home: Path, today: date,
             crm_warnings: Sequence[str] = ()) -> Verdict:
    rules = settings["rules"]
    register = register_for(draft.subject, rules, settings["register"])
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
        register=register,
    )


def refusal_text(verdict: "Verdict", gates_home: Path, contact: str) -> str:
    """Why a draft was refused, with the commands that fix a missing source.
    The hook and the command line both print this."""
    from .evidence import calc_command, draft_dir, record_command

    fails = [f for f in verdict.findings if f.severity == "fail"]
    lines = [f"{len(fails)} problem(s) with this draft:"]
    lines += [f"- {f.rule}: {f.message}" for f in fails]
    if any(f.rule in ("claims", "claims_unverified") for f in fails):
        lines.append(
            "To source a number, record the exact sentence from the company's own site "
            "(replace URL, QUOTE and VALUE):"
        )
        lines.append(record_command(gates_home, contact))
        lines.append("For a number you worked out from sourced numbers (a saving, a ratio), record the "
                     "calculation; the gate recomputes it from the verified sources (unit constants only, "
                     "within 2%):")
        lines.append(calc_command(gates_home, contact))
        lines.append(f"Claims folder read: {draft_dir(gates_home, contact) / 'claims'}")
    warns = [f.message for f in verdict.findings if f.rule == "crm_coverage"]
    if warns:
        lines.append(crm_summary(warns))
    return "\n".join(lines)


def crm_summary(warnings: Sequence[str]) -> str:
    """One line about what the CRM read couldn't check. The full list goes to the log."""
    n = len(warnings)
    return (f"CRM: {n} check{'s' if n != 1 else ''} couldn't run for this contact "
            "(full list in .gates/log/gate.jsonl)")


def main(argv: list[str] | None = None) -> int:
    """Run the whole send gate on a draft file, as the hook would on a Gmail draft."""
    import argparse
    import sys

    from .checker import UsageError

    parser = argparse.ArgumentParser(description="Run the send gate on one draft: the same check the hook runs.")
    parser.add_argument("draft", help="the email body, as a text file")
    parser.add_argument("--to", required=True, help="the one recipient's address")
    parser.add_argument("--subject", default="", help="the subject line (default: none)")
    parser.add_argument("--project", default=".", help="the project folder with .gates/ (default: here)")
    parser.add_argument("--user-config", help="your config file (default: ~/.config/outbound-gates/config.json)")
    parser.add_argument("--today", help="the date to check against, YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)

    project = Path(args.project).expanduser().resolve()
    gates_home = project / ".gates"
    try:
        if not gates_home.is_dir():
            raise UsageError(f"no .gates folder in {project}: run setup there, or pass --project")
        try:
            text = Path(args.draft).read_text(encoding="utf-8")
        except OSError as err:
            raise UsageError(f"cannot read the draft: {err}") from None
        try:
            today = date.fromisoformat(args.today) if args.today else date.today()
        except ValueError:
            raise UsageError(f"--today must be YYYY-MM-DD, got {args.today!r}") from None
        settings = evidence.load_settings(evidence.PLUGIN_ROOT, project, user_config=args.user_config)
        draft = Draft(args.to.strip(), args.subject, (("body", text),))
        evidence.contact_parts(draft.recipient)
        crm, warnings = evidence.read_crm(settings, draft.recipient)
        verdict = evaluate(draft, crm, settings, gates_home=gates_home, today=today, crm_warnings=warnings)
    except (UsageError, evidence.EvidenceError, ValueError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(f"style: {settings['style_source']}; register: {verdict.register}")
    if not verdict.passed:
        print(refusal_text(verdict, gates_home, draft.recipient))
        print("BLOCKED")
        return 1
    for finding in verdict.findings:
        if finding.severity != "fail" and finding.rule != "crm_coverage":
            print(finding)
    if warnings:
        print(crm_summary(warnings))
        for warning in warnings:
            print(f"  - {warning}")
    print("PASS")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
