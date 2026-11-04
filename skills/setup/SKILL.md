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

2. **Preview (the agent runs this; it writes nothing):**

   ```
   python3 "PLUGIN/gates/setup.py" --project "PROJECT" --plugin-root "PLUGIN"
   ```

   Show the person the whole plan, including every warning.

3. **Apply (the person runs this).** Print this line for the person to paste with the `!` prefix. Do not run it yourself:

   ```
   ! python3 "PLUGIN/gates/setup.py" --project "PROJECT" --plugin-root "PLUGIN" --yes
   ```

   Setup applies the plan, then runs the selftest and prints PASSED or FAILED with each check.

4. **Restart.** Tell the person to start a new Claude Code session in PROJECT. The new hooks only load at session start.

5. **Live check (in the new session).** Confirm the first lines include "send gate: on". Then call Gmail `update_draft` with draftId `gate-probe-nonexistent-0000` and subject `gate probe`. Expect a deny that starts with "send gate:". Never call a send, reply or forward tool to test anything.

## Check later

Anyone can re-run the selftest. It only reads:

```
python3 ~/.config/outbound-gates/session_check.py selftest "PROJECT"
```

## Remove

Uninstall runs from the copy in `~/.config/outbound-gates/`, so it still works after the plugin is removed. Print these for the person; do not run them yourself:

```
! python3 ~/.config/outbound-gates/setup.py --project "PROJECT" --uninstall
! python3 ~/.config/outbound-gates/setup.py --project "PROJECT" --uninstall --yes
```

Add `--all` to remove every project's entries and the user-level hooks. Run the uninstall before removing the plugin.
