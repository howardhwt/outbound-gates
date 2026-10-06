# Connecting a real CRM

`gates/presend.py` and `gates/funnel.py` work on a plain dictionary. The demo builds that dictionary from `demo/mock_crm.json`. To use your own CRM, build the same shape from it.

This repo does not ship a connector. In an agent setup the agent reads the CRM through whatever tool it already has, and writes the export to a file or passes it straight to `presend_check`.

## The shape

```json
{
  "companies": [
    {
      "domain": "example.com",
      "alt_domains": ["getexample.com"],
      "name": "Example",
      "status": "NEW",
      "lane": "transcribe",
      "segment": "reseller",
      "date_contacted": null,
      "date_replied": null,
      "notes": [{"date": "2026-10-01", "text": "SENT: first email", "to": "a@example.com"}],
      "tasks": [{"subject": "SEND: Example: draft waiting for a@example.com", "done": false}],
      "deals": [{"stage": "closed_lost", "dq_reason": "builds in house"}]
    }
  ]
}
```

| Field | Used by | Rule |
|---|---|---|
| `domain`, `alt_domains` | pre-send, source | Matched without regard to case |
| `status` | pre-send, funnel | `UNQUALIFIED` blocks a send |
| `date_contacted`, `date_replied` | funnel, pre-send | A reply date blocks a cold email |
| `notes[].text` | pre-send | Must start with a keyword from `docs/crm-model.md` |
| `notes[].to` | pre-send | The address a `SENT` note went to |
| `tasks[].subject`, `done` | pre-send | An open `SEND` task blocks a second draft |
| `deals[].dq_reason` | pre-send | Any value blocks a send |

## Mapping it onto HubSpot

| This repo | HubSpot |
|---|---|
| Company `status` | The company's Lead Status property |
| `lane`, `segment` | Two custom company properties |
| `date_contacted`, `date_replied` | Two custom date properties on the company |
| Notes with keywords | Notes associated with the company |
| Tasks with keywords | Tasks associated with the company |
| `deals[].dq_reason` | A closed-lost reason property on the deal |
| `alt_domains` | Additional domains on the company record |

Other CRMs need the same seven things: a company status, two dates, two labels, notes and tasks on the company, and a reason on lost deals.

## What to check after connecting

1. Run the pre-send check on an account you know is disqualified. It must block.
2. Run it on an account with a draft waiting. It must block on `draft_waiting`.
3. Run the funnel and compare the contacted count with a manual count for one week.
