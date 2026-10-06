---
name: presend-check
description: Before any email goes out, check that the account is not disqualified, that this person and their colleagues have not been contacted, and that no earlier promise is being broken. Use when asked "is it safe to send this", "has this company been contacted", or "run the pre-send check".
---

# Pre-send check

Four questions, in order. The first one blocks.

## Where the files are

The checker and the reference docs ship with this plugin. The plugin root is two folders above this skill: `${CLAUDE_SKILL_DIR}/../..`. If that path appears with the variable unfilled, use this skill's base directory and go up two folders. Paths below that start with `docs/`, `examples/` or `gates/` are relative to the plugin root. Drafts, claim files and CRM exports are the user's own files and stay in the working folder.

## 1. Is the account already dead?

Read the company status, every deal for the company at any stage, and the company notes. Block the draft if the status is disqualified, any deal carries a disqualification reason, or any note starts with `DQ` or `DO NOT EMAIL`. Read the reason out to the person and make them overrule it on purpose.

## 2. Has this person been contacted?

Use the CRM, not the mailbox. Sends from a shared inbox or a colleague never appear in one person's sent folder, so an empty mailbox search proves nothing.

## 3. What was said last time?

Read the earlier emails. Look for three things:

- **Promises.** "I won't write again" is a constraint, including for colleagues at the same company.
- **Angle collisions.** The same opening twice reads as an untracked mail merge.
- **Replies.** Any reply means this is no longer a cold email. Answer on the thread.

## 4. Who else at the company has been contacted?

Two people at one company inside a week, with no reference to each other, looks careless. Check the company, not only the person.

## Running it as code

With the CRM exported in the shape described in `docs/crm-adapter.md`:

```
PYTHONPATH="${CLAUDE_SKILL_DIR}/../.." python3 -m gates.presend --crm crm.json --domain example.com --contact a@example.com
```

It exits 0 when clean and 1 when blocked. Question 3 still needs a person or an agent to read the earlier emails for angle collisions. The code only sees promises recorded as a `PROMISE` note and replies recorded as a reply date.

## Report

One line per account, exceptions first: blocked with the reason quoted, clean, contacted on a date with no reply, or replied. Then state once what this check cannot see: calls, social messages and sends from mailboxes that do not log to the CRM.

## After a confirmed send

In one batch: move the company status forward, set the contact date if it is empty, write a SENT note, create a task to read the result in six days, and close the SEND task.
