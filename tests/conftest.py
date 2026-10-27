"""Pytest config: isolates every test from the real recordings and settings."""

import sys
from pathlib import Path

import pytest

# Make `src/` importable when running pytest from the project root.
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from call_recorder_mcp import config  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Recordings, app and vocabulary point to a temporary folder; French interface."""
    monkeypatch.setattr(config, "RECORDINGS", tmp_path / "recordings")
    monkeypatch.setattr(config, "HOME", tmp_path / "home")
    monkeypatch.setattr(config, "APP_BINARY", tmp_path / "home" / "missing")
    monkeypatch.setattr(config, "VOCABULARY", tmp_path / "home" / "vocabulary.txt")
    monkeypatch.setattr(config, "LOCALE", "fr")
    monkeypatch.setattr(config, "LANGUAGE", "")
    return tmp_path
