"""Turn one demo run into a single HTML page.

It shows each account, the step where it stopped, and the evidence behind
that: the verdict card, the claim file, the draft and every rule that fired.
The local copy (demo/out/report.html) needs no server and no network; the site
copy (pipeline.html) shares the portfolio's stylesheet and nav.
"""
from __future__ import annotations

from html import escape

from gates.funnel import funnel

STAGES = {
    "drafted": ("Drafted", "ok", "Passed both gates. Waiting for a person to send."),
    "gate": ("Blocked by the gate", "stop", "The draft broke a rule."),
    "presend": ("Blocked by pre-send", "stop", "The CRM already holds a reason not to send."),
    "parked": ("Parked", "hold", "No cold email for this kind of account."),
    "queue": ("Human queue", "hold", "One question a person must answer."),
    "none": ("Left as it is", "hold", "No verdict available."),
}
STEPS = ["Qualify", "Draft", "Gate", "Pre-send", "Logged"]
REACHED = {"parked": 1, "queue": 1, "gate": 3, "presend": 4, "drafted": 5, "none": 0}

# Two homes for one page. On the site (howardhwt.github.io/outbound-gates/pipeline.html) it loads the portfolio's
# own /style.css, like the gate page, so tokens, type, the nav pill and the footer are shared. The local copy
# (demo/out/report.html) must open from disk with no network, so it carries BASE: the few portfolio tokens and
# rules this page uses, with system fonts. Everything below BASE uses only the tokens, never its own colors.
BASE = """
:root{--bg:#181818;--surface:#1F1F1F;--surface-2:#272727;--line:#313131;--text:#EDEDED;--text-2:#C8C8C8;
--muted:#9B9B9B;--white:#FFFFFF;--black:#000000;--ease:cubic-bezier(0.22,1,0.36,1);
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--measure:52ch}
*{box-sizing:border-box;margin:0;padding:0}
html{color-scheme:dark}
body{background:var(--bg);color:var(--text);font:16px/24px system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
::selection{background:var(--text);color:var(--bg)}
a:focus-visible{outline:2px solid var(--white);outline-offset:2px}
.wrap{max-width:1200px;margin:0 auto;padding:0 24px}
main.wrap{padding-top:64px;padding-bottom:80px;display:grid;gap:64px;align-content:start}
h1{max-width:680px;font-size:48px;line-height:1;font-weight:600;letter-spacing:-0.03em;text-wrap:balance}
h2{font-size:20px;line-height:28px;font-weight:600;letter-spacing:-0.01em;margin-bottom:16px}
.lede{max-width:var(--measure);margin-top:24px;font-size:18px;line-height:28px;color:var(--muted);text-wrap:pretty}
.fine{font-size:12px;line-height:16px;color:var(--muted)}
.foot{display:flex;flex-wrap:wrap;justify-content:space-between;gap:0 16px;padding-bottom:32px;font-size:12px;line-height:16px;color:var(--muted)}
@media (min-width:768px){h1{font-size:60px}}
"""

