"""Read one account from HubSpot into the shape the pre-send check reads.

Read only. This module searches, lists associations and batch-reads records.
It never creates, updates or deletes anything in HubSpot.

Credentials: a HubSpot Service Key (Settings > Integrations > Service Keys),
or an existing legacy private app token. Both are sent as a Bearer token.
Put it in the HUBSPOT_SERVICE_KEY environment variable. It is never printed.

Usage:
    python -m gates.hubspot --domain example.com                 # print the export
    python -m gates.hubspot --domain example.com --out crm.json  # save it
    python -m gates.hubspot --domain example.com --contact a@example.com
                                                                 # run the pre-send check

Field mapping lives in the "hubspot" section of the config file. See
docs/crm-adapter.md. Anything not mapped is reported, with the check it weakens.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from typing import Callable, Optional, Tuple

from .checker import UsageError, _load_json, html_to_text, passed
from .presend import BLIND_SPOTS, normalise_domain, presend_check, _parse

BASE_URL = "https://api.hubapi.com"
TOKEN_ENV = ("HUBSPOT_SERVICE_KEY", "HUBSPOT_ACCESS_TOKEN")
BATCH_LIMIT = 100
RETRY_STATUSES = {429, 500, 502, 503, 504}
OUTGOING_DIRECTIONS = {"EMAIL", "FORWARDED_EMAIL"}
NOT_SENT_STATUSES = {"BOUNCED", "FAILED", "DRAFT"}
EMAIL_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")

DEFAULTS = {
    "status_property": "hs_lead_status",
    # HubSpot's built-in Lead Status uses ATTEMPTED_TO_CONTACT. Values not listed pass through unchanged.
    "status_map": {"ATTEMPTED_TO_CONTACT": "ATTEMPTED"},
    "dq_reason_property": "closed_lost_reason",
    "lane_property": None,
    "segment_property": None,
    # None means: derive from logged emails (first outgoing, first incoming).
    "date_contacted_property": None,
    "date_replied_property": None,
    "alt_domains_property": None,
    # Owner claim. Compared with --sender-owner-id; no id given means no check.
    "owner_property": "hubspot_owner_id",
    # Contact blocks, read only for the --contact address.
    # Any truthy value in these properties blocks the contact, with the label as the reason.
    "contact_block_properties": {
        "hs_email_optout": "unsubscribed from all email",
        "hs_email_hard_bounce_reason_enum": "hard bounced",
    },
    "contact_sequence_property": "hs_sequences_is_enrolled",
    "contact_unanswered_property": None,
    "max_unanswered_touches": None,
}

# Transport: (method, url, headers, body) -> (status, headers, body)
Transport = Callable[[str, str, dict, Optional[bytes]], Tuple[int, dict, bytes]]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None,
                     *, timeout: float = 30) -> tuple[int, dict, bytes]:
    """Bind a shorter timeout with functools.partial. It covers each socket
    operation, not DNS or the whole request, so callers that need a hard cap
    must also run a watchdog (see hooks/gate_gmail.py)."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers or {}), err.read() or b""


class BudgetExceeded(UsageError):
    """The read ran out of its time budget."""


