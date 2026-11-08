"""The real gate, run in the visitor's browser (Pyodide) on the bundled demo company.

The site's "Run it live" button loads Pyodide, unpacks live.zip (built by demo/site.py: the gates package, this
file and demo/quickstart) and calls Session. Nothing here is a copy of the gate: check() and record() run the
same command-line entry points the README shows, and return what they print. Only .example pages are served,
from demo/quickstart/pages, so nothing goes over the network.
"""
from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path

from gates import evaluate, evidence, quickstart

ROOT = quickstart.PLUGIN_ROOT


class Session:
    """One visitor's try folder: a fresh copy of the demo company, kept until the page is reloaded."""

    def __init__(self, root: Path | None = None):
        root = Path(root) if root else Path(tempfile.mkdtemp(prefix="og-live-"))
        self.project = quickstart.copy_to(root / "outbound-gates-try")
        self.gates_home = self.project / ".gates"

    @property
    def draft(self) -> str:
        return (self.project / "draft.txt").read_text(encoding="utf-8")

    def check(self, draft: str) -> dict:
        """Run the whole send gate on the visitor's draft. passed is the gate's own verdict."""
        path = self.project / "visitor-draft.txt"
        path.write_text(draft, encoding="utf-8")
        code, out = _run(evaluate.main, [str(path), "--project", str(self.project), "--to", quickstart.CONTACT])
        return {"passed": code == 0, "exit": code, "output": _shown(out, self.project)}

    def record(self) -> dict:
        """Record the demo number's source: the exact sentence from the company's own (bundled) pricing page."""
        code, out = _run(evidence.main, ["record", "--gates-home", str(self.gates_home), "--contact",
                                         quickstart.CONTACT, "--url", quickstart.URL, "--quote", quickstart.QUOTE,
                                         "--value", "$0.006"])
        return {"recorded": code == 0, "exit": code, "output": _shown(out, self.project)}


def _run(main, argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            code = main(argv)
        except SystemExit as stop:  # argparse
            code = stop.code if isinstance(stop.code, int) else 2
    return code, buf.getvalue().strip()


def _shown(text: str, project: Path) -> str:
    """Show paths the way the rest of the page does (demo/site.py _clean), not the browser's own folders."""
    for path in sorted({str(project), str(project.resolve())}, key=len, reverse=True):
        text = text.replace(path, "~/outbound-gates-try")
    text = text.replace(f"PYTHONPATH={ROOT} ", "").replace(f"PYTHONPATH='{ROOT}' ", "")
    return text.replace(str(ROOT), "$OG")
