"""Tests for reading the routine catalogue autonomon publishes to a file."""

import json

from nomothetic.routine_catalog import (
    autonomon_catalog,
    catalog_from_manifest,
    catalog_path,
    published_autonomon_bin,
)

# ---------------------------------------------------------------------------
# catalog_from_manifest — projection into the API response shape
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# autonomon_catalog — reads the published file (separate-venv handoff, ADR-005)
# ---------------------------------------------------------------------------


def _write_catalog(tmp_path, monkeypatch, document):
    """Write *document* to a temp catalogue file and point the env var at it."""
    path = tmp_path / "routine_catalog.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv("NOMON_ROUTINE_CATALOG_PATH", str(path))
    return path


def test_catalog_path_honours_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("NOMON_ROUTINE_CATALOG_PATH", str(tmp_path / "c.json"))
    assert catalog_path() == tmp_path / "c.json"


def test_autonomon_catalog_reads_published_file(tmp_path, monkeypatch):
    _write_catalog(
        tmp_path,
        monkeypatch,
        {
            "name": "autonomon",
            "version": "0.1.0",
            "routines": ["explore"],
            "params_schema": {"obstacle_threshold_cm": {"type": "number"}},
            "autonomon_bin": "/opt/autonomon/.venv/bin/nomon-autonomon",
        },
    )
    catalog = autonomon_catalog()
    assert catalog["routines"] == ["explore"]
    assert catalog["version"] == "0.1.0"
    # The internal bin path must not leak into the API projection.
    assert "autonomon_bin" not in catalog


def test_autonomon_catalog_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("NOMON_ROUTINE_CATALOG_PATH", str(tmp_path / "absent.json"))
    assert autonomon_catalog() == {"routines": [], "params_schema": {}, "version": None}


def test_autonomon_catalog_empty_when_file_malformed(tmp_path, monkeypatch):
    path = tmp_path / "routine_catalog.json"
    path.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setenv("NOMON_ROUTINE_CATALOG_PATH", str(path))
    assert autonomon_catalog()["routines"] == []


# ---------------------------------------------------------------------------
# published_autonomon_bin — the launch path autonomon advertises
# ---------------------------------------------------------------------------


def test_published_autonomon_bin_returns_advertised_path(tmp_path, monkeypatch):
    _write_catalog(
        tmp_path,
        monkeypatch,
        {"routines": [], "autonomon_bin": "/opt/autonomon/.venv/bin/nomon-autonomon"},
    )
    assert published_autonomon_bin() == "/opt/autonomon/.venv/bin/nomon-autonomon"


def test_published_autonomon_bin_none_when_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("NOMON_ROUTINE_CATALOG_PATH", str(tmp_path / "absent.json"))
    assert published_autonomon_bin() is None


def test_published_autonomon_bin_none_when_field_absent(tmp_path, monkeypatch):
    _write_catalog(tmp_path, monkeypatch, {"routines": ["explore"]})
    assert published_autonomon_bin() is None
