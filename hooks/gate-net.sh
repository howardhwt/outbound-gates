#!/bin/sh
# Wrapper for the user-level safety-net hooks that setup installs.
#   gate-net.sh draft-guard   (PreToolUse): any failure becomes a deny
#   gate-net.sh watchdog      (SessionStart): any failure becomes a warning
# Failures include python3 missing, session_check.py missing and any crash.
# Outside a project with a .gates/ folder it never blocks or warns.
mode=$1
dir=$(cd "$(dirname "$0")" && pwd)
project=${CLAUDE_PROJECT_DIR:-$PWD}
out=$("${GATE_PYTHON:-python3}" "$dir/session_check.py" "$mode" 2>/dev/null)
rc=$?
if [ "$rc" -eq 0 ]; then
  printf '%s' "$out"
  exit 0
fi
[ -d "$project/.gates" ] || exit 0
if [ "$mode" = "draft-guard" ]; then
  printf '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "send gate guard: the guard could not run (exit %d), so this call is refused. Re-run setup."}}' "$rc"
else
  printf '{"systemMessage": "WARNING send gate watchdog could not run (exit %d). Re-run setup (/outbound-gates:setup)."}' "$rc"
fi
exit 0
