# Shared

Shared Python library used by DocHub, Agent Engine, and Discovery. Provides database abstraction, LLM routing, and logging utilities.

## Modules

| Module | Purpose |
|--------|---------|
| `sdlc_db.py` | Main database interface (documents, audits, edges, requirements, decisions) |
| `db_writer.py` | Async write operations with backend-aware routing (PG pool or SQLite) |
| `log_writer.py` | Activity logging (fire-and-forget, backend-aware) |
| `llm_invoke.py` | LLM invocation with retries and token tracking |
| `llm_router.py` | Multi-provider LLM routing (model selection, cost tiers) |
| `model_defaults.py` | Default model configurations |

## Database Layer (`db/`)

| Module | Purpose |
|--------|---------|
| `connection.py` | psycopg 3 connection pool + SQLite-to-PG SQL translation (`_ConnWrapper`) |
| `compat.py` | Backend-aware SQL helpers (`use_pg()`, `param_style()`, `fts_search()`) |
| `graph_cypher.py` | Apache AGE graph operations (Cypher queries, graph sync) |
| `pg_schema.sql` | PostgreSQL schema definition |

## Migrations

- **PostgreSQL:** Alembic (`migrations/alembic/versions/`)
- **SQLite:** Numbered scripts (`migrations/sqlite/`)

Configuration in `alembic.ini`.

## Usage

This is a library, not a standalone service. It is added to `PYTHONPATH` by consuming services:

```bash
PYTHONPATH=shared:dochub python -m pytest dochub/tests/ -v
```
