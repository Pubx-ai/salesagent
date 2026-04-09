# Curation Integration — TODO Tracker

Dedicated tracking file for gaps and follow-up work in the curation integration. Companion to [`curation-integration-summary.md`](./curation-integration-summary.md).

- **Priority scale:** P0 = critical, P1 = high, P2 = medium, P3 = low, P4 = backlog
- **Status values:** `open`, `in_progress`, `blocked`, `done`
- **Current highest priority:** TODO-001

---

## TODO-001: `get_media_buys` list support for curation tenants

- **Beads:** `salesagent-ckh`
- **Priority:** P1 *(current highest priority)*
- **Status:** open
- **Type:** feature

### Why
`get_media_buys` currently returns an empty list for curation tenants because the tool queries local Postgres only, and `CurationAdapter` has `manages_own_persistence = True`. Curation media buys live exclusively in the `curation_sales` service. The sales service already exposes `GET /api/v1/sales` with filtering (`status`, `limit`, `cursor`) — the data path exists, it just isn't wired up.

### What (acceptance criteria)
- [ ] `SalesClient.list_sales(status=None, limit=..., cursor=None)` added with cursor pagination
- [ ] `CurationAdapter.list_media_buys()` (or equivalent) method added, mapping sales → AdCP media-buy records
- [ ] Early-return branch in the `get_media_buys` tool for `manages_own_persistence=True` adapters
- [ ] Status mapping goes through existing `SALE_STATUS_TO_ADCP` dict
- [ ] Unit tests in `tests/unit/test_curation_adapter.py` (pagination, status filtering, empty result)
- [ ] Integration test end-to-end

### Where
- `src/adapters/curation/sales_client.py`
- `src/adapters/curation/adapter.py`
- `src/core/tools/` — whichever file owns `get_media_buys`
- `tests/unit/test_curation_adapter.py`
- Sales service endpoint reference: `curation_sales/src/routes/sales.py:73-81`

---

## TODO-002: Integrate `curation_measurement` service

- **Beads:** `salesagent-s1i`
- **Priority:** P2
- **Status:** open
- **Type:** feature

### Why
There's a separate `curation_measurement` service that is the intended source of (a) activation status polling and (b) delivery reporting data (impressions, spend, clicks). Sales Agent has no client for it today, so `get_media_buy_delivery` returns zeros for curation tenants and the curation path has no async status refresh mechanism.

### What (acceptance criteria)
- [ ] New HTTP client `MeasurementClient(CurationHttpClient)` in `src/adapters/curation/`
- [ ] Adapter config extended with `measurement_service_url`
- [ ] `CurationAdapter.get_media_buy_delivery()` calls measurement service for real impression/spend/click numbers
- [ ] `CurationAdapter.check_media_buy_status()` (or a polling path) uses measurement service for activation status
- [ ] Admin UI "Pubx Curation" section: add measurement URL field + include in Test Connection
- [ ] Unit tests + integration tests

### Where
- `src/adapters/curation/` (new `measurement_client.py`)
- `src/adapters/curation/config.py` (new URL field)
- `src/adapters/curation/adapter.py` (wire into delivery + status methods)
- `templates/adapters/curation/connection_config.html`
- `src/admin/blueprints/adapters.py` (test-connection endpoint)

### Blockers / open questions
- Need the `curation_measurement` service API contract documented before starting

---

## TODO-003: Basic creative validation on curation `create_media_buy`

- **Beads:** `salesagent-ap9`
- **Priority:** P2
- **Status:** open
- **Type:** feature

### Why
The curation early-return in `media_buy_create.py` skips creative validation entirely. Curation adapter will not manage full creative workflows (assets, approvals, etc.), but basic creative validation — format/size sanity against the deal's `ad_format_types` — should still run before the sale is created. Otherwise buyers can submit a media buy that's structurally incompatible with the underlying segment formats.

### What (acceptance criteria)
- [ ] Define the minimum validation rules (format present, sizes in allowed list, MIME types)
- [ ] Hook basic validation into the curation early-return path, *before* `adapter.create_media_buy()`
- [ ] On validation failure, raise `AdCPValidationError` with `recovery="correctable"`
- [ ] Unit tests: valid creative, bad format, bad size, missing field

### Where
- `src/core/tools/media_buy_create.py` (curation early-return block around line 1405-1428)
- Potentially a shared helper in `src/core/helpers/` if reusable