CSS = """
#main>*{min-width:0} /* wide tables scroll inside their box instead of widening the page */
.rp-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.rp-note{max-width:var(--measure);color:var(--muted);text-wrap:pretty}
.rp-run{margin-top:16px;font-family:var(--mono);font-size:12px;line-height:16px;color:var(--muted)}
.rp-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px 16px}
.rp-facts>div{padding-top:16px;border-top:1px solid var(--line)}
.rp-facts dt{font-size:30px;line-height:36px;font-weight:600;letter-spacing:-0.02em;font-variant-numeric:tabular-nums}
.rp-facts dd{margin-top:8px;font-size:14px;line-height:20px;color:var(--muted)}
.rp-term{padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--black);
font-family:var(--mono);font-size:14px;line-height:20px;color:var(--text-2);list-style:none;display:grid;gap:4px}
.rp-term li{overflow-wrap:anywhere}
.rp-term li::before{content:"\\203A\\00A0";color:var(--muted)}
.rp-accts{display:grid;gap:8px;margin-top:24px}
.rp-acct{border:1px solid var(--line);border-radius:12px;background:var(--surface);transition:border-color 300ms var(--ease)}
.rp-acct:hover,.rp-acct[open]{border-color:var(--muted)}
.rp-acct summary{list-style:none;cursor:pointer;padding:16px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 16px;align-items:center;border-radius:12px}
.rp-acct summary::-webkit-details-marker{display:none}
.rp-acct summary:focus-visible{outline:2px solid var(--white);outline-offset:2px}
.rp-name{font-weight:600;color:var(--text)}
.rp-name::before{content:"";display:inline-block;width:6px;height:6px;margin:0 12px 2px 2px;border-right:1.5px solid var(--muted);
border-bottom:1.5px solid var(--muted);transform:rotate(-45deg);transition:transform 300ms var(--ease)}
.rp-acct[open] .rp-name::before{transform:rotate(45deg)}
.rp-dom{margin-left:8px;font-family:var(--mono);font-size:12px;line-height:16px;color:var(--muted);overflow-wrap:anywhere}
.rp-why{grid-column:1/-1;font-size:14px;line-height:20px;color:var(--text-2);max-width:var(--measure);text-wrap:pretty}
.rp-tag{font-family:var(--mono);font-size:12px;line-height:16px;border-radius:3px;padding:2px 6px;border:1px solid var(--muted);color:var(--muted);white-space:nowrap}
.rp-tag.stop{background:var(--text);border-color:var(--text);color:var(--bg)}
.rp-tag.ok{border-color:var(--text);color:var(--text)}
.rp-track{grid-column:1/-1;display:flex;flex-wrap:wrap;list-style:none;font-family:var(--mono);font-size:12px;line-height:16px;color:var(--muted)}
.rp-track li{display:flex;align-items:center}
.rp-track li+li::before{content:"";width:12px;height:1px;margin:0 4px;background:var(--line)}
.rp-track span{padding:2px 6px;border-radius:3px;border:1px solid transparent}
.rp-track .done span{color:var(--text)}
.rp-track .fail span{background:var(--text);color:var(--bg)}
.rp-track .done+li::before,.rp-track .done+.fail::before{background:var(--text-2)}
.rp-body{border-top:1px solid var(--line);padding:16px;display:grid;gap:24px}
.rp-body>section{min-width:0}
.rp-body h3{font-size:14px;line-height:20px;font-weight:500;color:var(--text);margin-bottom:8px}
.rp-body h3 .arm{font-weight:400;color:var(--muted)}
/* The draft is an email, so it reads as one: body type on the page colour, not a terminal. */
.rp-body pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:16px;
font:inherit;font-size:14px;line-height:22px;color:var(--text-2);white-space:pre-wrap;overflow-wrap:anywhere}
.rp-scroll{overflow-x:auto}
.rp-scroll:focus-visible{outline:2px solid var(--white);outline-offset:2px}
.rp-body table,.rp-funnel table{border-collapse:collapse;width:100%;font-size:14px;line-height:20px}
.rp-body td,.rp-body th,.rp-funnel td,.rp-funnel th{text-align:left;padding:8px 16px 8px 0;border-bottom:1px solid var(--line);vertical-align:top}
.rp-body th,.rp-funnel th{font-weight:500;color:var(--muted);font-size:12px;line-height:16px}
.rp-body td:first-child{color:var(--text)}
.rp-body td{color:var(--text-2)}
.rp-src{margin-top:4px;font-family:var(--mono);font-size:12px;line-height:16px;color:var(--muted);overflow-wrap:anywhere}
.rp-body .rp-note{font-size:14px;line-height:20px}
.rp-q{margin-top:12px;font-size:14px;line-height:20px;color:var(--text-2);max-width:var(--measure)}
.rp-q b{color:var(--text);font-weight:500}
.rp-find{list-style:none;display:grid;gap:8px}
.rp-find li{display:grid;grid-template-columns:auto minmax(0,1fr);gap:4px 12px;align-items:baseline;font-size:14px;line-height:20px;color:var(--text-2)}
.rp-find code{font-family:var(--mono);font-size:12px;color:var(--muted)}
.rp-find .msg{grid-column:2}
.rp-funnel td,.rp-funnel th{white-space:nowrap}
.rp-funnel table{max-width:720px}
.rp-funnel td:first-child{color:var(--text)}
.rp-funnel td{color:var(--text-2)}
.num{text-align:right!important;padding-right:0!important;padding-left:16px!important}
td.num{font-family:var(--mono);font-variant-numeric:tabular-nums}
.rp-funnel .rp-note{margin-top:16px}
.rp-back{font-family:var(--mono);font-size:14px;line-height:20px;margin-bottom:24px}
.rp-back a{color:var(--muted);text-decoration:none;display:inline-flex;align-items:center;min-height:44px}
.rp-back a:hover{color:var(--text)}
code{font-family:var(--mono)}
@media (max-width:599px){.rp-dom{display:block;margin:2px 0 0 20px}}
@media (min-width:900px){.rp-facts{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr)}
.rp-body{grid-template-columns:minmax(0,1fr) minmax(0,1fr);padding:24px}
.rp-body>.wide{grid-column:1/-1}}
@media (prefers-reduced-motion:reduce){.rp-acct,.rp-name::before{transition:none}}
@media print{.rp-acct{break-inside:avoid}.nav{position:static}}
"""


