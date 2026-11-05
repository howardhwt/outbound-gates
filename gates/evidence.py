"""Verified evidence for the send gate.

An agent may only put a number in a gated email after this module has fetched
the source page itself, found the quoted sentence on it, and found the number
inside that sentence. The page text is stored, and the claim points at it by
hash, so the gate can re-check the claim later without the network.

This module is the only code that knows how ``.gates/`` is laid out:

    <gates_home>/evidence/<sha256 of page text>.txt
    <gates_home>/drafts/<domain>/<contact>/claims/<sha256 of url|quote|value>.json

It also loads the gate's settings, because the record command needs them.

    PYTHONPATH=<plugin> python3 -m gates.evidence record \\
        --gates-home <project>/.gates --contact dana@acme.example \\
        --url https://acme.example/pricing --quote "..." --value '$0.006'
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .checker import (
    UsageError,
    extract_numbers,
    html_to_text,
    load_config,
    normalise_number,
    normalise_text,
)
from .presend import find_company, normalise_domain

FORMAT_VERSION = 1
MAX_BYTES = 2_000_000
TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 5
USER_AGENT = "outbound-gates-evidence/1 (+https://github.com/howardhwt/outbound-gates)"
TEXT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
USER_CONFIG = Path.home() / ".config" / "outbound-gates" / "config.json"
HUBSPOT_KEY_FILE = USER_CONFIG.parent / "hubspot.key"
CRM_SOURCES = ("file", "hubspot")
CRM_BUDGET_S = 40.0  # the hook's own limit; its watchdog fires at 45 s and Claude Code's timeout is 60 s
HUBSPOT_REQUEST_TIMEOUT_S = 8.0
_CONTACT_SAFE_RE = re.compile(r"[^a-z0-9._@+-]")


def _load_json(path: Path, what: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UsageError(f"{what} file not found: {path}") from None
    except json.JSONDecodeError as err:
        raise UsageError(f"{what} file is not valid JSON: {path} ({err})") from None


class EvidenceError(ValueError):
    """The source was refused. The message says why, in words an agent can act on."""


# --- layout ---------------------------------------------------------------

def contact_parts(contact: str) -> tuple[str, str]:
    """(domain, contact) as folder names, or EvidenceError("invalid recipient path")."""
    contact = (contact or "").strip().lower()
    if contact.count("@") != 1:
        raise EvidenceError(f"invalid recipient path: {contact!r} is not one email address")
    local, _, host = contact.partition("@")
    domain = normalise_domain(host)
    safe_contact = _CONTACT_SAFE_RE.sub("_", contact)
    for part in (local, domain, safe_contact):
        if part in ("", ".", "..") or "/" in part or "\\" in part:
            raise EvidenceError(f"invalid recipient path: {contact!r}")
    if any(seg in ("", ".", "..") for seg in domain.split(".")):
        raise EvidenceError(f"invalid recipient path: {contact!r}")
    return domain, safe_contact


def draft_dir(gates_home: Path, contact: str) -> Path:
    domain, safe_contact = contact_parts(contact)
    root = (Path(gates_home) / "drafts").resolve()
    path = (root / domain / safe_contact).resolve()
    if path == root or not path.is_relative_to(root):
        raise EvidenceError(f"invalid recipient path: {contact!r}")
    return path


def _evidence_path(gates_home: Path, sha: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        raise EvidenceError(f"bad evidence hash: {sha!r}")
    return Path(gates_home) / "evidence" / f"{sha}.txt"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- settings ---------------------------------------------------------------

def project_rules(plugin_root: Path, project_dir: Path) -> tuple[dict, str]:
    """The gate's rules for a project, and where their style came from.

    Truth rules always come from the plugin's defaults. The style comes from
    <project>/.gates/style.json when it exists. When it doesn't, the project
    keeps the example style, so a project set up before styles existed (or a
    bare folder) never gets looser on update; setup writes style.json for new
    projects. Returns (rules, source) with source "default", a preset name,
    "custom" or "example (no style.json)".
    """
    from .checker import merge_style

    plugin_root, project_dir = Path(plugin_root), Path(project_dir)
    defaults = _load_json(plugin_root / "gates" / "hook.config.json", "gate settings")
    base = load_config(plugin_root / "gates" / defaults.get("rules", "config.example.json"))
    styles_dir = plugin_root / "gates" / "styles"
    style_file = project_dir / ".gates" / "style.json"
    if not style_file.exists():
        return merge_style(base, {"preset": "example"}, styles_dir), "example (no style.json)"
    style = _load_json(style_file, "project style (.gates/style.json)")
    rules = merge_style(base, style, styles_dir, where=str(style_file))
    own_keys = {k for k in style if k != "_comment"}
    source = ("default" if not own_keys else
              style["preset"] if own_keys == {"preset"} else "custom")
    return rules, source


def _crm_bcc(user: dict, where: Path | str) -> list[str]:
    """Your CRM's logging addresses (for example HubSpot's BCC address). A draft
    may carry them in bcc on top of its one recipient. User config only, so the
    agent can't add an address that copies mail somewhere else."""
    value = user.get("crm_bcc", [])
    if not (isinstance(value, list) and all(isinstance(v, str) and "@" in v and not re.search(r"\s", v) for v in value)):
        raise UsageError(f"crm_bcc in {where} must be a list of email addresses")
    return [v.strip().lower() for v in value]


