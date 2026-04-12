"""Tests for the mode module."""

import os
from unittest.mock import patch

from nomothetic.mode import Mode, get_mode


def test_default_mode_is_device():
    """When NOMON_API_MODE is unset, mode defaults to device."""
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("NOMON_API_MODE", None)
        assert get_mode() == Mode.DEVICE


def test_device_mode():
    """Explicit device mode is recognised."""
    with patch.dict(os.environ, {"NOMON_API_MODE": "device"}):
        assert get_mode() == Mode.DEVICE


def test_central_mode():
    """Central mode is recognised."""
    with patch.dict(os.environ, {"NOMON_API_MODE": "central"}):
        assert get_mode() == Mode.CENTRAL


def test_case_insensitive():
    """Mode detection is case-insensitive."""
    with patch.dict(os.environ, {"NOMON_API_MODE": "CENTRAL"}):
        assert get_mode() == Mode.CENTRAL


def test_invalid_mode_falls_back_to_device():
    """Unknown mode values fall back to device."""
    with patch.dict(os.environ, {"NOMON_API_MODE": "bogus"}):
        assert get_mode() == Mode.DEVICE


def test_whitespace_stripped():
    """Leading/trailing whitespace is stripped from mode value."""
    with patch.dict(os.environ, {"NOMON_API_MODE": "  central  "}):
        assert get_mode() == Mode.CENTRAL