def _findings(findings, clean: str) -> str:
    if not findings:
        return f'<ul class="rp-find"><li><span class="rp-tag ok">clean</span><span>{escape(clean)}</span></li></ul>'
    rows = []
    for f in findings:
        tag = '<span class="rp-tag stop">blocked</span>' if f.severity == "fail" else '<span class="rp-tag">warning</span>'
        rows.append(f'<li>{tag}<code>{escape(f.rule)}</code><span class="msg">{escape(f.message)}</span></li>')
    return '<ul class="rp-find">' + "".join(rows) + "</ul>"


def _scroll(label: str, table: str) -> str:
    """A table that may be wider than a phone scrolls on its own, and the keyboard can reach it."""
    return f'<div class="rp-scroll" tabindex="0" role="region" aria-label="{escape(label)}"><table>{table}</table></div>'


def _card(verdict: dict) -> str:
    rows = "".join(
        f"<tr><td>{escape(r['field'])}</td><td>{escape(r['value'])}"
        f"<div class='rp-src'>{escape(r['url'])}</div></td></tr>"
        for r in verdict.get("card", [])
    )
    extra = ""
    if verdict.get("question"):
        extra = f"<p class='rp-q'><b>Open question:</b> {escape(verdict['question'])}</p>"
    return _scroll("Verdict card", rows) + extra


def _claims(claims: list) -> str:
    if not claims:
        return "<p class='rp-note'>No claims. The draft states no facts about the reader that need a source.</p>"
    rows = "".join(
        f"<tr><td>{escape(str(c.get('value', '')))}</td><td>{escape(c.get('claim', ''))}"
        f"<div class='rp-src'>{escape(c.get('url', 'no source'))} · checked {escape(c.get('checked', 'never'))}</div></td></tr>"
        for c in claims
    )
    return _scroll("Claim file", f"<tr><th>Value</th><th>Claim and source</th></tr>{rows}")


def _track(stage: str) -> str:
    reached = REACHED[stage]
    out = []
    for i, step in enumerate(STEPS, 1):
        cls = ""
        if i < reached or (i == reached and stage in ("drafted", "parked", "queue")):
            cls = "done"
        elif i == reached:
            cls = "fail"
        out.append(f'<li class="{cls}"><span>{step}</span></li>' if cls else f"<li><span>{step}</span></li>")
    return '<ol class="rp-track" aria-label="Steps reached">' + "".join(out) + "</ol>"


def _account(record: dict) -> str:
    label, cls, _ = STAGES[record["stage"]]
    verdict = record["verdict"] or {}
    arm = verdict.get("arm", "none")
    sections = []
    if verdict:
        sections.append(f"<section><h3>Verdict card <span class='arm'>· arm: {escape(arm)}</span></h3>{_card(verdict)}</section>")
    if record["draft"] is not None:
        sections.append(f"<section><h3>The draft</h3><pre>{escape(record['draft'])}</pre></section>")
        sections.append(f"<section><h3>Claim file</h3>{_claims(record['claims'])}</section>")
        sections.append(
            "<section><h3>Gate checker</h3>"
            + _findings(record["gate"], "Passed. No rule fired.") + "</section>"
        )
    if record["presend"] is not None:
        sections.append(
            "<section><h3>Pre-send check</h3>"
            + _findings(record["presend"], "Clean. Nothing in the CRM blocks this send.") + "</section>"
        )
    if len(sections) % 2 and len(sections) > 1:
        sections[-1] = sections[-1].replace("<section>", '<section class="wide">', 1)
    return (
        f'<details class="rp-acct"><summary><div><span class="rp-name">{escape(record["name"])}</span>'
        f'<span class="rp-dom">{escape(record["domain"])}</span></div>'
        f'<span class="rp-tag {cls}">{label}</span>'
        f'<p class="rp-why">{escape(record["outcome"])}</p>{_track(record["stage"])}</summary>'
        f'<div class="rp-body">{"".join(sections)}</div></details>'
    )


