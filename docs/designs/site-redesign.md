# Site redesign: one site with the portfolio

Status (2026-11-07): built and tested locally, **not committed**. Next session starts by opening the PR.

## What was asked

The GitHub site (howardhwt.github.io/outbound-gates) looked like a different site from the portfolio
(howardhwt.github.io). Bring it onto the portfolio's design system, make it work on phones, and make the
first screen show the gate working.

## Decisions (from the impeccable init interview)

| Question | Decision |
|---|---|
| Who the site wins first | GTM engineers who use Claude Code. Hiring managers are second; the portfolio case study makes the hiring case. |
| Visual authority | The portfolio's `DESIGN.md` and `.impeccable/design.json` ("The Instrument Panel": graphite, Geist, hairlines, flat, no red or green). |
| How tied to the portfolio | One universal site. The portfolio's floating pill nav, with short links only: Howard Tam (`/`), Work (`/work/`), outbound-gates (current), GitHub. Not a full copy of the portfolio nav, so it rarely drifts. |
| Stack | Keep the generator: `python -m demo.site` builds the page from a real gate run; `pages.yml` deploys it. |
| Styles | The page loads the portfolio's own `/style.css` (same domain), so tokens, nav, terminal and footer are shared, not copied. Page-only styles use the portfolio's tokens. |

Product facts and these decisions are recorded in `PRODUCT.md` (impeccable's product record).

## What changed (uncommitted)

- `PRODUCT.md`: new.
- `demo/site.py`: new page template.
  - From 1100px the catch (draft, Run the next step, the three steps) sits beside the claim in the first screen.
  - Step tags: "draft stopped", "source stored", "draft passes".
  - The gate's how-to-fix lines are folded under "How the gate says to fix it".
  - "Try it" rows with copy buttons; commands wrap only at spaces.
  - All steps show without JavaScript and in print; reduced motion turns off the entrance.
- `docs/catch.svg`: the README clip, now the portfolio terminal (black, Chalk and Smoke, `›` prompt, no traffic lights).
- `tests/test_site.py`:
  - The clip test now looks for `›`.
  - New test: the nav is exactly the four links and the page loads `/style.css`.
  - New test (runs only when the portfolio repo is checked out next to this one): the portfolio still has the styles and pages this site uses.

Checked: 558 tests pass. Viewed at 1440px and 390px with the portfolio served at the root: no sideways scroll, 44px tap areas. Impeccable's detector flags only Geist (pinned by DESIGN.md) and the CLI `--flags` (counted as em-dashes).

## Next, in order

1. **Open the PR** for the redesign (PR body: What I built / Why this approach / When it breaks / What I learned, then Tested). After merge, check `docs/catch.svg` on GitHub: the animated clip couldn't be screenshotted headless.
2. **Confirm `/impeccable` loads** in the fresh session. If it doesn't, run `/plugin` and read the load error.
3. **Restyle `pipeline.html`** (`demo/report.py`) the same way. It still has the old look.
4. **Live gate in the browser** (proposed, not started). Run the real gate in the visitor's browser with Pyodide (Python on WebAssembly). This is possible because the gate needs no installed packages and the demo company's page is bundled.
   - The visitor edits the draft, presses Check and sees the real verdict.
   - The replay stays the default; Pyodide (about 10MB) loads only when they click "Run it live".
   - It can check only against the bundled demo page, because browsers block fetching other sites.
   - Tests cover the Python entry point, not the in-browser run.
   - Separate PR.
5. Optional: run `/impeccable critique` or `/impeccable polish` on the page once the plugin loads.

## Local preview

The page needs the portfolio's `/style.css`, so `_site/index.html` alone opens unstyled. Serve it next to a copy of the portfolio:

```sh
python3 -m demo.site --out _site
M=$(mktemp -d); cp -R ../howardhwt.github.io/. "$M"/; rm -rf "$M/.git"; cp -R _site "$M/outbound-gates"
(cd "$M" && python3 -m http.server 8765) &
open http://localhost:8765/outbound-gates/
```

Stop it with `pkill -f "http.server 8765"`.
