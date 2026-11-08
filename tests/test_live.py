"""demo/live.py: the gate the site runs in the visitor's browser, and live.zip, the bundle that carries it there.

The browser (Pyodide) isn't run in CI; these run the same Python entry point and the same bundle in CPython.
"""
import json
import subprocess
import sys
import zipfile

from demo import live, site


def test_the_live_gate_blocks_records_and_passes(tmp_path):
    s = live.Session(tmp_path)
    first = s.check(s.draft)
    assert not first["passed"] and first["output"].endswith("BLOCKED") and "'$0.006' has no sourced claim" in first["output"]
    assert s.record()["recorded"]
    assert s.check(s.draft)["passed"]


def test_an_edited_number_is_checked_again(tmp_path):
    s = live.Session(tmp_path)
    s.record()
    assert not s.check(s.draft.replace("$0.006", "$0.004"))["passed"]
    assert s.check(s.draft.replace("$0.006 per audio minute", "a per-minute rate"))["passed"]  # no number left


def test_the_live_output_shows_no_local_paths(tmp_path):
    s = live.Session(tmp_path)
    out = s.check(s.draft)["output"] + s.record()["output"]
    assert str(tmp_path) not in out and str(live.ROOT) not in out and "~/outbound-gates-try" in out


def test_the_bundle_runs_on_its_own(tmp_path):
    """Unpacked alone, with nothing else on the path, live.zip runs the whole catch: what the browser does."""
    (tmp_path / "live.zip").write_bytes(site.build_live_bundle())
    with zipfile.ZipFile(tmp_path / "live.zip") as z:
        z.extractall(tmp_path / "og")
    script = ("import json, sys; sys.path.insert(0, sys.argv[1]); from demo.live import Session; s = Session(); "
              "a = s.check(s.draft); b = s.record(); c = s.check(s.draft); "
              "print(json.dumps([a['passed'], b['recorded'], c['passed']]))")
    done = subprocess.run([sys.executable, "-I", "-c", script, str(tmp_path / "og")], capture_output=True, text=True,
                          cwd=tmp_path, env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert json.loads(done.stdout) == [False, True, True], done.stderr


def test_the_bundle_is_the_same_every_build():
    assert site.build_live_bundle() == site.build_live_bundle()


def test_the_page_loads_python_only_when_asked():
    page = site.build_page(site.run_catch())
    assert 'id="og-live-start"' in page and "live.zip" in page
    assert '<script src="' not in page  # nothing loads up front; the runtime is added on click
    assert site.PYODIDE.startswith("https://cdn.jsdelivr.net/pyodide/v0.") and site.PYODIDE in page  # pinned version
