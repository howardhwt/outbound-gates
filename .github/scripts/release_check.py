"""Release guard: the tag must name the version in .claude-plugin/plugin.json.

Tags look like outbound-gates--v0.5.1 (the convention since 0.1.0).
Usage: python3 .github/scripts/release_check.py <tag>   -> prints the version, exit 0
       mismatched or malformed tag                      -> message on stderr, exit 1
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TAG_RE = re.compile(r"^outbound-gates--v(\d+\.\d+\.\d+)$")


def check(tag: str, plugin_json: Path) -> str:
    match = TAG_RE.match(tag)
    if not match:
        raise ValueError(f"tag {tag!r} is not outbound-gates--vX.Y.Z")
    version = json.loads(plugin_json.read_text(encoding="utf-8"))["version"]
    if match.group(1) != version:
        raise ValueError(f"tag {tag!r} says {match.group(1)}, but plugin.json says {version}")
    return version


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[2]
    try:
        print(check(argv[1] if len(argv) > 1 else "", root / ".claude-plugin" / "plugin.json"))
    except (ValueError, OSError, KeyError) as err:
        print(f"release check failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
