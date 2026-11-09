"""Build the GitHub site and the README clip from real runs of the gate.

    python -m demo.site [--out _site] [--clip docs/catch.svg]

Nothing on the site is written by hand. This copies the quickstart project
(one invented company, its pricing page, one draft) and runs the real gate on it
twice, turning that exact output into:

  _site/index.html      the gate page: the draft with a wrong number ($0.06) beside the company's page ($0.006);
                        stopped, recording $0.06 refused, fixed to $0.006 and passed (run_wrong_number)
  _site/pipeline.html   the full pipeline demo report (demo/report.py)
  _site/live.zip        the gate and the demo company, for "Run it live" (demo/live.py, in Pyodide)
  docs/catch.svg        the README clip, an animated terminal of the quickstart run as /outbound-gates:try does it
                        (blocked for an unsourced number, the source recorded, passed: run_catch)

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


RIGHT = "$0.006"  # what the company's pricing page says
WRONG = "$0.06"  # what the page's story has the agent write: the same price, off by ten times


def run_wrong_number() -> dict:
    """The page's story, run for real: the agent writes $0.06, the gate stops it, recording $0.06 against the
    page's sentence is refused, then the number is fixed to $0.006, recorded and checked again."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        project = quickstart.copy_to(tmp / "outbound-gates-try")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp / "home")}
        (tmp / "home").mkdir()
        draft_file = project / "draft.txt"
        right_draft = draft_file.read_text(encoding="utf-8")
        if right_draft.count(RIGHT) != 1:
            raise SystemExit(f"the demo draft should say {RIGHT} once; the page's story is built on it")
        wrong_draft = right_draft.replace(RIGHT, WRONG)
        check, record, _ = quickstart.next_commands(project)
        record_wrong = record.replace(f"--value '{RIGHT}'", f"--value '{WRONG}'")

        def sh(command: str) -> dict:
            done = subprocess.run(["sh", "-c", command], cwd=tmp, env=env, capture_output=True, text=True)
            return {"command": _clean(command, project), "exit": done.returncode,
                    "output": _clean((done.stdout + done.stderr).strip(), project)}

        draft_file.write_text(wrong_draft, encoding="utf-8")
        steps = [sh(check), sh(record_wrong)]
        draft_file.write_text(right_draft, encoding="utf-8")
        steps.append(sh(record))
        steps.append(sh(check))
        page = (project / "pages" / "quickstart-co.example" / "pricing.html").read_text(encoding="utf-8")
    sentence = re.sub(r"<[^>]+>", "", re.search(r"<p>(.*?)</p>", page, re.S).group(1)).strip()
    return {"wrong_draft": wrong_draft, "right_draft": right_draft, "to": quickstart.CONTACT, "url": quickstart.URL,
            "page_sentence": sentence, "steps": steps}


def check_wrong_number(run: dict) -> None:
    """Refuse to publish a page whose story the gate didn't just act out."""
    stopped, refused, recorded, passed = run["steps"]
    ok = (stopped["exit"] == 1 and f"'{WRONG}' has no sourced claim" in stopped["output"]
          and stopped["output"].endswith("BLOCKED")
          and refused["exit"] != 0 and "value not in quote" in refused["output"]
          and recorded["exit"] == 0 and passed["exit"] == 0 and passed["output"].endswith("PASS")
          and RIGHT in run["page_sentence"] and WRONG not in run["page_sentence"].replace(RIGHT, ""))
    if not ok:
        raise SystemExit("the gate didn't behave as the page says (stopped, refused, fixed and passed): "
                         + repr([(s["exit"], s["output"][-60:]) for s in run["steps"]]))


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
.og-hero h1{max-width:760px}
.og-acts{display:flex;flex-wrap:wrap;align-items:center;gap:8px 24px;margin-top:24px}
.og-acts .jump{margin-top:0}
.og-sec h2{margin-bottom:8px}
.og-sec>.fine,.og-sec>.og-note{max-width:var(--measure)}
.og-note{color:var(--muted);text-wrap:pretty}
/* The catch: the agent's draft beside the company's own page, the gate's verdict drawn between their numbers.
   With JavaScript the frame pins while the visitor scrolls through the three steps; without it, it reads top down. */
