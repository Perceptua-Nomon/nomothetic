"""Tests for projecting autonomon's manifest into the routine catalogue."""

from nomothetic.routine_catalog import catalog_from_manifest


def test_catalog_from_manifest_projects_fields():
    manifest = {
        "name": "autonomon",
        "version": "0.1.0",
        "routines": ["explore", "follow-user"],
        "params_schema": {"obstacle_threshold_cm": {"type": "number", "default": 40.0}},
    }
    catalog = catalog_from_manifest(manifest)
    assert catalog["routines"] == ["explore", "follow-user"]
    assert catalog["version"] == "0.1.0"
    assert catalog["params_schema"]["obstacle_threshold_cm"]["default"] == 40.0


def test_catalog_from_manifest_tolerates_missing_fields():
    catalog = catalog_from_manifest({})
    assert catalog == {"routines": [], "params_schema": {}, "version": None}


def test_catalog_from_manifest_tolerates_wrong_types():
    # A malformed manifest must not break the endpoint.
    catalog = catalog_from_manifest({"routines": "explore", "params_schema": 5, "version": 1})
    assert catalog["routines"] == []
    assert catalog["params_schema"] == {}
    assert catalog["version"] == 1
