"""Copy the quickstart project somewhere to try the send gate on it.

    python -m gates.quickstart [FOLDER]        (default: ./outbound-gates-try)

The project has one fictional company, one fixture pricing page and one draft
with one number in it. The gate blocks the draft until the number's source is
recorded, then lets it through. Nothing goes over the network. The command
prints the three commands to run next, with this plugin's path filled in.
"""
from __future__ import annotations

import argparse
import shlex
import shutil
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PLUGIN_ROOT / "demo" / "quickstart"
CONTACT = "dana@quickstart-co.example"
URL = "https://quickstart-co.example/pricing"
QUOTE = "Transcription is billed at $0.006 per audio minute"


def copy_to(dest: Path) -> Path:
    """Copy the project to dest (which must not exist) and return its absolute path."""
    dest = dest.expanduser().resolve()
    if dest.exists():
        raise FileExistsError(f"{dest} already exists; pick a new folder")
    shutil.copytree(SOURCE, dest)
    (dest / "gates").rename(dest / ".gates")
    return dest


def next_commands(dest: Path) -> list[str]:
    q = shlex.quote
    py = f"PYTHONPATH={q(str(PLUGIN_ROOT))} python3 -m"
    check = f"{py} gates.evaluate {q(str(dest / 'draft.txt'))} --project {q(str(dest))} --to {CONTACT}"
    record = (f"{py} gates.evidence record --gates-home {q(str(dest / '.gates'))} --contact {CONTACT}"
              f" --url {URL} --quote {q(QUOTE)} --value '$0.006'")
    return [check, record, check]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy the quickstart project to try the send gate.")
    parser.add_argument("folder", nargs="?", default="outbound-gates-try", help="where to put it (must not exist)")
    args = parser.parse_args(argv)
    try:
        dest = copy_to(Path(args.folder))
    except (FileExistsError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    check, record, again = next_commands(dest)
    print(f"Quickstart project ready in {dest}\n")
    print("1. Check the draft. It is BLOCKED: $0.006 has no recorded source.")
    print(f"   {check}\n")
    print("2. Record the source: the exact sentence on the company's pricing page.")
    print(f"   {record}\n")
    print("3. Check again. It passes.")
    print(f"   {again}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
