# CRM model

The model has one rule: **a prospect is a company and its notes until someone replies.** A deal is created only when a reply shows real interest.

## Why

If every prospect is a deal, the pipeline fills with records nobody has spoken to. Stage counts stop meaning anything, and the forecast is built on research, not conversations.

## Where things live

| Thing | Object | Detail |
|---|---|---|
| Prospect stage | Company status | `NEW`, `ATTEMPTED`, `IN_PROGRESS`, `CONNECTED`, `BAD_TIMING`, `UNQUALIFIED` |
| Lane and segment | Company fields | A company can be in more than one lane. Add a lane, never remove one |
| First contact and first reply | Company date fields | Set once, never overwritten |
| Research, sends, replies | Notes on the company | Each note starts with a keyword |
| Next steps | Tasks on the company | Each subject starts with a keyword |
| Real opportunities | Deals | Created on a positive reply, never before |

## Note keywords

`SOURCED`, `VERDICT`, `DRAFTED`, `SENT`, `BOUNCED`, `REPLIED`, `DQ`, `HUMAN QUEUE`

A keyword at the start makes a note searchable without a custom field. Any claim in a note that did not come from the account's own site, email or call carries its source URL. A claim with no source is treated as unverified and is never repeated.

## Task keywords

- `SEND: {Company}: draft waiting for {address}`
- `HUMAN QUEUE: {Company}: {the one question that settles it}`
- `{Company}: read the result of the email to {contact}`

Finished tasks are marked complete, not deleted. The open `SEND` task is what stops a second draft to the same company.

## Rules that prevent the common failures

- **Log each draft right after creating it.** Logging at the end of a batch means an interrupted run leaves drafts the system cannot see.
- **A draft changes no status.** The status moves when a person confirms the send. A drafted email must not count as a contacted company.
- **Never lower a status.** A connected company stays connected when a follow-up goes out.
- **One status per company.** A company worked in two lanes shows its most active one.
- **Check for duplicate companies created by email logging.** Mail logging can create a second record under the bare domain. Merge them before counting anything.