---

## TODO-004: Manual / auto approval toggle on curation path

- **Beads:** `salesagent-898`
- **Priority:** P2
- **Status:** open
- **Type:** feature

### Why
`create_media_buy` early-return for curation always auto-approves — the normal path's manual approval flow is skipped. Curation should honor a per-tenant manual-vs-auto approval toggle matching the behavior of normal ad-server adapters (`manual_approval_required`, `manual_approval_operations`).

### What (acceptance criteria)
- [ ] Respect `manual_approval_required` on the tenant/adapter config for curation
- [ ] When manual approval is required, create the sale in a `pending_approval` state (curation-sales already supports this status)
- [ ] Workflow step creation for the approval task (like normal adapters do)
- [ ] Approval resolution path triggers the actual activation
- [ ] Admin UI exposes the toggle in Pubx Curation settings
- [ ] Unit + integration tests for both modes

### Where
- `src/core/tools/media_buy_create.py` (curation early-return)
- `src/adapters/curation/adapter.py`
- `templates/adapters/curation/connection_config.html`
- Approval workflow machinery — wherever normal adapters plug into it

---

## TODO-005: Audit logging on curation adapter calls

- **Beads:** `salesagent-8zz`
- **Priority:** P2
- **Status:** open
- **Type:** feature

### Why
The normal `create_media_buy` path wraps adapter calls in `audit_logger`; the curation early-return skips it entirely. Missing audit trail is a compliance and debugging hazard — we lose traceability of who did what against which sale.

### What (acceptance criteria)
- [ ] `CurationAdapter` receives an audit logger reference (via constructor or DI)
- [ ] All four adapter methods log entry/exit/errors: `create_media_buy`, `update_media_buy`, `check_media_buy_status`, `get_media_buy_delivery`
- [ ] Log fields: principal_id, tenant_id, sale_id / media_buy_id, adapter method, outcome, latency
- [ ] Unit tests assert audit events are emitted on success and failure paths

### Where
- `src/adapters/curation/adapter.py`
- `src/core/tools/media_buy_create.py`, `media_buy_delivery.py`, plus the update path
- Wherever `audit_logger` is constructed for ad-server adapters (`src/adapters/base.py` `AdServerAdapter.__init__`)

### Design note
`audit_logger` currently lives on `AdServerAdapter`. `CurationAdapter` extends `ToolProvider` directly (not `AdServerAdapter`), so audit plumbing needs to be lifted up to `ToolProvider` or added to curation independently.

---

## TODO-006: Push notifications / webhooks on curation path

- **Beads:** `salesagent-cw3` *(depends on `salesagent-s1i` / TODO-002)*
- **Priority:** P2
- **Status:** open
- **Type:** feature

### Why
Curation `create_media_buy` skips push notification config registration. Needed for async workloads — buyers need status updates when a sale transitions from `pending_activation` → `active` → etc. Should support both the existing webhook mechanism and the Slack webhook integration already present in the Sales Agent.

### What (acceptance criteria)
- [ ] Curation early-return in `create_media_buy` registers `push_notification_config` like the normal path
- [ ] Status transitions on curation sales trigger webhook dispatch
- [ ] Slack integration works for curation tenants (same as ad-server tenants)
- [ ] Test: create curation media buy with webhook, simulate status change, verify delivery
- [ ] Test: Slack webhook end-to-end for curation

### Where
- `src/core/tools/media_buy_create.py` (curation early-return)
- Push notification service / dispatcher code
- Slack webhook integration
- Depends on TODO-002 (measurement service) for the status-change trigger

### Dependencies
- TODO-002 — without measurement service polling, we have no status-change events to fire on

---

## TODO-007: LLM ranking mode distinction — `buying_mode` vs `catalog_mode`

- **Beads:** `salesagent-aen`
- **Priority:** P2
- **Status:** open
- **Type:** feature

### Why
`get_products` currently raises a terminal `AdCPValidationError` for *any* curation tenant without an AI API key (`products.py:811-817`). Intended product behavior:

- **`buying_mode`** (brief + refine flows): AI ranking is **mandatory**. Missing key = hard error is correct.
- **`catalog_mode`**: AI ranking is **not required**. Missing key should fall through to unranked catalog results without error.