def apply_user_facts(rules: dict, user: dict, where: Path | str) -> dict:
    """Add the user's own facts to the rules. They pass without a source, so they
    come only from the user's own config, never the project, and only add."""
    user_facts = user.get("own_facts", [])
    if not isinstance(user_facts, list) or not all(isinstance(v, str) for v in user_facts):
        raise UsageError(f"own_facts in {where} must be a list of strings, e.g. [\"$0.01\"]")
    rules = dict(rules)
    rules["own_facts"] = _union(rules.get("own_facts", []), user_facts)
    return rules


def load_settings(
    plugin_root: Path, project_dir: Path, user_config: Path | None = None
) -> dict:
    """Plugin defaults, the project's style (project_rules), then the project file.

    A project may set register, sender_owner, max_unanswered, crm_source,
    crm_path, hubspot (the field mapping) and example_pages. It may only add to
    blocked_tools and banned_phrases.
    source_allowlist and extra own_facts come only from the user's own config
    file, outside the project the agent writes to.
    """
    plugin_root, project_dir = Path(plugin_root), Path(project_dir)
    defaults = _load_json(plugin_root / "gates" / "hook.config.json", "gate settings")
    rules, style_source = project_rules(plugin_root, project_dir)
    project_file = project_dir / ".gates" / "config.json"
    project = _load_json(project_file, "project gate settings") if project_file.exists() else {}
    user_file = Path(user_config) if user_config else USER_CONFIG
    user = _load_json(user_file, "user gate settings") if user_file.exists() else {}
    for name, data in (("project", project), ("user", user)):
        if not isinstance(data, dict):
            raise UsageError(f"{name} gate settings must be a JSON object")

    rules = dict(rules)
    rules["banned_phrases"] = _union(rules.get("banned_phrases", []), project.get("banned_phrases", []))
    rules = apply_user_facts(rules, user, user_file)
    settings = {
        "rules": rules,
        "style_source": style_source,
        "register": project.get("register", defaults.get("register", "cold")),
        "sender_owner": project.get("sender_owner", defaults.get("sender_owner")),
        "max_unanswered": project.get("max_unanswered", defaults.get("max_unanswered")),
        "blocked_tools": _union(defaults.get("blocked_tools", []), project.get("blocked_tools", [])),
        "source_allowlist": [normalise_domain(h) for h in user.get("source_allowlist", [])],
        "crm_bcc": _crm_bcc(user, user_file),
        "crm_source": project.get("crm_source", "file"),
        "crm_path": None,
        "hubspot": project.get("hubspot", {}),
        "example_pages": None,
    }
    if settings["crm_source"] not in CRM_SOURCES:
        raise UsageError(f"crm_source must be one of {', '.join(CRM_SOURCES)}, got {settings['crm_source']!r}")
    if not isinstance(settings["hubspot"], dict):
        raise UsageError("hubspot in .gates/config.json must be a JSON object")
    if "max_unanswered_touches" in settings["hubspot"]:
        raise UsageError("set the touch limit as max_unanswered in .gates/config.json, not hubspot.max_unanswered_touches")
    for key in ("crm_path", "example_pages"):
        value = project.get(key)
        if value:
            path = Path(value)
            settings[key] = path if path.is_absolute() else (project_dir / path)
    return settings


