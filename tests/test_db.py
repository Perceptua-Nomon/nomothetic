"""Tests for the ArcadeDB HTTP client module."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nomothetic.db import DatabaseClient, DatabaseConfig, DatabaseError

# ============================================================================
# DatabaseConfig
# ============================================================================


def test_config_from_env():
    """DatabaseConfig.from_env() reads all expected variables."""
    env = {
        "ARCADEDB_HOST": "db.example.com",
        "ARCADEDB_HTTP_PORT": "3000",
        "ARCADEDB_DATABASE": "test_db",
        "ARCADEDB_ROOT_PASSWORD": "s3cret",
    }
    with patch.dict(os.environ, env, clear=False):
        cfg = DatabaseConfig.from_env()
    assert cfg.host == "db.example.com"
    assert cfg.port == 3000
    assert cfg.database == "test_db"
    assert cfg.user == "root"
    assert cfg.password == "s3cret"


def test_config_from_env_defaults():
    """DatabaseConfig.from_env() applies correct defaults."""
    env = {
        "ARCADEDB_HOST": "localhost",
        "ARCADEDB_ROOT_PASSWORD": "pass",
    }
    with patch.dict(os.environ, env, clear=False):
        cfg = DatabaseConfig.from_env()
    assert cfg.port == 2480
    assert cfg.database == "nomon_central"
    assert cfg.user == "root"


def test_config_missing_host_raises():
    """Missing ARCADEDB_HOST raises ValueError."""
    env = {"ARCADEDB_ROOT_PASSWORD": "pass"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValueError, match="ARCADEDB_HOST"):
            DatabaseConfig.from_env()


def test_config_missing_password_raises():
    """Missing ARCADEDB_ROOT_PASSWORD raises ValueError."""
    env = {"ARCADEDB_HOST": "localhost"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValueError, match="ARCADEDB_ROOT_PASSWORD"):
            DatabaseConfig.from_env()


def test_database_config_tls_from_env():
    """DatabaseConfig.from_env() reads ARCADEDB_USE_TLS."""
    env = {
        "ARCADEDB_HOST": "db.example.com",
        "ARCADEDB_ROOT_PASSWORD": "pass",
        "ARCADEDB_USE_TLS": "true",
    }
    with patch.dict(os.environ, env, clear=False):
        cfg = DatabaseConfig.from_env()
    assert cfg.use_tls is True


def test_database_config_tls_default_false():
    """DatabaseConfig.from_env() defaults use_tls to False."""
    env = {
        "ARCADEDB_HOST": "db.example.com",
        "ARCADEDB_ROOT_PASSWORD": "pass",
    }
    with patch.dict(os.environ, env, clear=False):
        cfg = DatabaseConfig.from_env()
    assert cfg.use_tls is False


def test_database_client_uses_https():
    """DatabaseClient with use_tls=True uses https base URL."""
    cfg = DatabaseConfig(
        host="db.example.com",
        port=2480,
        database="test_db",
        user="root",
        password="pass",
        use_tls=True,
    )
    client = DatabaseClient(cfg)
    assert "https://" in str(client._client.base_url)


def test_database_client_uses_http():
    """DatabaseClient with use_tls=False uses http base URL."""
    cfg = DatabaseConfig(
        host="db.example.com",
        port=2480,
        database="test_db",
        user="root",
        password="pass",
        use_tls=False,
    )
    client = DatabaseClient(cfg)
    assert "http://" in str(client._client.base_url)


# ============================================================================
# DatabaseClient
# ============================================================================


@pytest.fixture
def db_config():
    """Provide a test DatabaseConfig."""
    return DatabaseConfig(
        host="localhost",
        port=2480,
        database="test_db",
        user="root",
        password="test_pass",
    )


@pytest.fixture
def db_client(db_config):
    """Provide a DatabaseClient with a test config."""
    return DatabaseClient(db_config)


# ============================================================================
# Gremlin
# ============================================================================


@pytest.mark.asyncio
async def test_execute_gremlin_success(db_client):
    """execute_gremlin returns the result list on success."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": [{"name": "alice"}]}
    db_client._client.post = AsyncMock(return_value=mock_response)

    result = await db_client.execute_gremlin("g.V().hasLabel('User')")
    assert result == [{"name": "alice"}]

    call_args = db_client._client.post.call_args
    assert call_args[0][0] == "/api/v1/command/test_db"
    body = call_args[1]["json"]
    assert body["language"] == "gremlin"
    assert body["command"] == "g.V().hasLabel('User')"


@pytest.mark.asyncio
async def test_execute_gremlin_with_params(db_client):
    """execute_gremlin passes params to the request body."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": []}
    db_client._client.post = AsyncMock(return_value=mock_response)

    await db_client.execute_gremlin("g.V()", params={"key": "value"})
    body = db_client._client.post.call_args[1]["json"]
    assert body["params"] == {"key": "value"}


@pytest.mark.asyncio
async def test_execute_gremlin_error(db_client):
    """execute_gremlin raises DatabaseError on non-2xx response."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    db_client._client.post = AsyncMock(return_value=mock_response)

    with pytest.raises(DatabaseError) as exc_info:
        await db_client.execute_gremlin("g.V()")
    assert exc_info.value.status_code == 500
    assert "Database query failed" in exc_info.value.message


# ============================================================================
# SQL
# ============================================================================


@pytest.mark.asyncio
async def test_execute_sql_success(db_client):
    """execute_sql returns the result list on success."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": [{"count": 42}]}
    db_client._client.post = AsyncMock(return_value=mock_response)

    result = await db_client.execute_sql("SELECT count(*) FROM User")
    assert result == [{"count": 42}]

    body = db_client._client.post.call_args[1]["json"]
    assert body["language"] == "sql"


# ============================================================================
# Health
# ============================================================================


@pytest.mark.asyncio
async def test_health_ok(db_client):
    """health() returns True when server responds 200."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    db_client._client.get = AsyncMock(return_value=mock_response)

    assert await db_client.health() is True


@pytest.mark.asyncio
async def test_health_unreachable(db_client):
    """health() returns False when connection fails."""
    db_client._client.get = AsyncMock(side_effect=Exception("Connection refused"))

    assert await db_client.health() is False


@pytest.mark.asyncio
async def test_health_non_200(db_client):
    """health() returns False on non-200 status."""
    mock_response = MagicMock()
    mock_response.status_code = 503
    db_client._client.get = AsyncMock(return_value=mock_response)

    assert await db_client.health() is False


# ============================================================================
# Close
# ============================================================================


@pytest.mark.asyncio
async def test_close(db_client):
    """close() calls aclose on the underlying client."""
    db_client._client.aclose = AsyncMock()
    await db_client.close()
    db_client._client.aclose.assert_awaited_once()


# ============================================================================
# Connection error
# ============================================================================


@pytest.mark.asyncio
async def test_execute_connection_error(db_client):
    """Connection failure raises DatabaseError with status_code 0."""
    db_client._client.post = AsyncMock(side_effect=Exception("Connection refused"))

    with pytest.raises(DatabaseError) as exc_info:
        await db_client.execute_gremlin("g.V()")
    assert exc_info.value.status_code == 0
    assert "Database connection failed" in exc_info.value.message
