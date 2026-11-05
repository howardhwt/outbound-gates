---
name: try
description: Show the send gate working in two minutes on a tiny bundled demo, with no setup, no CRM and no network. Copies a one-company project to a new folder, runs the gate on its draft (blocked for an unsourced number), records the number's source, and runs it again (passes). Use when asked to "try outbound-gates", "show me the gate", or "demo the send gate".
---

# Try the send gate

Run these steps in order and show the person each command's output. Don't edit any file, and don't send or draft anything in Gmail: this runs entirely on the bundled demo.

1. **Copy the demo project** to a new folder in the current directory:
   ```
   PYTHONPATH="${CLAUDE_SKILL_DIR}/../.." python3 -m gates.quickstart outbound-gates-try
   ```
   If the folder already exists, use `outbound-gates-try-2` (then `-3`, and so on). The command prints three commands with the right paths filled in. Run those, exactly as printed, one at a time, in steps 2 to 4.

2. **Check the draft.** Expect `BLOCKED`: the draft says `$0.006`, and no source is recorded for it. Point out the refusal names the number and prints the command that fixes it.

3. **Record the source.** This reads the company's pricing page (a local fixture page, because `.example` domains are never fetched), checks that the exact sentence is on it and contains the number, and stores the page with its hash.

4. **Check again.** Expect `PASS`.

Then explain in three short lines:
- every number in a gated email needs a source from the company's own site, or a calculation from sourced numbers, or one of the sender's own facts;
- in a real project the same check runs on every Gmail draft the agent creates, and send tools are blocked;
- to set it up for real, follow `docs/quickstart.md` ("Use it on your own project").

If a step fails differently from the above, show the full output and stop. Suggest opening an issue with the "Stuck setting up" template.
