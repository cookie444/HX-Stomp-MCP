"""Shared pytest fixtures. Pins captured byte fixtures for offline tests."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _load_hex(name: str) -> bytes:
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    return bytes(int(tok, 16) for tok in raw.split())


@pytest.fixture
def hex_fixture():
    """Return a loader for a named hex fixture in tests/fixtures/."""
    return _load_hex