class HubSpot:
    """A small read-only client. Retries rate limits and server errors with backoff.

    For the send gate: retries=1, max_backoff=1, honour_retry_after=False and a
    deadline (a time.monotonic() value), so a slow or rate-limited HubSpot ends
    the read with BudgetExceeded instead of running past the hook's timeout.
    """

    def __init__(self, token: str, transport: Transport = urllib_transport,
                 base_url: str = BASE_URL, sleep: Callable[[float], None] = time.sleep,
                 retries: int = 4, *, max_backoff: float | None = None,
                 honour_retry_after: bool = True, deadline: float | None = None,
                 clock: Callable[[], float] = time.monotonic):
        if not token:
            raise UsageError(f"no HubSpot credential: set {TOKEN_ENV[0]}")
        self._token = token
        self._transport = transport
        self._base = base_url.rstrip("/")
        self._sleep = sleep
        self._retries = retries
        self._max_backoff = max_backoff
        self._honour_retry_after = honour_retry_after
        self._deadline = deadline
        self._clock = clock
        self.requests = 0

    def _check_budget(self, extra: float = 0.0) -> None:
        if self._deadline is not None and self._clock() + extra >= self._deadline:
            raise BudgetExceeded(f"CRM read ran out of time after {self.requests} HubSpot request(s)")

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        for attempt in range(self._retries + 1):
            self._check_budget()
            self.requests += 1
            status, resp_headers, raw = self._transport(method, self._base + path, headers, data)
            if status in RETRY_STATUSES and attempt < self._retries:
                wait = (self._honour_retry_after and _retry_after(resp_headers)) or 2 ** attempt
                if self._max_backoff is not None:
                    wait = min(wait, self._max_backoff)
                self._check_budget(wait)
                self._sleep(wait)
                continue
            break
        if status in (401, 403):
            raise UsageError(
                f"HubSpot refused the request ({status}) for {path}. Check the key and that it "
                "has read access to companies, deals, notes, tasks and emails."
            )
        if status >= 400:
            raise UsageError(f"HubSpot returned {status} for {path}: {raw[:200].decode('utf-8', 'replace')}")
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise UsageError(f"HubSpot returned a non-JSON body for {path}") from None

    def find_companies(self, domain: str, properties: list[str]) -> list[dict]:
        domain = normalise_domain(domain)
        groups = [{"filters": [{"propertyName": "domain", "operator": "EQ", "value": value}]}
                  for value in (domain, "www." + domain)]
        body = {"filterGroups": groups, "properties": properties, "limit": 10}
        return self._call("POST", "/crm/v3/objects/companies/search", body).get("results", [])

    def associated_ids(self, company_id: str, to_type: str) -> list[str]:
        ids, after = [], None
        while True:
            path = f"/crm/v4/objects/companies/{company_id}/associations/{to_type}?limit=500"
            if after:
                path += f"&after={after}"
            page = self._call("GET", path)
            ids += [str(r["toObjectId"]) for r in page.get("results", []) if "toObjectId" in r]
            after = page.get("paging", {}).get("next", {}).get("after")
            if not after:
                return ids

    def batch_read(self, object_type: str, ids: list[str], properties: list[str]) -> list[dict]:
        records = []
        for start in range(0, len(ids), BATCH_LIMIT):
            chunk = ids[start:start + BATCH_LIMIT]
            body = {"properties": properties, "inputs": [{"id": i} for i in chunk]}
            records += self._call("POST", f"/crm/v3/objects/{object_type}/batch/read", body).get("results", [])
        return records


def _retry_after(headers: dict) -> float | None:
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _day(value) -> str | None:
    """HubSpot sends ISO timestamps or epoch milliseconds. Return YYYY-MM-DD."""
    if value in (None, ""):
        return None
    text = str(value)
    if text.isdigit():
        return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc).date().isoformat()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10] if re.match(r"\d{4}-\d{2}-\d{2}", text) else None


def _addresses(value: str | None) -> list[str]:
    return [a.lower() for a in EMAIL_RE.findall(value or "")]


def load_mapping(config_path: str | None) -> dict:
    mapping = dict(DEFAULTS)
    if config_path:
        config = _load_json(config_path, "config")
        mapping.update((config or {}).get("hubspot", {}))
    return mapping


def coverage_warnings(mapping: dict) -> list[str]:
    warnings = []
    if not mapping.get("date_replied_property"):
        warnings.append("replied date comes from logged incoming emails; auto-replies count as replies, "
                        "and replies from mailboxes that do not log to HubSpot are missed")
    if not mapping.get("date_contacted_property"):
        warnings.append("contacted date comes from logged outgoing emails; sends that were not logged are missed")
    if not mapping.get("alt_domains_property"):
        warnings.append("no alternate-domain property mapped: a company under a second domain is not matched")
    if not mapping.get("contact_unanswered_property") or mapping.get("max_unanswered_touches") is None:
        warnings.append("no unanswered-touches property and limit mapped: the touch limit is off")
    if mapping.get("contact_sequence_property"):
        warnings.append("sequence check sees HubSpot sequences only; other sending tools are invisible unless "
                        "they write to a mapped contact property")
    if not mapping.get("lane_property") or not mapping.get("segment_property"):
        warnings.append("lane or segment not mapped: the funnel cannot group by them")
    return warnings


