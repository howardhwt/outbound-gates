---
version: 1
slug: "demo-site-py"
primary_target: "demo/site.py"
related_targets: []
---

# The catch section (index.html, "The gate catches a number")

Mode: Persuade. Built inside the portfolio's world ("The Instrument Panel": graphite, Geist, hairlines, emphasis by luminance, no red or green for state). No new visual world.

## Job and audience
- GTM engineers who live in Claude Code, arriving from the README, LinkedIn or the portfolio, usually on desktop next to a terminal.
- They must get the idea in about 10 seconds: the agent wrote a number, the gate refused it until the number had a source, then let it through.
- Success: they scroll on to the install command and run `/outbound-gates:start`.

## Selected direction: the email is the stage
- Like a spellchecker underlining the word in your document, not printing an error log in another window. The gate acts on the draft itself; the three stacked terminal logs go.
- The story is a wrong number: the draft says `$0.06`, the company's pricing page says `$0.006`. Real gate behaviour: check blocks `$0.06` (no sourced claim); recording `$0.06` against the page's sentence is refused (`value not in quote`); a made-up quote is refused (`quote not found at URL`). Never call it "catching a typo": the gate refuses any number the page doesn't say.
- Step 1, Check: `$0.06` is marked in the email, with a note pinned to it: "No source. Stopped."
- Step 2, Find the source: the pricing page's own sentence attaches to the number like a footnote, $0.006 underlined, with the URL and "saved with a copy of the page" (replaces the hashed file path). "The page says $0.006. Recording $0.06 was refused." Pill: "Not on the page".
- Step 3, Fix and check: the missing 0 grows into the number, the note detaches, the mark settles to a quiet underline, pill "Clear to send" with the check drawn.
- Each step leads with one plain sentence. The gate's real output for that step sits behind "What the gate printed", one click away, still generated from the run, never pasted.
- Focal moment: the number in the email changing state.

## Motion
- Plays once when the email is 55% in view; each step dwells 2.8s, shown by the active step's bar filling. Any click stops it; "Play again" appears at the end.
- A band of light reads the email once on each check (0.9s), then the number reacts (small scale pulse) and the note opens downward from the number (grid rows + clip-path, 0.5s). Between notes: close, swap, reopen.
- The status pill morphs its width between labels; the check mark draws itself.
- The step sentence changes with a short blur-in.
- Reduced motion: no autoplay, no scan, no pulse; states switch with colour fades only.
- Reference mock: scratchpad mock, not committed.

## Interaction and layout
- One control row under the email: three step dots (Check · Find the source · Check again), labelled, clickable, keyboard reachable. They replace Next and Start over.
- Plays itself once when the section scrolls into view (about 1.5s per step), stops on any interaction, never loops. Reduced motion: no autoplay, steps change instantly.
- "Try it yourself" turns the same email into an editable box and runs the real gate (Pyodide, loaded on that click only). Its verdict uses the same pinned note on the number, so live and replay speak one language. Replaces the separate live panel.
- What the gate doesn't check ("our rate sits below that") stays as one honest line after step 3.
- Phone: email full width, note below the line holding the number rather than floating beside it.

## Boundaries
- Untouched: hero, nav, "Try it on your prospect" section, pipeline.html, `docs/catch.svg` (README clip stays the terminal version).
- No fake terminal to type into. No red or green. No invented copy beyond the step sentences above.
- No-JS and print: all three steps written out in order, each with its sentence and the gate's output, so the page still reads blocked, recorded, passed.
- Tests keep checking blocked, recorded, passed in order and no build-machine paths.

## Open decisions
- Whether the email sits in the left column (with the hero moved above) or stays in the right column.
- Exact wording of the three step sentences, to confirm against the gate's output.
- Changing the demo draft to `$0.06` touches the try command, `docs/catch.svg` and tests; change it in one place.
