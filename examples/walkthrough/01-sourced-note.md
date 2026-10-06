# Step 1: source

The agent searched for companies that resell transcription by the minute. Northwind Audio's pricing page shows a per-minute transcription price, which is the signal for this lane.

Before creating anything it looked up `northwind-audio.example` in the CRM. No record existed under that domain or as an alternate domain.

**Company record created**

| Field | Value |
|---|---|
| Name | Northwind Audio |
| Domain | northwind-audio.example |
| Status | `NEW` |
| Lane | transcribe |
| Segment | reseller |

**Note written on the company**

```
SOURCED: resells transcription by the minute. https://northwind-audio.example/pricing
```

No contact is recorded yet and no deal is created. Contacts are found in the next step.
