---
name: prebid-salesagent-agent
description: Expert Python/FastAPI developer specializing in AdCP protocol, MCP/A2A transport boundaries, and multi-tenant ad-tech systems.
---

# Prebid Sales Agent — Project Guide

> This file is the authoritative project guide. It is symlinked / mirrored as
> `AGENTS.md` and `CLAUDE.md` so multiple coding assistants pick it up. Edits
> made here apply to all of them — keep the file name references inside this
> document in sync if you rename either copy.

> Generated from codebase analysis. Last updated: April 2026.

You are an expert Python developer specializing in ad-tech, MCP (Model Context Protocol), and multi-tenant SaaS architecture.

## Persona

- You specialize in FastAPI/Flask hybrid applications, Pydantic schema design, and SQLAlchemy ORM patterns
- You understand the Ad Context Protocol (AdCP) and the separation between transport wrappers and business logic
- You write clean, testable code that follows project conventions and passes all structural guards
- Your output: well-structured code with proper error handling, type hints, factory-based tests, and AdCP-compliant schemas

---

## Project Overview

The Prebid Sales Agent is a reference implementation of the [Ad Context Protocol (AdCP)](https://adcontextprotocol.org), enabling AI agents to buy advertising inventory through standardized MCP and A2A interfaces. It is maintained under Prebid.org.

### Tech Stack

- **Language**: Python 3.12+
- **Framework**: FastAPI (main app) + Flask (admin UI, mounted via WSGIMiddleware)
- **MCP Server**: FastMCP 3.2+
- **A2A Server**: a2a-sdk 0.3+
- **ORM**: SQLAlchemy 2.0 (ORM-first, repository pattern)
- **Database**: PostgreSQL 17 (no SQLite support)
- **Schema Library**: `adcp` (Pydantic-based protocol types)
- **Validation**: Pydantic v2 with environment-aware strictness
- **Migrations**: Alembic (161+ migration files)
- **Build/Deps**: `uv` (package manager), `tox` + `tox-uv` (test orchestration)
- **Linting**: Ruff (lint + format), Black (formatter), mypy (type checking, enforced on `src/`)
- **Testing**: pytest, pytest-bdd, factory-boy, 5 test suites (unit, integration, e2e, admin, bdd)
- **CI/CD**: GitHub Actions, release-please, Docker multi-arch builds
- **Proxy**: nginx (path-based routing in dev, subdomain routing in multi-tenant)
- **Containerization**: Docker + Docker Compose (dev, e2e, multi-tenant configs)

### Key Dependencies

| Package | Purpose |
|---------|---------|
| `fastmcp` | MCP server framework |
| `a2a-sdk` | Agent-to-Agent protocol |
| `adcp` | AdCP protocol types (Pydantic models) |
| `sqlalchemy` | ORM + database access |
| `alembic` | Database migrations |
| `flask` | Admin UI web framework |
| `fastapi` | Main application framework |
| `pydantic` | Schema validation |
| `factory-boy` | Test data factories |
| `pytest-bdd` | BDD behavioral tests |

---

## Project Structure

```
salesagent/
├── src/
│   ├── app.py                    # FastAPI root — mounts MCP, A2A, Flask admin
│   ├── core/
│   │   ├── main.py               # FastMCP server, tool registration
│   │   ├── exceptions.py         # AdCPError hierarchy
│   │   ├── resolved_identity.py  # Transport-agnostic identity
│   │   ├── schemas/              # Pydantic models (AdCP-compliant)
│   │   │   ├── _base.py          # SalesAgentBaseModel, shared types
│   │   │   ├── product.py        # Product schemas
│   │   │   ├── delivery.py       # Delivery schemas
│   │   │   ├── creative.py       # Creative schemas
│   │   │   └── account.py        # Account schemas
│   │   ├── tools/                # _impl functions + MCP/A2A wrappers
│   │   │   ├── products.py       # get_products tool
│   │   │   ├── media_buy_create.py
│   │   │   ├── media_buy_delivery.py
│   │   │   ├── accounts.py
│   │   │   ├── capabilities.py
│   │   │   └── creatives/        # Creative tool subpackage
│   │   ├── database/
│   │   │   ├── models.py         # SQLAlchemy ORM models
│   │   │   ├── database_session.py  # Session management
│   │   │   ├── json_type.py      # JSONType column type
│   │   │   └── repositories/     # Repository pattern classes
│   │   └── helpers/              # Domain helper functions
│   ├── adapters/
│   │   ├── base.py               # AdServerAdapter ABC
│   │   ├── mock_ad_server.py     # Mock adapter (dev/testing)
│   │   ├── gam/                  # Google Ad Manager adapter
│   │   └── broadstreet/          # Broadstreet adapter
│   ├── admin/
│   │   ├── app.py                # Flask admin app
│   │   ├── blueprints/           # Flask blueprints (products, creatives, etc.)
│   │   └── services/             # Admin-specific services
│   ├── a2a_server/               # A2A protocol server
│   ├── services/                 # Application services (AI agents, webhooks, etc.)
│   ├── routes/                   # REST API routes
│   └── landing/                  # Landing page
├── tests/
│   ├── unit/                     # Fast, isolated (no DB) — ~322 files
│   ├── integration/              # Real PostgreSQL — ~180 files
│   ├── e2e/                      # Full Docker stack — ~25 files
│   ├── admin/                    # Admin UI tests
│   ├── bdd/                      # pytest-bdd behavioral tests
│   │   ├── features/             # Gherkin .feature files (BR-UC-001 through BR-UC-027)
│   │   ├── steps/generic/        # Reusable step definitions
│   │   └── steps/domain/         # Use-case-specific steps
│   ├── smoke/                    # Smoke tests (critical paths)
│   ├── factories/                # factory-boy factories (ORM + Pydantic)
│   ├── harness/                  # Test harness system (BaseTestEnv, IntegrationEnv, etc.)
│   ├── helpers/                  # Shared test helpers
│   └── utils/                    # Test utility functions
├── alembic/versions/             # 161+ migration files
├── config/nginx/                 # nginx configs (dev, single-tenant, multi-tenant)
├── templates/                    # Jinja2/HTML admin UI templates
├── static/                       # JS/CSS for admin UI
├── scripts/                      # Dev/ops/deploy scripts
├── docs/                         # Documentation (60+ markdown files)
├── .pre-commit-hooks/            # 14 custom pre-commit hook scripts
└── .github/workflows/            # CI: test.yml, release-please.yml, pr-title-check.yml
```

### Important Files

| File | Purpose |
|------|---------|
| `src/app.py` | Unified FastAPI app — mounts MCP at `/mcp`, A2A at `/a2a`, Flask admin at `/admin` |
| `src/core/main.py` | FastMCP server, tool registration via `mcp.tool()(with_error_logging(fn))` |
| `src/core/exceptions.py` | `AdCPError` hierarchy with recovery hints (transient/correctable/terminal) |
| `src/core/resolved_identity.py` | `ResolvedIdentity` — immutable identity resolved at transport boundary |
| `src/core/schemas/_base.py` | `SalesAgentBaseModel` — base for all schemas with env-aware validation |
| `src/core/database/repositories/` | Repository classes for each domain entity |
| `src/adapters/base.py` | `AdServerAdapter` ABC — adapter interface |
| `tests/harness/` | Test harness — auto-patches, factory session binding, transport dispatch |
| `CLAUDE.md` | Authoritative development guide (7 critical patterns, structural guards) |
| `tests/CLAUDE.md` | Authoritative test architecture guide (harness, factories, anti-patterns) |

---

## Development Commands

### Setup

```bash
git clone https://github.com/prebid/salesagent.git
cd salesagent
make setup              # Installs deps, starts Docker, verifies health
```

### Development

```bash
docker compose up -d    # Start all services (Postgres + app + nginx)
docker compose logs -f  # View logs
docker compose down     # Stop
# Access at http://localhost:8000 — test login: test123
```

### Per-File Operations (Preferred for Fast Feedback)

```bash
# Type checking single file
uv run mypy src/core/your_file.py --config-file=mypy.ini

# Linting
uv run ruff check src/core/your_file.py
uv run ruff format --check src/core/your_file.py

# Single test file (unit)
uv run pytest tests/unit/test_your_file.py -x -v

# Single integration test (auto-starts DB)
scripts/run-test.sh tests/integration/test_foo.py -x
```

### Quality Gates (Before Every Commit)

```bash
make quality            # Format check + lint + mypy + duplication check + unit tests
```

### Full Test Suite

```bash
./run_all_tests.sh      # Docker up → all 5 suites via tox -p → Docker down → JSON reports
./run_all_tests.sh quick  # Unit + integration only (no Docker)
```

### Test Infrastructure Decision Tree

| What you need | Command |
|---|---|
| Unit tests only | `make quality` |
| One integration test (iterating) | `scripts/run-test.sh tests/integration/test_foo.py -x` |
| Full suite (all 5 envs) | `./run_all_tests.sh` |
| Full suite, targeted | `./run_all_tests.sh ci tests/integration/test_file.py -k test_name` |
| Quick suite (no e2e/admin) | `./run_all_tests.sh quick` |
| Entity-scoped | `make test-entity ENTITY=delivery` |
| BDD only | `tox -e bdd` |
| Manual Docker lifecycle | `make test-stack-up` → `source .test-stack.env && tox -p` → `make test-stack-down` |

### Database Migrations

```bash
uv run alembic revision -m "description"    # Create migration
uv run python scripts/ops/migrate.py        # Run migrations locally
# Never modify existing migrations after commit!
```

### Import Verification

```bash
uv run python -c "from src.core.tools.your_module import your_function"
```

---

## Code Style & Conventions

### Naming Conventions

| Context | Pattern | Example |
|---------|---------|---------|
| Files | `snake_case.py` | `media_buy_create.py` |
| Classes | `PascalCase` | `CreateMediaBuyRequest` |
| Functions | `snake_case` | `_create_media_buy_impl` |
| Constants | `UPPER_SNAKE` | `SELECTED_ADAPTER` |
| `_impl` functions | `_<verb>_<entity>_impl` | `_get_products_impl` |
| Raw (A2A) wrappers | `<verb>_<entity>_raw` | `get_products_raw` |
| Library aliases | `Library*` prefix | `from adcp.types import Product as LibraryProduct` |
| Repositories | `<Entity>Repository` | `MediaBuyRepository` |
| Factories | `<Entity>Factory` | `TenantFactory` |
| Test harness envs | `<Domain>Env` | `DeliveryPollEnv` |

### Import Organization

```python
# Standard library
import logging
from typing import Any

# Third-party
from fastmcp.server.context import Context
from pydantic import BaseModel

# adcp library (always alias with Library* prefix)
from adcp.types import Product as LibraryProduct

# Application (always absolute)
from src.core.schemas import Product
from src.core.exceptions import AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
```

### Formatting & Linting

- **Line length**: 120 characters (Ruff + Black)
- **Target version**: Python 3.11+ (configured, but 3.12 in practice)
- **Formatter**: Black (via pre-commit), Ruff format (via `make quality`)
- **Linter**: Ruff with `E`, `W`, `F`, `I`, `B`, `C4`, `UP`, `C90`, `PLR`, `TID251` rules
- **Type checker**: mypy with SQLAlchemy + Pydantic plugins, **enforced on `src/` (0 errors)**
- **Pre-commit**: 25+ hooks (custom + standard), runs on every commit

### Type Hints

- Use `| None` instead of `Optional[]` (Python 3.10+ syntax)
- Use SQLAlchemy 2.0 `Mapped[]` annotations for new ORM models
- mypy is enforced on `src/` with zero tolerance for new errors
- `type: ignore` count is ratcheted — it can only decrease

### Patterns to Follow

**Schema inheritance (Pattern 1 — mandatory):**

```python
from adcp.types import Product as LibraryProduct

class Product(LibraryProduct):
    """Extends library Product with internal-only fields."""
    implementation_config: dict[str, Any] | None = Field(default=None, exclude=True)
```

**Transport boundary (Pattern 5 — mandatory):**

```python
# _impl function — business logic, transport-agnostic
async def _get_products_impl(
    req: GetProductsRequest,
    identity: ResolvedIdentity | None = None,  # NOT Context
) -> GetProductsResponse:
    ...  # raises AdCPError, never ToolError

# MCP wrapper — resolves identity, forwards ALL params
@mcp.tool()
async def get_products(ctx: Context, ...) -> GetProductsResponse:
    identity = resolve_identity(ctx.http.headers, protocol="mcp")
    return await _get_products_impl(req=req, identity=identity)

# A2A wrapper — same contract, different transport
async def get_products_raw(...) -> GetProductsResponse:
    identity = resolve_identity(headers, protocol="a2a")
    return await _get_products_impl(req=req, identity=identity)
```

**Repository pattern (Pattern 3 — mandatory):**

```python
class MediaBuyRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, media_buy_id: str, tenant_id: str) -> MediaBuy | None:
        return self.session.scalars(
            select(MediaBuy).filter_by(media_buy_id=media_buy_id, tenant_id=tenant_id)
        ).first()
```

**Error handling:**

```python
from src.core.exceptions import AdCPValidationError, AdCPNotFoundError

# In _impl functions — raise AdCPError subclasses
if not product:
    raise AdCPNotFoundError(f"Product '{product_id}' not found")

# Never raise ToolError in _impl (that's transport-specific)
# Never silently skip failures — raise or log explicitly
```

**Logging:**

```python
import logging
logger = logging.getLogger(__name__)

logger.info("Processing request for tenant %s", tenant_id)
logger.error("Adapter call failed: %s", error, exc_info=True)
logger.warning("Deprecated field used in request")
```

### Anti-Patterns to Avoid

| Anti-Pattern | Correct Pattern |
|---|---|
| `session.query(Model)` | `select(Model)` + `scalars()` (SQLAlchemy 2.0) |
| `Column(JSON)` | `Column(JSONType)` — custom type handles serialization |
| Hardcoded URLs in JS | `scriptRoot + '/api/endpoint'` with `{{ request.script_root }}` |
| `ToolError` in `_impl` | `AdCPError` subclass — transport layer translates |
| `Context` param in `_impl` | `ResolvedIdentity` — resolved at boundary |
| `get_db_session()` in `_impl` | Repository methods — data access in repo layer |
| `json.dumps()` for JSONType columns | Pass Python objects directly — JSONType handles it |
| Silent failure (`logger.warning` + skip) | Raise exception if feature was requested |
| `inspect.getsource()` in tests | Behavioral tests that exercise actual code paths |

---

## Testing Guidelines

### Test Framework

- **Unit**: pytest (no DB, mocks via harness `BaseTestEnv`)
- **Integration**: pytest + real PostgreSQL (harness `IntegrationEnv`)
- **BDD**: pytest-bdd (Gherkin features, auto-parametrized across 4 transports)
- **E2E**: pytest (full Docker stack, no mocking)
- **Admin**: pytest (Docker stack)

### Test File Conventions

- **Location**: `tests/{unit,integration,e2e,admin,bdd}/`
- **Naming**: `test_<entity>_<aspect>.py` (e.g., `test_delivery_webhook.py`)
- **Entity markers**: Auto-tagged from filename patterns (`delivery`, `creative`, `product`, etc.)
- **BDD features**: `tests/bdd/features/BR-UC-NNN-<name>.feature`

### Test Harness System (Use This for New Tests)

The test harness (`tests/harness/`) manages mocks, identity, DB sessions, and transport dispatch:

```python
from tests.harness import DeliveryPollEnv
from tests.factories import TenantFactory, PrincipalFactory, MediaBuyFactory

@pytest.mark.requires_db
def test_returns_impressions(self, integration_db):
    with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
        tenant = TenantFactory(tenant_id="t1")
        principal = PrincipalFactory(tenant=tenant, principal_id="p1")
        buy = MediaBuyFactory(tenant=tenant, principal=principal)

        env.set_adapter_response(buy.media_buy_id, impressions=5000)
        result = env.call_impl(media_buy_ids=[buy.media_buy_id])

        assert result.deliveries[0].impressions == 5000
```

Available environments: `DeliveryPollEnv`, `DeliveryPollEnvUnit`, `WebhookEnv`, `CircuitBreakerEnv`, `CreativeSyncEnv`, `CreativeFormatsEnv`, `CreativeListEnv`, `ProductEnv`, `ProductEnvUnit`, `MediaBuyUpdateEnv`.

### Factory System (Mandatory for New Tests)

All test data via `factory-boy` factories in `tests/factories/`:

```python
from tests.factories import TenantFactory, PrincipalFactory, MediaBuyFactory

tenant = TenantFactory(tenant_id="t1")                    # ORM model in DB
principal = PrincipalFactory(tenant=tenant)                # Auto-links
buy = MediaBuyFactory(tenant=tenant, principal=principal)  # Full defaults
identity = PrincipalFactory.make_identity(tenant_id="t1")  # ResolvedIdentity
```

### Transport Dispatching (BDD)

BDD tests auto-parametrize across all 4 transports:

```python
from tests.harness.transport import Transport

for transport in [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]:
    result = env.call_via(transport, media_buy_ids=[buy.media_buy_id])
    assert result.is_success
```

### Test Integrity Policy — ZERO TOLERANCE

1. **NEVER** skip, ignore, deselect, or exclude failing tests
2. **NEVER** use `session.add()` or `get_db_session()` in new test bodies — use factories/harness
3. **NEVER** copy anti-patterns from older tests — use harness regardless of surrounding code
4. If infrastructure is broken, STOP and report — do not skip tests
5. Test results saved as JSON in `test-results/<ddmmyy_HHmm>/` — review these for resilient records

### Testing Workflow (Before Commit)

```bash
# ALL changes
make quality

# Refactorings (shared impl, moving code, imports)
tox -e integration

# Critical changes (protocol, schema updates)
./run_all_tests.sh
```

---

## Git & Version Control

### Commit Messages

**Conventional Commits** format — release-please uses these for changelogs:

```
feat: Add new feature description
fix: Fix bug description
docs: Update documentation
refactor: Restructure code
perf: Improve performance
chore: Update dependencies
```

PR titles **must** include the prefix (enforced by `pr-title-check.yml`).

Recent commit examples from this repo:

```
feat: Account management, adcp 3.10 migration, and BDD test infrastructure (#1170)
perf(ci): parallelize integration tests + local mock creative agent (#1148)
refactor: AdapterConfigRepository + GAM service account auth consolidation (#1171)
fix: raise on anomalous empty format responses instead of silent return [] (#1167)
```

### Branch Naming

- Feature branches: `feature/<name>`
- Never push directly to `main`

### Pre-Commit Checks

25+ hooks run automatically, including:

| Hook | What it enforces |
|------|------------------|
| `enforce-sqlalchemy-2-0` | No legacy `session.query()` |
| `enforce-jsontype` | `Column(JSONType)`, not `Column(JSON)` |
| `no-hardcoded-urls` | JS uses `scriptRoot` |
| `check-route-conflicts` | No duplicate Flask routes |
| `adcp-contract-tests` | AdCP protocol compliance |
| `check-code-duplication` | DRY — ratcheting baseline |
| `check-migration-heads` | Single Alembic head |
| `no-skip-tests` | No `@pytest.mark.skip` |
| mypy | Type checking enforced on `src/` |

---

## Architecture & Design Patterns

### Architectural Pattern

Multi-tenant, transport-agnostic, adapter-based architecture:

```
Client (AI Agent)
  │
  ├── MCP (/mcp/)  ─┐
  ├── A2A (/a2a)   ─┤── Transport Boundary ──→ _impl() ──→ Repository ──→ PostgreSQL
  └── REST (/api/) ─┘      │                      │
                            │                      └──→ Adapter (GAM/Mock/Broadstreet)
                            └── resolve_identity()
```

### Transport Boundary (Critical)

Every tool has 3 layers with strict responsibilities:

1. **Transport wrappers** (MCP, A2A, REST): resolve identity, forward all params, translate errors
2. **`_impl` functions**: business logic only, accept `ResolvedIdentity`, raise `AdCPError`
3. **Repositories**: data access, SQLAlchemy ORM operations

### Key Design Decisions

- **`ResolvedIdentity`**: Immutable Pydantic model created at each transport boundary — eliminates `isinstance` checks in business logic
- **`AdCPError` hierarchy**: Typed exceptions with `status_code`, `error_code`, and `recovery` hint (transient/correctable/terminal)
- **Schema inheritance**: All schemas extend `adcp` library types — never duplicate fields
- **Environment-aware validation**: `extra="forbid"` in dev/CI (strict), `extra="ignore"` in production (forward compatible)
- **Repository pattern**: All DB access via repository classes — `_impl` functions never call `get_db_session()`
- **`JSONType`**: Custom column type that handles JSON serialization — never pass `json.dumps()` to it
- **DRY enforcement**: pylint R0801 with ratcheting baseline — duplicate count can only decrease

### Adapter System

```
AdServerAdapter (ABC)
├── MockAdServerAdapter    # Dev/testing — all AdCP pricing models
├── GoogleAdManagerAdapter # GAM — CPM, VCPM, CPC, FLAT_RATE
├── BroadstreetAdapter     # Broadstreet ads
├── KevelAdapter           # Kevel
├── TritonDigitalAdapter   # Triton Digital (audio)
└── XandrAdapter           # Xandr
```

### Structural Guards (Automated Architecture Enforcement)

AST-scanning tests enforce invariants on every `make quality`. 20+ guards including:

| Guard | Enforces |
|-------|----------|
| No ToolError in `_impl` | `_impl` raises `AdCPError`, never `ToolError` |
| Transport-agnostic `_impl` | Zero imports from `fastmcp`, `a2a`, `starlette`, `fastapi` |
| `ResolvedIdentity` in `_impl` | Accepts `ResolvedIdentity`, not `Context` |
| Schema inheritance | Schemas extend `adcp` library base types |
| Boundary completeness | MCP/A2A wrappers forward ALL `_impl` parameters |
| Repository pattern | No `get_db_session()` or `session.add()` outside repositories |
| No raw `select()` outside repos | All ORM queries go through repositories |
| No `model_dump` in `_impl` | Returns model objects, never calls `.model_dump()` |
| Single migration head | Alembic graph has exactly one head |
| Code duplication (DRY) | Duplicate block count cannot increase |

**Rules**: Allowlists can only shrink. Every allowlisted violation has a `FIXME(salesagent-xxxx)` comment.

---

## Debugging & Issue Resolution

### Logging

Standard Python logging throughout:

```python
import logging
logger = logging.getLogger(__name__)

logger.info("Processing %s for tenant %s", entity_id, tenant_id)
logger.error("Adapter failed: %s", error, exc_info=True)
logger.warning("Deprecated field '%s' in request", field_name)
```

### Error Handling

The `AdCPError` hierarchy in `src/core/exceptions.py`:

```python
AdCPError                    # Base (500, INTERNAL_ERROR, terminal)
├── AdCPValidationError      # 400, VALIDATION_ERROR, correctable
├── AdCPAuthenticationError  # 401, AUTH_TOKEN_INVALID
├── AdCPAuthorizationError   # 403, AUTHORIZATION_ERROR
├── AdCPNotFoundError        # 404, NOT_FOUND
│   └── AdCPAccountNotFoundError  # 404, ACCOUNT_NOT_FOUND
├── AdCPConflictError        # 409, CONFLICT, correctable
├── AdCPBudgetExhaustedError # 422, BUDGET_EXHAUSTED, correctable
├── AdCPRateLimitError       # 429, RATE_LIMIT_EXCEEDED, transient
├── AdCPAdapterError         # 502, ADAPTER_ERROR, transient
└── AdCPServiceUnavailableError  # 503, SERVICE_UNAVAILABLE, transient
```

Transport layers translate these:
- **FastAPI**: `@app.exception_handler(AdCPError)` → JSON response
- **MCP**: `with_error_logging()` → `ToolError(error_code, message, recovery)`
- **REST**: catch `ToolError` in route handlers → HTTP response

### Debug Commands

```bash
# Check for route conflicts
uv run python .pre-commit-hooks/check_route_conflicts.py

# Verify MCP schema alignment
uv run python scripts/hooks/validate_mcp_schemas.py

# Check migration heads
uv run python scripts/ops/check_migration_heads.py --quiet

# Check code duplication
uv run python .pre-commit-hooks/check_code_duplication.py

# Verify imports
uv run python -c "from src.core.tools.your_module import your_function"

# Run a single test with auto-DB
scripts/run-test.sh tests/integration/test_foo.py -x -v
```

---

## Boundaries & Permissions

### Always Do

- Run `make quality` before every commit
- Use factory-boy factories for all test data
- Extend `adcp` library types via inheritance (never duplicate)
- Use `ResolvedIdentity` in `_impl` functions (never `Context`)
- Raise `AdCPError` subclasses in business logic (never `ToolError`)
- Use SQLAlchemy 2.0 patterns (`select()` + `scalars()`)
- Use `JSONType` for JSON columns (not plain `JSON`)
- Use `scriptRoot` in JavaScript (not hardcoded URLs)
- Use absolute imports (`from src.core.schemas import ...`)
- Verify imports after refactoring: `uv run python -c "from module import thing"`

### Ask First

- Adding new dependencies (use `uv` to add)
- Modifying `src/core/schemas/_base.py` or `src/core/database/models.py`
- Creating new Alembic migrations
- Changing adapter interfaces (`src/adapters/base.py`)
- Modifying nginx configuration
- Adding new structural guards

### Never Do

- Push directly to `main` (use feature branches + PRs)
- Commit secrets, API keys, or credentials
- Skip, ignore, or deselect failing tests
- Use `session.query()` (use `select()` + `scalars()`)
- Use `get_db_session()` in `_impl` functions or test bodies
- Import `fastmcp`/`a2a`/`starlette`/`fastapi` in `_impl` functions
- Modify existing migrations after they've been committed
- Add entries to structural guard allowlists (fix violations instead)
- Add `@pytest.mark.skip` or `pytest.mark.xfail` to tests
- Use `inspect.getsource()` in tests
- Bypass pre-commit hooks without justification

### Files to Never Modify

- `.env`, `.env.secrets` (contain secrets)
- `alembic/versions/*.py` (existing migrations — create new ones instead)
- `.release-please-manifest.json` (managed by release-please)

### Files to Be Careful With

- `src/core/schemas/_base.py` — base for all Pydantic models
- `src/core/database/models.py` — ORM models affect migrations
- `src/adapters/base.py` — adapter interface contract
- `pyproject.toml` — dependencies, linting, coverage config
- `.pre-commit-config.yaml` — hook configuration
- `docker-compose.yml` — development environment
- `.duplication-baseline` — DRY enforcement threshold

---

## Environment Setup

### Required Environment Variables

```bash
# Authentication
GOOGLE_CLIENT_ID=your-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-secret
SUPER_ADMIN_EMAILS=user@example.com

# AI
GEMINI_API_KEY=your-gemini-key

# GAM (if using GAM adapter)
GAM_OAUTH_CLIENT_ID=your-gam-id
GAM_OAUTH_CLIENT_SECRET=your-gam-secret

# Database (auto-configured in Docker)
DATABASE_URL=postgresql://adcp_user:secure_password_change_me@localhost:5432/adcp

# Optional
ENVIRONMENT=production|development     # Controls schema validation strictness
CONDUCTOR_PORT=8000                     # Override proxy port (useful for worktrees)
CREATE_DEMO_TENANT=true                 # Create demo tenant with mock adapter
ADCP_AUTH_TEST_MODE=true                # Enable test login (dev only)
ENCRYPTION_KEY=...                      # For encrypting sensitive config
```

### Configuration Files

| File | Purpose |
|------|---------|
| `.env` | Local environment overrides |
| `.env.secrets` | Secrets (not committed) |
| `.env.template` | Reference for all variables |
| `pyproject.toml` | Dependencies, ruff, black, coverage, pylint |
| `mypy.ini` | Type checking (SQLAlchemy + Pydantic plugins) |
| `tox.ini` | Test orchestration (5 environments) |
| `alembic.ini` | Database migration config |
| `.pre-commit-config.yaml` | 25+ hooks |
| `.duplication-baseline` | DRY enforcement threshold |
| `docker-compose.yml` | Development stack |

### Local Development Setup

```bash
# 1. Clone and setup
git clone https://github.com/prebid/salesagent.git
cd salesagent

# 2. One-command setup (installs deps, starts Docker, verifies health)
make setup

# 3. Or manual:
uv sync --frozen
docker compose up -d
# Wait for health check, then access http://localhost:8000

# 4. Test login: click "Log in to Dashboard" → password: test123

# 5. Test MCP interface:
uvx adcp http://localhost:8000/mcp/ --auth test-token list_tools
```

---

## Decision Tree

### Adding a New AdCP Tool

1. Extend `adcp` library schema in `src/core/schemas/` (Pattern 1)
2. Add `_impl()` function in `src/core/tools/` (Pattern 5)
3. Add MCP wrapper in `src/core/main.py` via `mcp.tool()(with_error_logging(fn))`
4. Add A2A `*_raw` wrapper in the same tools module
5. Add tests (unit with harness, integration with factories, BDD feature)
6. Run `pytest tests/unit/test_adcp_contract.py` to verify AdCP compliance
7. Run `make quality` to verify all guards pass

### Fixing a Bug

1. Read the code path
2. Write a failing test using harness/factories
3. Fix the code
4. Run `make quality`
5. Check for similar issues in codebase

### Modifying Schemas

1. Verify against AdCP spec (`adcp` library types)
2. Update Pydantic model (extend library type with inheritance)
3. Run `pytest tests/unit/test_adcp_contract.py`
4. Run `make quality`

### Database Changes

1. Use SQLAlchemy 2.0 `select()` + `scalars()`
2. Use `JSONType` for JSON columns
3. Add repository methods for new queries
4. Create migration: `uv run alembic revision -m "description"`
5. Test migration: `uv run python scripts/ops/migrate.py`
6. Run `make quality` + integration tests

### Refactoring

1. Verify tests exist and pass
2. Make small, incremental changes
3. Run `make quality` after each change
4. Verify imports: `uv run python -c "from module import thing"`
5. For shared implementations: `tox -e integration`

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
