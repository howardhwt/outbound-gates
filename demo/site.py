"""Build the GitHub site and the README clip from real runs of the gate.

    python -m demo.site [--out _site] [--clip docs/catch.svg]

Nothing on the site is written by hand. This copies the quickstart project
(one invented company, its pricing page, one draft), runs the real gate on it
(blocked for an unsourced number, the source recorded, passed), and turns that
exact output into:

  _site/index.html      the page that replays the run, step by step
  _site/pipeline.html   the full pipeline demo report (demo/report.py)
  _site/live.zip        the gate and the demo company, for "Run it live" (demo/live.py, in Pyodide)
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
    """The gate form of each command, as a person would type it."""
    if step["kind"] == "record":
        return (f"gate record --contact {run['to']} --url {run['url']} "
                f"--quote '{quickstart.QUOTE}' --value '$0.006'")
    return f"gate check draft.txt --to {run['to']}"


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
        rows.append(("cmd", "› " + _short_command(step, run)))
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
    # The portfolio's terminal (DESIGN.md, "Terminal"): Ink panel, Hairline edge, commands in Chalk after a Smoke
    # "›", output in Smoke, the problem and the verdict in Chalk. State is brightness, never red or green.
    colours = {"cmd": "#EDEDED", "out": "#9B9B9B", "bad": "#EDEDED", "ok": "#EDEDED"}
    for i, (kind, text) in enumerate(wrapped):
        if kind == "gap":
            continue
        weight = ' font-weight="600"' if kind in ("bad", "ok") else ""
        texts.append(f'<text class="l{i}" x="20" y="{top + line_h * i}" fill="{colours[kind]}"{weight}>'
                     f"{escape(text)}</text>")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="The send gate blocks a draft for an unsourced '
            f'number, the source is recorded from the company page, and the same draft passes">\n'
            f"<style>text{{font:14px 'Geist Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre}}"
            f"{''.join(styles)}</style>\n"
            f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" fill="#000000" stroke="#313131"/>\n'
            f'<text x="20" y="28" fill="#9B9B9B" style="font-size:12px">outbound-gates · a real run on the bundled demo company</text>\n'
            + "\n".join(texts) + "\n</svg>\n")


# --- the page ----------------------------------------------------------------------------

PORTFOLIO = "https://howardhwt.github.io"
# Pyodide (Python on WebAssembly) runs the real gate in the visitor's browser; loaded only on "Run it live".
PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/"
# The site lives on the portfolio's origin (howardhwt.github.io/outbound-gates), so it loads the portfolio's own
# stylesheet and nav classes rather than a copy of them: one site, one set of tokens. Only the pieces this page
# adds (the draft, the steps, the commands) are styled here, and only with the portfolio's tokens.
NAV = [("/", "Howard Tam"), ("/work/", "Work"), ("/outbound-gates/", "outbound-gates"), (REPO, "GitHub")]

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>outbound-gates · Howard Tam</title>
<meta name="description" content="A send gate for AI-written cold email: every number needs a source from the company's own site, and a person sends.">
<meta property="og:title" content="outbound-gates">
<meta property="og:description" content="Your agent can't put an unsourced number in a cold email, and it can't send.">
<meta property="og:url" content="__PORTFOLIO__/outbound-gates/">
<link rel="canonical" href="__PORTFOLIO__/outbound-gates/">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<script>document.documentElement.classList.add('og-js');</script>
<link rel="stylesheet" href="/style.css">
<style>
.og-top{display:grid;gap:64px}
.og-hero h1{max-width:760px}
.og-acts{display:flex;flex-wrap:wrap;align-items:center;gap:8px 24px;margin-top:24px}
.og-acts .jump{margin-top:0}
.og-sec h2{margin-bottom:8px}
.og-sec>.fine,.og-sec>.og-note{max-width:var(--measure)}
.og-note{color:var(--muted);text-wrap:pretty}
.og-catch{display:grid;gap:24px;margin-top:24px;align-items:start}
.og-draft{border:1px solid var(--line);border-radius:12px;background:var(--surface);padding:24px}
.og-draft .to{font-family:var(--mono);font-size:12px;line-height:16px;color:var(--muted);margin-bottom:16px}
.og-draft p{color:var(--text-2);max-width:var(--measure)}
.og-draft p+p{margin-top:12px}
.og-num{background:var(--text);color:var(--bg);border-radius:3px;padding:0 4px;font-variant-numeric:tabular-nums}
.og-steps,.og-run{display:grid;gap:24px}
.og-step .head{display:flex;align-items:center;gap:12px;font-size:14px;line-height:20px;font-weight:500;color:var(--text)}
.og-step .term{margin:8px 0 0}
.og-after{margin-top:16px;max-width:var(--measure)}
.og-step pre{margin:0;font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}
.og-step .res{display:block;padding-left:16px;color:var(--muted)}
.og-step .hit{color:var(--text)}
.og-step .verdict{color:var(--text);font-weight:500}
.og-tag{font-family:var(--mono);font-size:12px;line-height:16px;border-radius:3px;padding:2px 6px;border:1px solid var(--muted);color:var(--muted)}
.og-tag.stop{background:var(--text);border-color:var(--text);color:var(--bg)}
.og-tag.pass{border-color:var(--text);color:var(--text)}
.og-ctl{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px}
.og-ctl button{font:inherit;font-weight:500;min-height:44px;padding:8px 12px;border-radius:8px;cursor:pointer;
border:1px solid var(--line);background:transparent;color:var(--text);
transition:background-color 300ms var(--ease),border-color 300ms var(--ease),transform 500ms var(--ease)}
.og-ctl button:hover{background:var(--surface);border-color:var(--muted)}
.og-ctl button.primary{background:var(--text);border-color:var(--text);color:var(--bg)}
.og-ctl button.primary:hover{background:var(--white)}
.og-ctl button:active{transform:scale(0.98)}
.og-ctl button:disabled{opacity:.6;cursor:default;transform:none}
.og-ctl button[hidden]{display:none}
.og-step,.og-ctl{scroll-margin:96px 0 24px}
.og-ctl button:focus-visible,.og-copy:focus-visible{outline:2px solid var(--white);outline-offset:2px}
.og-ctl{display:none}
.og-hint{flex-basis:100%;max-width:var(--measure);text-wrap:pretty}
.og-js .og-ctl{display:flex}
.og-js .og-step:not(.shown){display:none}
.og-js .og-step.first{animation:none}
.og-js .og-step.shown{animation:og-rise 800ms var(--e) both}
@keyframes og-rise{from{opacity:0;transform:translateY(6px);filter:blur(2px)}}
.og-live{display:none;margin-top:8px;padding-top:24px;border-top:1px solid var(--line)}
.og-js .og-live{display:grid;gap:16px}
.og-js .og-ctl[hidden]{display:none}
.og-live-run{display:grid;gap:12px}.og-live-run[hidden]{display:none}
.og-live-label{font-size:14px;line-height:20px;font-weight:500;color:var(--text)}
#og-live-draft{width:100%;min-height:200px;resize:vertical;padding:16px;border:1px solid var(--line);border-radius:12px;
background:var(--surface);color:var(--text-2);font:inherit;font-size:16px;line-height:24px}
#og-live-draft:focus-visible{outline:2px solid var(--white);outline-offset:2px}
.og-live-out{margin:0}.og-live-out pre{margin:0;font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}
.og-live-out .og-tag{display:inline-block;margin-bottom:8px}.og-live,.og-live-run>*{min-width:0}.og-live-out:has(pre:empty){display:none}
.og-try{list-style:none;margin-top:24px;counter-reset:og}
.og-try li{display:grid;gap:8px;align-items:start;padding:16px 0;border-top:1px solid var(--line);counter-increment:og}
.og-try .what::before{content:counter(og);display:inline-block;min-width:24px;font-family:var(--mono);font-size:12px;color:var(--muted)}
.og-goal .og-cmd{border-color:var(--text)}
.og-also{margin-top:24px}
.og-also a{color:var(--text);text-underline-offset:4px;text-decoration-color:var(--line)}
.og-also a:hover{text-decoration-color:var(--text)}
.og-try li:last-child{border-bottom:1px solid var(--line)}
.og-try .what{font-weight:500;color:var(--text)}
.og-try .why{font-size:14px;line-height:20px;color:var(--muted);max-width:var(--measure);padding-left:24px}
.og-try .cmds{display:grid;gap:8px;min-width:0}.og-try li>*{min-width:0}
.og-cmd{display:flex;min-width:0;max-width:100%;align-items:center;justify-content:space-between;gap:12px;min-height:44px;padding:4px 4px 4px 12px;
border:1px solid var(--line);border-radius:8px;background:var(--black);font-family:var(--mono);font-size:14px;line-height:20px}
.og-cmd code .w{white-space:nowrap}
.og-cmd code{font:inherit;color:var(--text);min-width:0;padding:8px 0;overflow-wrap:break-word}
.og-copy{flex:none;position:relative;font-family:var(--mono);font-size:12px;line-height:16px;min-height:36px;padding:0 10px;border-radius:6px;
border:1px solid var(--line);background:transparent;color:var(--muted);cursor:pointer;transition:color 300ms var(--ease),border-color 300ms var(--ease)}
.og-copy:hover{color:var(--text);border-color:var(--muted)}
.og-copy::after{content:"";position:absolute;inset:-4px}
.og-more summary{list-style:none;cursor:pointer;padding-left:16px;color:var(--muted);text-decoration:underline;text-decoration-color:var(--line);text-underline-offset:4px;min-height:24px}
.og-more summary::-webkit-details-marker{display:none}.og-more summary:hover{color:var(--text)}
.og-more summary:focus-visible{outline:2px solid var(--white);outline-offset:2px}
.og-more[open] summary{margin-bottom:4px}
@media (min-width:768px){h1{font-size:60px}}
@media (min-width:900px){.og-catch{grid-template-columns:5fr 7fr}.og-draft{position:sticky;top:96px}
.og-try li{grid-template-columns:4fr 7fr;grid-template-rows:auto 1fr;column-gap:24px;row-gap:4px}.og-try .what,.og-try .why{grid-column:1}.og-try .cmds{grid-column:2;grid-row:1 / span 2;align-self:start}}
/* From 1100px the catch moves up beside the claim, like the pipeline on the portfolio home. */
@media (min-width:1100px){.og-top{grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:48px;align-items:start}
.og-hero{position:sticky;top:128px}.og-catch{grid-template-columns:1fr}.og-draft{position:static}.og-watch{display:none}}
@media (prefers-reduced-motion:reduce){.og-js .og-step.shown{animation:none}.og-ctl button{transition:none}}
@media print{.og-live,.og-ctl,.og-copy,.og-more summary{display:none}.og-js .og-step:not(.shown){display:block}.og-draft{position:static}}
</style></head><body>
<a class="skip" href="#main">Skip to content</a>
<nav class="nav" aria-label="Main">__NAV__</nav>
<main id="main" class="wrap">
<div class="og-top">
<header class="intro og-hero">
<h1>Your agent can't put an unsourced number in a cold email.</h1>
<p class="lede">outbound-gates is a Claude Code plugin for agents that draft outbound. Every number in a draft needs
the exact sentence from the company's own site, a calculation the gate recomputes, or one of your own facts. Send,
reply and forward are blocked; a person sends.</p>
<p class="og-acts"><a class="jump ask" href="#try-h">Try it on your prospect ↓</a><a class="jump og-watch" href="#catch-h">Watch it catch a number ↓</a></p>
</header>

<section class="og-sec" aria-labelledby="catch-h">
<h2 id="catch-h">The gate catches a number</h2>
<p class="og-note">A real run on the bundled demo company, fictional like every company here. The output below is
the gate's own, rebuilt from the code on every change. Commands are shown as <code>gate</code>, the shortcut setup
installs.</p>
<div class="og-catch">
<article class="og-draft" aria-label="The agent's draft"><p class="to">Draft to __TO__</p>__DRAFT__</article>
<div class="og-steps">
<div aria-live="polite" class="og-run">
__STEPS__
</div>
<div class="og-ctl"><button type="button" class="primary" id="og-next">Record the source →</button><button type="button" id="og-reset">Start over</button><span class="fine og-hint" id="og-hint">Step 1 of 3 from one real run.</span></div>
<div class="og-live" id="og-live">
<div class="og-ctl"><button type="button" id="og-live-start">Run it live in your browser</button>
<span class="fine og-hint" id="og-live-hint">Edit the draft and the real gate checks it, in this page. Loads Python once, about 10 MB.</span></div>
<div class="og-live-run" id="og-live-run" hidden>
<label class="og-live-label" for="og-live-draft">The draft. Change the number, delete it, add your own.</label>
<textarea id="og-live-draft" rows="9" spellcheck="false"></textarea>
<div class="og-ctl"><button type="button" class="primary" id="og-live-check">Check the draft</button>
<button type="button" id="og-live-record">Record the source</button>
<span class="fine og-hint" id="og-live-state">The source isn't recorded yet.</span></div>
<figure class="term og-live-out"><pre id="og-live-out" aria-live="polite"></pre></figure>
</div>
</div>
</div>
</div>
<p class="og-note og-also">The whole pipeline (source, qualify, draft, gate, pre-send and CRM on seven demo accounts):
<a href="pipeline.html">the demo report</a>. Code, docs and design notes: <a href="__REPO__">github.com/howardhwt/outbound-gates</a>.</p>
</section>
</div>

<section class="og-sec" aria-labelledby="try-h">
<h2 id="try-h">Try it on your prospect</h2>
<p class="og-note">In Claude Code. Nothing goes to Gmail until you set it up on your own outbound folder.</p>
<ol class="og-try">
<li><span class="what">Install the plugin</span><span class="why">Two commands, once.</span>
<span class="cmds">__CMD_ADD____CMD_INSTALL__</span></li>
<li><span class="what">Optional: the run above, on your machine</span><span class="why">Two minutes, offline, on the
bundled demo company.</span><span class="cmds">__CMD_TRY__</span></li>
<li class="og-goal"><span class="what">First catch on your prospect</span><span class="why">About five minutes. It makes a
new folder, your agent writes the email it would normally write, and the gate refuses any number it can't back up
until the source is recorded.</span><span class="cmds">__CMD_START__</span></li>
</ol>
</section>
</main>
<footer class="wrap foot"><span>© 2026 Howard Tam · this page is generated from a real gate run</span>
<nav aria-label="Elsewhere"><a href="https://www.linkedin.com/in/howardhwt">LinkedIn</a><a href="https://github.com/howardhwt">GitHub</a><a href="__REPO__">outbound-gates repo</a></nav></footer>
<script>
(function(){
var steps=[].slice.call(document.querySelectorAll('.og-step')),i=1,next=document.getElementById('og-next'),
reset=document.getElementById('og-reset'),hint=document.getElementById('og-hint'),ctl=next.parentNode;
function sync(){var done=i>=steps.length;next.hidden=done;reset.disabled=i<=1;
if(!done)next.textContent=steps[i].getAttribute('data-run')+' →';
hint.textContent=done?'All three steps from one real run.':'Step '+i+' of 3 from one real run.';}
next.onclick=function(){if(i<steps.length){steps[i].classList.add('shown');i++;sync();
var smooth=!matchMedia('(prefers-reduced-motion: reduce)').matches;
(i>=steps.length?reset:next).focus({preventScroll:true});
ctl.scrollIntoView({behavior:smooth?'smooth':'auto',block:'nearest'});}};
reset.onclick=function(){steps.forEach(function(s,n){if(n)s.classList.remove('shown')});i=1;sync();
steps[0].scrollIntoView({block:'nearest'});next.focus({preventScroll:true});};
var NL=String.fromCharCode(10),L=function(id){return document.getElementById(id)},py=null,session=null;
L('og-live-start').onclick=function(){var b=this;b.disabled=true;b.textContent='Loading Python…';
var t=document.createElement('script');t.src='__PYODIDE__pyodide.js';
t.onerror=function(){fail("the Python runtime didn’t download")};
t.onload=function(){loadPyodide({indexURL:'__PYODIDE__'}).then(function(p){py=p;return fetch('live.zip')})
.then(function(r){if(!r.ok)throw new Error('live.zip: '+r.status);return r.arrayBuffer()})
.then(function(buf){py.unpackArchive(buf,'zip',{extractDir:'/og'});
session=py.runPython(["import sys","sys.path.insert(0, '/og')","from demo.live import Session","Session()"].join(NL));
L('og-live-draft').value=session.draft;L('og-live-run').hidden=false;b.parentNode.hidden=true;L('og-live-draft').focus();})
.catch(function(e){fail(e.message||String(e))});};document.head.appendChild(t);
function fail(m){b.disabled=false;b.textContent='Run it live in your browser';
L('og-live-hint').textContent='Couldn’t start Python here ('+m+'). The replay above is the same gate, run when the page was built.';}};
function show(r,tag){var out=L('og-live-out');out.textContent='';var h=document.createElement('span');
h.className='og-tag'+(tag==='draft stopped'?' stop':tag==='draft passes'?' pass':'');h.textContent=tag;
out.appendChild(h);out.appendChild(document.createTextNode(NL+r.get('output')));r.destroy();}
L('og-live-check').onclick=function(){var r=session.check(L('og-live-draft').value);
show(r,r.get('passed')?'draft passes':'draft stopped');};
L('og-live-record').onclick=function(){var r=session.record(),ok=r.get('recorded');show(r,ok?'source stored':'not recorded');
if(ok){L('og-live-state').textContent='Source recorded: the pricing page’s own sentence for $0.006.';this.disabled=true;}};
addEventListener('beforeprint',function(){[].forEach.call(document.querySelectorAll('.og-more'),function(d){d.open=true})});
[].forEach.call(document.querySelectorAll('.og-cmd'),function(row){
var b=document.createElement('button');b.type='button';b.className='og-copy';b.textContent='copy';
b.setAttribute('aria-label','Copy: '+row.textContent);
b.onclick=function(){var t=row.querySelector('code').textContent,done=function(m){b.textContent=m;setTimeout(function(){b.textContent='copy'},2000)};
(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(function(){done('copied')},function(){done('select the text')});};
row.appendChild(b);});
sync();
})();
</script></body></html>
"""


