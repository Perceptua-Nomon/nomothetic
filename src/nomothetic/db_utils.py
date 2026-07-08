"""Shared database utility functions.

Small helpers reused across multiple store modules (UserStore,
TokenStore, FleetStore).
"""

from datetime import datetime, timezone
from typing import Any, Optional, Union

# ArcadeDB's default ``dateTimeFormat`` (second precision, no timezone). A
# DATETIME column silently stores ``null`` for any value that does not match
# this pattern — notably ISO-8601 strings with a ``+00:00`` offset or ``Z``, or
# any other precision. This format is never changed via ``ALTER DATABASE``:
# that setting does not reliably persist (observed reverting after an ordinary
# schema change), so relying on a non-default format risks writes silently
# nulling out. Values crossing into a DATETIME column must be formatted with
# this pattern; values read back are parsed from it. Timestamps are treated as
# UTC on both sides, so the round-trip is lossless apart from sub-second
# precision (dropped by the second-precision DB format).
_DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# Legacy millisecond-precision format (written briefly during a since-reverted
# attempt to use a non-default dateTimeFormat) still accepted on read.
_LEGACY_MS_DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


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


def to_db_datetime(value: Union[str, datetime]) -> str:
    """Format an ISO-8601 string or ``datetime`` for an ArcadeDB DATETIME column.

    The result is a UTC wall-clock timestamp in the database's default
    ``yyyy-MM-dd HH:mm:ss`` format (second precision). Aware datetimes/strings
    are converted to UTC first; naive ones are assumed to already be UTC.
    Sub-second precision is dropped (the DB format is second-precision).

    Parameters
    ----------
    value : str or datetime
        An ISO-8601 timestamp string (e.g. ``"2026-07-05T12:34:56+00:00"``) or a
        :class:`datetime.datetime`.

    Returns
    -------
    str
        ``"YYYY-MM-DD HH:MM:SS"`` suitable for binding to a DATETIME column.

    Raises
    ------
    ValueError
        If *value* is a string that cannot be parsed as ISO-8601.
    """
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime(_DB_DATETIME_FORMAT)


def db_datetime_to_iso(value: Any) -> Optional[str]:
    """Convert a value read from an ArcadeDB DATETIME column back to ISO-8601.

    Inverse of :func:`to_db_datetime`. The stored wall-clock is interpreted as
    UTC, so the returned string carries a ``+00:00`` offset. Also tolerates a
    legacy millisecond-precision wall-clock format and a raw ISO-8601 string,
    for values written under prior formatting schemes.

    Parameters
    ----------
    value : Any
        The raw value from a result row (an ArcadeDB string in
        ``YYYY-MM-DD HH:MM:SS`` format, or a ``datetime``). ``None`` or an
        empty string yields ``None`` (for nullable columns).

    Returns
    -------
    str or None
        An ISO-8601 UTC string, or ``None`` when there is no value.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value)
        dt = None
        for fmt in (_DB_DATETIME_FORMAT, _LEGACY_MS_DB_DATETIME_FORMAT):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(text)
            except ValueError as e:
                raise ValueError(f"Could not parse datetime: {text}") from e
    assert isinstance(dt, datetime)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()