def _truthy(value) -> bool:
    return str(value or "").strip().lower() not in ("", "false", "0", "none", "null")


def read_contact(client: HubSpot, email: str, mapping: dict) -> dict | None:
    """The one contact a draft is for, with its block, sequence and touch fields."""
    blocks = mapping.get("contact_block_properties") or {}
    props = ["email", *blocks, mapping.get("contact_sequence_property"), mapping.get("contact_unanswered_property")]
    body = {"filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email.strip().lower()}]}],
            "properties": [p for p in props if p], "limit": 1}
    results = client._call("POST", "/crm/v3/objects/contacts/search", body).get("results", [])
    if not results:
        return None
    cp = results[0].get("properties", {})
    reasons = [label for prop, label in blocks.items() if _truthy(cp.get(prop))]
    touches = cp.get(mapping["contact_unanswered_property"]) if mapping.get("contact_unanswered_property") else None
    return {
        "email": (cp.get("email") or email).lower(),
        "blocked": "; ".join(reasons) or None,
        "in_sequence": _truthy(cp.get(mapping["contact_sequence_property"])) if mapping.get("contact_sequence_property") else False,
        "unanswered_touches": int(float(touches)) if str(touches or "").replace(".", "", 1).isdigit() else None,
    }


def build_company(client: HubSpot, domain: str, mapping: dict) -> tuple[dict | None, list[str]]:
    """Return (company in the pre-send shape, extra warnings). None when HubSpot has no match."""
    company_props = [p for p in (
        "domain", "name", mapping["status_property"], mapping.get("lane_property"),
        mapping.get("segment_property"), mapping.get("date_contacted_property"),
        mapping.get("date_replied_property"), mapping.get("alt_domains_property"),
        mapping.get("owner_property"),
    ) if p]
    matches = client.find_companies(domain, company_props)
    if not matches:
        return None, []
    warnings = []
    if len(matches) > 1:
        warnings.append(f"{len(matches)} HubSpot companies share {normalise_domain(domain)}; "
                        "their records were combined. Merge them in HubSpot.")

    props = matches[0].get("properties", {})
    status = props.get(mapping["status_property"]) or "NEW"
    company = {
        "domain": normalise_domain(props.get("domain") or domain),
        "alt_domains": [],
        "name": props.get("name") or "",
        "status": mapping.get("status_map", {}).get(status, status),
        "lane": props.get(mapping["lane_property"]) if mapping.get("lane_property") else None,
        "segment": props.get(mapping["segment_property"]) if mapping.get("segment_property") else None,
        "date_contacted": _day(props.get(mapping["date_contacted_property"])) if mapping.get("date_contacted_property") else None,
        "date_replied": _day(props.get(mapping["date_replied_property"])) if mapping.get("date_replied_property") else None,
        "owner": (props.get(mapping["owner_property"]) or None) if mapping.get("owner_property") else None,
        "notes": [], "tasks": [], "deals": [], "contacts": [],
    }

    sent_days, replied_days = [], []
    for match in matches:
        p = match.get("properties", {})
        if mapping.get("alt_domains_property"):
            company["alt_domains"] += [d.strip() for d in re.split(r"[;,\s]+", p.get(mapping["alt_domains_property"]) or "") if d.strip()]
        if match is not matches[0] and p.get("domain"):
            company["alt_domains"].append(p["domain"])
        cid = str(match["id"])

        for note in client.batch_read("notes", client.associated_ids(cid, "notes"), ["hs_note_body", "hs_timestamp"]):
            text = html_to_text(note.get("properties", {}).get("hs_note_body") or "")
            entry = {"date": _day(note["properties"].get("hs_timestamp")), "text": text}
            if text.upper().startswith("SENT"):
                found = _addresses(text)
                if found:
                    entry["to"] = found[0]
            company["notes"].append(entry)

        for task in client.batch_read("tasks", client.associated_ids(cid, "tasks"), ["hs_task_subject", "hs_task_status"]):
            tp = task.get("properties", {})
            company["tasks"].append({"subject": tp.get("hs_task_subject") or "",
                                     "done": (tp.get("hs_task_status") or "").upper() == "COMPLETED"})

        dq_prop = mapping["dq_reason_property"]
        for deal in client.batch_read("deals", client.associated_ids(cid, "deals"), ["dealstage", dq_prop]):
            dp = deal.get("properties", {})
            company["deals"].append({"stage": dp.get("dealstage"), "dq_reason": dp.get(dq_prop) or None})

        email_props = ["hs_email_direction", "hs_email_status", "hs_email_to_email", "hs_email_subject", "hs_timestamp"]
        for email in client.batch_read("emails", client.associated_ids(cid, "emails"), email_props):
            ep = email.get("properties", {})
            direction = (ep.get("hs_email_direction") or "").upper()
            day = _day(ep.get("hs_timestamp"))
            subject = ep.get("hs_email_subject") or "(no subject)"
            if direction in OUTGOING_DIRECTIONS and (ep.get("hs_email_status") or "").upper() not in NOT_SENT_STATUSES:
                for address in _addresses(ep.get("hs_email_to_email")):
                    company["notes"].append({"date": day, "text": f"SENT: {subject} (logged email)", "to": address})
                if day:
                    sent_days.append(day)
            elif direction == "INCOMING_EMAIL":
                company["notes"].append({"date": day, "text": f"REPLIED: {subject} (logged email)"})
                if day:
                    replied_days.append(day)

    if not mapping.get("date_contacted_property") and sent_days:
        company["date_contacted"] = min(sent_days)
    if not mapping.get("date_replied_property") and replied_days:
        company["date_replied"] = min(replied_days)
    company["alt_domains"] = sorted({normalise_domain(d) for d in company["alt_domains"]} - {company["domain"]})
    return company, warnings


