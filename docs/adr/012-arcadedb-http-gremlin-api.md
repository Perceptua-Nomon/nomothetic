# ADR-012: ArcadeDB HTTP Gremlin API for Persistence

**Status:** Superseded  
**Date:** 2026-04-11  
**Deciders:** Perceptua  

> **Note (2026-04-13):** This ADR is superseded. The stores now use
> `execute_sql()` with named parameters instead of Gremlin traversals.
> The `_sanitize_gremlin_value()` helper and `execute_gremlin()` code path
> have been removed. Parameterized SQL eliminates the injection risks
> discussed below.

---

## Context

Phase 13 delivered central-mode authentication and fleet management with
transient in-memory stores. This was sufficient for the API contract and test
suite, but production use requires durable persistence. The nomographic
repository already manages ArcadeDB schemas (User, Vehicle, OwnsDevice) via
ArcadeDB-native migrations.

The question is how nomothetic should communicate with ArcadeDB.

Options evaluated:

1. **ArcadeDB Java driver via subprocess** — requires JVM on the Python host;
   high latency per call; complex error handling.
2. **PyOrient (OrientDB Python driver)** — ArcadeDB maintains partial
   compatibility with OrientDB binary protocol, but this path is
   undocumented and fragile.
3. **ArcadeDB HTTP API with `httpx`** — first-class REST endpoint at
   `POST /api/v1/command/{database}` supporting Gremlin, SQL, and Cypher
   query languages. Uses Basic Auth, JSON payloads, and standard HTTP
   status codes. `httpx.AsyncClient` provides connection pooling and
   integrates naturally with FastAPI's async event loop.
4. **gremlinpython (TinkerPop driver)** — ArcadeDB supports the Gremlin
   Server WebSocket protocol, but running the Gremlin Server plugin adds
   operational complexity and another port to manage.

## Decision

Use the **ArcadeDB HTTP API with `httpx.AsyncClient`** (option 3).

- `src/nomothetic/db.py` provides `DatabaseClient` with `execute_gremlin()`
  and `execute_sql()` methods.
- Gremlin is the primary query language for graph traversals (User → OwnsDevice
  → Vehicle).
- SQL is available for simple lookups and aggregations.
- `DatabaseConfig.from_env()` reads connection parameters from environment
  variables (`ARCADEDB_HOST`, `ARCADEDB_ROOT_PASSWORD`, etc.).
- `httpx` is already a dev dependency and is added to the `[central]` optional
  dependency group.

### Store Abstraction

A Protocol-based store pattern decouples business logic from the persistence
backend:

- `UserStore` protocol with `InMemoryUserStore` and `GremlinUserStore`
- `FleetStore` protocol with `InMemoryFleetStore` and `GremlinFleetStore`
- `AuthService` accepts an optional `user_store` parameter (defaults to
  in-memory)
- `create_app()` selects the backend based on `ARCADEDB_HOST` presence

This enables:
- Tests to run without a database (in-memory stores)
- Development mode without ArcadeDB
- Production deployment with full graph persistence

### Gremlin Query Safety

ArcadeDB's HTTP API does not reliably support Gremlin parameter binding.
Queries use string interpolation with strict input sanitization:

- Email addresses are normalised (lowercase, stripped) by `AuthService`
- VINs are validated by FastAPI path parameter regex `^[A-Za-z0-9_-]+$`
- Both store implementations reject values containing `'` or `\` characters
  at the store boundary via `_sanitize_gremlin_value()`

## Consequences

**Positive:**
- No new infrastructure beyond ArcadeDB (already planned in nomographic)
- Async-native integration with FastAPI
- Same test suite runs with or without a database
- Protocol-based stores are easy to swap or extend

**Negative:**
- String interpolation in Gremlin queries requires disciplined sanitization
- `httpx` is a runtime dependency for central mode (already in `[central]`
  extras)
- `AuthService.create_user()`, `authenticate()`, `get_user()`, and
  `refresh_token()` are now async, requiring `await` at all call sites

**Risks:**
- ArcadeDB HTTP API behaviour may differ between versions for edge cases
  (mitigated by integration tests against a real instance in CI)
- Gremlin query performance at scale is untested (acceptable for current
  fleet size; index on `User.email` and `Vehicle.vin` defined in migrations)
