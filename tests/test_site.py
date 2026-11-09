"""The GitHub site and the README clip: built from real gate runs, nothing local leaks out."""
import re
from pathlib import Path

import pytest

from demo import site

ROOT = Path(__file__).resolve().parents[1]
LEAKS = ("/Users/", "/home/", "/private/", "/var/folders", "/tmp/", str(ROOT))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    clip = out / "clip.svg"
    run = site.build(out, clip)
    return out, clip, run


def test_the_page_replays_blocked_recorded_passed(built):
    out, _, run = built
    assert [(s["kind"], s["exit"]) for s in run["steps"]] == [("check", 1), ("record", 0), ("check", 0)]
    page = (out / "index.html").read_text()
    blocked, passed = page.index("has no sourced claim"), page.rindex(">PASS<")
    assert blocked < page.index("recorded:") < passed


def test_nothing_from_the_build_machine_leaks(built):
    out, clip, _ = built
    for path in (out / "index.html", out / "catch.svg", clip):
        text = path.read_text()
        assert not [leak for leak in LEAKS if leak in text], path


def test_the_committed_clip_is_what_the_gate_prints_today():
    """docs/catch.svg is generated. If this fails, the gate's output changed: run
    python -m demo.site --clip docs/catch.svg and commit the result."""
    run = site.run_catch()
    assert (ROOT / "docs" / "catch.svg").read_text() == site.build_clip(run)


def test_the_clip_shows_the_three_steps_in_order(built):
    _, clip, _ = built
    text = re.sub(r"<[^>]+>", "\n", clip.read_text())
    order = [text.index(s) for s in ("› gate check", "has no sourced claim", "BLOCKED", "› gate record", "PASS")]
    assert order == sorted(order)


def test_the_pipeline_report_is_published(built):
    out, _, _ = built
    assert "Northwind Audio" in (out / "pipeline.html").read_text()
    assert (out / ".nojekyll").exists()


def test_the_pipeline_report_joins_the_portfolio(built):
    out, _, _ = built
    page = (out / "pipeline.html").read_text()
    assert '<link rel="stylesheet" href="/style.css">' in page and "--bg:" not in page  # shared tokens, not a copy
    nav = re.search(r'<nav class="nav" aria-label="Main">(.*?)</nav>', page).group(1)
    assert re.findall(r'href="([^"]+)"', nav) == ["/", "/work/", "/outbound-gates/", site.REPO]
    assert 'href="./"' in page  # back to the gate page


def test_the_build_refuses_a_page_that_would_lie(monkeypatch, tmp_path):
    fake = {"draft": "", "to": "", "url": "", "page_sentence": "",
            "steps": [{"kind": "check", "exit": 0, "output": "PASS", "label": "", "command": ""}] * 3}
    monkeypatch.setattr(site, "run_catch", lambda: fake)
    with pytest.raises(SystemExit, match="blocked, recorded, passed"):
        site.build(tmp_path / "out", None)


PORTFOLIO_REPO = ROOT.parent / "howardhwt.github.io"


def test_the_nav_is_the_portfolio_pill_with_short_links(built):
    out, _, _ = built
    page = (out / "index.html").read_text()
    nav = re.search(r'<nav class="nav" aria-label="Main">(.*?)</nav>', page).group(1)
    assert re.findall(r'href="([^"]+)"', nav) == ["/", "/work/", "/outbound-gates/", site.REPO]
    assert 'aria-current="page">outbound-gates<' in nav
    assert '<link rel="stylesheet" href="/style.css">' in page  # the portfolio's own stylesheet, same origin


@pytest.mark.skipif(not PORTFOLIO_REPO.exists(), reason="portfolio repo not checked out next to this one")
def test_the_portfolio_still_has_what_the_page_borrows():
    """The page shares the portfolio's stylesheet and links into it. If this fails, the portfolio changed."""
    css = (PORTFOLIO_REPO / "style.css").read_text()
    for rule in (".nav", ".term", ".jump", ".foot", ".lede", ".skip", "--surface", "--line", "--mono", "--measure"):
        assert rule in css, rule
    for href, _ in site.NAV:
        if href.startswith("/") and href != "/outbound-gates/":
            assert (PORTFOLIO_REPO / href.lstrip("/") / "index.html").exists(), href


def test_the_catch_is_on_the_page_before_any_click(built):
    """PRODUCT.md principle 1: without JavaScript the page reads top down, the gate's output open, BLOCKED first."""
    out, _, _ = built
    page = (out / "index.html").read_text()
    assert 'id="og-printed" open>' in page  # the script closes it; without the script it stays open
    outs = re.findall(r'<div class="og-out" data-o="(\d)">(.*?)</figure></div>', page, re.S)
    assert [n for n, _ in outs] == ["0", "1", "2"] and "BLOCKED" in outs[0][1]


def test_the_page_tells_the_wrong_number_story(built):
    """The draft says $0.06, the company's page says $0.006: stopped, refused, then fixed and passed, all real."""
    out, _, run = built
    story = run["story"]
    assert [s["exit"] for s in story["steps"]] == [1, 1, 0, 0]
    page = (out / "index.html").read_text()
    assert page.index(f"&#x27;{site.WRONG}&#x27; has no sourced claim") < page.index("refused: value not in quote") < page.rindex(">PASS<")
    assert site.WRONG in story["wrong_draft"] and site.RIGHT not in story["wrong_draft"]
    assert f'<span class="og-mark">{site.RIGHT}</span>' in page  # the company's own sentence, its number marked
    assert '<span class="og-num">$0.0<span class="og-zero">0</span>6</span>' in page  # step 3 grows the missing 0
    for lock in (f"Locked: nothing backs up {site.WRONG}", "Unlocked: a person sends it"):
        assert lock in page


def test_the_build_refuses_a_story_the_gate_did_not_act_out(monkeypatch, tmp_path):
    real = site.run_wrong_number()
    real["steps"][1] = dict(real["steps"][1], exit=0, output="recorded: …")  # as if $0.06 had been accepted
    monkeypatch.setattr(site, "run_wrong_number", lambda: real)
    with pytest.raises(SystemExit, match="stopped, refused, fixed and passed"):
        site.build(tmp_path / "out", None)


def test_the_page_says_what_the_gate_lets_through(built):
    out, _, _ = built
    page = (out / "index.html").read_text()
    assert site.UNCHECKED in page and "What it doesn't check" in page


def test_the_page_ends_on_the_first_catch_command(built):
    """PRODUCT.md principle 2: the last command on the page is /outbound-gates:start."""
    out, _, _ = built
    page = (out / "index.html").read_text()
    commands = re.findall(r'<span class="og-cmd"><code>(.*?)</code>', page)
    assert re.sub(r"<[^>]+>", "", commands[-1]) == "/outbound-gates:start"
