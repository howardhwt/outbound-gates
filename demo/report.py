"""Turn one demo run into a single HTML page.

The page needs no server and no network. It shows each account, the step
where it stopped, and the evidence behind that: the verdict card, the claim
file, the draft and every rule that fired.
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

CSS = """
:root{--bg:#f6f7f5;--panel:#fff;--fg:#1b2420;--muted:#5c6964;--line:#dce2df;
--ok:#1d7448;--ok-bg:#e3f3ea;--stop:#a3352a;--stop-bg:#fbe9e6;--hold:#8a6100;--hold-bg:#fbf1d8;
--code:#f0f3f1;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#111614;--panel:#19201d;--fg:#e5ebe8;--muted:#98a6a1;
--line:#2c3733;--ok:#63d197;--ok-bg:#173527;--stop:#f08b7f;--stop-bg:#3d1f1b;--hold:#e3b34f;
--hold-bg:#3a2f14;--code:#202925;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:960px;margin:0 auto;padding:32px 16px 64px;display:flex;flex-direction:column;gap:28px}
h1{font-size:26px;margin:0 0 6px}h2{font-size:17px;margin:0 0 10px}
p{margin:0}.muted{color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:1px;background:var(--line);
border:1px solid var(--line);border-radius:8px;overflow:hidden}
.tiles div{background:var(--panel);padding:12px 14px}
.tiles b{display:block;font:500 24px var(--mono)}
.tiles span{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
.acct{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin-bottom:10px}
summary{cursor:pointer;padding:14px 16px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 14px;align-items:center}
summary:focus-visible{outline:2px solid var(--ok);outline-offset:2px}
.name{font-weight:600}.name::before{content:"\\25B8\\00A0";color:var(--muted)}details[open] .name::before{content:"\\25BE\\00A0"}summary{list-style:none}summary::-webkit-details-marker{display:none}.dom{font:13px var(--mono);color:var(--muted)}
.pill{font-size:12px;font-weight:600;padding:3px 10px;border-radius:99px;white-space:nowrap}
.ok{color:var(--ok);background:var(--ok-bg)}.stop{color:var(--stop);background:var(--stop-bg)}
.hold{color:var(--hold);background:var(--hold-bg)}
.why{grid-column:1/-1;font-size:14px;color:var(--muted)}
.track{grid-column:1/-1;display:flex;gap:4px;flex-wrap:wrap;font-size:12px}
.track span{padding:2px 8px;border-radius:4px;background:var(--code);color:var(--muted)}
.track span.done{color:var(--ok);background:var(--ok-bg)}.track span.fail{color:var(--stop);background:var(--stop-bg)}
.body{border-top:1px solid var(--line);padding:16px;display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}
.body>section{min-width:0}
h3{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);margin:0 0 6px}
pre{margin:0;background:var(--code);padding:12px;border-radius:6px;font:13px/1.5 var(--mono);white-space:pre-wrap;overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%;font-size:14px}
td,th{text-align:left;padding:5px 8px 5px 0;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:500;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.num{text-align:right;font-family:var(--mono)}
ul.find{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
ul.find li{padding:7px 10px;border-radius:6px;font-size:14px}
code{font:13px var(--mono)}.src{overflow-wrap:anywhere;font:12px var(--mono);color:var(--muted)}
.wrap{overflow-x:auto}footer{font-size:13px;color:var(--muted)}
"""


def _findings(findings, clean: str) -> str:
    if not findings:
        return f'<ul class="find"><li class="ok">{escape(clean)}</li></ul>'
    rows = []
    for f in findings:
        cls = "stop" if f.severity == "fail" else "hold"
        label = "Blocked" if f.severity == "fail" else "Warning"
        rows.append(
            f'<li class="{cls}"><b>{label}</b> <code>{escape(f.rule)}</code> {escape(f.message)}</li>'
        )
    return '<ul class="find">' + "".join(rows) + "</ul>"


def _card(verdict: dict) -> str:
    rows = "".join(
        f"<tr><td>{escape(r['field'])}</td><td>{escape(r['value'])}"
        f"<div class='src'>{escape(r['url'])}</div></td></tr>"
        for r in verdict.get("card", [])
    )
    extra = ""
    if verdict.get("question"):
        extra = f"<p style='margin-top:8px'><b>Open question:</b> {escape(verdict['question'])}</p>"
    return f"<div class='wrap'><table>{rows}</table></div>{extra}"


def _claims(claims: list) -> str:
    if not claims:
        return "<p class='muted'>No claims. The draft states no facts about the reader that need a source.</p>"
    rows = "".join(
        f"<tr><td>{escape(str(c.get('value', '')))}</td><td>{escape(c.get('claim', ''))}"
        f"<div class='src'>{escape(c.get('url', 'no source'))} · checked {escape(c.get('checked', 'never'))}</div></td></tr>"
        for c in claims
    )
    return f"<div class='wrap'><table><tr><th>Value</th><th>Claim and source</th></tr>{rows}</table></div>"


def _track(stage: str) -> str:
    reached = REACHED[stage]
    out = []
    for i, step in enumerate(STEPS, 1):
        cls = ""
        if i < reached or (i == reached and stage in ("drafted", "parked", "queue")):
            cls = "done"
        elif i == reached:
            cls = "fail"
        out.append(f'<span class="{cls}">{step}</span>')
    return '<div class="track">' + "".join(out) + "</div>"


def _account(record: dict) -> str:
    label, cls, _ = STAGES[record["stage"]]
    verdict = record["verdict"] or {}
    arm = verdict.get("arm", "none")
    sections = []
    if verdict:
        sections.append(f"<section><h3>Verdict card · arm: {escape(arm)}</h3>{_card(verdict)}</section>")
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
    return (
        f'<details class="acct"><summary><div><span class="name">{escape(record["name"])}</span> '
        f'<span class="dom">{escape(record["domain"])}</span></div>'
        f'<span class="pill {cls}">{label}</span>'
        f'<div class="why">{escape(record["outcome"])}</div>{_track(record["stage"])}</summary>'
        f'<div class="body">{"".join(sections)}</div></details>'
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
    return (
        "<div class='wrap'><table><tr><th>Segment</th><th class='num'>Contacted before</th>"
        "<th class='num'>Contacted after</th><th class='num'>Replied</th>"
        f"<th class='num'>Reply rate</th></tr>{rows}</table></div>"
    )


def build_report(before: dict, after: dict, records: list, source_lines: list, confirmed: list) -> str:
    count = lambda *stages: sum(r["stage"] in stages for r in records)
    tiles = [
        ("Accounts worked", len(records)),
        ("Drafted", count("drafted")),
        ("Blocked", count("gate", "presend")),
        ("Parked or queued", count("parked", "queue")),
        ("Emails sent", 0),
    ]
    tile_html = "".join(f"<div><b>{v}</b><span>{escape(k)}</span></div>" for k, v in tiles)
    order = {"drafted": 0, "gate": 1, "presend": 1, "queue": 2, "parked": 2, "none": 3}
    accounts = "".join(_account(r) for r in sorted(records, key=lambda r: order[r["stage"]]))
    source = "".join(f"<li><code>{escape(line.strip())}</code></li>" for line in source_lines)
    if confirmed:
        note = "Contacted moved only for the sends a person confirmed: " + ", ".join(confirmed) + "."
    else:
        note = ("Contacted did not move. A draft sets no contact date and no status. "
                "Both change only when a person confirms the send.")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>outbound-gates demo run</title><style>{CSS}</style></head><body><main>
<header><h1>outbound-gates: one run, account by account</h1>
<p class="muted">Run date {escape(before.get("today", ""))}. Every company here is fictional.
Research and drafts come from fixture files. The two gates are the real code.</p></header>
<div class="tiles">{tile_html}</div>
<section><h2>Sourcing</h2><ul>{source}</ul></section>
<section><h2>Accounts</h2><p class="muted" style="margin-bottom:12px">Open an account to see its verdict card,
its draft, and each rule that passed or blocked it.</p>{accounts}</section>
<section><h2>Funnel</h2>{_funnel_table(before, after)}<p class="muted" style="margin-top:10px">{escape(note)}</p></section>
<footer>Generated by <code>python -m demo.run_demo</code>. Nothing in this project sends email.</footer>
</main></body></html>
"""
