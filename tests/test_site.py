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
    """PRODUCT.md principle 1: BLOCKED is visible without JavaScript doing anything."""
    out, _, _ = built
    page = (out / "index.html").read_text()
    steps = re.split(r'(?=<div class="og-step[ "])', page)[1:]
    assert 'class="og-step shown first"' in steps[0] and "BLOCKED" in steps[0]
    assert 'data-run="Record the source"' in page and 'data-run="Check again"' in page


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