def export(client: HubSpot, domain: str, mapping: dict, contact: str | None = None) -> tuple[dict, list[str]]:
    company, warnings = build_company(client, domain, mapping)
    if company and contact:
        person = read_contact(client, contact, mapping)
        if person:
            company["contacts"].append(person)
    return {"companies": [company] if company else []}, warnings + coverage_warnings(mapping)


def _token() -> str:
    for name in TOKEN_ENV:
        if os.environ.get(name):
            return os.environ[name]
    return ""


def main(argv: list[str] | None = None, transport: Transport = urllib_transport) -> int:
    parser = argparse.ArgumentParser(description="Read one account from HubSpot (read only).")
    parser.add_argument("--domain", required=True, help="the company domain")
    parser.add_argument("--contact", help="run the pre-send check for this address")
    parser.add_argument("--config", help="config file with a 'hubspot' mapping section")
    parser.add_argument("--out", help="write the export to this file instead of printing it")
    parser.add_argument("--today", help="YYYY-MM-DD for the pre-send check (default: the real date)")
    parser.add_argument("--sender-owner-id", help="your HubSpot owner id; blocks accounts owned by someone else")
    args = parser.parse_args(argv)

    try:
        today = None
        if args.today:
            try:
                today = _parse(args.today)
            except ValueError:
                raise UsageError(f"--today must be YYYY-MM-DD, got {args.today!r}") from None
        client = HubSpot(_token(), transport=transport)
        mapping = load_mapping(args.config)
        crm, warnings = export(client, args.domain, mapping, args.contact)
    except UsageError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(crm, handle, indent=2)
        print(f"wrote {args.out}", file=sys.stderr)
    if not args.contact:
        if not args.out:
            print(json.dumps(crm, indent=2))
        return 0

    if not args.sender_owner_id:
        print("warning: no --sender-owner-id given: the owner-claim check is off", file=sys.stderr)
    findings = presend_check(crm, args.domain, args.contact, today, sender_owner=args.sender_owner_id,
                             max_unanswered=mapping.get("max_unanswered_touches"))
    for finding in findings:
        print(finding)
    print(BLIND_SPOTS)
    print("CLEAN" if passed(findings) else "BLOCKED")
    return 0 if passed(findings) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
