"""Build the GitHub site and the README clip from real runs of the gate.

    python -m demo.site [--out _site] [--clip docs/catch.svg]

Nothing on the site is written by hand. This copies the quickstart project
(one invented company, its pricing page, one draft), runs the real gate on it
(blocked for an unsourced number, the source recorded, passed), and turns that
exact output into:

  _site/index.html      the page that replays the run, step by step
  _site/pipeline.html   the full pipeline demo report (demo/report.py)
  docs/catch.svg        the README clip, an animated terminal of the same run

Local paths are replaced (the plugin folder by $OG, the temporary project by
~/outbound-gates-try) so nothing from the machine that built it leaks out.
tests/test_site.py rebuilds the clip and checks it matches the committed one.
Stdlib only, offline.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

from gates import quickstart

ROOT = Path(__file__).resolve().parents[1]
SHOWN_PROJECT = "~/outbound-gates-try"
REPO = "https://github.com/howardhwt/outbound-gates"


# --- the real run --------------------------------------------------------------------

def _clean(text: str, project: Path) -> str:
    for path in sorted({str(project), str(project.resolve())}, key=len, reverse=True):
        text = text.replace(path, SHOWN_PROJECT)
    text = text.replace(f"PYTHONPATH={ROOT} ", "").replace(f"PYTHONPATH='{ROOT}' ", "")
    return text.replace(str(ROOT), "$OG")


def run_catch() -> dict:
    """Run blocked -> record -> passed on the quickstart company and return what happened."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        project = quickstart.copy_to(tmp / "outbound-gates-try")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp / "home")}
        (tmp / "home").mkdir()
        steps = []
        labels = [("check", "Check the draft"), ("record", "Record the source"), ("check", "Check again")]
        for (kind, label), command in zip(labels, quickstart.next_commands(project)):
            done = subprocess.run(["sh", "-c", command], cwd=tmp, env=env, capture_output=True, text=True)
            steps.append({"kind": kind, "label": label, "command": _clean(command, project),
                          "output": _clean((done.stdout + done.stderr).strip(), project), "exit": done.returncode})
        draft = (project / "draft.txt").read_text(encoding="utf-8")
        page = (project / "pages" / "quickstart-co.example" / "pricing.html").read_text(encoding="utf-8")
    sentence = re.sub(r"<[^>]+>", "", re.search(r"<p>(.*?)</p>", page, re.S).group(1)).strip()
    return {"draft": draft, "to": quickstart.CONTACT, "url": quickstart.URL, "page_sentence": sentence,
            "steps": steps}


def _short_command(step: dict, run: dict) -> str:
    """The og form of each command, as a person would type it."""
    if step["kind"] == "record":
        return (f"og record --contact {run['to']} --url {run['url']} "
                f"--quote '{quickstart.QUOTE}' --value '$0.006'")
    return f"og check draft.txt --to {run['to']}"


def _key_lines(step: dict) -> list[str]:
    lines = step["output"].splitlines()
    if step["kind"] == "record":
        return ["recorded: .gates/drafts/quickstart-co.example/…/claims/…json (page stored with its hash)"]
    keep = [l for l in lines if l.startswith("- claims") or l.strip() in ("BLOCKED", "PASS")]
    return keep or lines[-1:]


# --- the README clip: an animated terminal, plain SVG --------------------------------------

