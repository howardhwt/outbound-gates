# Person-only changes are approved by typing a code, not by a permission prompt

The agent may propose a change that loosens what it can do or say (own facts, names, the logging BCC, allowed tools, a project's style, a safety-net refresh), but only the person applies it, by typing `! ~/.config/outbound-gates/og yes CODE`. We chose this over having the guard answer `ask`, which would show Claude Code's Yes/No prompt, because a test on 2026-11-05 showed that in bypass-permissions mode a hook's `ask` is approved without any prompt, and people who use the gate daily tend to run in that mode. Commands typed with `!` skip the hooks, and in strict mode the guard refuses the agent's own `og yes`, so the approval works in every permission mode.

## Considered options

- **Hook `ask`:** nicest in normal mode, silently becomes "the agent decides" in bypass mode.
- **`ask` in normal mode, refuse in bypass:** two behaviours to learn for the same change.
- **The person pastes a full command** (the setup PLANHASH flow): safe in every mode, but long commands with paths and versions were the main reason the setup felt hard to use.

## Consequences

- The code is a hash of exactly what is staged, so a change added after the person read the proposal makes the code fail.
- Outside strict mode the guard doesn't watch Bash, so an agent could run `og yes` itself. The protection holds in strict projects; elsewhere it's a convention.
