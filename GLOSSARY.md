# outbound-gates

Gates that stop an AI agent's outbound email from saying anything untrue or reaching anyone it shouldn't. A person sends; the agent only drafts.

## Language

### The check

**Gate**:
The check every agent-written draft passes before it exists in the mail client: rules, sources and the pre-send check.
_Avoid_: filter, validator, linter

**Truth rules**:
The rules everyone shares and no project can loosen: every number needs a source, no statements of what the reader pays.
_Avoid_: defaults, base config

**Style**:
How a project's emails read (length, sign-off, closing line, banned phrases, bold), chosen per project.
_Avoid_: tone config, house rules

**Register**:
A kind of email with its own style, such as a cold email or a follow-up.
_Avoid_: template, email type

**Verified source**:
The exact sentence on the company's own page that contains a number in the draft, stored with the page.
_Avoid_: claim file, citation, evidence row

**Calculation**:
A number the draft works out from verified sources and own facts, which the gate recomputes.
_Avoid_: derived value, maths

**Try folder**:
A new folder made for a first catch, holding a generated one-company CRM for the tester's prospect that is clearly marked as not their real CRM.
_Avoid_: no-CRM mode, sandbox, demo project

**First catch**:
The moment a tester's own agent, writing to the tester's own prospect, is refused for an unsourced number and then passes once the source is recorded.
_Avoid_: aha moment, demo, onboarding

### The person's facts

**Own facts**:
The sender's own numbers, such as their price, which pass without a source.
_Avoid_: product facts, constants

**Names**:
Words with digits in them that aren't numbers, such as a model name or a video format.
_Avoid_: ignore list, exceptions

**Logging BCC**:
The CRM's address that a draft may copy so the send is recorded.
_Avoid_: CRM BCC, tracking address

### Control

**Person-only change**:
A change that loosens what the agent may do or say: own facts, names, logging BCC, the allow list, style, a safety-net refresh.
_Avoid_: admin change, config edit

**Proposal**:
A person-only change the agent has staged but that has no effect until the person approves it.
_Avoid_: pending change, request, draft change

**Approval code**:
The short code printed with a proposal that the person types to apply exactly that proposal.
_Avoid_: PLANHASH, token, OTP

**Safety net**:
The parts installed outside the plugin that still hold when the plugin isn't loaded: deny rules, a guard and a watchdog.
_Avoid_: fallback, backup hooks

**Strict mode**:
A project setting where the agent may only read, create gated drafts and use allowed tools, and can't change the gate's own files.
_Avoid_: lockdown, safe mode

## Relationships

- A **Gate** applies the **Truth rules** and one **Style**; the **Style** holds one or more **Registers**
- A number in a draft passes through a **Verified source**, a **Calculation**, or the **Own facts**; **Names** are not numbers
- An agent turns a **Person-only change** into a **Proposal**; only the person's **Approval code** applies it
- **Strict mode** is what stops the agent applying a **Proposal** itself
- A **First catch** happens in a **Try folder**; daily work happens in a project connected to the real CRM
