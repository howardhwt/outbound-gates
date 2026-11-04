#!/bin/sh
# Runs the send gate and makes sure every failure blocks.
# If the Python gate cannot start or crashes (python3 missing, syntax error,
# import error, any non-zero exit), its output is discarded and a deny is
# printed instead. Only a clean exit lets the gate's own answer through.
root=${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}
out=$(PYTHONPATH="$root" "${GATE_PYTHON:-python3}" -m hooks.gate_gmail)
rc=$?
if [ "$rc" -ne 0 ]; then
  printf '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "send gate: gate crashed: exit %d"}}' "$rc"
  exit 0
fi
printf '%s' "$out"
exit 0
