---
name: setup
description: Install, check or remove the outbound-gates safety net (deny rules, guard and watchdog) for this project. Only a person starts this; it prints commands for the person to run.
disable-model-invocation: true
---

# Safety-net setup

The gate in this plugin only runs while the plugin is loaded. Setup installs the layers that hold when it isn't: deny rules for known send tools in this project, a guard that refuses send tools from any server, and a watchdog that warns at session start. See docs/send-gate.md, "Setting it up".

A person applies every change. The agent may run the preview, which writes nothing. It never runs a command with `--yes` or `--uninstall`.

## Steps

1. **Find the plugin folder.** The plugin root is two folders above this skill: `${CLAUDE_SKILL_DIR}/../..`. Turn it into an absolute path first (if the variable appears unfilled, use this skill's base directory and go up two folders). Call it PLUGIN below, and the current project folder PROJECT.

2. **Name check input (the agent does this).** Write the full names of every MCP tool you currently have, including deferred ones, one per line, to a temporary file outside the project, for example `$TMPDIR/loaded-tools.txt`. It is only read by the preview. The comparison is advisory: you supply the list.

3. **Preview (the agent runs this; it writes nothing):**

   ```
   python3 "PLUGIN/gates/setup.py" --project "PROJECT" --plugin-root "PLUGIN" --check-names "$TMPDIR/loaded-tools.txt"
   ```

   Add `--strict` if the person asked for strict mode. For a first run, add `--crm-source hubspot --sender-owner ID` or `--crm-source file --crm-path PATH` if the person gave them. Show the person the whole plan, every warning, the name check and the PLANHASH line. Then delete the temporary file.

4. **Apply (the person runs this).** The preview ends with the exact command, which includes `--yes PLANHASH`. Print it for the person to paste with the `!` prefix. Do not run it yourself. If anything changed since the preview, setup refuses with exit 3, and the person runs the preview again.

   Setup applies the plan, logs it to `~/.config/outbound-gates/setup.log`, runs the selftest and prints PASSED or FAILED with each check. If the first-run config step couldn't run (for example, the HubSpot key file is missing or too open), everything else is still installed. Setup says why and exits 1.

5. **Restart.** Tell the person to start a new Claude Code session in PROJECT. The new hooks only load at session start.

6. **Live check (in the new session).** Confirm the first lines include "send gate: on". Then call Gmail `update_draft` with draftId `gate-probe-nonexistent-0000` and subject `gate probe`. Expect a deny that starts with "send gate:". Never call a send, reply or forward tool to test anything.

## Strict mode

If the person asks for strict mode, add `--strict` to both the preview and the apply command. In a strict project only read tools and entries in `~/.config/outbound-gates/allow.json` run. Bash mail and data-sending commands are refused, and so are edits to the gate's own files. Tell the person that write tools their workflow needs (for example HubSpot updates) must be added to `allow.json` by them. `--no-strict` turns it off.

## Check later

Anyone can re-run the selftest. It only reads:

```
python3 ~/.config/outbound-gates/session_check.py selftest "PROJECT"
```

## Remove

Uninstall runs from the copy in `~/.config/outbound-gates/`, so it still works after the plugin is removed. Print these for the person; do not run them yourself:

```
! python3 ~/.config/outbound-gates/setup.py --project "PROJECT" --uninstall
```

That previews and prints the exact `--yes PLANHASH` command to run next.

Add `--all` to remove every project's entries and the user-level hooks. Run the uninstall before removing the plugin.
