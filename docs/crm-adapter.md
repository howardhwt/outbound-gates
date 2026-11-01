# Connecting a real CRM

`gates/presend.py` and `gates/funnel.py` work on a plain dictionary. The demo builds that dictionary from `demo/mock_crm.json`. To use your own CRM, build the same shape from it.

For HubSpot the repo ships a read-only adapter, `gates/hubspot.py` (below). For any other CRM, build the same shape from it: in an agent setup the agent reads the CRM through whatever tool it already has, and writes the export to a file or passes it straight to `presend_check`.

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
      "deals": [{"stage": "closed_lost", "dq_reason": "builds in house"}],
      "owner": "rep-1",
      "contacts": [{"email": "a@example.com", "blocked": null, "in_sequence": false, "unanswered_touches": 1}]
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
| `owner` | pre-send | Optional. With `--owner`, an account owned by someone else is blocked |
| `contacts[].blocked` | pre-send | Optional. Any value blocks that contact, with the value as the reason |
| `contacts[].in_sequence` | pre-send | Optional. True blocks: the contact is already being emailed by a sequence |
| `contacts[].unanswered_touches` | pre-send | Optional. With `--max-unanswered`, reaching the limit blocks |

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

## HubSpot adapter

`gates/hubspot.py` reads one company and its notes, tasks, deals and logged emails, and builds the shape above. It only searches and reads: it never creates, updates or deletes a HubSpot record. Standard library only.

### Credential

A HubSpot Super Admin, or a user with developer tools access, creates a **Service Key** once: Settings, then Integrations, then Service Keys. Give it read access to companies, deals, notes, tasks and emails, and nothing else. An existing legacy private app token also works; HubSpot stopped allowing new private apps on 26 October 2026.

```
export HUBSPOT_SERVICE_KEY=...        # never printed by the adapter
python -m gates.hubspot --domain example.com                      # print the export
python -m gates.hubspot --domain example.com --out crm.json       # save it
python -m gates.hubspot --domain example.com --contact a@example.com --config my-config.json
                                                                   # run the pre-send check
```

No one needs to be logged in to HubSpot when it runs. It works from a script, a scheduled job or an agent.

### What it reads, and how it maps

| This repo | HubSpot | Default |
|---|---|---|
| `status` | Lead Status, through `status_map` | `hs_lead_status`; `ATTEMPTED_TO_CONTACT` becomes `ATTEMPTED`, other values pass through |
| `deals[].dq_reason` | the closed-lost reason, never the stage | `closed_lost_reason`. Stage values are pipeline IDs that differ per account |
| `notes` | notes on the company, HTML stripped | a `SENT` note's first email address becomes `to` |
| `notes` (extra) | logged outgoing emails (`EMAIL`, `FORWARDED_EMAIL`) | each recipient becomes a `SENT` note; `BOUNCED`, `FAILED` and `DRAFT` are skipped |
| `notes` (extra) | logged incoming emails | each becomes a `REPLIED` note |
| `tasks[].done` | task status | only `COMPLETED` is done; `WAITING`, `DEFERRED` and the rest stay open |
| `date_contacted` | a mapped property, or the first logged outgoing email | derived |
| `date_replied` | a mapped property, or the first logged incoming email | derived |
| `lane`, `segment`, `alt_domains` | mapped properties | not mapped |
| `owner` | company owner | `hubspot_owner_id`; checked when you pass `--sender-owner-id` |
| `contacts[].blocked` | contact properties in `contact_block_properties` | unsubscribed from all email, or hard bounced. Add your own, such as an outreach-block property |
| `contacts[].in_sequence` | "Currently in Sequence" | `hs_sequences_is_enrolled`. HubSpot sequences only |
| `contacts[].unanswered_touches` | a mapped number property, with `max_unanswered_touches` | not mapped |

Set the mapping in the `hubspot` section of your config (see `gates/config.example.json`).

### What it cannot see, and says so

Every run prints warnings for what is not mapped. With the defaults:

- An auto-reply counts as a reply, so the check can block an account that has not really answered. That errs toward a person looking, not toward a second cold email.
- Sends and replies from mailboxes that do not log to HubSpot are invisible.
- The sequence check sees HubSpot sequences only. A contact enrolled in another sending tool is invisible unless that tool writes to a contact property you map.
- Without `--sender-owner-id`, two people can both pass the check on the same account.
- Disqualification is per company, not per product line. If you pursue one company for several products and disqualify only one, the check does not know which product a draft is for.
- A company filed under a second domain is not matched unless you map an alternate-domain property. If several companies share the domain, their records are combined and the adapter tells you to merge them.

## What to check after connecting

1. Run the pre-send check on an account you know is disqualified. It must block.
2. Run it on an account with a draft waiting. It must block on `draft_waiting`.
3. Run the funnel and compare the contacted count with a manual count for one week.