def build_clip(run: dict) -> str:
    rows: list[tuple[str, str]] = []
    for step in run["steps"]:
        rows.append(("cmd", "$ " + _short_command(step, run)))
        for line in _key_lines(step):
            kind = "bad" if ("BLOCKED" in line or "no sourced" in line) else "ok" if line == "PASS" else "out"
            rows.append((kind, "  " + line))
        rows.append(("gap", ""))
    rows = rows[:-1]
    wrapped: list[tuple[str, str]] = []
    for kind, text in rows:  # wrap long command lines at 78 characters
        while len(text) > 78:
            cut = text.rfind(" ", 0, 78)
            cut = cut if cut > 20 else 78
            wrapped.append((kind, text[:cut]))
            text = "    " + text[cut:].lstrip()
        wrapped.append((kind, text))
    line_h, top, width = 22, 52, 860
    height = top + line_h * len(wrapped) + 24
    cycle = 14.0
    styles, texts = [], []
    visible = [i for i, (k, _) in enumerate(wrapped) if k != "gap"]
    for n, i in enumerate(visible):
        start = round(4 + 70 * n / max(len(visible) - 1, 1), 2)
        styles.append(f"@keyframes l{i}{{0%,{start}%{{opacity:0}}{min(start + 2, 99)}%,96%{{opacity:1}}100%{{opacity:0}}}}"
                      f".l{i}{{animation:l{i} {cycle}s linear infinite}}")
    colours = {"cmd": "#e8eef2", "out": "#9fb0ba", "bad": "#ff8a7a", "ok": "#7ee2a8"}
    for i, (kind, text) in enumerate(wrapped):
        if kind == "gap":
            continue
        weight = ' font-weight="700"' if kind in ("bad", "ok") else ""
        texts.append(f'<text class="l{i}" x="20" y="{top + line_h * i}" fill="{colours[kind]}"{weight}>'
                     f"{escape(text)}</text>")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="The send gate blocks a draft for an unsourced '
            f'number, the source is recorded from the company page, and the same draft passes">\n'
            f"<style>text{{font:14px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre}}"
            f"{''.join(styles)}</style>\n"
            f'<rect width="{width}" height="{height}" rx="10" fill="#151b1f"/>\n'
            f'<circle cx="22" cy="20" r="6" fill="#ff5f57"/><circle cx="42" cy="20" r="6" fill="#febc2e"/>'
            f'<circle cx="62" cy="20" r="6" fill="#28c840"/>\n'
            f'<text x="90" y="25" fill="#6c7a83">outbound-gates: a real run on the bundled demo company</text>\n'
            + "\n".join(texts) + "\n</svg>\n")


