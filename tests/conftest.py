"""Shared test setup.

Every test gets a user config path of its own (empty by default), so no test
reads the real ~/.config/outbound-gates/config.json of the machine it runs on.
Tests that run the demo's gate write the demo's own facts there with
demo_user_config().
"""
import json
import shutil
from pathlib import Path

import pytest

from gates import evidence

ROOT = Path(__file__).resolve().parents[1]
DEMO_USER_CONFIG = ROOT / "demo" / "gate" / "user-config.json"


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path, monkeypatch):
    path = tmp_path / "home-config" / "outbound-gates" / "config.json"
    monkeypatch.setattr(evidence, "USER_CONFIG", path)
    return path


def demo_user_config(path: Path) -> Path:
    """Write the demo's own facts (the fictional sender's price) to a user config path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(DEMO_USER_CONFIG, path)
    return path


@pytest.fixture
def demo_facts(isolated_user_config):
    """The demo's own facts, for tests that run the demo through the gate in-process."""
    return demo_user_config(isolated_user_config)
