"""Run the whole pipeline on a mock CRM, offline.

    python -m demo.run_demo

Research and drafting are done by an agent in real use. Here they are read
from fixture files in demo/accounts/, so the demo needs no API key and gives
the same result every time. The two gates are the real code.

Nothing is sent and the mock CRM file is never modified. The resulting CRM
state is written to demo/out/crm_after.json, and a visual report of the run
to demo/out/report.html.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path

from gates.checker import check, load_claims, load_config, passed
from gates.funnel import funnel, render
from gates.presend import find_company, load_crm, presend_check

from .report import build_report

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT.parent / "gates" / "config.example.json"


def note(company: dict, today: str, text: str, **extra) -> None:
    company.setdefault("notes", []).append({"date": today, "text": text, **extra})


def task(company: dict, subject: str) -> None:
    company.setdefault("tasks", []).append({"subject": subject, "done": False})


def source(crm: dict, candidates: list[dict], today: str) -> list[str]:
    """Create a record for each new company. Skip any domain the CRM already holds."""
    lines = []
    for cand in candidates:
        existing = find_company(crm, cand["domain"])
        if existing:
            lines.append(
                f"  skipped  {cand['domain']:<26} already in the CRM as {existing['domain']}"
            )
            continue
        company = {
            "domain": cand["domain"], "alt_domains": [], "name": cand["name"],
            "status": "NEW", "lane": "transcribe", "segment": cand["segment"],
            "date_contacted": None, "date_replied": None,
            "notes": [], "tasks": [], "deals": [],
        }
        note(company, today, f"SOURCED: {cand['signal']}. {cand['url']}")
        crm["companies"].append(company)
        lines.append(f"  created  {cand['domain']:<26} {cand['signal']}")
    return lines


def work_account(crm: dict, company: dict, config: dict, today: str) -> dict:
    """Qualify, draft, gate and pre-send check one account.

    Returns a record of every step, so the report can show why an account
    ended where it did. The "outcome" key holds the one-line result."""
    record = {"domain": company["domain"], "name": company["name"], "stage": "none",
              "verdict": None, "claims": None, "draft": None, "gate": None, "presend": None}
    folder = ROOT / "accounts" / company["domain"].rsplit(".example", 1)[0]
    verdict_file = folder / "verdict.json"
    if not verdict_file.exists():
        record["outcome"] = "no verdict fixture, left as it is"
        return record
    verdict = json.loads(verdict_file.read_text(encoding="utf-8"))
    record["verdict"] = verdict
    arm = verdict["arm"]
    note(company, today, f"VERDICT: arm {arm}")

    if arm == "expansion":
        record.update(stage="parked",
                      outcome="parked: they would have to add a new feature, so no cold email")
        return record
    if arm == "unclear":
        task(company, f"HUMAN QUEUE: {company['name']}: {verdict['question']}")
        record.update(stage="queue", outcome="human queue: one question would settle it")
        return record

    day = datetime.strptime(today, "%Y-%m-%d").date()
    draft = (folder / "draft.txt").read_text(encoding="utf-8")
    claims = load_claims(folder / "claims.json")
    gate = check(draft, config, verdict["register"], claims, today=day)
    record.update(draft=draft, claims=claims, gate=gate)
    if not passed(gate):
        failed = sorted({f.rule for f in gate if f.severity == "fail"})
        record.update(stage="gate", outcome="BLOCKED by the gate: " + ", ".join(failed))
        return record

    pre = presend_check(crm, company["domain"], verdict["contact"], today=day)
    record["presend"] = pre
    if not passed(pre):
        first = next(f for f in pre if f.severity == "fail")
        record.update(stage="presend", outcome=f"BLOCKED by pre-send: {first.message}")
        return record

    note(company, today, f"DRAFTED: first email to {verdict['contact']}")
    task(company, f"SEND: {company['name']}: draft waiting for {verdict['contact']}")
    record.update(stage="drafted",
                  outcome=f"DRAFTED for {verdict['contact']}, waiting for a person to send")
    return record


def confirm_send(crm: dict, domain: str, today: str) -> None:
    """What happens after a person says the email went out. One batch of writes."""
    company = find_company(crm, domain)
    open_send = [t for t in company["tasks"] if t["subject"].startswith("SEND") and not t["done"]]
    if not open_send:
        raise ValueError(f"{domain} has no draft waiting")
    contact = open_send[0]["subject"].rsplit(" ", 1)[-1]
    if company["status"] == "NEW":
        company["status"] = "ATTEMPTED"
    company["date_contacted"] = company["date_contacted"] or today
    note(company, today, "SENT: first email", to=contact)
    open_send[0]["done"] = True
    task(company, f"{company['name']}: read the result of the email to {contact}")


def run_detailed(
    crm: dict, candidates: list[dict], config: dict
) -> tuple[dict, list[str], list[dict], list[str]]:
    crm = copy.deepcopy(crm)
    today = crm["today"]
    source_lines = source(crm, candidates, today)
    out = ["Source", *source_lines, "", "Qualify, draft, gate, pre-send"]
    records = []
    for company in crm["companies"]:
        if company["status"] != "NEW":
            continue
        record = work_account(crm, company, config, today)
        records.append(record)
        out.append(f"  {company['domain']:<26} {record['outcome']}")
    return crm, out, records, source_lines


def run(crm: dict, candidates: list[dict], config: dict) -> tuple[dict, list[str], dict]:
    crm_after, out, records, _ = run_detailed(crm, candidates, config)
    return crm_after, out, {r["domain"]: r["outcome"] for r in records}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the pipeline on the mock CRM.")
    parser.add_argument(
        "--confirm-send", metavar="DOMAIN", action="append", default=[],
        help="mark a drafted account as sent, to show what a confirmed send changes",
    )
    args = parser.parse_args(argv)
    crm_before = load_crm(ROOT / "mock_crm.json")
    candidates = json.loads((ROOT / "candidates.json").read_text(encoding="utf-8"))
    config = load_config(CONFIG)

    crm_after, lines, records, source_lines = run_detailed(crm_before, candidates, config)
    print("\n".join(lines))
    for domain in args.confirm_send:
        confirm_send(crm_after, domain, crm_after["today"])
        print(f"\nConfirmed send: {domain} is now ATTEMPTED with a contact date")

    print("\nFunnel before the run")
    print(render(funnel(crm_before)))
    print("\nFunnel after the run")
    print(render(funnel(crm_after)))
    if args.confirm_send:
        print("\nContacted moved only for the confirmed sends.")
    else:
        print(
            "\nContacted did not move. A draft sets no contact date and no status."
            "\nBoth change only when a person confirms the send."
            "\nTry: python -m demo.run_demo --confirm-send northwind-audio.example"
        )

    out_dir = ROOT / "out"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "crm_after.json").write_text(
        json.dumps(crm_after, indent=2), encoding="utf-8"
    )
    report = out_dir / "report.html"
    report.write_text(
        build_report(crm_before, crm_after, records, source_lines, args.confirm_send),
        encoding="utf-8",
    )
    print(f"\nCRM state written to {out_dir / 'crm_after.json'}")
    print(f"Visual report written to {report}  (open it in a browser)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
