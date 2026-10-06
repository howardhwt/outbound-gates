# Step 6: what the CRM holds

## After the draft is logged

The draft is created in the mail client with the CRM logging address in BCC. Then, before the next account is started:

**Notes on Northwind Audio**

```
SOURCED: resells transcription by the minute. https://northwind-audio.example/pricing
VERDICT: arm supply
DRAFTED: first email to dana@northwind-audio.example
```

**Open task**

```
SEND: Northwind Audio: draft waiting for dana@northwind-audio.example
```

| Field | Value |
|---|---|
| Status | `NEW` (unchanged) |
| First contact date | empty |

A draft changes no status and sets no date. Northwind is not yet counted as contacted.

## After a person confirms the send

```
$ python -m demo.run_demo --confirm-send northwind-audio.example
```

One batch of writes:

| Change | Value |
|---|---|
| Status | `NEW` to `ATTEMPTED` |
| First contact date | 2026-10-03, set once |
| New note | `SENT: first email` to dana@northwind-audio.example |
| `SEND` task | marked complete, not deleted |
| New task | `Northwind Audio: read the result of the email to dana@northwind-audio.example` |

The reseller segment's contacted count goes from 2 to 3.