def _render_output(text: str) -> str:
    """The gate's output, line by line: the problem and the verdict in Chalk, the rest in Smoke.

    The how-to-fix instructions the gate prints after a problem stay on the page, folded under a summary,
    so the problem and the verdict are what a reader (on a phone especially) sees first."""
    lines = text.splitlines()
    first_hit = next((i for i, l in enumerate(lines) if l.startswith("- ")), None)
    verdict = next((i for i, l in enumerate(lines) if l.strip() in ("BLOCKED", "PASS")), len(lines))
    fold = [] if first_hit is None else [i for i in range(first_hit + 1, verdict) if not lines[i].startswith("- ")]

    def one(line: str) -> str:
        if line.strip() in ("BLOCKED", "PASS"):
            return f'<span class="res verdict">{escape(line.strip())}</span>'
        cls = "res hit" if line.startswith("- ") else "res"
        return f'<span class="{cls}">{escape(line)}</span>'

    out = []
    for i, line in enumerate(lines):
        if fold and i == fold[0]:
            out.append(f'<details class="og-more"><summary>How the gate says to fix it ({len(fold)} lines)</summary>'
                       + "".join(one(lines[j]) for j in fold) + "</details>")
        if i in fold:
            continue
        out.append(one(line))
    return "".join(out)