def _union(base: Iterable[str], extra: Iterable[str]) -> list[str]:
    out = list(base)
    for item in extra:
        if item not in out:
            out.append(item)
    return out


def read_crm(settings: dict, contact: str, *, deadline: float | None = None,
             transport=None, key_file: Path | None = None) -> tuple[dict, list[str]]:
    """The CRM for one draft, and warnings about what it couldn't check.

    File mode loads crm_path. HubSpot mode reads the contact's company and the
    contact live, with short timeouts, one retry, Retry-After ignored and a
    deadline, and never falls back to a file. Any failure raises, so the
    caller refuses the draft.
    """
    if settings.get("crm_source", "file") == "file":
        return load_crm(settings), []

    import functools
    import time

    from . import hubspot

    key_path = Path(key_file) if key_file else HUBSPOT_KEY_FILE
    try:
        token = key_path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token:
        raise UsageError(f"no HubSpot key: put a read-only Service Key in {key_path} (chmod 600)")
    client = hubspot.HubSpot(
        token,
        transport=transport or functools.partial(hubspot.urllib_transport, timeout=HUBSPOT_REQUEST_TIMEOUT_S),
        retries=1, max_backoff=1.0, honour_retry_after=False,
        deadline=deadline if deadline is not None else time.monotonic() + CRM_BUDGET_S,
    )
    mapping = dict(hubspot.DEFAULTS)
    mapping.update(settings.get("hubspot") or {})
    mapping["max_unanswered_touches"] = settings.get("max_unanswered")
    domain, _ = contact_parts(contact)
    try:
        company, extra = hubspot.build_company(client, domain, mapping)
        person = hubspot.read_contact(client, contact, mapping) if company else None
    except OSError as err:  # DNS failure, refused connection, socket timeout
        raise UsageError(f"HubSpot unreachable after {client.requests} request(s): {err}") from None

    warnings = list(extra)
    if company and person:
        company["contacts"].append(person)
    elif company:
        warnings.append(f"{contact} is not a HubSpot contact: unsubscribe, bounce and sequence checks did not run")
    if not settings.get("sender_owner"):
        warnings.append("no sender_owner in .gates/config.json: the owner check is off")
    gaps = hubspot.coverage_warnings(mapping)
    if gaps:
        warnings.append("HubSpot mapping gaps: " + "; ".join(gaps))
    warnings.append(f"read live from HubSpot in {client.requests} request(s)")
    return {"companies": [company] if company else []}, warnings


def load_crm(settings: dict) -> dict:
    path = settings.get("crm_path")
    if not path:
        raise UsageError("set crm_path in .gates/config.json")
    crm = _load_json(path, "CRM")
    if not isinstance(crm, dict):
        raise UsageError(f"CRM file must hold a JSON object: {path}")
    return crm


# --- the source rule and the fetch -----------------------------------------

def allowed_hosts(crm: dict, contact: str, settings: dict) -> list[str]:
    """The company's own domains (main and alternates) plus the user allow-list."""
    domain, _ = contact_parts(contact)
    company = find_company(crm, domain)
    if company is None:
        raise EvidenceError(f"company not in CRM: {domain}")
    hosts = [company["domain"], *company.get("alt_domains", [])]
    return [normalise_domain(h) for h in hosts] + list(settings.get("source_allowlist", []))


def host_allowed(host: str, hosts: Iterable[str]) -> bool:
    host = normalise_domain(host)
    return any(host == h or host.endswith("." + h) for h in hosts)


Resolve = Callable[[str], list[str]]


