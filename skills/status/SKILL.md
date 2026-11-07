---
name: status
description: Show where the send gate stands in this folder and what to do next. The gate and safety-net versions, strict mode, the style in use, own facts, names, logging BCC, allowed tools, today's passed and refused drafts, and any change waiting for the person's approval. Use when asked "what's the status", "is the gate on", "what should I do next", "what's set up here", or at the start of outbound work.
---

# Send gate status

Run:

```
PYTHONPATH="${CLAUDE_SKILL_DIR}/../.." python3 -m gates.gate status --project .
```

Show the output as it is, then say the first line under "Next:" in plain words.

If a change is waiting for approval, remind the person that only they can apply it, by typing the `! ~/.config/outbound-gates/gate yes CODE` line shown. Never run `gate yes` yourself: in strict mode it is refused, and outside strict mode it would defeat the point.

## Making a person-only change

Own facts, names, the logging BCC, allowed tools, the project's style and a safety-net refresh change what the agent may do or say, so the agent proposes and the person approves:

```
~/.config/outbound-gates/gate propose add-fact '$0.02'
~/.config/outbound-gates/gate propose add-name 1440p
~/.config/outbound-gates/gate propose add-bcc 12345@bcc.crm.example
~/.config/outbound-gates/gate propose allow manage_crm_objects
~/.config/outbound-gates/gate propose style neutral|example|FILE
~/.config/outbound-gates/gate propose refresh
```

Each prints the staged changes and an approval code. Show the person exactly what was staged and the `! ... gate yes CODE` line, then stop. Propose only what the person asked for, or what a refusal shows is needed and the person agrees to.
