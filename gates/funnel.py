"""Recount the funnel from a CRM export, using docs/funnel-definitions.md.

Contacted means the company has a first-contact date. Replied means it also
has a first-reply date. Drafts are never counted: a draft sets no date.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from .checker import UsageError
from .presend import load_crm


def funnel(crm: dict) -> dict:
    contacted: Counter = Counter()
    replied: Counter = Counter()
    status: Counter = Counter()
    deals: Counter = Counter()
    for company in crm.get("companies", []):
        segment = company.get("segment", "unknown")
        status[company.get("status", "NEW")] += 1
        if company.get("date_contacted"):
            contacted[segment] += 1
            if company.get("date_replied"):
                replied[segment] += 1
        for deal in company.get("deals", []):
            deals[deal.get("stage", "unknown")] += 1
    return {
        "contacted": dict(contacted),
        "replied": dict(replied),
        "status": dict(status),
        "deals": dict(deals),
    }


def render(result: dict) -> str:
    lines = ["Segment        Contacted  Replied  Reply rate"]
    for segment in sorted(result["contacted"]):
        sent = result["contacted"][segment]
        got = result["replied"].get(segment, 0)
        lines.append(f"{segment:<14} {sent:>9}  {got:>7}  {got / sent:>9.0%}")
    lines.append("")
    lines.append(
        "Status: " + ", ".join(f"{k} {v}" for k, v in sorted(result["status"].items()))
    )
    lines.append(
        "Deals:  "
        + (", ".join(f"{k} {v}" for k, v in sorted(result["deals"].items())) or "none")
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recount the funnel from a CRM export.")
    parser.add_argument("--crm", required=True)
    args = parser.parse_args(argv)
    try:
        print(render(funnel(load_crm(args.crm))))
    except UsageError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
