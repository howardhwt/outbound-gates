"""Pre-send check: block a draft on what the CRM already knows.

The gate checker reads the draft. This module reads the account. It answers
four questions before a draft is handed to a person to send:

1. Is the account already dead?
2. Has this person been contacted?
3. Was a promise made, or has someone replied?
4. Who else at the company has been contacted?

The check works on a plain dictionary, described in docs/crm-adapter.md.
Anything that can produce that shape from a real CRM can use this module.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from .checker import FAIL, WARN, Finding, UsageError, _load_json, passed

DEAD_STATUSES = {"UNQUALIFIED"}
DEAD_NOTE_PREFIXES = ("DQ", "DO NOT EMAIL")
PROMISE_PREFIX = "PROMISE"
COLLEAGUE_WINDOW_DAYS = 7


def load_crm(path: str | Path) -> dict:
    crm = _load_json(path, "CRM")
    if not isinstance(crm, dict) or "companies" not in crm:
        raise UsageError(f"CRM file has no 'companies' list: {path}")
    return crm


def _parse(day: str) -> date:
    return datetime.strptime(day, "%Y-%m-%d").date()


def normalise_domain(value: str) -> str:
    """Accept a bare domain, a URL or an email address, in any case."""
    value = value.strip().lower()
    value = value.split("://", 1)[-1]
    value = value.split("/", 1)[0]
    value = value.rsplit("@", 1)[-1]
    return value[4:] if value.startswith("www.") else value


def find_company(crm: dict, domain: str) -> dict | None:
    """Match on the main domain or any alternate domain."""
    domain = normalise_domain(domain)
    for company in crm.get("companies", []):
        domains = [company["domain"], *company.get("alt_domains", [])]
        if domain in (normalise_domain(d) for d in domains):
            return company
    return None


def _starts(note: dict, *keywords: str) -> bool:
    return note.get("text", "").lstrip().upper().startswith(keywords)


def _notes(company: dict, keyword: str) -> list[dict]:
    return [n for n in company.get("notes", []) if _starts(n, keyword)]


def check_dead(company: dict) -> list[Finding]:
    found = []
    if str(company.get("status", "")).upper() in DEAD_STATUSES:
        found.append(
            Finding("disqualified", FAIL, f"company status is {company['status']}")
        )
    for deal in company.get("deals", []):
        if deal.get("dq_reason"):
            found.append(
                Finding(
                    "disqualified",
                    FAIL,
                    f"a deal carries a disqualification reason: {deal['dq_reason']!r}",
                )
            )
    for note in company.get("notes", []):
        if _starts(note, *DEAD_NOTE_PREFIXES):
            found.append(Finding("disqualified", FAIL, f"note says {note['text']!r}"))
    return found


def check_contacted(company: dict, contact: str) -> list[Finding]:
    found = []
    contact = contact.strip().lower()
    for note in _notes(company, "SENT"):
        if note.get("to", "").strip().lower() == contact:
            found.append(
                Finding(
                    "contacted",
                    FAIL,
                    f"{contact} was emailed on {note.get('date', 'an unknown date')}, "
                    "so this is not a first email",
                )
            )
    for task in company.get("tasks", []):
        if task.get("subject", "").upper().startswith("SEND") and not task.get("done"):
            found.append(
                Finding(
                    "draft_waiting",
                    FAIL,
                    f"an unsent draft is already logged: {task['subject']!r}",
                )
            )
    return found


def check_history(company: dict) -> list[Finding]:
    found = []
    for note in _notes(company, PROMISE_PREFIX):
        found.append(Finding("promise", FAIL, f"an earlier promise applies: {note['text']!r}"))
    if company.get("date_replied"):
        found.append(
            Finding(
                "replied",
                FAIL,
                f"the company replied on {company['date_replied']}, answer on that thread",
            )
        )
    return found


def check_colleagues(company: dict, contact: str, today: date) -> list[Finding]:
    found = []
    contact = contact.strip().lower()
    for note in _notes(company, "SENT"):
        other = note.get("to", "").strip().lower()
        if not other or other == contact:
            continue
        try:
            age = (today - _parse(note.get("date", ""))).days
        except ValueError:
            found.append(
                Finding(
                    "colleague_contacted",
                    WARN,
                    f"{other} was emailed on an unreadable date: {note.get('date')!r}",
                )
            )
            continue
        if age <= COLLEAGUE_WINDOW_DAYS:
            found.append(
                Finding(
                    "colleague_contacted",
                    WARN,
                    f"{other} at the same company was emailed {age} days ago",
                )
            )
    return found


def presend_check(
    crm: dict, domain: str, contact: str, today: date | None = None
) -> list[Finding]:
    """Run all four questions. A company that is not in the CRM is a failure:
    the source step creates the record, so a missing record means a step was skipped."""
    today = today or date.today()
    company = find_company(crm, domain)
    if company is None:
        return [Finding("unknown_company", FAIL, f"{domain} has no company record")]
    findings: list[Finding] = []
    findings += check_dead(company)
    findings += check_contacted(company, contact)
    findings += check_history(company)
    findings += check_colleagues(company, contact, today)
    return findings


BLIND_SPOTS = (
    "This check cannot see calls, social messages, or sends from mailboxes "
    "that do not log to the CRM."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check an account before a send.")
    parser.add_argument("--crm", required=True, help="path to the CRM JSON export")
    parser.add_argument("--domain", required=True, help="the company domain")
    parser.add_argument("--contact", required=True, help="the address the draft is for")
    parser.add_argument("--today", help="YYYY-MM-DD (default: the real date)")
    args = parser.parse_args(argv)

    try:
        try:
            today = _parse(args.today) if args.today else None
        except ValueError:
            raise UsageError(f"--today must be YYYY-MM-DD, got {args.today!r}") from None
        findings = presend_check(load_crm(args.crm), args.domain, args.contact, today)
    except UsageError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding)
    print(BLIND_SPOTS)
    if passed(findings):
        print("CLEAN")
        return 0
    print("BLOCKED")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
