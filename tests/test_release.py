"""The release guard: a tag must name the version in plugin.json."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "release_check.py"
spec = importlib.util.spec_from_file_location("release_check", SCRIPT)
release_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_check)


def plugin(tmp_path, version):
    path = tmp_path / "plugin.json"
    path.write_text(json.dumps({"name": "outbound-gates", "version": version}))
    return path


def test_matching_tag(tmp_path):
    assert release_check.check("outbound-gates--v0.5.1", plugin(tmp_path, "0.5.1")) == "0.5.1"


@pytest.mark.parametrize("tag,error", [
    ("outbound-gates--v0.5.2", "says 0.5.2, but plugin.json says 0.5.1"),
    ("v0.5.1", "is not outbound-gates--vX.Y.Z"),
    ("outbound-gates--v0.5", "is not outbound-gates--vX.Y.Z"),
    ("", "is not outbound-gates--vX.Y.Z"),
])
def test_bad_tags_are_refused(tmp_path, tag, error):
    with pytest.raises(ValueError, match=error):
        release_check.check(tag, plugin(tmp_path, "0.5.1"))


def test_script_against_the_real_plugin_json():
    version = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
    ok = subprocess.run([sys.executable, str(SCRIPT), f"outbound-gates--v{version}"], capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.strip() == version
    bad = subprocess.run([sys.executable, str(SCRIPT), "outbound-gates--v9.9.9"], capture_output=True, text=True)
    assert bad.returncode == 1 and "release check failed" in bad.stderr


def test_workflow_publishes_a_final_latest_release():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert 'tags: ["outbound-gates--v*"]' in text
    assert "release_check.py" in text and "--latest" in text
    assert "--draft" not in text and "--prerelease" not in text
    assert "':!tests' ':!.github'" in text
