"""Shared database utility functions.

Small helpers reused across multiple store modules (UserStore,
TokenStore, FleetStore).
"""

from typing import Any


def coerce_count(rows: list[Any]) -> int:
    """Coerce an ArcadeDB ``SELECT count(*)`` result to a plain int.

    ArcadeDB may return the count as an ``int``, ``float``, or a
    ``{"count": <number>}`` dict depending on the query shape and driver
    version.  This helper normalises all variants to ``int``.

    Parameters
    ----------
    rows : list[Any]
        Result rows from :meth:`DatabaseClient.execute_sql`.

    Returns
    -------
    int
        Coerced count, or ``0`` when *rows* is empty or unparseable.
    """
    if not rows:
        return 0
    first = rows[0]
    if isinstance(first, int):
        return first
    if isinstance(first, float):
        return int(first)
    if isinstance(first, dict):
        val = first.get("count", 0)
        if isinstance(val, (int, float)):
            return int(val)
    return 0
