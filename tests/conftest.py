from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    """Point HOME at a temp dir and clear config-discovery env vars.

    Ensures config-discovery / init tests never read or write the real
    ~/.config/voxnote, the OS app-support dir, or the repo's own config.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("VOXNOTE_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home
