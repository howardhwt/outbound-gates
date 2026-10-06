# Walkthrough: one account, start to finish

Northwind Audio is a fictional company. These files show what each step leaves behind for it, in order. The same account runs inside the demo (`python -m demo.run_demo`), and a test checks that the files here match what the code produces.

| File | Step | What it is |
|---|---|---|
| `01-sourced-note.md` | Source | The note written when the company record is created |
| `02-verdict-card.md` | Qualify | The verdict card, one source per line |
| `03-claims.json` | Draft | The claim file built from the card |
| `04-draft.txt` | Draft | The email |
| `05-gate-output.txt` | Gate | The checker's output on that email |
| `06-presend-report.md` | Pre-send | The four questions, answered |
| `07-crm-after.md` | Log | What the CRM holds once the draft is logged, and after the send |
