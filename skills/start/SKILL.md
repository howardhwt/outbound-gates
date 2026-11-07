---
name: start
description: First catch on your own prospect in about five minutes, with no setup and no CRM. Makes a new try folder for one real prospect's work address, has the agent write a normal cold email, runs the send gate on it (refused for any number without a source), records the source from the company's own page, and runs it again (passes). Nothing goes to Gmail. Use when asked to "start with outbound-gates", "try it on my prospect", "show me the gate on a real company", or right after installing the plugin.
---

# First catch on your own prospect

Goal: the person watches their own agent's email to their own prospect get refused for a number it can't back up, then pass once the source is recorded. Nothing is sent and nothing goes to Gmail. Work only inside the try folder; never edit `.gates/` files or the generated `crm.json`.

## 1. Make the try folder

Ask the person for one prospect's **work** email address. Then run, in the current directory:

```
PYTHONPATH="${CLAUDE_SKILL_DIR}/../.." python3 -m gates.quickstart --prospect ADDRESS outbound-gates-try
```

- If it says the folder exists, use `outbound-gates-try-2` (then `-3`, and so on). It never writes into an existing folder.
- If it's refused as a free-mail address, ask for a work address at the company and run it again.

It prints three commands with the right paths. Use them exactly as printed. The try folder's CRM is generated for this one company and says so: it is not the person's CRM, and contact history isn't checked.

## 2. Write a normal cold email

Write the email you'd normally write to this prospect, in the try folder as `draft.txt`, plain text, with a greeting and a sign-off. Don't add a number just for the demo.

## 3. Check it

Run the printed check command. Show the person the result, **the number finding first**, then any style findings one line each.

- If it's refused for a number, go to step 4.
- If it passes because the email has no numbers, add one specific fact you believe about the company (a price, a count, a date) to the email and check again.

## 4. Record the source

For each refused number, find the exact sentence on the company's **own** site that contains it, and run the printed record command with URL, QUOTE (the exact sentence) and VALUE (the number as written in the email). Fix any style findings in the same rewrite.

- If the page can't be read (often a pricing page drawn by JavaScript), try **one** other page on the same site.
- If the site is on a different domain from the email address, say so plainly: "the address is at DOMAIN; SITE isn't trusted for this company without a CRM".
- If neither works, or the network is blocked, finish the demo on the bundled company instead: run `PYTHONPATH="${CLAUDE_SKILL_DIR}/../.." python3 -m gates.quickstart outbound-gates-demo` and its three printed commands (blocked, record, pass).

Never invent a quote, and never change the number to make it pass.

## 5. Check again and wrap up

Run the check command again. When it passes, show a five-line before/after: the number, why it was refused, the sentence and page that now back it, and PASS.

Then end with exactly one result line:
- `First catch on DOMAIN` when it passed on the prospect's own page, or
- `Finished on the demo company because REASON` (page not readable, site on another domain, or network blocked).

And this pointer: "To use it on real drafts, run /outbound-gates:setup in your outbound folder; /outbound-gates:status shows what's set."
