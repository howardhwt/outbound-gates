---
name: source
description: Find new companies that match the target profile and record them in the CRM as sourced, with one line of evidence each. Use when the qualify step has run out of unqualified records.
---

# Source

Find companies worth researching. This step is wide and shallow. It does not qualify and it does not draft.

## Where the files are

The checker and the reference docs ship with this plugin. The plugin root is two folders above this skill: `${CLAUDE_SKILL_DIR}/../..`. If that path appears with the variable unfilled, use this skill's base directory and go up two folders. Paths below that start with `docs/`, `examples/` or `gates/` are relative to the plugin root. Drafts, claim files and CRM exports are the user's own files and stay in the working folder.

## Input

- The target profile for the lane: who buys, what signal on their own site shows it.
- A count to find, usually 10 to 20.

## Steps

1. **Search by the signal, not the category.** A company that lists a competing product on its pricing page is a stronger lead than one that sits in the right industry. Write down the page where the signal appears.
2. **Dedupe before creating anything.** Look the domain up in the CRM. If a company record exists under any status, skip it and say so. Check for a second record under an alternate domain.
3. **Create the company record.** Status `NEW`. Set the lane and the segment. Never create a deal at this stage.
4. **Write a SOURCED note on the company.** One line: what the signal is and the URL it came from.
5. **Report.** One line per company: name, domain, signal, URL. List the companies you skipped and why.

## Rules

- A company with no URL behind its signal is not sourced. Leave it out.
- Do not guess a contact. Contacts are found in the qualify step.
- Do not write to any record you did not create in this run.
