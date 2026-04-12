"""Shared Gremlin query utilities.

Provides input sanitization for Gremlin string literals used across
UserStore, FleetStore, and TokenStore modules.
"""


def sanitize_gremlin_value(value: str) -> str:
    """Reject values containing characters unsafe for Gremlin string literals.

    Parameters
    ----------
    value : str
        The string to validate for use in a Gremlin query literal.

    Returns
    -------
    str
        The unchanged *value* if it passes validation.

    Raises
    ------
    ValueError
        If *value* contains ``'``, ``\\``, null bytes, or control characters.
    """
    if "'" in value or "\\" in value:
        raise ValueError(f"Unsafe characters in value: {value!r}")
    if any(ord(c) < 0x20 for c in value):
        raise ValueError(f"Control characters in value: {value!r}")
    return value