def _command_row(command: str) -> str:
    # Each word is unbreakable, so a narrow screen wraps a command only at its spaces, never inside
    # outbound-gates; the copy button copies the text, spaces and all.
    words = " ".join(f'<span class="w">{escape(w)}</span>' for w in command.split(" "))
    return f'<span class="og-cmd"><code>{words}</code></span>'


UNCHECKED = "our rate sits below that"  # a comparison with no number in the demo draft: the gate lets it through


def _after_pass(run: dict) -> str:
    """What the pass rests on, and what the gate does not check (PRODUCT.md: honest edges)."""
    if UNCHECKED not in run["draft"]:
        raise SystemExit(f"the demo draft no longer says {UNCHECKED!r}; update the page's note on what the gate skips")
    return (f'<p class="og-note og-after">The number now rests on the exact sentence from the company\'s own page: '
            f'<q>{escape(run["page_sentence"])}</q> (<code>{escape(run["url"])}</code>). The gate stored the page with '
            f'its hash and checks it again on every draft.</p>'
            f'<p class="og-note og-after">What it doesn\'t check: <q>{UNCHECKED}</q> has no number in it, so it passes. '
            f'A comparison like that is yours to check.</p>')


def nav_links(current: str) -> str:
    """The four nav links. outbound-gates is marked current: "page" on the gate page, "true" on pages under it."""
    return "".join(
        (f'<a class="brand" href="{href}" aria-label="Howard Tam, home"><span class="long">{text}</span>'
         f'<span class="short">HT</span></a>') if href == "/" else
        (f'<a href="{href}" aria-current="{current}">{text}</a>' if href == "/outbound-gates/" else f'<a href="{href}">{text}</a>')
        for href, text in NAV)


