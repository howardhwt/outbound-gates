# Step 5: pre-send check

The demo runs this check in memory, after the gate passes and before the draft is logged. At that moment Northwind is clean:

| Question | Answer |
|---|---|
| 1. Is the account already dead? | No. Status is `NEW`, no deal carries a disqualification reason, no `DQ` or `DO NOT EMAIL` note |
| 2. Has this person been contacted? | No `SENT` note to dana@northwind-audio.example, and no open `SEND` task |
| 3. What was said last time? | Nothing. No promise on file and no reply date |
| 4. Who else at the company has been contacted? | Nobody in the last 7 days |

The check also states what it cannot see: calls, social messages, and sends from mailboxes that do not log to the CRM.

## The same check after the draft is logged

Run the demo, then run the check against the CRM state it wrote:

```
$ python -m demo.run_demo
$ python -m gates.presend --crm demo/out/crm_after.json \
    --domain northwind-audio.example --contact dana@northwind-audio.example --today 2026-10-03
[FAIL] draft_waiting: an unsent draft is already logged: 'SEND: Northwind Audio: draft waiting for dana@northwind-audio.example'
This check cannot see calls, social messages, or sends from mailboxes that do not log to the CRM.
BLOCKED
```

That block is intended. The open `SEND` task is what stops a second run from drafting to Northwind again.
