"""API deployment mode selection.

Reads the ``NOMON_API_MODE`` environment variable to determine whether
the current process serves device-mode (hardware) or central-mode (fleet
management / auth) endpoints.  See ADR-011.
"""

import enum
import os


class Mode(enum.Enum):
    """Deployment mode for the nomothetic API."""

    DEVICE = "device"
    CENTRAL = "central"


def get_mode() -> Mode:
    """Return the active API mode from the environment.

    Reads ``NOMON_API_MODE`` (case-insensitive).  Defaults to ``device``
    when the variable is unset or contains an unrecognised value.

    Returns
    -------
    Mode
        The resolved deployment mode.
    """
    raw = os.environ.get("NOMON_API_MODE", "device").strip().lower()
    try:
        return Mode(raw)
    except ValueError:
        return Mode.DEVICE
