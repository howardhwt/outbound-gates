# Funnel definitions

A funnel number is only useful if someone else can recount it and get the same answer. These definitions and queries make that possible.

## Definitions

- **Contacted.** The company has a first-contact date. The date is the day of the first real email, not the day the record was created.
- **Replied.** A person answered, whether positive or negative. Auto-replies, ticket acknowledgements and bounces do not count.
- **Verified send.** An email to the company's domain exists in the sender's mailbox. Sends from a shared inbox are invisible to this check, so the two counts are reported side by side when they differ.

## Queries

```sql
-- Contacted, by lane and segment
SELECT lane, segment, COUNT(*)
FROM company
WHERE date_contacted IS NOT NULL
GROUP BY lane, segment;

-- Replied, by lane and segment
SELECT lane, segment, COUNT(*)
FROM company
WHERE date_contacted IS NOT NULL AND date_replied IS NOT NULL
GROUP BY lane, segment;

-- Prospect stage, by lane
SELECT lane, status, COUNT(*)
FROM company
WHERE lane IS NOT NULL
GROUP BY lane, status;

-- Real pipeline
SELECT stage, COUNT(*) FROM deal GROUP BY stage;
```

## Reading the result

- **Report reply rate by segment, not overall.** A blended rate hides the fact that one segment replies at ten times the rate of another. The segment split is what changes targeting.
- **Name what is excluded.** Inherited accounts and inbound leads are not replies to outbound. Count them separately.
- **Keep a change log.** When a number changes between two counts, record the old value, the new value and the reason. A number that moves without an explanation is not trusted the next time.

## Common ways the count goes wrong

| Mistake | Effect |
|---|---|
| Counting parked records as replies | Reply rate looks higher than it is |
| Using the record creation date as the contact date | Weekly activity is misplaced |
| Counting duplicate company records | Contacted count is inflated |
| Counting drafts as sends | Contacted count is inflated |
