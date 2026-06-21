"""Read the autonomy-routine catalogue from the installed autonomon package.

nomothetic does not own the routine catalogue — autonomon does (its registry, via
the public ``nomon_manifest``). autonomon is installed into this gateway's venv at
deploy time but is *not* a hard dependency, so the import is lazy and a missing
autonomon yields an empty catalogue rather than an error (autonomon ADR-004: the
brain owns the routines; the gateway only relays them).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

_EMPTY_CATALOG: dict[str, Any] = {"routines": [], "params_schema": {}, "version": None}


def autonomon_catalog() -> dict[str, Any]:
    """Return the routine catalogue from autonomon's public ``nomon_manifest``.

    Returns
    -------
    dict
        ``{"routines": list[str], "params_schema": dict, "version": str | None}``.
        Empty (no routines) when autonomon is not installed.
    """
    try:
        from autonomon.routines import nomon_manifest
    except ImportError:
        logger.warning("autonomon is not installed; routine catalogue is empty")
        return dict(_EMPTY_CATALOG)
    return catalog_from_manifest(nomon_manifest)


def catalog_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Project an autonomon ``nomon_manifest`` into the catalogue response shape.

    Defensive about field presence/types so a manifest from a newer or older
    autonomon never breaks the endpoint.
    """
    routines = manifest.get("routines", [])
    params_schema = manifest.get("params_schema", {})
    return {
        "routines": [str(r) for r in routines] if isinstance(routines, (list, tuple)) else [],
        "params_schema": dict(params_schema) if isinstance(params_schema, Mapping) else {},
        "version": manifest.get("version"),
    }
