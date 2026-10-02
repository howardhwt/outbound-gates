# outbound-gates

A small, opinionated system for running AI-assisted outbound without sending anything untrue.

A language model writes a plausible cold email in seconds. The hard part is everything around it: finding the right company, proving each fact, not emailing someone twice, and keeping the CRM honest. This repo holds the parts that do that work.

Everything here uses a fictional product, **Acme Transcribe**, a speech-to-text API priced at $0.002 per audio minute. Swap in your own product, prices and rules.

## What's in it

| Path | What it is |
|---|---|
| `gates/checker.py` | A deterministic checker that blocks a draft on length, banned phrases, dashes, unsourced numbers and cost assertions |
| `skills/` | Four agent skills: source, qualify, draft and pre-send check |
| `docs/crm-model.md` | The CRM conventions the skills write to |
| `docs/funnel-definitions.md` | Definitions and queries that make the funnel recountable |
| `examples/` | A draft that passes, a draft that fails, and a claim file |

## How the pieces fit

```
source  ->  qualify  ->  draft  ->  gate checker  ->  pre-send check  ->  human sends
 (wide)    (verdict     (claim       (blocks on         (blocks on
            card with    file per     mechanical         CRM history)
            URLs)        draft)       rules)
```

Each step writes to the CRM before the next one starts. A person sends every email. Nothing in this repo sends.

## The gate checker

```
python -m gates examples/draft_pass.txt \
  --config gates/config.example.json --register cold --claims examples/claims.json
```

It exits 0 on a pass and 1 on a block, so it fits in a script or a pipeline.

| Rule | What it blocks |
|---|---|
| `dashes` | Em dashes, en dashes, and more spaced hyphens than the config allows |
| `length` | A word count outside the range for the register |
| `sign_off` | A missing or wrong sign-off |
| `required_close` | A cold email without the fixed closing line |
| `banned_phrase` | Any phrase on the banned list |
| `bold` | Bold text that is not the product name or a price |
| `cost_assertion` | A sentence that states what the reader pays. Asking is allowed |
| `claims` | A number in the draft with no sourced claim behind it, or a claim with no URL |
| `rhythm` | Warns on few contractions or three same-length sentences in a row |

The `claims` rule matters most. Every number in an email must appear in that draft's claim file with a URL and the date it was checked. A claim older than the configured age produces a warning. Numbers you derive from a sourced value, such as a margin, are listed under that claim's `derived` field.

Run the tests with `python -m pytest`.

## Why each rule exists

Each rule exists because the failure it prevents is easy to make with a model in the loop.

- **Invented facts.** A model will state a headcount, a funding round or a price with full confidence. The claim file forces a URL behind every number.
- **Stale facts.** A price that was true a month ago goes out in a follow-up. Claims carry a checked date.
- **Stating what the reader pays.** You can verify what a company lists. You cannot know what it pays, and the first technical reply will say so.
- **Double drafting.** A run that drafts ten emails and logs them at the end will, if interrupted, draft the same ten tomorrow. Each draft is logged before the next begins.
- **Emailing a disqualified account.** A check that only reads email history misses a disqualification recorded on the company. The pre-send check reads status, deals and notes first.
- **Counting drafts as sends.** Status moves when a person confirms the send, never when a draft is created.

## Adapting it

1. Copy `gates/config.example.json` and set your registers, banned phrases and bold allow list.
2. Put your own product's facts in `own_facts`.
3. Rewrite the arms in `skills/qualify` for how your buyers differ.
4. Map the statuses and keywords in `docs/crm-model.md` onto your CRM.

## License

MIT