Code needs to distinguish the two modes and only enforce the key requirement for `buying_mode`.

### What (acceptance criteria)
- [ ] Identify where `buying_mode` vs `catalog_mode` is determined (request field, tenant config, URL, or header)
- [ ] Branch in `products.py` AI-key check: require key only for `buying_mode`
- [ ] For `catalog_mode` without a key: return unranked products, log an info message (not a warning)
- [ ] Unit tests for both modes × both key states (4 cases)

### Where
- `src/core/tools/products.py` (around lines 757-826)
- Wherever mode is defined / plumbed through

### Open questions
- How is `buying_mode` / `catalog_mode` surfaced today? Need to confirm before implementation.

---

## TODO-008: Migrate curation HTTP calls off blocking `httpx.Client`

- **Beads:** `salesagent-802`
- **Priority:** P2
- **Status:** open
- **Type:** performance

### Why
All three curation HTTP clients (`catalog_client`, `sales_client`, `activation_client`) use synchronous `httpx.Client` and are called from inside async FastAPI handlers. Sync calls inside an async handler block the entire event loop — one slow curation call stalls all in-flight MCP/A2A/REST requests. The 100-page pagination path in `fetch_all_segments()` makes this worse.

The summary doc cites "asyncio deadlocks inside FastAPI's event loop" as the original reason for going sync. That's not a real failure mode of `httpx.AsyncClient` used correctly, so the original concern should be verified before picking a fix.

### What (acceptance criteria)
**Near-term (Option A — minimal diff):**
- [ ] Verify the original "asyncio deadlock" claim — check git log and any incident notes for the commit that introduced sync httpx. Understand what actually broke.
- [ ] Wrap the three curation adapter calls in `products.py`, `media_buy_create.py`, `media_buy_delivery.py` with `asyncio.to_thread(...)`
- [ ] Load test: concurrent `get_products` calls should no longer stall unrelated endpoints

**Long-term (Option B — full async, separate TODO when prioritized):**
- Migrate `CurationHttpClient` to `httpx.AsyncClient`
- Make `CurationAdapter` methods `async def`
- Decide whether `ToolProvider` interface goes async or splits into sync/async variants

### Where
- `src/core/tools/products.py`
- `src/core/tools/media_buy_create.py`
- `src/core/tools/media_buy_delivery.py`
- Long-term: `src/adapters/curation/http_client.py`, `src/adapters/curation/adapter.py`, `src/adapters/base.py`

---

## TODO-009: Multi-tenant curation support (end-to-end)

- **Beads:** `salesagent-585`
- **Priority:** P3
- **Status:** open
- **Type:** feature

### Why
Sales Agent is running single-tenant for now, but we need to support multi-tenant behavior end-to-end. Currently: `curation_catalog`, `curation_sales`, `curation_activation` (and future `curation_measurement`) have **no tenant concept** — all segments and sales are global. Tenant isolation must be added at the service layer, not just in the Sales Agent's adapter config.

### What (acceptance criteria)
**Sales Agent side:**
- [ ] Per-tenant service URLs stored in `AdapterConfig.config_json`
- [ ] `CurationAdapter` reads URLs from tenant config, not env vars
- [ ] Tenant ID passed through to all curation service calls (header or query param)

**Curation services side:**
- [ ] `curation_catalog` — add tenant field to segments, filter by tenant on list/get
- [ ] `curation_sales` — add tenant field to sales, filter on list/get
- [ ] `curation_activation` — pass tenant through, isolate activation records
- [ ] `curation_measurement` — same treatment once it exists
- [ ] Auth layer — API gateway / mesh enforces tenant access (currently no auth at all)

### Where
- `src/adapters/curation/config.py` (per-tenant URLs)
- `src/adapters/curation/adapter.py`
- `curation_catalog/*` — schema migration, route filters
- `curation_sales/*` — schema migration, route filters
- `curation_activation/*`, `curation_measurement/*`

### Dependencies
- Large cross-repo effort. Consider splitting into per-service child TODOs when prioritized.

---

## TODO-010: Curation media buy local sync

- **Beads:** `salesagent-7cl`
- **Priority:** P3
- **Status:** open
- **Type:** feature