def build_page(run: dict) -> str:
    blocks = []
    for n, step in enumerate(run["steps"]):
        tag = ('<span class="og-tag stop">draft stopped</span>' if step["exit"] == 1 else
               '<span class="og-tag pass">draft passes</span>' if "PASS" in step["output"] else
               '<span class="og-tag">source stored</span>')
        blocks.append(
            f'<div class="og-step{" shown first" if n == 0 else ""}" data-run="{escape(step["label"])}">'
            f'<p class="head">{escape(step["label"])} {tag}</p>'
            f'<figure class="term"><pre><span class="cmd">{escape(_short_command(step, run))}</span>'
            f'{_render_output(step["output"])}</pre></figure>'
            + (_after_pass(run) if n == len(run["steps"]) - 1 else "") + '</div>')
    paragraphs = [p for p in run["draft"].strip().split("\n\n")]
    draft = "".join("<p>" + escape(p).replace("\n", "<br>").replace("$0.006", '<span class="og-num">$0.006</span>')
                    + "</p>" for p in paragraphs)
    nav = nav_links(current="page")
    return (PAGE.replace("__STEPS__", "\n".join(blocks)).replace("__DRAFT__", draft).replace("__NAV__", nav)
            .replace("__TO__", escape(run["to"])).replace("__PYODIDE__", PYODIDE).replace("__REPO__", REPO).replace("__PORTFOLIO__", PORTFOLIO)
            .replace("__CMD_ADD__", _command_row("/plugin marketplace add howardhwt/outbound-gates"))
            .replace("__CMD_INSTALL__", _command_row("/plugin install outbound-gates@outbound-gates"))
            .replace("__CMD_START__", _command_row("/outbound-gates:start"))
            .replace("__CMD_TRY__", _command_row("/outbound-gates:try")))


def build_pipeline() -> str:
    from gates.checker import load_config
    from demo.report import build_report
    from demo.run_demo import CONFIG, load_crm, run_detailed

    demo = ROOT / "demo"
    crm = load_crm(demo / "mock_crm.json")
    candidates = json.loads((demo / "candidates.json").read_text(encoding="utf-8"))
    after, _, records, source_lines = run_detailed(crm, candidates, load_config(CONFIG))
    return build_report(crm, after, records, source_lines, [], nav=nav_links(current="true"))


LIVE_FILES = ["gates/*.py", "gates/*.json", "gates/styles/*.json", "demo/__init__.py", "demo/live.py",
              "demo/quickstart/**/*"]


def build_live_bundle() -> bytes:
    """live.zip: what the browser needs to run demo/live.py. Fixed dates and order, so a rebuild is byte-identical."""
    import io
    import zipfile
    files = sorted({p for pattern in LIVE_FILES for p in ROOT.glob(pattern) if p.is_file() and "__pycache__" not in p.parts})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())
    return buf.getvalue()


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
    (out / "live.zip").write_bytes(build_live_bundle())
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
