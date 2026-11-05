# Design: the send service

Date: 2026-11-04
Status: DRAFT, for review. No code yet.
Repo: outbound-gates
Related: `docs/send-gate.md` (wave 1 and the local safety net), `docs/designs/mcp-send-gate.md` (the gate's design), `TODOS.md` ("The gate becomes the only way to send").

## Summary

Today an agent writes a Gmail draft, the gate checks it, and a person clicks Send. That stays for the next few months.

This doc is for later, when agents send. The decision on 2026-11-04 was "option 2 now, plan option 1 in parallel": keep drafts plus a human click now, and plan the service now so it is ready.

The core idea is simple. The agent never holds a credential that can send email. A separate service holds it. The service sends only what passes the same `evaluate()` the hook runs today, checked on the exact text it will send, against live HubSpot, with sources the service fetched itself.

Every local layer we have built (deny rules, hooks, the guard, strict mode) runs as the same OS user as the agent. A determined agent can edit settings, use Bash, or drive the signed-in browser. The service is the first layer that does not depend on the agent's good behaviour, because the thing it protects is not on the agent's side of the line.

## Goal

- An agent (Claude Code now, Clay later) can ask for an email to be sent. The email goes out only if it passes every gate.
- The agent can't send any other way from the sending mailbox, even if it ignores every local rule.
- Every send is capped, logged to HubSpot, can be stopped at once, and is never sent twice.

## Non-goals

- Not a sequencer. First touch only. No follow-ups, no replies, no threads in the first versions.
- No open or click tracking. No tracking pixels, no link rewriting.
- No model judges the email. The gates stay mechanical. Judgement is a person in the approval queue.
- Not multi-tenant. One owner, one or two sending mailboxes.
- Not a replacement for HubSpot. HubSpot stays the record of who was contacted.
- It does not contain the agent in general. Other channels (LinkedIn in a browser, Slack, calendar invites) are outside this service. See "What this does not close".

## Trust boundary

Who holds what, and what the agent can reach:

```
  OWNER'S MAC (OS user: owner)                 |  SEND SERVICE (hosted; see "Hosting")
  the agent runs here, as the owner            |  the agent can't log in here
                                               |
  Claude Code agent                            |  Holds (agent can never read these):
    has:   service request token               |    - Gmail OAuth refresh token, gmail.send only
           (can ASK to send, cannot send)      |    - HubSpot write key (log sends only)
           HubSpot read (connector)            |    - HubSpot read key
    not:   Gmail connector (removed)           |    - evidence store (pages + claims)
           browser skills (off)                |    - send ledger, suppression list
           any mailbox password or token       |    - caps, kill switch, approval queue
       |                                       |
       |  local MCP client (stdio)             |
       +------------- HTTPS + token -----------+-->  /claims   /check   /send
                                               |         |
  Clay HTTP column ----- HTTPS + own token ----+-->      |
                                               |         |
  Owner on phone ------- HTTPS + passkey ------+-->  /approve   /kill
                                               |         |
                                               |         v
                                               |   Gmail API  --------> recipient
                                               |   HubSpot API (read live, log the send)
                                               |   company websites (fetch sources)
```

| Actor | Can | Cannot |
|---|---|---|
| Agent | Record a claim (the service fetches the page). Ask for a check. Ask for a send. Read its own requests' status. | Read any service credential. Send without the gates. Approve. Flip the kill switch. Change caps or rules. Write to the evidence store. |
| Clay | The same as the agent, with its own token and lower caps. | The same as the agent. |
| Owner | Approve, reject, kill, unkill, change caps and rules, rotate tokens. | (Everything; the owner is the root of trust.) |
| Service | Send through Gmail, read and log to HubSpot, fetch public pages. | Read or send from any other mailbox. |

The request token is not a secret worth much. Whoever holds it can only ask. Every ask goes through the same gates, caps and approval as any other. If it leaks, the worst case is a burst of refused requests, and the owner rotates it.

## Options compared

### Hosting

The service has to run where the agent's OS user can't read its files. That is the whole point, so it decides the hosting.

| Option | Effort | Risk | What it closes |
|---|---|---|---|
| **A. Hosted: one small VM** (any cloud, smallest size, SQLite on disk) | M: about 2 to 3 days including TLS, secrets, backups | A public endpoint to defend. One more thing to patch. A few dollars a month. | All three local gaps: settings edits, Bash, and the token file. The agent's machine holds no send credential at all. Clay can reach it. Runs when the laptop is asleep. |
| **B. Hosted: serverless** (one function plus a managed database) | M to L: same as A, plus a managed store for the ledger, caps and evidence | Cold starts. State lives in a second service. More moving parts for idempotency. | Same as A. |
| **C. Same Mac, separate macOS user** (a non-admin `_gatesend` user, service under a LaunchDaemon, files mode 700) | S to M: about 1 to 2 days | Only holds if the agent's user can't become that user or root. The owner's account is usually an admin, so `sudo` with a cached ticket or a stored password breaks it. Backups, Time Machine and Spotlight must not expose its files. Clay can't reach it without a tunnel. Stops when the laptop sleeps. | Settings edits and the token file, if the permissions are right. Not Bash with `sudo`. |
| **D. Docker on the same Mac** | S: about 1 day | On macOS, Docker Desktop belongs to the owner's user. The agent can `docker exec` into the container, read its volumes, or read the env file the container started from. | Almost nothing against a determined agent. It only helps if the Docker daemon runs as a different user the agent can't reach, which is option C with extra steps. |

Honest note on C and D: a same-machine option only helps if the agent's user can't read the service's files or control the process. On a single-user Mac where the owner is an admin, that is hard to guarantee and easy to break by accident later. C is acceptable as a stepping stone. D is not a security boundary here.

### Channel

| Option | Effort | Risk | What it closes |
|---|---|---|---|
| **Gmail API**, own OAuth app, `gmail.send` scope only | M: OAuth app, consent once, token refresh, MIME building (stdlib) | `gmail.send` is a sensitive scope. If the mailbox is on Google Workspace, an Internal app avoids Google's app verification (confirm when building). Google's daily limits apply, but cold-email limits should be far lower anyway. | The service controls the exact bytes sent: it builds the MIME from the strings it checked. Nothing is rendered later by another system. |
| **Apollo API** (sequences or send-now) | M: plus mapping our draft onto Apollo's model | Apollo fills template variables and schedules at send time, so the text that leaves may not be the text the gate checked. The kill switch has to reach Apollo's own queue. An Apollo key usually carries far more power than "send one email". | Little that Gmail doesn't. It adds Apollo's mailbox rotation and sequences, which are non-goals for now. |
| **Both** | L | The weaknesses of both, and two paths to keep identical. | Only worth it if volume outgrows one mailbox. |

### How agents call it

| Caller | How | Notes |
|---|---|---|
| Claude Code | A local MCP server (stdio), the one already planned in `TODOS.md` as `gates/mcp_server.py`. In service mode it is a thin client: each tool forwards to the service over HTTPS with the request token. Tools: `record_claim`, `check`, `send`, `status`. | The MCP server holds only the request token. It runs as the agent's user, so treat it as untrusted: it can lie, and that is fine, because the service decides. |
| Clay | An HTTP API column calling `/check` or `/send` directly, with Clay's own token. | Clay's enrichment values are claims like any other. Clay must call `/claims` with a URL and quote for every number, or the send is refused. Clay gets lower caps. |
| Anyone else | Refused. One token per caller, no anonymous access. | |

### How the agent authenticates

| Option | Effort | Risk | Notes |
|---|---|---|---|
| **Request-only bearer token per caller**, stored in the agent's environment or a file it can read | S | A leaked token lets someone ask, not send. | Recommended. The token's scope is "ask", enforced by the service, not by the token. |
| Signed requests (HMAC per request) | S to M | Same as above; the key sits on the same machine. | Adds nothing while the key lives where the agent can read it. |
| Mutual TLS | M | Same as above. | Same reasoning. |

The point is not to hide the agent's credential from the agent. It can't be hidden on a machine it controls. The point is that the agent's credential is worth little. The valuable credentials (Gmail, HubSpot write) never leave the service.

The owner's own actions (approve, kill, caps) need a credential the agent can't use: a passkey on the owner's phone, or a sign-in the agent's browser never holds. Never a token in a file on the Mac.

## The send flow

```
 agent / Clay
     |
     |  POST /send  {idempotency_key, to, subject, body, mode}
     v
 1. Authenticate the caller token. Unknown token: 401, logged.
     |
 2. Idempotency: same key seen before? Return the stored result. Do nothing else.
     |
 3. Kill switch on? Refuse: "sending is stopped".
     |
 4. Shape: one recipient, plain text only, no attachments, no reply ids,
    no unknown fields. Build the exact text the recipient will get:
    body + the service's fixed footer (address, opt-out line).
     |
 5. Suppression list (service-side): unsubscribed, bounced, do-not-email,
    or already in the send ledger? Refuse.
     |
 6. Live HubSpot read (service's read key): company, notes, tasks, deals,
    logged emails, contact.  Any error or timeout: refuse.
     |
 7. Claims: load from the SERVICE's evidence store only. Re-verify each
    snapshot (hash, quote, value). Too old? The service re-fetches the
    page itself; if the quote is gone, refuse.
     |
 8. evaluate(Draft(to, subject, [("body", final_text)]), crm, settings,
             gates_home=<service store>, today=<service clock>)
    = checker rules on the exact final text + pre-send on live HubSpot.
    Fail: refuse with every finding.
     |
 9. Caps: per day, per domain, per hour pacing. Over: queue for later or refuse.
     |
10. Approval: in "approve every send" or when an exception rule fires,
    park it in the queue. The owner approves on the phone. Before sending,
    steps 3, 5, 6 and 8 run AGAIN (the CRM may have changed while it waited).
     |
11. Ledger: write {key, recipient, sha256 of the exact MIME, state: sending}.
     |
12. Mode:  dry_run -> stop here, return "would send".
           test    -> send only if the recipient is on the test-inbox list.
           live    -> send.
    Gmail API users.messages.send with the MIME built in step 4.
     |
13. Ledger: state sent + Gmail message id.  Gmail error: state failed.
    No answer (timeout, crash): state unknown, never retried automatically.
     |
14. Log: HubSpot SENT note and email engagement on the contact and company
    (service's write key), and one line in the service audit log.
    A HubSpot logging failure does not unsend; it raises an alert and
    the ledger still blocks a second send.
```

Rules that hold through the whole flow:

- The service never trusts anything the caller says about the verdict, the CRM, or the sources. It only takes the recipient, the subject and the body as the caller's words.
- The text checked in step 8 is byte for byte the text sent in step 12. Nothing is templated or rendered after the check.
- Every refusal names the rule, like the hook does today, so the agent can fix and resubmit with a new idempotency key.

### Where claims live now, and how that changes

**Now.** The agent runs `python -m gates.evidence record` through Bash. It fetches the page and writes `.gates/evidence/<sha256>.txt` and `.gates/drafts/<domain>/<contact>/claims/<id>.json` in the project folder. The hook reads them back. Both folders are writable by the agent, so a determined agent can write a fake page and a matching claim (`docs/send-gate.md`, "Trust boundary").

**With the service.** The same `record_claim()` runs inside the service, on the service's own store:

- The agent calls `record_claim(contact, url, quote, value)` through MCP. The service fetches the URL itself, with the existing checks (http(s) only, public address, the company's own domain, at most 5 redirects, 2 MB cap).
- The page text and the claim are stored service-side, keyed per recipient as today.
- At send time the service reads claims only from its own store. A `.gates/` folder on the agent's machine is ignored. It can stay as a read-only copy for the agent to look at, nothing more.
- `source_allowlist` moves to service config.

One gap stays and needs a decision (Open decision 8): the allowed source hosts include alternate domains read from HubSpot. If the agent can write to HubSpot, it can add a domain it controls. The service should accept only the primary domain plus alternates set by a person, or plus the service's own allow list.

## Safety controls

### Caps

| Cap | Starting value | Why |
|---|---|---|
| Per day, all callers | 10 | Small enough to read every send; matches a new mailbox's warm-up. |
| Per day, Clay | 5, inside the total | Clay runs unattended in batches. |
| Per company domain | 1 first touch per 7 days | Matches the 7-day colleague window in `presend.py`. Turns that warning into a hard rule for automatic sends. |
| Per recipient | 1 first touch, ever | First touch only. The ledger enforces it even if HubSpot logging lags. |
| Pacing | At least 3 minutes between sends, with random jitter | Bursts look like a bulk sender. |
| Send window | Weekdays, business hours in the sender's time zone | Fewer sends at odd hours that look automated. |

Caps live in service config. Only the owner changes them.

### Kill switch

- One flag in the service. When on, every send is refused and every queued send stays queued.
- The owner flips it from the phone (passkey). The agent's token can't touch it.
- It trips by itself when: a hard bounce rate over 5% in the last 20 sends, any spam complaint the service hears about, three Gmail errors in a row, any send in state `unknown`, or HubSpot logging failing twice in a row.
- Turning it off again is always manual.

### Modes

| Mode | What happens | Use |
|---|---|---|
| `dry_run` | Every step except the Gmail call. Returns "would send" with the verdict. | Default for new callers. The agent's "check" path. |
| `test` | Sends only to addresses on the test-inbox list (mailboxes the owner controls on a test domain). Any other recipient: refused. | First PRs, and after any change to the service. |
| `live` | Sends to real recipients, subject to everything above. | Only after the test phase. |

The mode is set per caller token in service config. A caller can ask for a stricter mode, never a looser one.

### Approval: every send, or by exception

| Mode | How it works | Cost | Risk |
|---|---|---|---|
| **Approve every send** | Each passing send waits in a queue. The owner sees the exact text and the verdict on the phone and taps approve or reject. | About 20 seconds a send. The same as clicking Send in Gmail today, but from one queue. | Low. A person reads every email. |
| **Approve by exception** | Sends go out by themselves unless a rule flags them: any `crm_coverage` warning, a colleague emailed in the last 30 days, a new company domain for this caller, a claim older than 14 days, a body over the usual length, or 1 in 10 at random. | Only flagged sends cost time. | Mechanical gates don't catch a false claim with no number ("we already work with your team"), a wrong name, or a bad tone. Sampling catches some of that, not all. |
| **Fully automatic** | No queue. | None. | The above, at full volume. Not recommended at any stage of this plan. |

### Idempotency (no double sends)

- Every request carries an `idempotency_key`. Same key: same answer, no new work.
- The ledger also holds a content key: recipient plus the sha256 of the exact MIME. A second request with a new key and the same content is refused.
- Per recipient, only one request at a time (a lock), so two agents can't race.
- The ledger state is written before the Gmail call. A crash between "sending" and "sent" leaves `unknown`, which trips the kill switch and waits for the owner to check the Sent folder. Never retry an `unknown` automatically: a duplicate cold email is worse than a missed one.
- The pre-send check's "already contacted" rule is a second, slower layer, because HubSpot logging can lag.

### Replies and threads

- First touch only. Any reply id, `In-Reply-To` or `References` header, or `Re:` subject is refused.
- When the company replies, HubSpot's `date_replied` or an incoming logged email blocks any further first touch (the existing `replied` rule). The owner answers in Gmail.
- Follow-ups are a later design. They need the thread, the earlier text and a promise check.

### Bounces and unsubscribes

- The service keeps its own suppression list. It is checked before HubSpot, so a slow HubSpot can't let a suppressed address through.
- Unsubscribe: the footer says to reply with "unsubscribe", and the message carries a `List-Unsubscribe` header pointing at a service URL. A click there adds the address to the suppression list at once and writes the opt-out to HubSpot (`hs_email_optout` or the mapped property).
- Bounces, first version: the owner sees bounces in Gmail and adds them with one tap in the service page.
- Bounces and "unsubscribe" replies, later version: the service reads only bounce and reply metadata. That needs a read scope on the mailbox (`gmail.metadata` or `gmail.readonly`, both restricted), so it is a separate decision with its own risk.

### CAN-SPAM basics

Not legal advice. Check with counsel before the first live send, and before any recipient outside the US (other countries are stricter, and some need consent first).

- A valid physical postal address in every email. The footer adds it; the agent can't remove it.
- A clear way to opt out, honoured within 10 business days. The service honours it at once.
- Accurate From, Reply-To and routing headers. The service sets them; the agent can't.
- A subject line that isn't misleading. The gate's banned-phrase list can carry patterns such as a fake `Re:`.
- The footer is the service's text, not the agent's. It is checked once against the rules when it is configured. The length rule counts the body without the footer.

### Deliverability

- Send from a dedicated mailbox on a sending subdomain (for example `mail.yourcompany.example`), not the main domain, so a bad week doesn't hurt the company's everyday email.
- SPF, DKIM and DMARC set up and passing before the first live send. The service checks DKIM alignment on test sends.
- Warm up: 5 a day for the first week, then raise the cap by a few a week. Watch Google Postmaster Tools.
- Plain text only, no tracking, few or no links. That suits the gate's rules already.
- Keep bounces under 2% and spam complaints well under 0.3%. The kill switch trips earlier than that.

## What changes in the existing repo

**Reused as is, run inside the service:**

| Module | Use in the service |
|---|---|
| `gates/evaluate.py` | The verdict, unchanged. `gates_home` points at the service store; `today` comes from the service clock. |
| `gates/checker.py` | Unchanged. |
| `gates/presend.py` | Unchanged. The service adds its own ledger and suppression checks in front of it. |
| `gates/evidence.py` | `record_claim`, `verify_claim`, `claims_for`, the URL and fetch checks. `load_settings` needs a service variant that reads service config, not the project's `.gates/config.json` (the agent can write that file). |
| `gates/hubspot.py` | The read path, unchanged, with a looser budget (no 60 s hook limit, but still a deadline). A new, separate write path for logging sends, using a second key. |

**New:** the service (HTTP server, Gmail sender, ledger in SQLite, queue, kill switch, owner page), all stdlib if possible; and the MCP client tools.

**Must change outside the code, for the service to mean anything:**

- **Remove the Gmail connector from outbound sessions.** Deny the whole Gmail server in the outbound project, not only send, reply and forward. With the service, the agent has no reason to touch Gmail at all.
- **Turn browser skills off** in outbound projects, and keep the sending mailbox signed in only to the service. If any browser the agent can drive is signed in to that mailbox, the agent can click Send and the service protects nothing.
- **The owner reads replies** in a browser profile or device the agent can't drive, or the mailbox forwards replies to the owner's main inbox.
- **No HubSpot write key on the Mac** beyond what the agent strictly needs. See Risk 3.

**What of the local safety net stays useful:**

| Layer | Still useful? | Why |
|---|---|---|
| `hooks/gate_gmail.py` | Yes, until the Gmail connector is removed. Then it is a backstop that should never fire. | Drafts stay the path during migration. |
| Deny rules and the guard | Yes | Other channels (Apollo, Slack, Make, calendar) are not covered by the service. These stop the obvious mistakes there. |
| Strict mode | Yes | Same reason, plus it stops casual `curl` posts. |
| The watchdog and heartbeat | Partly | Useful while drafts exist. Less important once sending is service-only. |
| `.gates/` evidence on the Mac | No, for trust | Becomes a read-only view at most. |

The local net goes back to what it is good at: catching mistakes and shortcuts early with a fast, clear refusal. Containment moves to the service.

## Migration path

Each PR is small and proves one thing. Nothing sends to a real prospect before PR 4.

| PR | What it adds | What it proves |
|---|---|---|
| 0 | This doc. | Agreement on the boundary and the open decisions. |
| 1 | Service core: `/check` and `/send`, `evaluate()` server-side, service-side `record_claim`, Gmail API with `gmail.send`, SQLite ledger with idempotency, kill switch, `dry_run` and `test` modes. **Test inboxes only**; `live` doesn't exist yet in the code. | The service can send. What it sends is byte for byte what it checked. A second request is not a second send. From the agent's OS user, reading the Gmail token fails with "permission denied" (or the file isn't on the machine at all). |
| 2 | MCP client tools (`record_claim`, `check`, `send`, `status`) and the setup change that denies the whole Gmail server and browser skills in outbound projects. | An agent can do the whole flow with no Gmail connector. A forged `.gates/` claim on the Mac makes no difference to the verdict. |
| 3 | Live HubSpot read in the service, HubSpot logging of each send (write key), caps and pacing. | A test send shows up in HubSpot as a SENT note. A second first touch to the same test address is refused by both the ledger and the pre-send check. Cap 11 of 10 is refused. |
| 4 | Approval queue (every send) on the owner's phone, the compliance footer, the unsubscribe URL and suppression list, `live` mode with a cap of 5 a day. Sending domain DNS done. | First real sends, each one read and approved by a person. An unsubscribe click blocks the next send within seconds. |
| 5 | Kill switch auto-trips, bounce entry from the owner page, the alert channel. | A simulated bounce burst on test inboxes stops sending, and only the owner can restart it. |
| 6 | Approve by exception, after the bar in Open decision 4 is met. | Flagged sends still wait. Sampling works. |
| 7 | Clay token and HTTP column recipe, with its own caps. | Clay can't send a number it didn't source through `/claims`. |
| 8 | Retire agent-side evidence for trust. Update `docs/send-gate.md` and the README. | The docs match what runs. |

## Open decisions for Howard

1. **Hosting.** Recommended: **hosted, one small VM with SQLite** (option A). It is the only option that closes Bash and settings edits for certain, works with the laptop closed, and lets Clay call it. Use the separate macOS user (C) only if you want a one-week stepping stone before paying for hosting, and never Docker on the same Mac (D) as the boundary.
2. **Channel.** Recommended: **Gmail API only, `gmail.send` scope, an Internal OAuth app** on the Workspace domain. Leave Apollo for sending out until sequences are in scope, because Apollo renders text after the check.
3. **Sending mailbox.** Recommended: **a dedicated mailbox on a sending subdomain**, signed in only to the service. The owner reads its replies through forwarding or in a profile the agent can't drive.
4. **Approval mode.** Recommended: **approve every send for the first 50 live sends or 4 weeks, whichever is later**, then approve by exception with the triggers above and 1 in 10 sampled. Never fully automatic. Go back to "approve every send" after any incident.
5. **Caps.** Recommended: **10 a day total, 1 per company domain per 7 days, 1 first touch per recipient ever, 3 minutes apart with jitter, weekday business hours.** Start live at 5 a day during warm-up. Raise only after two clean weeks.
6. **Clay.** Recommended: **defer until the Claude Code path has run 4 clean weeks.** Then give Clay its own token, a cap of 5 a day inside the total, and no way around `/claims`.
7. **Bounce and reply reading.** Recommended: **start manual** (owner adds bounces in one tap) and keep the service on `gmail.send` only. Add a read scope later only if bounces become frequent enough to matter.
8. **Source hosts from HubSpot.** Recommended: **primary domain plus the service's own allow list only**, ignoring HubSpot alternate domains unless a person set them. This stops an agent with HubSpot write access from adding its own site as a source.
9. **Claim age at send time.** Recommended: **a snapshot is good for 30 days; older ones are re-fetched by the service at send time**, and the send is refused if the quote is gone.

## Risks

1. **Low volume makes this not worth building.** If sends stay around 10 a day, clicking Send in Gmail costs a few minutes and the draft gate already does most of the work. The service costs about 2 to 3 weeks to build through PR 5, plus hosting, patching and compliance upkeep for one person. Build it when one of these is true: drafting volume makes the human click the bottleneck, you want unattended runs (overnight, Clay), or a second person's agents need to send under the same rules. If none is true by the time PR 1 would start, keep drafts and stop at this doc.
2. **The service holds a credential that can send anything.** A compromised service is worse than a compromised agent, because the refresh token can send any text. Mitigations: `gmail.send` only, secrets in the host's secret store, no shell on the host from the Mac, alerts on unusual volume, a documented token revoke.
3. **The CRM is the agent's to write.** The pre-send check reads HubSpot. If the agent can delete a "DO NOT EMAIL" note, close a task, or clear an opt-out, it can make the check pass. The service's own ledger and suppression list cover sends it made and opt-outs it heard. They don't cover a note a person wrote. Mitigation: keep the agent's HubSpot access to reads plus named writes (strict mode's allow list), and consider making the service treat HubSpot property history as a signal ("opt-out cleared in the last day: refuse").
4. **Mechanical gates miss soft falsehoods.** A claim without a number, a wrong first name, an overfamiliar tone. Approval catches these; approval by exception catches some. That is why "fully automatic" is not in this plan.
5. **Google's view of automated cold email.** Even well-behaved API sending from a new subdomain can land in spam, or get the mailbox limited. Warm-up, low caps and the auto kill switch reduce this, and the subdomain keeps it away from everyday mail.
6. **Legal.** CAN-SPAM is the floor in the US. Other countries require consent first. The service can refuse recipients by country only if we know the country, which we often don't. Counsel review before PR 4.
7. **HubSpot or Gmail down.** The service fails closed: no CRM read, no send. Sends wait in the queue. That is the right failure, but it means a HubSpot outage is a sending outage.
8. **Two systems to keep in step.** The hook and the service must give the same verdict on the same input. Both call `evaluate()`, and a shared test feeds both the same fixtures.

## What this does not close

The service protects one thing well: email from the sending mailbox. It does not stop an agent from:

- sending from another mailbox the owner is signed in to in a browser the agent can drive,
- messaging on LinkedIn, Slack or other tools the local net doesn't cover,
- running code elsewhere (cloud routines, remote sandboxes).

Those stay with the local net, strict mode, and keeping browser skills off in outbound projects. Say so plainly in the README when the service ships, as `docs/send-gate.md` does for the hook today.