### Why
Curation media buys are not persisted to the Sales Agent's Postgres (`manages_own_persistence=True`). This means:
- No local mapping of AdCP `media_buy_id` ↔ curation-sales `sale_id` ↔ `buyer_ref`
- Every lookup round-trips to curation-sales
- `get_media_buys` has no local index to query (related to TODO-001)
- Cross-references (workflow steps, webhooks, audit records) have nothing to foreign-key against

### What (acceptance criteria)
- [ ] Design the mapping schema: which fields are mirrored locally vs. fetched on demand
- [ ] New Postgres table (e.g., `curation_media_buy_refs`) with `media_buy_id`, `sale_id`, `buyer_ref`, `tenant_id`, `created_at`, `last_synced_at`
- [ ] Adapter writes to this table on `create_media_buy` / `update_media_buy`
- [ ] Repository + Alembic migration
- [ ] Sync-refresh mechanism (cron / webhook / lazy fetch on lookup)

### Where
- `src/core/database/models.py` (new table)
- `src/core/database/repositories/` (new repo)
- `src/adapters/curation/adapter.py`
- `alembic/versions/` (new migration)

### Open questions
- Full mirror vs. pointer table only? Affects how much drift-tolerance we need.

---

## TODO-011: Activation service idempotency

- **Beads:** `salesagent-b3y`
- **Priority:** P3
- **Status:** open
- **Type:** feature

### Why
`curation-activation`'s `POST /activations` has no deduplication — every call creates a fresh Magnite PMP deal + RTB rule, even for the same `sale_id`. Any retry (network blip, timeout, caller re-invoke) produces duplicate live deals in Magnite. Right now we rely on the Sales Agent not retrying, which is fragile.

### What (acceptance criteria)
**Option A — activation service side:**
- [ ] Support an `Idempotency-Key` header (or use `sale_id` as natural key)
- [ ] On duplicate POST, return the existing activation record with HTTP 200 instead of creating a new deal
- [ ] DynamoDB lookup before Magnite call

**Option B — Sales Agent side (interim):**
- [ ] Before calling `/activations`, the adapter fetches the sale and checks if `activations[]` is already populated. If yes, short-circuit and return the existing activation.

### Where
- `curation-activation/src/routes/activation.py` (idempotency logic)
- `curation-activation/src/repositories/activation_repo.py` (duplicate check)
- `src/adapters/curation/adapter.py` (interim short-circuit)

---

## TODO-012: Consolidate curation mock modes

- **Beads:** `salesagent-up5`
- **Priority:** P3
- **Status:** open
- **Type:** chore

### Why
Two independent mock flags exist today:
- `CurationConnectionConfig.mock_activation` (adapter side) — fabricates `mock-deal-{uuid}` without calling activation service
- `curation-activation`'s `MOCK_MAGNITE` / `MOCK_CATALOG` / `MOCK_SALES` (service side) — uses in-process mock clients

Intentional for now, but it's two knobs that do overlapping things. Developers will get confused about which to flip for which scenario.

### What (acceptance criteria)
- [ ] Decide the canonical mock path (likely: activation service owns all mock behavior; adapter-side flag removed)
- [ ] Document the chosen approach in `curation-integration-summary.md`
- [ ] Remove the redundant flag
- [ ] Update tests

### Where
- `src/adapters/curation/config.py` (`mock_activation` field)
- `src/adapters/curation/adapter.py` (mock branch around lines 215-220)
- `curation-activation/src/config.py`
- `tests/unit/test_curation_adapter.py`

---

## TODO-013: End-to-end `update_media_buy` on curation staging

- **Beads:** `salesagent-rk6`
- **Priority:** P3
- **Status:** open
- **Type:** test

### Why
`update_media_buy` is implemented in `CurationAdapter` (pause/resume/cancel via status mapping) but hasn't been fully tested end-to-end on staging. Unit tests cover the status mapping dict but not the real HTTP round-trip against curation-sales or the resulting Magnite state.

### What (acceptance criteria)
- [ ] Manual test run on staging: create curation sale → pause → resume → cancel
- [ ] Verify curation-sales status transitions correctly
- [ ] Verify Magnite deal state reflects pause/resume (if applicable)
- [ ] Add integration test covering the full flow (if harness supports it)
- [ ] Document any edge cases found (e.g., paused sale + already-live RTB rule)

### Where
- `src/adapters/curation/adapter.py` (`update_media_buy` method around line 323)
- `tests/integration/` (new test file if none exists for curation)
