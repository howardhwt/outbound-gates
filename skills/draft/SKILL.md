---
name: draft
description: Write one email from one verdict card, run it through the gates, and leave it as a draft for a person to send. Never sends.
---

# Draft

One verdict card in, one email draft out. The draft stops at the first gate it fails.

## Steps

1. **Build the claim file first.** Every number, name and product fact in the email gets a row: the claim, its value, the URL, the date checked. Rows come from the verdict card and nowhere else. A claim with no URL becomes a question in the email, or it is cut. See `examples/claims.json`.
2. **Pick the register.** A first email to a named person, a first email to a role address, or a follow-up. Each has its own length and sign-off in the gate config.
3. **Write by arm.**
   - *switch*: one observation about their product, then a question about unit cost, then the offer.
   - *supply*: their listed price, your price, then what they keep per unit as a number and a percentage.
   - Other arms get no draft. Say why and stop.
4. **Run the gate checker.**
   ```
   python -m gates draft.txt --config gates/config.json --register cold --claims claims.json
   ```
   Fix what it reports and run it again. After two failed rounds, stop and report the failing rule.
5. **Run the pre-send check.** See `skills/presend-check`.
6. **Create the draft in the mail client.** Include the CRM logging address in BCC so the send is recorded.
7. **Log it at once.** Write a DRAFTED note and a SEND task on the company before starting the next account. A draft that is not logged will be written twice.

## What a draft never contains

- A capability that is not on the approved capability list.
- A claim about quality, speed or capacity that nobody has measured.
- A statement of what the reader pays. Quote what they list. Ask what they pay.
- A reason the reader could use to say no.