.og-frame{display:grid;gap:24px}
.og-duo{position:relative;display:grid;gap:48px;align-items:start;grid-template-areas:"hero" "mail" "page"}
.og-hero{grid-area:hero}.og-mailwrap{grid-area:mail}.og-pagewrap{grid-area:page}
.og-hero .lede{margin-top:20px}
.og-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.og-real{margin-top:8px;max-width:64ch}
.og-duo>*{min-width:0}
.og-cap{font-size:13px;line-height:20px;color:var(--muted);margin-bottom:8px}
.og-mail,.og-page{position:relative;z-index:1;border:1px solid var(--line);border-radius:16px;background:var(--surface);overflow:hidden}
.og-mail-h{display:flex;align-items:center;min-height:48px;padding:12px 20px;border-bottom:1px solid var(--line);font-size:13px;line-height:20px;color:var(--muted)}
.og-mail-h span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.og-mail-h b{font:400 12px/20px var(--mono);color:var(--text-2)}
.og-mail-b{padding:18px 20px}
.og-mail-b p{color:var(--text-2)}
.og-mail-b p+p{margin-top:12px}
.og-num{display:inline-flex;align-items:baseline;padding:0 3px;margin:0 -1px;border-radius:4px;color:var(--text);font-variant-numeric:tabular-nums;
transition:background-color 500ms var(--e),color 500ms var(--e),box-shadow 500ms var(--e)}
.og-zero{display:inline-block;max-width:0;opacity:0;overflow:hidden;filter:blur(3px);transition:max-width 700ms var(--e),opacity 400ms 150ms,filter 700ms var(--e)}
.og-frame:not([data-s="2"]) .og-num{background:var(--text);color:var(--bg)}
[data-s="2"] .og-num{box-shadow:inset 0 0 0 1px var(--text)}
[data-s="2"] .og-zero{max-width:.7em;opacity:1;filter:none}
.og-mail-f{display:flex;align-items:center;gap:12px;padding:12px 20px 16px}
.og-send{display:inline-flex;align-items:center;gap:8px;flex:none;min-height:36px;padding:0 16px;border-radius:999px;border:1px solid var(--line);
font-size:14px;font-weight:500;color:var(--muted);transition:background-color 600ms var(--e),color 600ms var(--e),border-color 600ms var(--e)}
.og-send svg{width:16px;height:16px}
.og-shackle{transition:transform 600ms var(--e);transform-origin:12px 7px}
[data-s="2"] .og-send,.og-live-run.passed .og-send{background:var(--text);border-color:var(--text);color:var(--bg)}
[data-s="2"] .og-shackle,.og-live-run.passed .og-shackle{transform:translateY(-2px) rotate(25deg)}
.og-lock{font-size:13px;line-height:20px;color:var(--muted);min-width:0}
.og-lock span{display:none}
.og-frame:not([data-s]) .og-lock span:first-child,[data-s="0"] .og-lock span:nth-child(1),[data-s="1"] .og-lock span:nth-child(2),[data-s="2"] .og-lock span:nth-child(3){display:inline}
.og-page{transition:opacity 700ms var(--e),filter 700ms var(--e),transform 800ms var(--e)}
.og-js [data-s="0"] .og-page{opacity:.35;filter:blur(1px) saturate(0)}
.og-bar{display:flex;gap:6px;align-items:center;min-height:48px;padding:8px 14px;border-bottom:1px solid var(--line)}
.og-bar i{width:9px;height:9px;border-radius:50%;background:var(--line)}
.og-url{flex:1;min-width:0;margin-left:8px;padding:4px 10px;border-radius:6px;background:var(--bg);font:12px/20px var(--mono);color:var(--muted);
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.og-page-b{padding:18px 20px}
.og-page-b h3{font-size:20px;line-height:28px;font-weight:600;color:var(--text);margin-bottom:10px}
.og-q{border-top:1px solid var(--line);padding-top:12px;color:var(--text-2)}
.og-mark{padding:0 3px;border-radius:4px;color:var(--text);box-shadow:inset 0 0 0 1px var(--text);font-variant-numeric:tabular-nums}
.og-saved{display:flex;align-items:center;gap:6px;margin-top:14px;font-size:12px;line-height:16px;color:var(--muted)}
.og-saved svg{width:13px;height:13px;flex:none}
.og-wire,.og-badge{display:none}
.og-js .og-wire{display:block;position:absolute;inset:0;width:100%;height:100%;overflow:visible;pointer-events:none;z-index:0;transition:opacity 600ms var(--e)}
.og-wire path{fill:none;stroke:var(--muted);stroke-width:1.5;transition:stroke 500ms}
[data-s="1"] .og-wire path{stroke-dasharray:5 5}
[data-s="2"] .og-wire path{stroke:var(--text)}
.og-js .og-badge{display:grid;position:absolute;z-index:2;transform:translate(-50%,-50%);place-items:center;width:36px;height:36px;border-radius:50%;
background:var(--bg);border:1px solid var(--muted);font-size:18px;font-weight:600;color:var(--text);
transition:opacity 600ms var(--e),background-color 500ms,color 500ms,border-color 500ms}
[data-s="2"] .og-badge{background:var(--text);border-color:var(--text);color:var(--bg)}
[data-s="0"] .og-wire,[data-s="0"] .og-badge{opacity:0}
.og-foot{display:grid;gap:8px}
.og-dots{display:none;gap:4px;margin-left:-10px}
.og-js .og-dots{display:flex}
.og-dots button{all:unset;cursor:pointer;width:28px;height:28px;display:grid;place-items:center;border-radius:50%}
.og-dots button::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--line);transition:background-color 300ms,transform 400ms var(--e)}
.og-dots button.done::before{background:var(--muted)}
.og-dots button[aria-current]::before{background:var(--text);transform:scale(1.25)}
.og-dots button:focus-visible{outline:2px solid var(--white)}
.og-story{list-style:none;display:grid;gap:16px}
.og-story small{display:block;font:12px/16px var(--mono);color:var(--muted);margin-bottom:4px}
.og-story p{font-size:20px;line-height:28px;color:var(--text);max-width:40em;text-wrap:pretty}
.og-js .og-story{position:relative;display:block;min-height:96px}
.og-js .og-story li{position:absolute;inset:0 auto auto 0;transition:opacity 500ms var(--e),transform 600ms var(--e),filter 500ms}
.og-js .og-story li:not(.on){opacity:0;transform:translateY(8px);filter:blur(3px);pointer-events:none}
.og-edge{margin-top:24px}
.og-edge q{color:var(--text-2)}
.og-printed{margin-top:16px;border-top:1px solid var(--line);padding-top:12px}
.og-printed>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:6px;min-height:44px;width:max-content;font-size:14px;color:var(--muted)}
.og-printed>summary::-webkit-details-marker{display:none}
.og-printed>summary:hover{color:var(--text)}
.og-printed>summary:focus-visible{outline:2px solid var(--white);outline-offset:2px}
.og-printed>summary svg{width:14px;height:14px;transition:transform 250ms var(--e)}
.og-printed[open]>summary svg{transform:rotate(90deg)}
.og-out .lbl{font-size:13px;line-height:20px;color:var(--muted);margin-top:16px}
.og-out .term{margin:8px 0 0}
.og-js .og-printed .og-out{display:none}
.og-js .og-printed.s0 .og-out[data-o="0"],.og-js .og-printed.s1 .og-out[data-o="1"],.og-js .og-printed.s2 .og-out[data-o="2"]{display:block}
.og-js .og-printed .og-out .lbl{display:none}
.og-term pre{margin:0;font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}
.og-term .res{display:block;padding-left:16px;color:var(--muted)}
.og-term .hit{color:var(--text)}
.og-term .verdict{color:var(--text);font-weight:500}
.og-term .cmd+.cmd,.og-term .res+.cmd{display:block;margin-top:12px}
.og-term .cmd{display:block}
/* Try it yourself: the same two cards, the draft editable, the real gate run in the browser. */
.og-live{display:none;margin-top:32px;padding-top:24px;border-top:1px solid var(--line)}
.og-js .og-live{display:grid;gap:16px}
.og-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px}
.og-row[hidden]{display:none}
.og-btn{font:inherit;font-weight:500;min-height:44px;padding:8px 16px;border-radius:999px;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--text);
transition:background-color 300ms var(--ease),border-color 300ms var(--ease),transform 150ms var(--ease)}
.og-btn:hover{background:var(--surface);border-color:var(--muted)}
.og-btn.primary{background:var(--text);border-color:var(--text);color:var(--bg)}
.og-btn.primary:hover{background:var(--white)}
.og-btn:active{transform:scale(0.97)}
.og-btn:disabled{opacity:.6;cursor:default;transform:none}
.og-btn:focus-visible,.og-copy:focus-visible{outline:2px solid var(--white);outline-offset:2px}
.og-hint{max-width:var(--measure);text-wrap:pretty}
.og-live-run{display:grid;gap:16px}.og-live-run[hidden]{display:none}
.og-live-run .og-duo{gap:16px;margin-top:0}
#og-live-draft{display:block;width:100%;min-height:220px;resize:vertical;padding:18px 20px;border:0;background:transparent;color:var(--text-2);font:inherit;font-size:16px;line-height:24px}
#og-live-draft:focus-visible{outline:2px solid var(--white);outline-offset:-2px}
.og-live-run .og-mail-f{flex-wrap:wrap;border-top:1px solid var(--line);padding-top:12px}
.og-live-run .og-page-b .og-btn{margin-top:16px}
.og-live-run .og-q{border:0;padding:0}
.og-live-out{margin:0}.og-live-out:has(pre:empty){display:none}
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
@media (min-width:900px){.og-duo{grid-template-columns:1fr 1fr;gap:48px 72px;grid-template-areas:"hero mail" "page mail"}
.og-live-run .og-duo{gap:24px;grid-template-areas:none}
.og-try li{grid-template-columns:4fr 7fr;grid-template-rows:auto 1fr;column-gap:24px;row-gap:4px}.og-try .what,.og-try .why{grid-column:1}.og-try .cmds{grid-column:2;grid-row:1 / span 2;align-self:start}}
/* Pinned: the frame holds still for three screens of scrolling, one per step. JS adds og-pin only when the frame fits. */
.og-pin main.wrap{padding-top:0}
.og-pin .og-track{height:var(--og-track,340vh)}
.og-pin .og-frame{position:sticky;top:var(--og-top,0px);min-height:100vh;min-height:100svh;align-content:center;padding:88px 0 24px}
@media (min-width:900px){.og-pin .og-duo{grid-template-areas:"left mail";align-items:center}
.og-pin .og-hero,.og-pin .og-pagewrap{grid-area:left;transition:opacity 600ms var(--e),transform 800ms var(--e),filter 600ms var(--e),visibility 0s}
.og-pin .og-frame:not([data-s="0"]) .og-hero{opacity:0;transform:translateY(-24px);filter:blur(4px);visibility:hidden;transition-delay:0s,0s,0s,600ms}
.og-pin [data-s="0"] .og-pagewrap{opacity:0;transform:translateY(24px);filter:blur(4px);visibility:hidden;transition-delay:0s,0s,0s,600ms}
.og-pin [data-s="0"] .og-page{opacity:1;filter:none}}
@media (max-width:899px){.og-pagewrap .og-cap{display:none}}
@media (max-width:599px){.og-hero h1{font-size:34px;line-height:38px}.og-hero .lede{font-size:16px;line-height:24px;margin-top:16px}.og-acts{margin-top:16px}}
@media (max-width:899px){.og-pin .og-frame{gap:14px;padding-top:88px;min-height:0;align-content:start}.og-pin .og-cap{display:none}.og-pin .og-duo{gap:28px}.og-pin .og-saved{display:none}.og-pin .og-story p{font-size:15px;line-height:22px}
.og-pin .og-mail-b .og-x{display:none}.og-pin .og-page-b h3{display:none}.og-pin .og-q{border:0;padding:0}
.og-story p{font-size:16px;line-height:24px}}
@media (prefers-reduced-motion:reduce){.og-frame *,.og-live *{transition-duration:1ms!important}}
@media print{.og-live,.og-copy,.og-dots,.og-wire,.og-badge,.og-printed>summary{display:none}.og-pin .og-track{height:auto}.og-pin .og-frame{position:static;min-height:0}
.og-js .og-story{display:grid;min-height:0}.og-js .og-story li{position:static}.og-js .og-story li:not(.on){opacity:1;transform:none;filter:none}
.og-js .og-printed .og-out{display:block}.og-js .og-printed .og-out .lbl{display:block}}
</style></head><body>
<a class="skip" href="#main">Skip to content</a>
<nav class="nav" aria-label="Main">__NAV__</nav>
<main id="main" class="wrap">
<section class="og-sec og-catch" aria-labelledby="catch-h">
<div class="og-track" id="og-track">
<div class="og-frame" id="og-stage">
<div class="og-duo" id="og-duo">
<header class="og-hero">
<h1>Your agent can't put an unsourced number in a cold email.</h1>
<p class="lede">outbound-gates is a Claude Code plugin for agents that draft outbound. Every number in a draft needs
the exact sentence from the company's own site, a calculation the gate recomputes, or one of your own facts. Send,
reply and forward are blocked; a person sends.</p>
<p class="og-acts"><a class="jump ask" href="#try-h">Try it on your prospect ↓</a></p>
</header>
<div class="og-mailwrap"><p class="og-cap">What the agent wrote</p>
<article class="og-mail" aria-label="The agent's draft">
<div class="og-mail-h"><span>To <b>__TO__</b></span></div>
<div class="og-mail-b">__DRAFT__</div>
<div class="og-mail-f"><span class="og-send">__LOCK_ICON__Send</span><span class="og-lock" id="og-lock">__LOCKS__</span></div>
</article></div>
<div class="og-pagewrap"><p class="og-cap">What their own page says</p>
<article class="og-page" aria-label="The company's pricing page">
<div class="og-bar" aria-hidden="true"><i></i><i></i><i></i><span class="og-url">__URL_SHOWN__</span></div>
<div class="og-page-b"><h3>Pricing</h3><p class="og-q">__QUOTE_HTML__</p>
<p class="og-saved">__SAVE_ICON__The gate saved a copy of this page with its hash.</p></div>
</article></div>
<svg class="og-wire" id="og-wire" aria-hidden="true"><path id="og-path"/></svg><span class="og-badge" id="og-badge" aria-hidden="true">≠</span>
</div>
<div class="og-foot">
<h2 id="catch-h" class="og-sr">The gate catches a wrong number</h2>
<div class="og-dots" role="group" aria-label="Steps"><button type="button" data-go="0" aria-label="Step 1: Check"></button><button type="button" data-go="1" aria-label="Step 2: Find the source"></button><button type="button" data-go="2" aria-label="Step 3: Fix and check"></button></div>
<ol class="og-story" aria-live="polite">__STORY__</ol>
<p class="fine og-real">A real gate run on the bundled demo company (fictional, like every company here), rebuilt from the code on every change.</p>
</div>
</div>
</div>
<p class="og-note og-edge">What it doesn't check: <q>__UNCHECKED__</q> has no number in it, so it passes. A comparison like that is yours to check.</p>
<details class="og-printed" id="og-printed" open><summary><svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M5 3l4 4-4 4"/></svg>What the gate printed</summary>
__OUTS__
</details>
<div class="og-live" id="og-live">
<div class="og-row"><button type="button" class="og-btn" id="og-live-start">Try it yourself</button>
<span class="fine og-hint" id="og-live-hint">Edit the draft and the real gate checks it, in this page. Loads Python once, about 10 MB.</span></div>
<div class="og-live-run" id="og-live-run" hidden>
<p class="fine og-hint">Check the draft, record the page's sentence, then fix the number. Or write your own.</p>
<div class="og-duo">
<div><article class="og-mail" aria-label="Your draft">
<div class="og-mail-h"><span>To <b>__TO__</b></span></div>
<textarea id="og-live-draft" aria-label="Your draft" rows="9" spellcheck="false">__LIVE_DRAFT__</textarea>
<div class="og-mail-f"><button type="button" class="og-btn" id="og-live-check">Check the draft</button>
<span class="og-send">__LOCK_ICON__Send</span><span class="og-lock" id="og-live-state">Not checked yet</span></div>
</article></div>
<div><article class="og-page" aria-label="The company's pricing page">
<div class="og-bar" aria-hidden="true"><i></i><i></i><i></i><span class="og-url">__URL_SHOWN__</span></div>
<div class="og-page-b"><p class="og-q">__QUOTE_HTML__</p>
<button type="button" class="og-btn" id="og-live-record">Record this sentence</button></div>
</article></div>
</div>
<figure class="term og-term og-live-out"><pre id="og-live-out" aria-live="polite"></pre></figure>
</div>
</div>
<p class="og-note og-also">The whole pipeline (source, qualify, draft, gate, pre-send and CRM on seven demo accounts):
<a href="pipeline.html">the demo report</a>. Code, docs and design notes: <a href="__REPO__">github.com/howardhwt/outbound-gates</a>.</p>
</section>
<section class="og-sec" aria-labelledby="try-h">
<h2 id="try-h">Try it on your prospect</h2>
<p class="og-note">In Claude Code. Nothing goes to Gmail until you set it up on your own outbound folder.</p>
<ol class="og-try">
<li><span class="what">Install the plugin</span><span class="why">Two commands, once.</span>
<span class="cmds">__CMD_ADD____CMD_INSTALL__</span></li>
<li><span class="what">Optional: a run like the one above, on your machine</span><span class="why">Two minutes, offline, on the
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
var L=function(id){return document.getElementById(id)},NL=String.fromCharCode(10),
stage=L('og-stage'),track=L('og-track'),duo=L('og-duo'),path=L('og-path'),badge=L('og-badge'),printed=L('og-printed'),
num=stage.querySelector('.og-num'),mark=stage.querySelector('.og-mark'),
dots=[].slice.call(stage.querySelectorAll('[data-go]')),story=[].slice.call(stage.querySelectorAll('.og-story li')),
reduce=matchMedia('(prefers-reduced-motion: reduce)').matches,cur=-1,root=document.documentElement;
printed.removeAttribute('open');
function wire(){var d=duo.getBoundingClientRect(),a=num.getBoundingClientRect(),b=mark.getBoundingClientRect(),p,x1,y1,x2,y2,m;
if(b.top>a.bottom+20){x1=a.left+a.width/2-d.left;y1=stage.querySelector('.og-mail').getBoundingClientRect().bottom-d.top;x2=b.left+b.width/2-d.left;y2=stage.querySelector('.og-page').getBoundingClientRect().top-d.top;m=(y1+y2)/2;
p='M'+x1+' '+y1+' C'+x1+' '+m+' '+x2+' '+m+' '+x2+' '+y2;}
else{var ltr=b.left>a.left;x1=(ltr?a.right:a.left)-d.left;y1=a.top+a.height/2-d.top;x2=(ltr?b.left:b.right)-d.left;y2=b.top+b.height/2-d.top;m=(x1+x2)/2;
p='M'+x1+' '+y1+' C'+m+' '+y1+' '+m+' '+y2+' '+x2+' '+y2;}
path.setAttribute('d',p);var mid=path.getPointAtLength(path.getTotalLength()/2);badge.style.left=mid.x+'px';badge.style.top=mid.y+'px';}
function go(i){if(i===cur)return;cur=i;stage.setAttribute('data-s',i);printed.className='og-printed s'+i;
story.forEach(function(li,j){li.classList.toggle('on',j===i)});
dots.forEach(function(b,j){if(j===i)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');b.classList.toggle('done',j<i)});
badge.textContent=i===2?'=':'≠';
var t0=performance.now();(function f(){wire();if(performance.now()-t0<900)requestAnimationFrame(f)})();
if(i===1&&!reduce){var len=path.getTotalLength();path.style.transition='none';path.style.strokeDasharray=len;path.style.strokeDashoffset=len;
path.getBoundingClientRect();path.style.transition='stroke-dashoffset 900ms cubic-bezier(.16,1,.3,1)';path.style.strokeDashoffset=0;
setTimeout(function(){if(cur===1){path.style.transition='';path.style.strokeDasharray='';path.style.strokeDashoffset=''}},950);}}
function pinned(){return root.classList.contains('og-pin')}
var NAVH=72,stick=0,span=1;
function fit(){root.classList.add('og-pin');root.style.removeProperty('--og-top');root.style.removeProperty('--og-track');
var h=stage.offsetHeight,wide=innerWidth>=900,mail=stage.querySelector('.og-mailwrap').getBoundingClientRect().top-stage.getBoundingClientRect().top;
var ok=wide?h<=innerHeight+1:h-mail+NAVH+8<=innerHeight;
var most=0;story.forEach(function(li){most=Math.max(most,li.offsetHeight)});stage.querySelector('.og-story').style.minHeight=most+'px';
if(!ok){root.classList.remove('og-pin');wire();return;}
stick=wide?Math.min(0,innerHeight-h):Math.min(0,NAVH+8-mail);span=Math.round(innerHeight*2.4);
root.style.setProperty('--og-top',stick+'px');root.style.setProperty('--og-track',(h+span)+'px');wire();}
var auto=-1;
var hero=stage.querySelector('.og-hero');
function onScroll(){var k;if(innerWidth<900){var hb=hero.getBoundingClientRect().bottom;hero.style.opacity=Math.min(1,Math.max(0,(hb-NAVH)/160));}else hero.style.opacity='';
if(pinned()){var p=Math.min(1,Math.max(0,(stick-track.getBoundingClientRect().top)/span));k=p<0.34?0:p<0.67?1:2;}
else{var r=stage.querySelector('.og-page').getBoundingClientRect(),h=innerHeight;k=r.top>h*0.75?0:r.bottom>h*0.6?1:2;}
if(k!==auto){auto=k;go(k);}}
dots.forEach(function(b){b.onclick=function(){var k=+b.getAttribute('data-go');
if(!pinned()){go(k);stage.querySelector(k?'.og-pagewrap':'.og-mailwrap').scrollIntoView({block:'center',behavior:reduce?'auto':'smooth'});return;}
var top=track.getBoundingClientRect().top+scrollY-stick;
scrollTo({top:top+span*[0.15,0.5,0.85][k],behavior:reduce?'auto':'smooth'});}});
go(0);fit();onScroll();
addEventListener('scroll',onScroll,{passive:true});
addEventListener('resize',function(){fit();onScroll();});
if(document.fonts)document.fonts.ready.then(fit);
var py=null,session=null;
L('og-live-start').onclick=function(){var b=this;b.disabled=true;b.textContent='Loading Python…';
var t=document.createElement('script');t.src='__PYODIDE__pyodide.js';
t.onerror=function(){fail("the Python runtime didn’t download")};
t.onload=function(){loadPyodide({indexURL:'__PYODIDE__'}).then(function(p){py=p;return fetch('live.zip')})
.then(function(r){if(!r.ok)throw new Error('live.zip: '+r.status);return r.arrayBuffer()})
.then(function(buf){py.unpackArchive(buf,'zip',{extractDir:'/og'});
session=py.runPython(["import sys","sys.path.insert(0, '/og')","from demo.live import Session","Session()"].join(NL));
L('og-live-run').hidden=false;b.parentNode.hidden=true;L('og-live-draft').focus();})
.catch(function(e){fail(e.message||String(e))});};document.head.appendChild(t);
function fail(m){b.disabled=false;b.textContent='Try it yourself';
L('og-live-hint').textContent='Couldn’t start Python here ('+m+'). The run above is the same gate, run when the page was built.';}};
function show(r){var out=r.get('output');L('og-live-out').textContent=out;r.destroy();return out;}
L('og-live-check').onclick=function(){var r=session.check(L('og-live-draft').value),ok=r.get('passed'),out=show(r),
why=out.split(NL).filter(function(l){return l.indexOf('- ')===0})[0];
L('og-live-run').classList.toggle('passed',ok);
L('og-live-state').textContent=ok?'Unlocked: a person sends it':'Locked: '+(why?why.slice(2).replace(/^[a-z_]+: /,''):'the draft didn’t pass');};
L('og-live-record').onclick=function(){var r=session.record(),ok=r.get('recorded');show(r);
if(ok){this.disabled=true;this.textContent='Sentence recorded';}};
addEventListener('beforeprint',function(){printed.open=true;[].forEach.call(document.querySelectorAll('.og-more'),function(d){d.open=true})});
[].forEach.call(document.querySelectorAll('.og-cmd'),function(row){
var b=document.createElement('button');b.type='button';b.className='og-copy';b.textContent='copy';
b.setAttribute('aria-label','Copy: '+row.textContent);
b.onclick=function(){var t=row.querySelector('code').textContent,done=function(m){b.textContent=m;setTimeout(function(){b.textContent='copy'},2000)};
(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(function(){done('copied')},function(){done('select the text')});};
row.appendChild(b);});
})();
</script></body></html>
"""


def _render_output(text: str) -> str:
    """The gate's output, line by line: the problem, a refusal and the verdict in Chalk, the rest in Smoke.

    The how-to-fix instructions the gate prints after a problem stay on the page, folded under a summary,
    so the problem and the verdict are what a reader (on a phone especially) sees first."""
    lines = text.splitlines()
    first_hit = next((i for i, l in enumerate(lines) if l.startswith("- ")), None)
    verdict = next((i for i, l in enumerate(lines) if l.strip() in ("BLOCKED", "PASS")), len(lines))
    fold = [] if first_hit is None else [i for i in range(first_hit + 1, verdict) if not lines[i].startswith("- ")]

    def one(line: str) -> str:
        if line.strip() in ("BLOCKED", "PASS"):
            return f'<span class="res verdict">{escape(line.strip())}</span>'
        cls = "res hit" if line.startswith(("- ", "refused:")) else "res"
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

LOCK_ICON = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
             '<rect x="3.5" y="7" width="9" height="6.5" rx="1.5"/><path class="og-shackle" d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/></svg>')
SAVE_ICON = ('<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">'
             '<path d="M3 2.5h6l2 2v7H3z"/><path d="M5 2.5v3h4v-3"/></svg>')
STEP_NAMES = ["Check", "Find the source", "Fix and check"]


def _story(run: dict) -> list[str]:
    """One sentence per step, in plain words; the gate's own output for each sits under "What the gate printed"."""
    return [f"The agent got the competitor's price wrong. The gate stopped the draft: nothing on file backs up {WRONG}.",
            f"Their own page says {RIGHT}, not {WRONG}. The gate refuses to record a number the page doesn't say.",
            f"The agent fixes it to {RIGHT}, records the page's sentence and checks again. The number matches its "
            "source, so the draft passes. A person sends it; the agent can't."]


def _number(run: dict) -> str:
    """The draft's number, written so step 3 can grow the missing digit in: $0.0[0]6."""
    head = os.path.commonprefix([WRONG, RIGHT])
    tail = WRONG[len(head):]
    added = RIGHT[len(head):len(RIGHT) - len(tail)]
    if head + added + tail != RIGHT or not added:
        raise SystemExit(f"{WRONG} and {RIGHT} must differ by inserted digits for the page's fix animation")
    return f'<span class="og-num">{escape(head)}<span class="og-zero">{escape(added)}</span>{escape(tail)}</span>'


def _terminal(pairs: list[tuple[str, str]]) -> str:
    body = "".join(f'<span class="cmd">{escape(cmd)}</span>{_render_output(out)}' for cmd, out in pairs)
    return f'<figure class="term og-term"><pre>{body}</pre></figure>'


def nav_links(current: str) -> str:
    """The four nav links. outbound-gates is marked current: "page" on the gate page, "true" on pages under it."""
    return "".join(
        (f'<a class="brand" href="{href}" aria-label="Howard Tam, home"><span class="long">{text}</span>'
         f'<span class="short">HT</span></a>') if href == "/" else
        (f'<a href="{href}" aria-current="{current}">{text}</a>' if href == "/outbound-gates/" else f'<a href="{href}">{text}</a>')
        for href, text in NAV)


def build_page(run: dict) -> str:
    """The gate page, from run_wrong_number(): the draft beside the company's page, three steps, the gate's output."""
    if UNCHECKED not in run["right_draft"]:
        raise SystemExit(f"the demo draft no longer says {UNCHECKED!r}; update the page's note on what the gate skips")
    paragraphs = run["wrong_draft"].strip().split("\n\n")
    draft = "".join(
        f'<p{"" if WRONG in p else " class=\"og-x\""}>'
        + escape(p).replace("\n", "<br>").replace(escape(WRONG), _number(run)) + "</p>" for p in paragraphs)
    quote = escape(run["page_sentence"]).replace(escape(RIGHT), f'<span class="og-mark">{escape(RIGHT)}</span>', 1)
    locks = "".join(f"<span>{escape(t)}</span>" for t in
                    (f"Locked: nothing backs up {WRONG}", f"Locked: {WRONG} isn't on their page", "Unlocked: a person sends it"))
    story = "".join(f'<li data-t="{n}"><small>{n + 1} · {name}</small><p>{escape(text)}</p></li>'
                    for n, (name, text) in enumerate(zip(STEP_NAMES, _story(run))))
    stopped, refused, recorded, passed = run["steps"]
    check = f"gate check draft.txt --to {run['to']}"
    record = f"gate record --contact {run['to']} --url {run['url']} --quote '{quickstart.QUOTE}' --value"
    printed = [[(check, stopped["output"])], [(f"{record} '{WRONG}'", refused["output"])],
               [(f"{record} '{RIGHT}'", recorded["output"]), (check, passed["output"])]]
    outs = ('<p class="fine og-hint">Commands are shown as <code>gate</code>, the shortcut setup installs.</p>'
            + "".join(f'<div class="og-out" data-o="{n}"><p class="lbl">{n + 1} · {STEP_NAMES[n]}</p>{_terminal(pairs)}</div>'
                      for n, pairs in enumerate(printed)))
    nav = nav_links(current="page")
    return (PAGE.replace("__DRAFT__", draft).replace("__NAV__", nav).replace("__LOCKS__", locks)
            .replace("__STORY__", story).replace("__OUTS__", outs).replace("__QUOTE_HTML__", quote)
            .replace("__URL_SHOWN__", escape(run["url"].split("://", 1)[-1]))
            .replace("__LIVE_DRAFT__", escape(run["wrong_draft"].strip())).replace("__UNCHECKED__", UNCHECKED)
            .replace("__LOCK_ICON__", LOCK_ICON).replace("__SAVE_ICON__", SAVE_ICON)
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
    story = run_wrong_number()
    check_wrong_number(story)
    out.mkdir(parents=True, exist_ok=True)
    svg = build_clip(run)
    (out / "index.html").write_text(build_page(story), encoding="utf-8")
    (out / "catch.svg").write_text(svg, encoding="utf-8")
    (out / "pipeline.html").write_text(build_pipeline(), encoding="utf-8")
    (out / "live.zip").write_bytes(build_live_bundle())
    (out / ".nojekyll").write_text("", encoding="utf-8")
    if clip:
        clip.write_text(svg, encoding="utf-8")
    run["story"] = story
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