# --- the page ----------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>outbound-gates</title>
<meta name="description" content="A send gate for AI-written cold email: every number needs a source from the company's own site, and a person sends.">
<style>
:root{--bg:#f6f7f5;--panel:#fff;--fg:#1b2420;--muted:#5c6964;--line:#dce2df;--term:#151b1f;--termfg:#e8eef2;
--bad:#c0392b;--ok:#1e8449;--accent:#2b6cb0}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1417;--panel:#161d21;--fg:#e6ecef;
--muted:#9aa8b0;--line:#26323a;--bad:#ff8a7a;--ok:#7ee2a8;--accent:#7fb3ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:860px;margin:0 auto;padding:40px 16px 64px}
h1{font-size:30px;margin:0 0 6px}h2{font-size:20px;margin:40px 0 10px}p{margin:8px 0}
.muted{color:var(--muted)}a{color:var(--accent)}
.lead{font-size:18px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin:14px 0}
pre{margin:0;white-space:pre-wrap;word-break:break-word;font:13.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.term{background:var(--term);color:var(--termfg);border-radius:10px;padding:14px 16px;margin:10px 0}
.term .cmd{color:#e8eef2}.term .out{color:#9fb0ba}
.mark{background:#fff3c4;color:#1b2420;border-radius:3px;padding:0 2px}
.step{display:none}.step.shown{display:block}
.badge{display:inline-block;font-weight:700;border-radius:6px;padding:2px 8px;font-size:13px}
.badge.bad{background:var(--bad);color:#fff}.badge.ok{background:var(--ok);color:#fff}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
button{font:inherit;border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:8px;
padding:8px 14px;cursor:pointer}button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
ul{padding-left:20px}code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:14px}
</style></head><body><main>
<h1>outbound-gates</h1>
<p class="lead">AI-written cold email, without anything untrue in it.</p>
<p>Outbound goes wrong around the email, not in it: the wrong company, a number nobody checked, the same person
emailed twice, a CRM that no longer says what happened. These gates hold that line for an agent that drafts in
your Gmail. <strong>Every number needs a source on the company's own site, and a person sends.</strong></p>

<h2>Watch the gate catch a number</h2>
<p class="muted">A real run of the gate on the bundled demo company. Nothing below is written by hand: it is the
gate's own output, rebuilt from the code on every change.</p>
<div class="panel"><div class="muted" style="font-size:13px">The agent's draft to __TO__</div><pre>__DRAFT__</pre></div>
<div class="controls"><button class="primary" id="next">Run the next step</button><button id="reset">Start over</button></div>
__STEPS__
<div class="step panel" id="done"><p><span class="badge ok">PASS</span> The number now rests on the exact sentence from
the company's own page: <q>__SENTENCE__</q> (<code>__URL__</code>). The gate stored the page with its hash and checks
it again on every draft.</p></div>

<h2>Try it</h2>
<ul>
<li><strong>On your own prospect, in five minutes:</strong> install the plugin in Claude Code
(<code>/plugin marketplace add howardhwt/outbound-gates</code>, then <code>/plugin install outbound-gates@outbound-gates</code>)
and type <code>/outbound-gates:start</code>. It makes a new folder, your agent writes its usual email, and the gate
refuses any number it can't back up. Nothing goes to Gmail.</li>
<li><strong>The demo above, on your machine:</strong> <code>/outbound-gates:try</code>.</li>
<li><strong>The whole pipeline</strong> (source, qualify, draft, gate, pre-send, CRM):
<a href="pipeline.html">the demo report</a>.</li>
</ul>
<p class="muted">Code, docs and the design notes: <a href="__REPO__">github.com/howardhwt/outbound-gates</a>.</p>
</main>
<script>
var steps=[].slice.call(document.querySelectorAll('.step')),i=0;
document.getElementById('next').onclick=function(){if(i<steps.length){steps[i].classList.add('shown');
steps[i].scrollIntoView({behavior:'smooth',block:'nearest'});i++;}};
document.getElementById('reset').onclick=function(){steps.forEach(function(s){s.classList.remove('shown')});i=0;};
</script></body></html>
"""


def build_page(run: dict) -> str:
    blocks = []
    for step in run["steps"]:
        badge = ('<span class="badge bad">BLOCKED</span>' if step["exit"] == 1 else
                 '<span class="badge ok">PASS</span>' if "PASS" in step["output"] else "")
        blocks.append(
            f'<div class="step"><p><strong>{escape(step["label"])}</strong> {badge}</p>'
            f'<div class="term"><pre><span class="cmd">$ {escape(_short_command(step, run))}</span>\n'
            f'<span class="out">{escape(step["output"])}</span></pre></div></div>')
    draft = escape(run["draft"]).replace("$0.006", '<span class="mark">$0.006</span>')
    return (PAGE.replace("__STEPS__", "\n".join(blocks)).replace("__DRAFT__", draft)
            .replace("__TO__", escape(run["to"])).replace("__SENTENCE__", escape(run["page_sentence"]))
            .replace("__URL__", escape(run["url"])).replace("__REPO__", REPO))


def build_pipeline() -> str:
    from gates.checker import load_config
    from demo.report import build_report
    from demo.run_demo import CONFIG, load_crm, run_detailed

    demo = ROOT / "demo"
    crm = load_crm(demo / "mock_crm.json")
    candidates = json.loads((demo / "candidates.json").read_text(encoding="utf-8"))
    after, _, records, source_lines = run_detailed(crm, candidates, load_config(CONFIG))
    return build_report(crm, after, records, source_lines, [])


def build(out: Path, clip: Path | None) -> dict:
    run = run_catch()
    kinds = [(s["kind"], s["exit"]) for s in run["steps"]]
    if kinds != [("check", 1), ("record", 0), ("check", 0)]:
        raise SystemExit(f"the gate didn't behave as the page says (blocked, recorded, passed): {kinds}")
    out.mkdir(parents=True, exist_ok=True)
    svg = build_clip(run)
    (out / "index.html").write_text(build_page(run), encoding="utf-8")
    (out / "catch.svg").write_text(svg, encoding="utf-8")
    (out / "pipeline.html").write_text(build_pipeline(), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    if clip:
        clip.write_text(svg, encoding="utf-8")
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the site and README clip from real gate runs.")
    parser.add_argument("--out", default="_site", help="where to write the site (default: _site)")
    parser.add_argument("--clip", help="also write the README clip here, e.g. docs/catch.svg")
    args = parser.parse_args(argv)
    build(Path(args.out), Path(args.clip) if args.clip else None)
    print(f"site written to {args.out}" + (f"; clip written to {args.clip}" if args.clip else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