def _funnel_table(before: dict, after: dict) -> str:
    fb, fa = funnel(before), funnel(after)
    rows = ""
    for segment in sorted(set(fb["contacted"]) | set(fa["contacted"])):
        b, a = fb["contacted"].get(segment, 0), fa["contacted"].get(segment, 0)
        rb = fb["replied"].get(segment, 0)
        rate = f"{rb / b:.0%}" if b else "n/a"
        rows += (
            f"<tr><td>{escape(segment)}</td><td class='num'>{b}</td><td class='num'>{a}</td>"
            f"<td class='num'>{rb}</td><td class='num'>{rate}</td></tr>"
        )
    return _scroll("Funnel by segment",
                   "<tr><th>Segment</th><th class='num'>Contacted before</th><th class='num'>Contacted after</th>"
                   f"<th class='num'>Replied</th><th class='num'>Reply rate</th></tr>{rows}")


SITE_HEAD = """<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/style.css">"""


def build_report(before: dict, after: dict, records: list, source_lines: list, confirmed: list,
                 nav: str | None = None) -> str:
    """The run as one page. Without nav it's the offline copy; with nav (the site's links) it joins the portfolio."""
    count = lambda *stages: sum(r["stage"] in stages for r in records)
    figures = [
        ("Accounts worked", len(records)),
        ("Drafted", count("drafted")),
        ("Blocked", count("gate", "presend")),
        ("Parked or queued", count("parked", "queue")),
        ("Emails sent", 0),
    ]
    facts = "".join(f"<div><dt>{v}</dt><dd>{escape(k)}</dd></div>" for k, v in figures)
    order = {"drafted": 0, "gate": 1, "presend": 1, "queue": 2, "parked": 2, "none": 3}
    accounts = "".join(_account(r) for r in sorted(records, key=lambda r: order[r["stage"]]))
    source = "".join(f"<li>{escape(line.strip())}</li>" for line in source_lines)
    if confirmed:
        note = "Contacted moved only for the sends a person confirmed: " + ", ".join(confirmed) + "."
    else:
        note = ("Contacted did not move. A draft sets no contact date and no status. "
                "Both change only when a person confirms the send.")
    if nav is None:
        head, style, top = "", BASE + CSS, ""
        foot = ('<footer class="wrap foot"><span>Generated by <code>python -m demo.run_demo</code>. '
                "Nothing in this project sends email.</span></footer>")
    else:
        head, style = SITE_HEAD, CSS
        top = f'<a class="skip" href="#main">Skip to content</a>\n<nav class="nav" aria-label="Main">{nav}</nav>\n'
        foot = ('<footer class="wrap foot"><span>© 2026 Howard Tam · generated from a real run of the demo pipeline'
                '</span><nav aria-label="Elsewhere"><a href="https://www.linkedin.com/in/howardhwt">LinkedIn</a>'
                '<a href="https://github.com/howardhwt">GitHub</a>'
                '<a href="https://github.com/howardhwt/outbound-gates">outbound-gates repo</a></nav></footer>')
    back = '<p class="rp-back"><a href="./">← The gate, step by step</a></p>' if nav is not None else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipeline demo · outbound-gates</title>
<meta name="description" content="One run of the outbound-gates demo pipeline on seven fictional accounts: what was drafted, what was blocked and why.">
{head}<style>{style}</style></head><body>
{top}<main id="main" class="wrap">
<header>{back}<h1>One run, account by account</h1>
<p class="lede">Seven fictional accounts go through source, qualify, draft, the send gate and the pre-send check.
Research and drafts come from fixture files; the two gates are the real code.</p>
<p class="rp-run">Run date {escape(before.get("today", ""))}</p></header>
<section aria-labelledby="fig-h"><h2 id="fig-h" class="rp-sr">What happened</h2><dl class="rp-facts">{facts}</dl></section>
<section aria-labelledby="src-h"><h2 id="src-h">Sourcing</h2><ul class="rp-term">{source}</ul></section>
<section aria-labelledby="acct-h"><h2 id="acct-h">Accounts</h2><p class="rp-note">Open an account to see its verdict
card, its draft, and each rule that passed or blocked it.</p><div class="rp-accts">{accounts}</div></section>
<section class="rp-funnel" aria-labelledby="fun-h"><h2 id="fun-h">Funnel</h2>{_funnel_table(before, after)}<p class="rp-note">{escape(note)}</p></section>
</main>
{foot}
</body></html>
"""