def _system_resolve(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def check_url(url: str, hosts: Iterable[str], resolve: Resolve = _system_resolve) -> str:
    """Refuse anything but http(s) to an allowed, public host. Returns the host."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise EvidenceError(f"scheme not allowed: {parts.scheme or 'none'}")
    host = (parts.hostname or "").lower()
    if not host:
        raise EvidenceError("URL has no host")
    if not host_allowed(host, hosts):
        raise EvidenceError(f"source host not allowed: {host}")
    if host.endswith(".example"):
        return host  # reserved name: served from fixture pages, never the network
    try:
        addresses = resolve(host)
    except OSError as err:
        raise EvidenceError(f"fetch failed: cannot resolve {host} ({err})") from None
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise EvidenceError(f"private address not allowed: {host} -> {ip}")
    return host


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


Fetch = Callable[[str, Callable[[str], None]], tuple[str, str, bytes]]


def urllib_fetch(url: str, check: Callable[[str], None]) -> tuple[str, str, bytes]:
    """GET with every redirect hop re-checked. Returns (final URL, content type, body)."""
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(MAX_REDIRECTS + 1):
        check(url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as resp:
                body = resp.read(MAX_BYTES + 1)
                return url, resp.headers.get("Content-Type", ""), body
        except urllib.error.HTTPError as err:
            if err.code in (301, 302, 303, 307, 308) and err.headers.get("Location"):
                url = urllib.parse.urljoin(url, err.headers["Location"])
                continue
            raise EvidenceError(f"fetch failed: HTTP {err.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise EvidenceError(f"fetch failed: {getattr(err, 'reason', err)}") from None
    raise EvidenceError(f"fetch failed: more than {MAX_REDIRECTS} redirects")


def _fixture_fetch(pages: Path | None, url: str) -> tuple[str, str, bytes]:
    """Serve a committed page for a reserved .example host (the demo)."""
    if not pages:
        raise EvidenceError("fetch failed: .example pages are not configured")
    parts = urllib.parse.urlsplit(url)
    name = parts.path.strip("/") or "index"
    root = (Path(pages) / parts.hostname).resolve()
    path = (root / f"{name}.html").resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise EvidenceError(f"fetch failed: HTTP 404 for {url}")
    return url, "text/html", path.read_bytes()


def page_text(content_type: str, body: bytes) -> str:
    if len(body) > MAX_BYTES:
        raise EvidenceError(f"page too large: over {MAX_BYTES} bytes")
    kind = content_type.split(";", 1)[0].strip().lower()
    if kind not in TEXT_TYPES:
        raise EvidenceError(f"unsupported content type: {kind or 'unknown'}")
    charset = "utf-8"
    for part in content_type.split(";")[1:]:
        key, _, val = part.strip().partition("=")
        if key.lower() == "charset" and val:
            charset = val.strip("\"'")
    try:
        text = body.decode(charset, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    if kind != "text/plain":
        text = html_to_text(text)
    return _flatten(text)


def _flatten(text: str) -> str:
    """Typography and whitespace normalised, so a copied quote still matches."""
    return " ".join(normalise_text(text).replace(" ", " ").split())


# --- record, read, verify -----------------------------------------------------

def _claim_id(url: str, quote: str, value: str) -> str:
    return hashlib.sha256(f"{url}\n{_flatten(quote)}\n{normalise_number(value)}".encode()).hexdigest()


def _quote_supports(text: str, quote: str, value: str) -> str | None:
    """None if the quote is on the page and holds the value, else the reason."""
    quote_flat = _flatten(quote)
    if not quote_flat:
        return "quote is empty"
    if quote_flat.lower() not in text.lower():
        return "quote not found at URL"
    if normalise_number(value) not in {v for _, v in extract_numbers(quote_flat)}:
        return "value not in quote"
    return None


def record_claim(
    gates_home: Path,
    contact: str,
    url: str,
    quote: str,
    value: str,
    *,
    settings: dict,
    crm: dict,
    fetch: Fetch = urllib_fetch,
    resolve: Resolve = _system_resolve,
    now: datetime | None = None,
) -> Path:
    """Fetch, verify and store one claim. Returns the claim file. Writes nothing on refusal."""
    gates_home = Path(gates_home)
    folder = draft_dir(gates_home, contact)
    if not normalise_number(value or "") or not any(ch.isdigit() for ch in value):
        raise EvidenceError(f"value is not a number: {value!r}")
    hosts = allowed_hosts(crm, contact, settings)

    def check(u: str) -> None:
        check_url(u, hosts, resolve)

    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host.endswith(".example"):
        check(url)
        final_url, content_type, body = _fixture_fetch(settings.get("example_pages"), url)
    else:
        final_url, content_type, body = fetch(url, check)
    check(final_url)
    text = page_text(content_type, body)
    reason = _quote_supports(text, quote, value)
    if reason:
        raise EvidenceError(reason)

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    fetched_at = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    claim = {
        "v": FORMAT_VERSION,
        "claim": _flatten(quote)[:120],
        "value": value,
        "url": url,
        "quote": _flatten(quote),
        "evidence_sha256": sha,
        "fetched_at": fetched_at.isoformat(),
        "checked": fetched_at.date().isoformat(),
    }
    _atomic_write(_evidence_path(gates_home, sha), text.encode("utf-8"))
    path = folder / "claims" / f"{_claim_id(url, quote, value)}.json"
    _atomic_write(path, (json.dumps(claim, indent=2) + "\n").encode("utf-8"))
    return path


def claims_for(gates_home: Path, contact: str) -> list[dict]:
    """Every stored claim for a recipient, in a stable order. Missing folder: none."""
    folder = draft_dir(Path(gates_home), contact) / "claims"
    if not folder.is_dir():
        return []
    claims = []
    for path in sorted(folder.glob("*.json")):
        try:
            claim = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise UsageError(f"claim file unreadable: {path} ({err})") from None
        if not isinstance(claim, dict):
            raise UsageError(f"claim file must hold a JSON object: {path}")
        claims.append(claim)
    return claims


# --- checked maths ---------------------------------------------------------

def parse_calc_inputs(raw: Iterable[str]) -> dict[str, dict]:
    """["theirs=0.006", "ours=own:$0.002"] -> {name: {"value", "source"}}."""
    inputs: dict[str, dict] = {}
    for item in raw:
        name, sep, value = item.partition("=")
        name = name.strip()
        if not sep or not name.isidentifier() or not value.strip():
            raise UsageError(f"--input must be NAME=VALUE or NAME=own:VALUE, got {item!r}")
        if name in inputs:
            raise UsageError(f"input {name} is given twice")
        source = "claim"
        value = value.strip()
        if value.startswith("own:"):
            source, value = "own", value[4:].strip()
        inputs[name] = {"value": value, "source": source}
    return inputs


def check_calc(calc: dict, verified_values: set[str], own_facts: set[str]) -> str | None:
    """Recompute one stored calculation from current inputs. None if it holds, else why not."""
    from . import calc as calc_module

    inputs = calc.get("inputs") or {}
    for name, item in inputs.items():
        value = normalise_number(str(item.get("value", "")))
        if item.get("source") == "own":
            if value not in own_facts:
                return f"input {name} = {item.get('value')} is not one of your own facts (own_facts)"
        elif value not in verified_values:
            return f"input {name} = {item.get('value')} has no verified stored source for this contact"
    try:
        calc_module.check(str(calc.get("formula", "")), inputs, str(calc.get("show", "")))
    except calc_module.CalcError as err:
        return str(err)
    return None


def record_calc(gates_home: Path, contact: str, formula: str, inputs: dict[str, dict], show: str,
                *, settings: dict, now: datetime | None = None) -> Path:
    """Check a calculation against this contact's verified claims and own facts, then store it."""
    gates_home = Path(gates_home)
    claims = claims_for(gates_home, contact)
    verified = {normalise_number(str(c.get("value", ""))) for c in claims if verify_claim(c, gates_home)}
    own = {normalise_number(v) for v in settings["rules"].get("own_facts", [])}
    calc = {"v": FORMAT_VERSION, "formula": formula.strip(), "inputs": inputs, "show": show.strip()}
    reason = check_calc(calc, verified, own)
    if reason:
        raise EvidenceError(reason)
    calc["recorded_at"] = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    key = hashlib.sha256(json.dumps([calc["formula"], inputs, calc["show"]], sort_keys=True).encode()).hexdigest()[:16]
    path = draft_dir(gates_home, contact) / "calcs" / f"{key}.json"
    _atomic_write(path, (json.dumps(calc, indent=2) + "\n").encode("utf-8"))
    return path


def calcs_for(gates_home: Path, contact: str) -> list[dict]:
    """Every stored calculation for a recipient. Missing folder: none."""
    folder = draft_dir(Path(gates_home), contact) / "calcs"
    if not folder.is_dir():
        return []
    calcs = []
    for path in sorted(folder.glob("*.json")):
        try:
            calc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise UsageError(f"calculation file unreadable: {path} ({err})") from None
        if not isinstance(calc, dict):
            raise UsageError(f"calculation file must hold a JSON object: {path}")
        calcs.append(calc)
    return calcs


def verify_claim(claim: dict, gates_home: Path) -> bool:
    """Offline re-check: stored page exists, matches its hash, holds the quote and value."""
    try:
        path = _evidence_path(Path(gates_home), claim.get("evidence_sha256", ""))
        data = path.read_bytes()
    except (EvidenceError, OSError):
        return False
    if hashlib.sha256(data).hexdigest() != claim.get("evidence_sha256"):
        return False
    text = data.decode("utf-8", errors="replace")
    return _quote_supports(text, str(claim.get("quote", "")), str(claim.get("value", ""))) is None


def snapshot_digests(claims: list[dict], gates_home: Path) -> list[str]:
    """The actual sha256 of each stored page the claims point at ("missing" if absent).

    Hashing the files themselves, not the names the claims record, means an
    edited page changes the verdict fingerprint.
    """
    digests = set()
    for claim in claims:
        try:
            data = _evidence_path(Path(gates_home), str(claim.get("evidence_sha256", ""))).read_bytes()
            digests.add(hashlib.sha256(data).hexdigest())
        except (EvidenceError, OSError):
            digests.add("missing")
    return sorted(digests)


# --- command line -------------------------------------------------------------

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def record_command(gates_home: Path, contact: str) -> str:
    """The record command as a shell line, for the gate's block message.

    URL, QUOTE and VALUE are placeholders for the agent to fill in; paths and
    the contact are already shell-quoted.
    """
    import shlex

    return (
        f"PYTHONPATH={shlex.quote(str(PLUGIN_ROOT))} python3 -m gates.evidence record"
        f" --gates-home {shlex.quote(str(gates_home))} --contact {shlex.quote(contact)}"
        " --url URL --quote 'QUOTE' --value VALUE"
    )


def calc_command(gates_home: Path, contact: str) -> str:
    """The calc command as a shell line, for the gate's block message."""
    import shlex

    return (
        f"PYTHONPATH={shlex.quote(str(PLUGIN_ROOT))} python3 -m gates.evidence calc"
        f" --gates-home {shlex.quote(str(gates_home))} --contact {shlex.quote(contact)}"
        " --formula '(theirs - ours) / theirs * 100' --input theirs=VALUE --input ours=own:VALUE --show 'SHOWN'"
    )


def main(argv: list[str] | None = None, fetch: Fetch = urllib_fetch) -> int:
    parser = argparse.ArgumentParser(description="Record a verified source for a gated email.")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="fetch a page, check the quote and value, store the claim")
    rec.add_argument("--gates-home", required=True, help="the project's .gates folder")
    rec.add_argument("--contact", required=True, help="the recipient's email address")
    rec.add_argument("--url", required=True, help="a page on the company's own site")
    rec.add_argument("--quote", required=True, help="the exact sentence on the page")
    rec.add_argument("--value", required=True, help="the number, as written in the email")
    cal = sub.add_parser("calc", help="check and store a number the email works out from sourced numbers")
    cal.add_argument("--gates-home", required=True, help="the project's .gates folder")
    cal.add_argument("--contact", required=True, help="the recipient's email address")
    cal.add_argument("--formula", required=True, help="arithmetic over input names, e.g. '(theirs - ours) / theirs * 100'")
    cal.add_argument("--input", action="append", default=[], metavar="NAME=VALUE",
                     help="a verified claim value (NAME=0.006) or one of your own facts (NAME=own:0.002); repeat")
    cal.add_argument("--show", required=True, help="the number as written in the email, e.g. 70%%")
    args = parser.parse_args(argv)

    gates_home = Path(args.gates_home).expanduser()
    try:
        if not gates_home.is_absolute():
            raise UsageError("--gates-home must be an absolute path")
        settings = load_settings(PLUGIN_ROOT, gates_home.parent)
        if args.command == "calc":
            path = record_calc(gates_home, args.contact, args.formula, parse_calc_inputs(args.input),
                               args.show, settings=settings)
            print(f"recorded: {path}")
            return 0
        crm, _ = read_crm(settings, args.contact)
        path = record_claim(
            gates_home, args.contact, args.url, args.quote, args.value,
            settings=settings, crm=crm, fetch=fetch,
        )
    except EvidenceError as err:
        print(f"refused: {err}", file=sys.stderr)
        return 1
    except UsageError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(f"recorded: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
