# get_media_buys — Curation Tenant Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `get_media_buys` return real media buys for curation tenants (currently returns `[]`) by wiring the tool to the external `curation_sales` service via the existing `CurationAdapter` early-return pattern.

**Architecture:** Two-phase cross-repo change. Phase 1 extends `curation_sales` `GET /api/v1/sales` with list-valued filters (`sale_ids`, `buyer_refs`, `statuses`). Phase 2 adds `SalesClient.list_sales()`, `CurationAdapter.list_media_buys()`, and an early-return branch in `_get_media_buys_impl` — fetch-all pagination up to a configurable safety cap, soft truncation signal in `errors[]`, status reverse-mapping via `ADCP_STATUS_TO_SALE_STATUSES`.

**Tech Stack:** Python 3.12, FastAPI, PynamoDB (DynamoDB), Pydantic v2, pytest. Salesagent uses FastMCP + A2A SDK + SQLAlchemy 2.0; curation_sales is a standalone FastAPI service.

**Spec:** `docs/superpowers/specs/2026-04-09-get-media-buys-curation-support-design.md`

**Beads:** `salesagent-ckh`

**Cross-repo note:** Phase 1 work happens in `/Users/hrishikeshjangir/Dev/curation_sales/`. Phase 2 work happens in `/Users/hrishikeshjangir/Dev/salesagent/`. Each repo has its own git history and PR. Salesagent unit tests mock `SalesClient` at the object boundary, so Phase 2 unit tests can pass without Phase 1 being deployed — but the Phase 2 integration test and staging smoke test need Phase 1 merged and deployed to staging first.

---

# Phase 1 — `curation_sales` repo

**Working directory for all Phase 1 tasks:** `/Users/hrishikeshjangir/Dev/curation_sales/`

**Test command:** `uv run pytest tests/test_sales.py -v` (or whatever the repo's canonical runner is — check `pyproject.toml` / `Makefile` if `uv` isn't set up).

---

## Task 1.1: Extend `SaleListParams` model

**Files:**
- Modify: `src/models/sale.py` (around lines 110–115 — the existing `SaleListParams` class)

- [ ] **Step 1: Read the current model**

Run: `sed -n '105,120p' src/models/sale.py`
Expected: shows the current `SaleListParams` class with `status`, `limit`, `cursor`.

- [ ] **Step 2: Update the class with new list fields**

Replace the existing `SaleListParams` class with:

```python
class SaleListParams(BaseModel):
    """Query parameters for listing sales."""

    status: Optional[str] = None  # legacy single-status filter (backward compat)
    statuses: Optional[List[str]] = None  # multi-status filter
    sale_ids: Optional[List[str]] = None  # primary-key batch lookup
    buyer_refs: Optional[List[str]] = None  # buyer reference filter (post-filter, no GSI)
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    cursor: Optional[str] = None
```

- [ ] **Step 3: Verify imports**

Check the top of `src/models/sale.py` already imports `List` and `Optional` from `typing`. If not, add:

```python
from typing import Annotated, List, Optional
```

- [ ] **Step 4: Run model sanity check**

Run: `uv run python -c "from src.models.sale import SaleListParams; p = SaleListParams(sale_ids=['a','b'], buyer_refs=['c'], statuses=['active','paused']); print(p)"`
Expected: prints a valid `SaleListParams` object with all fields populated.

- [ ] **Step 5: Commit**

```bash
git add src/models/sale.py
git commit -m "feat(sales): extend SaleListParams with list filters

Add sale_ids, buyer_refs, and statuses fields to SaleListParams for
multi-value filtering. Preserves existing status field for backward
compatibility."
```

---

## Task 1.2: Extend `MockSaleRepository.list()` in test fixtures

**Files:**
- Modify: `tests/conftest.py` (lines 89–97 — `MockSaleRepository.list()`)

The mock repo is used by existing tests via the `client` fixture. Extend it to support the new filters so downstream tests pass.

- [ ] **Step 1: Read the current mock list method**

Run: `sed -n '89,97p' tests/conftest.py`
Expected: current mock list method with status-only filtering.

- [ ] **Step 2: Replace with filter-aware version**

Replace the existing `list` method in `MockSaleRepository` with:

```python
def list(self, params: SaleListParams) -> SaleListResponse:
    """List sales with optional filters."""
    items = []
    for data in self._sales.values():
        # sale_ids: primary-key batch (overrides cursor pagination)
        if params.sale_ids and data["sale_id"] not in params.sale_ids:
            continue
        # status (legacy single)
        if params.status and data["status"] != params.status:
            continue
        # statuses (multi)
        if params.statuses and data["status"] not in params.statuses:
            continue
        # buyer_refs
        if params.buyer_refs and data["buyer_ref"] not in params.buyer_refs:
            continue
        items.append(SaleResponse(**data))

    return SaleListResponse(items=items[: params.limit], next_cursor=None)
```

- [ ] **Step 3: Run existing tests to confirm no regression**

Run: `uv run pytest tests/test_sales.py::TestListSales -v`
Expected: 3 existing tests still pass (`test_list_all_sales`, `test_list_by_status`, `test_list_with_limit`).

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py
git commit -m "test(sales): extend MockSaleRepository with new filter support

Adds sale_ids, buyer_refs, and statuses filter handling to the mock repo
so downstream tests can exercise the new filter behavior through the
FastAPI TestClient."
```

---

## Task 1.3: Add `sale_ids` filter — failing test

**Files:**
- Modify: `tests/test_sales.py` (after the existing `TestListSales` tests, around line 238)

- [ ] **Step 1: Add failing test**

Append to `TestListSales` class in `tests/test_sales.py`:

```python
def test_list_by_sale_ids(self, client, sample_sale_data):
    """Filter by explicit list of sale IDs returns only those sales."""
    # Create 3 sales
    ids = []
    for _ in range(3):
        resp = client.post("/api/v1/sales", json=sample_sale_data)
        ids.append(resp.json()["sale_id"])

    # Filter to 2 of them
    response = client.get(
        "/api/v1/sales",
        params={"sale_ids": [ids[0], ids[2]]},
    )

    assert response.status_code == 200
    data = response.json()
    returned_ids = {item["sale_id"] for item in data["items"]}
    assert returned_ids == {ids[0], ids[2]}
    assert data["next_cursor"] is None  # sale_ids path does not paginate

def test_list_by_sale_ids_nonexistent_silently_skipped(self, client, sample_sale_data):
    """Non-existent sale IDs in the list are silently skipped."""
    resp = client.post("/api/v1/sales", json=sample_sale_data)
    real_id = resp.json()["sale_id"]

    response = client.get(
        "/api/v1/sales",
        params={"sale_ids": [real_id, "nonexistent-id-123"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["sale_id"] == real_id
```

- [ ] **Step 2: Run the new tests, expect failures**

Run: `uv run pytest tests/test_sales.py::TestListSales::test_list_by_sale_ids tests/test_sales.py::TestListSales::test_list_by_sale_ids_nonexistent_silently_skipped -v`
Expected: FAIL because the route doesn't accept a `sale_ids` query param yet (or the values are ignored).

---

## Task 1.4: Add `sale_ids` filter — route + real repo implementation

**Files:**
- Modify: `src/routes/sales.py` (lines 73–81 — `list_sales` handler)
- Modify: `src/repositories/sale_repo.py` (lines 156–189 — `list()` method)

- [ ] **Step 1: Update route signature to accept new list params**

Replace the `list_sales` function in `src/routes/sales.py` with:

```python
@router.get("", response_model=SaleListResponse)
async def list_sales(
    status: str | None = Query(None, description="Filter by single status (legacy)"),
    statuses: list[str] | None = Query(None, description="Filter by multiple statuses"),
    sale_ids: list[str] | None = Query(None, description="Filter by sale IDs"),
    buyer_refs: list[str] | None = Query(None, description="Filter by buyer references"),
    limit: int = Query(20, ge=1, le=100, description="Maximum results"),
    cursor: str | None = Query(None, description="Pagination cursor"),
) -> SaleListResponse:
    """List sales with optional filters."""
    params = SaleListParams(
        status=status,
        statuses=statuses,
        sale_ids=sale_ids,
        buyer_refs=buyer_refs,
        limit=limit,
        cursor=cursor,
    )
    return get_sale_repository().list(params)
```

- [ ] **Step 2: Extract cursor decoding + add post-filter helper on `SaleRepository`**

In `src/repositories/sale_repo.py`, add these two helper methods to `SaleRepository` (place them after the existing `list` method, before `delete`):

```python
@staticmethod
def _decode_cursor(cursor: str | None) -> dict | None:
    """Decode a Base64 JSON cursor. Returns None for missing/invalid."""
    if not cursor:
        return None
    try:
        return json.loads(base64.b64decode(cursor).decode())
    except (ValueError, json.JSONDecodeError):
        return None

@staticmethod
def _encode_cursor(last_evaluated_key: dict | None) -> str | None:
    """Encode a DynamoDB last_evaluated_key as a Base64 JSON cursor."""
    if not last_evaluated_key:
        return None
    return base64.b64encode(json.dumps(last_evaluated_key).encode()).decode()

@staticmethod
def _apply_post_filters(items: list, params: SaleListParams) -> list:
    """Apply in-memory filters that cannot be pushed down to DynamoDB.

    Currently: buyer_refs (no GSI), statuses (no multi-value GSI query).
    """
    if params.buyer_refs:
        items = [s for s in items if s.buyer_ref in params.buyer_refs]
    if params.statuses:
        items = [s for s in items if s.status in params.statuses]
    return items
```

- [ ] **Step 3: Replace `SaleRepository.list()` with the dispatch version**

Replace the entire existing `list` method in `SaleRepository` with:

```python
@retry_on_error()
def list(self, params: SaleListParams) -> SaleListResponse:
    """List sales with optional filters.

    Dispatch order:
        1. sale_ids present → batch_get (primary-key lookup), then post-filter.
           sale_ids path does NOT paginate — next_cursor is always None.
        2. Single `status` only (no statuses/buyer_refs) → GSI query fast path.
        3. Otherwise → full scan + Python post-filter.

    NOTE: When filters are applied post-query (branches 2/3 with filters, or
    branch 3 unconditionally), a page may return fewer than `limit` items
    because DynamoDB's scan budget is spent BEFORE filtering. The cursor still
    advances correctly; clients must follow `next_cursor` until it's null.
    """
    last_evaluated_key = self._decode_cursor(params.cursor)

    # 1. sale_ids path — batch_get, no pagination
    if params.sale_ids:
        items: list = []
        for sid in params.sale_ids:
            try:
                items.append(SaleModel.get(sid))
            except DoesNotExist:
                continue
        items = self._apply_post_filters(items, params)
        # Also apply legacy single-status filter if provided
        if params.status:
            items = [s for s in items if s.status == params.status]
        responses = [self._to_response(sale) for sale in items[: params.limit]]
        return SaleListResponse(items=responses, next_cursor=None)

    # 2. Single-status GSI fast path
    needs_post_filter = bool(params.buyer_refs or params.statuses)
    if params.status and not needs_post_filter:
        query = SaleModel.status_index.query(
            params.status,
            limit=params.limit,
            last_evaluated_key=last_evaluated_key,
        )
        items = list(query)
        responses = [self._to_response(sale) for sale in items]
        return SaleListResponse(
            items=responses,
            next_cursor=self._encode_cursor(query.last_evaluated_key),
        )

    # 3. Scan + post-filter (multi-status, buyer_refs, or unfiltered)
    query = SaleModel.scan(
        limit=params.limit,
        last_evaluated_key=last_evaluated_key,
    )
    scanned = list(query)
    # Apply legacy single-status too (acts as a synonym for statuses=[status])
    if params.status:
        scanned = [s for s in scanned if s.status == params.status]
    filtered = self._apply_post_filters(scanned, params)
    responses = [self._to_response(sale) for sale in filtered]
    return SaleListResponse(
        items=responses,
        next_cursor=self._encode_cursor(query.last_evaluated_key),
    )
```

- [ ] **Step 4: Run the sale_ids tests, expect pass**

Run: `uv run pytest tests/test_sales.py::TestListSales::test_list_by_sale_ids tests/test_sales.py::TestListSales::test_list_by_sale_ids_nonexistent_silently_skipped -v`
Expected: PASS.

- [ ] **Step 5: Run full list test class to verify no regression**

Run: `uv run pytest tests/test_sales.py::TestListSales -v`
Expected: all 5 tests pass (3 existing + 2 new).

- [ ] **Step 6: Commit**

```bash
git add src/routes/sales.py src/repositories/sale_repo.py tests/test_sales.py
git commit -m "feat(sales): add sale_ids filter to GET /api/v1/sales

Primary-key batch lookup path via SaleModel.get() loop (PynamoDB does not
expose BatchGetItem directly). sale_ids path ignores cursor pagination
and returns all matching sales up to limit.

Extracts cursor encode/decode and post-filter logic into helpers for
reuse across the other filter branches."
```

---

## Task 1.5: Add `buyer_refs` filter — test + verify

**Files:**
- Modify: `tests/test_sales.py` (extend `TestListSales`)

The repo dispatch logic from Task 1.4 already handles `buyer_refs` via scan+filter. This task just verifies with tests.

- [ ] **Step 1: Add failing test**

Append to `TestListSales` class:

```python
def test_list_by_buyer_refs(self, client, sample_sale_data):
    """Filter by buyer_refs returns only matching sales."""
    # Create sales for buyer A
    data_a = {**sample_sale_data, "buyer_ref": "buyer-a"}
    client.post("/api/v1/sales", json=data_a)
    client.post("/api/v1/sales", json=data_a)

    # Create sale for buyer B
    data_b = {**sample_sale_data, "buyer_ref": "buyer-b"}
    client.post("/api/v1/sales", json=data_b)

    response = client.get(
        "/api/v1/sales",
        params={"buyer_refs": ["buyer-a"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert all(item["buyer_ref"] == "buyer-a" for item in data["items"])

def test_list_by_multiple_buyer_refs(self, client, sample_sale_data):
    """Filter by multiple buyer_refs returns union."""
    for ref in ["buyer-a", "buyer-b", "buyer-c"]:
        data = {**sample_sale_data, "buyer_ref": ref}
        client.post("/api/v1/sales", json=data)

    response = client.get(
        "/api/v1/sales",
        params={"buyer_refs": ["buyer-a", "buyer-c"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    returned_refs = {item["buyer_ref"] for item in data["items"]}
    assert returned_refs == {"buyer-a", "buyer-c"}
```

- [ ] **Step 2: Run the new tests**

Run: `uv run pytest tests/test_sales.py::TestListSales::test_list_by_buyer_refs tests/test_sales.py::TestListSales::test_list_by_multiple_buyer_refs -v`
Expected: PASS (mock already handles it, real repo already handles it via Task 1.4).

- [ ] **Step 3: Commit**

```bash
git add tests/test_sales.py
git commit -m "test(sales): add buyer_refs filter coverage"
```

---

## Task 1.6: Add `statuses` multi-value filter — test + verify

**Files:**
- Modify: `tests/test_sales.py` (extend `TestListSales`)

- [ ] **Step 1: Add failing test**

Append to `TestListSales` class:

```python
def test_list_by_multiple_statuses(self, client, sample_sale_data):
    """Filter by list of statuses returns sales in any of those statuses."""
    # Create 3 sales, set 2 different statuses
    ids = []
    for _ in range(3):
        resp = client.post("/api/v1/sales", json=sample_sale_data)
        ids.append(resp.json()["sale_id"])

    # Update statuses: [active, paused, pending_activation]
    client.patch(f"/api/v1/sales/{ids[0]}", json={"status": "active"})
    client.patch(f"/api/v1/sales/{ids[1]}", json={"status": "paused"})
    # ids[2] stays pending_activation (default)

    response = client.get(
        "/api/v1/sales",
        params={"statuses": ["active", "paused"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    returned_statuses = {item["status"] for item in data["items"]}
    assert returned_statuses == {"active", "paused"}
```

- [ ] **Step 2: Run the new test**

Run: `uv run pytest tests/test_sales.py::TestListSales::test_list_by_multiple_statuses -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_sales.py
git commit -m "test(sales): add multi-status filter coverage"
```

---

## Task 1.7: Combined filters — test + verify

**Files:**
- Modify: `tests/test_sales.py` (extend `TestListSales`)

- [ ] **Step 1: Add test covering combined filters**

Append to `TestListSales` class:

```python
def test_list_combined_sale_ids_and_buyer_refs(self, client, sample_sale_data):
    """sale_ids + buyer_refs → intersection (AND logic)."""
    # Create sales for two buyers
    ids_a = []
    for _ in range(2):
        resp = client.post(
            "/api/v1/sales",
            json={**sample_sale_data, "buyer_ref": "buyer-a"},
        )
        ids_a.append(resp.json()["sale_id"])

    ids_b = []
    for _ in range(2):
        resp = client.post(
            "/api/v1/sales",
            json={**sample_sale_data, "buyer_ref": "buyer-b"},
        )
        ids_b.append(resp.json()["sale_id"])

    # Filter: union of IDs from both buyers, but restrict to buyer-a only
    response = client.get(
        "/api/v1/sales",
        params={
            "sale_ids": ids_a + ids_b,
            "buyer_refs": ["buyer-a"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert all(item["buyer_ref"] == "buyer-a" for item in data["items"])
    returned_ids = {item["sale_id"] for item in data["items"]}
    assert returned_ids == set(ids_a)

def test_list_combined_statuses_and_buyer_refs(self, client, sample_sale_data):
    """statuses + buyer_refs → intersection (AND logic)."""
    # Create 2 sales for buyer-a (one will be active, one pending)
    resp_a1 = client.post("/api/v1/sales", json={**sample_sale_data, "buyer_ref": "buyer-a"})
    client.patch(f"/api/v1/sales/{resp_a1.json()['sale_id']}", json={"status": "active"})
    client.post("/api/v1/sales", json={**sample_sale_data, "buyer_ref": "buyer-a"})

    # Create 1 active sale for buyer-b
    resp_b = client.post("/api/v1/sales", json={**sample_sale_data, "buyer_ref": "buyer-b"})
    client.patch(f"/api/v1/sales/{resp_b.json()['sale_id']}", json={"status": "active"})

    # Filter: active AND buyer-a → 1 result
    response = client.get(
        "/api/v1/sales",
        params={
            "statuses": ["active"],
            "buyer_refs": ["buyer-a"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["buyer_ref"] == "buyer-a"
    assert data["items"][0]["status"] == "active"
```

- [ ] **Step 2: Run combined-filter tests**

Run: `uv run pytest tests/test_sales.py::TestListSales::test_list_combined_sale_ids_and_buyer_refs tests/test_sales.py::TestListSales::test_list_combined_statuses_and_buyer_refs -v`
Expected: PASS.

- [ ] **Step 3: Run the entire list test class for a full regression check**

Run: `uv run pytest tests/test_sales.py::TestListSales -v`
Expected: all tests pass (3 original + 7 new = 10 total).

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass, no regressions in unrelated tests.

- [ ] **Step 5: Commit**

```bash
git add tests/test_sales.py
git commit -m "test(sales): add combined-filter intersection tests"
```

---

## Task 1.8: Backward-compat regression check

**Files:** none (pure verification task)

- [ ] **Step 1: Confirm existing clients that only pass `status=X` still work**

Run: `uv run pytest tests/test_sales.py::TestListSales::test_list_by_status -v`
Expected: PASS. This is the existing test; it asserts the legacy single-status path still works.

- [ ] **Step 2: Manually verify no query-param typo or missing `Query()` annotation**

Run: `uv run python -c "from src.main import app; import json; print(json.dumps([r.path for r in app.routes if 'sales' in r.path], indent=2))"`
Expected: route list includes `/api/v1/sales` and friends.

- [ ] **Step 3: Create Phase 1 summary PR**

Phase 1 is complete. Push branch and create PR:

```bash
git push -u origin <branch-name>
gh pr create --title "feat(sales): add list filters (sale_ids, buyer_refs, statuses)" --body "..."
```

Commit message body template:

```
## Summary
- Extend GET /api/v1/sales with three new list-valued filters: sale_ids, buyer_refs, statuses
- sale_ids uses primary-key batch lookup (bypasses cursor pagination)
- buyer_refs and multi-status use scan + Python post-filter (no GSI exists)
- Keeps legacy `status` single-value param for backward compatibility
- Documents sparse-page behavior: post-filtering may return fewer than `limit` items

## Test plan
- [x] test_list_by_sale_ids
- [x] test_list_by_sale_ids_nonexistent_silently_skipped
- [x] test_list_by_buyer_refs
- [x] test_list_by_multiple_buyer_refs
- [x] test_list_by_multiple_statuses
- [x] test_list_combined_sale_ids_and_buyer_refs
- [x] test_list_combined_statuses_and_buyer_refs
- [x] All existing tests still pass

## Follow-ups
- Add `buyer_ref-index` GSI for scale (separate ticket)
- Document that scan-based filters are O(n) on table size

Companion to salesagent `feature/pubx-integration` — see `salesagent-ckh`.
```

---

# Phase 2 — `salesagent` repo

**Working directory for all Phase 2 tasks:** `/Users/hrishikeshjangir/Dev/salesagent/`

**Test command:** `uv run pytest tests/unit/test_curation_adapter.py -v` (unit), `scripts/run-test.sh tests/integration/test_curation_get_media_buys.py -x` (integration).

**Branch:** Work on the existing `feature/pubx-integration` branch.

---

## Task 2.1: Add `max_media_buys_per_list` to `CurationConnectionConfig`

**Files:**
- Modify: `src/adapters/curation/config.py`

- [ ] **Step 1: Read current config class**

Run: `uv run python -c "from src.adapters.curation.config import CurationConnectionConfig; print([(f, m.annotation) for f, m in CurationConnectionConfig.model_fields.items()])"`
Expected: lists existing fields — `catalog_service_url`, `sales_service_url`, `activation_service_url`, `pricing_multiplier`, `pricing_floor_cpm`, `pricing_max_suggested_cpm`, `mock_activation`, `http_timeout_seconds`.

- [ ] **Step 2: Add the new field**

Insert before `http_timeout_seconds` in `src/adapters/curation/config.py`:

```python
    max_media_buys_per_list: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="Safety cap on the number of sales fetched in a single get_media_buys call",
    )
```

- [ ] **Step 3: Verify field is accepted**

Run: `uv run python -c "from src.adapters.curation.config import CurationConnectionConfig; c = CurationConnectionConfig(sales_service_url='http://x', catalog_service_url='http://y', activation_service_url='http://z', max_media_buys_per_list=250); print(c.max_media_buys_per_list)"`
Expected: prints `250`.

- [ ] **Step 4: Verify default is 500**

Run: `uv run python -c "from src.adapters.curation.config import CurationConnectionConfig; c = CurationConnectionConfig(); print(c.max_media_buys_per_list)"`
Expected: prints `500`.

- [ ] **Step 5: Verify validation bounds**

Run: `uv run python -c "from src.adapters.curation.config import CurationConnectionConfig; CurationConnectionConfig(max_media_buys_per_list=6000)"`
Expected: raises `ValidationError` (exceeds `le=5000`).

- [ ] **Step 6: Commit**

```bash
git add src/adapters/curation/config.py
git commit -m "feat(curation): add max_media_buys_per_list config field

Safety cap (default 500, range 1-5000) for get_media_buys paginated
fetch-all loop. Configurable per tenant via the Pubx Curation admin UI
(form field added in a later task)."
```

---

## Task 2.2: Wire `max_media_buys_per_list` into `CurationAdapter.__init__`

**Files:**
- Modify: `src/adapters/curation/adapter.py` (around lines 95–128)

- [ ] **Step 1: Add field capture in `__init__`**

In `src/adapters/curation/adapter.py`, within `CurationAdapter.__init__`, add after `self._mock_activation = conn.mock_activation`:

```python
        self._max_media_buys_per_list = conn.max_media_buys_per_list
```

- [ ] **Step 2: Sanity check**

Run: `uv run python -c "
from src.adapters.curation.adapter import CurationAdapter
from src.core.schemas import Principal
p = Principal(principal_id='p1', name='p', platform_mappings={})
a = CurationAdapter(
    config={'sales_service_url': 'http://s', 'catalog_service_url': 'http://c', 'activation_service_url': 'http://a'},
    principal=p,
    tenant_id='t1',
)
print(a._max_media_buys_per_list)
"`
Expected: prints `500` (the default).

- [ ] **Step 3: Commit**

```bash
git add src/adapters/curation/adapter.py
git commit -m "feat(curation): capture max_media_buys_per_list on CurationAdapter"
```

---

## Task 2.3: Add `SalesClient.list_sales()` — failing unit test

**Files:**
- Modify: `tests/unit/test_curation_adapter.py` (append to end of file)

- [ ] **Step 1: Add failing test class**

Append to `tests/unit/test_curation_adapter.py`:

```python
# ── SalesClient.list_sales Tests ─────────────────────────────────────────


class TestSalesClientListSales:
    """SalesClient.list_sales() wraps the /api/v1/sales GET endpoint."""

    def test_list_sales_passes_filters_as_query_params(self):
        from src.adapters.curation.sales_client import SalesClient

        client = SalesClient(base_url="http://test")
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = {"items": [], "next_cursor": None}
            client.list_sales(
                status="active",
                statuses=["active", "paused"],
                sale_ids=["s1", "s2"],
                buyer_refs=["b1"],
                limit=50,
                cursor="tok",
            )

        mock_request.assert_called_once_with(
            "GET",
            "/api/v1/sales",
            params={
                "limit": 50,
                "cursor": "tok",
                "status": "active",
                "statuses": ["active", "paused"],
                "sale_ids": ["s1", "s2"],
                "buyer_refs": ["b1"],
            },
        )

    def test_list_sales_omits_none_filters(self):
        from src.adapters.curation.sales_client import SalesClient

        client = SalesClient(base_url="http://test")
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = {"items": [], "next_cursor": None}
            client.list_sales(limit=20)

        mock_request.assert_called_once_with(
            "GET",
            "/api/v1/sales",
            params={"limit": 20},
        )

    def test_list_sales_returns_raw_dict(self):
        from src.adapters.curation.sales_client import SalesClient

        client = SalesClient(base_url="http://test")
        expected = {"items": [{"sale_id": "s1"}], "next_cursor": "next"}
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = expected
            result = client.list_sales()

        assert result == expected
```

- [ ] **Step 2: Run the new tests, expect failures**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestSalesClientListSales -v`
Expected: all 3 FAIL with `AttributeError: 'SalesClient' object has no attribute 'list_sales'`.

---

## Task 2.4: Implement `SalesClient.list_sales()`

**Files:**
- Modify: `src/adapters/curation/sales_client.py`

- [ ] **Step 1: Add the method**

Append to the `SalesClient` class in `src/adapters/curation/sales_client.py` (after `update_sale`):

```python
    def list_sales(
        self,
        *,
        status: str | None = None,
        statuses: list[str] | None = None,
        sale_ids: list[str] | None = None,
        buyer_refs: list[str] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List sales with optional filters. Returns a single page.

        Args:
            status: Legacy single-value status filter.
            statuses: Multi-value status filter (wins over ``status`` if both set).
            sale_ids: Filter to specific sale IDs (primary-key lookup).
            buyer_refs: Filter to specific buyer references.
            limit: Max items per page (sales service max is 100).
            cursor: Opaque pagination cursor from a prior response.

        Returns:
            dict with keys ``items`` (list of sale dicts) and ``next_cursor``
            (str or None when no more pages).
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        if statuses:
            params["statuses"] = statuses
        if sale_ids:
            params["sale_ids"] = sale_ids
        if buyer_refs:
            params["buyer_refs"] = buyer_refs
        return self._request("GET", "/api/v1/sales", params=params)
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestSalesClientListSales -v`
Expected: all 3 PASS.

- [ ] **Step 3: Commit**

```bash
git add src/adapters/curation/sales_client.py tests/unit/test_curation_adapter.py
git commit -m "feat(curation): add SalesClient.list_sales single-page primitive

Mirrors the curation_sales GET /api/v1/sales endpoint contract. Single
page only; pagination loop is owned by CurationAdapter.list_media_buys
so it can enforce the safety cap."
```

---

## Task 2.5: Add `ADCP_STATUS_TO_SALE_STATUSES` reverse map + test

**Files:**
- Modify: `src/adapters/curation/adapter.py`
- Modify: `tests/unit/test_curation_adapter.py`

- [ ] **Step 1: Add failing test**

Append to `tests/unit/test_curation_adapter.py`:

```python
# ── ADCP_STATUS_TO_SALE_STATUSES Tests ────────────────────────────────────


class TestAdcpToSaleStatusReverseMap:
    """Reverse mapping of AdCP MediaBuyStatus values to curation sale statuses."""

    def test_pending_activation_maps_to_both_pending_states(self):
        from src.adapters.curation.adapter import ADCP_STATUS_TO_SALE_STATUSES

        assert ADCP_STATUS_TO_SALE_STATUSES["pending_activation"] == [
            "pending_approval",
            "pending_activation",
        ]

    def test_active_maps_to_single_active(self):
        from src.adapters.curation.adapter import ADCP_STATUS_TO_SALE_STATUSES

        assert ADCP_STATUS_TO_SALE_STATUSES["active"] == ["active"]

    def test_completed_maps_to_completed_and_canceled(self):
        from src.adapters.curation.adapter import ADCP_STATUS_TO_SALE_STATUSES

        assert ADCP_STATUS_TO_SALE_STATUSES["completed"] == ["completed", "canceled"]

    def test_failed_maps_to_failed_and_rejected(self):
        from src.adapters.curation.adapter import ADCP_STATUS_TO_SALE_STATUSES

        assert ADCP_STATUS_TO_SALE_STATUSES["failed"] == ["failed", "rejected"]

    def test_reverse_map_covers_all_forward_mapping_values(self):
        from src.adapters.curation.adapter import (
            ADCP_STATUS_TO_SALE_STATUSES,
            SALE_STATUS_TO_ADCP,
        )

        forward_adcp_values = set(SALE_STATUS_TO_ADCP.values())
        reverse_keys = set(ADCP_STATUS_TO_SALE_STATUSES.keys())
        assert forward_adcp_values == reverse_keys, (
            "Every AdCP status in SALE_STATUS_TO_ADCP.values() must be a key "
            "in ADCP_STATUS_TO_SALE_STATUSES, and vice versa."
        )
```

- [ ] **Step 2: Run the tests, expect failures**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestAdcpToSaleStatusReverseMap -v`
Expected: all FAIL with `ImportError: cannot import name 'ADCP_STATUS_TO_SALE_STATUSES'`.

- [ ] **Step 3: Add the dict**

In `src/adapters/curation/adapter.py`, add immediately after `SALE_STATUS_TO_ADCP` (around line 57):

```python
# Inverse of SALE_STATUS_TO_ADCP. One AdCP status may map to multiple curation
# statuses because the forward mapping is lossy (both `completed` and `canceled`
# map to AdCP `completed`; both `failed` and `rejected` map to AdCP `failed`;
# both `pending_approval` and `pending_activation` map to AdCP `pending_activation`).
ADCP_STATUS_TO_SALE_STATUSES: dict[str, list[str]] = {
    "pending_activation": ["pending_approval", "pending_activation"],
    "active": ["active"],
    "paused": ["paused"],
    "completed": ["completed", "canceled"],
    "failed": ["failed", "rejected"],
}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestAdcpToSaleStatusReverseMap -v`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adapters/curation/adapter.py tests/unit/test_curation_adapter.py
git commit -m "feat(curation): add ADCP_STATUS_TO_SALE_STATUSES reverse map"
```

---

## Task 2.6: Add `ListMediaBuysResult` dataclass + `_parse_iso` helper

**Files:**
- Modify: `src/adapters/curation/adapter.py`
- Modify: `tests/unit/test_curation_adapter.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/test_curation_adapter.py`:

```python
# ── Helpers Tests ─────────────────────────────────────────────────────────


class TestListMediaBuysResult:
    def test_default_construction(self):
        from src.adapters.curation.adapter import ListMediaBuysResult

        result = ListMediaBuysResult(
            media_buys=[],
            truncated=False,
            total_fetched=0,
        )
        assert result.media_buys == []
        assert result.truncated is False
        assert result.total_fetched == 0

    def test_with_items(self):
        from src.adapters.curation.adapter import ListMediaBuysResult

        result = ListMediaBuysResult(
            media_buys=["placeholder"],
            truncated=True,
            total_fetched=500,
        )
        assert len(result.media_buys) == 1
        assert result.truncated is True
        assert result.total_fetched == 500


class TestParseIso:
    def test_parses_z_suffix(self):
        from src.adapters.curation.adapter import _parse_iso

        result = _parse_iso("2026-04-09T12:34:56Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 9
        assert result.hour == 12

    def test_parses_plus_offset(self):
        from src.adapters.curation.adapter import _parse_iso

        result = _parse_iso("2026-04-09T12:34:56+00:00")
        assert result is not None
        assert result.year == 2026

    def test_returns_none_for_none(self):
        from src.adapters.curation.adapter import _parse_iso

        assert _parse_iso(None) is None

    def test_returns_none_for_empty_string(self):
        from src.adapters.curation.adapter import _parse_iso

        assert _parse_iso("") is None
```

- [ ] **Step 2: Run the tests, expect failures**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestListMediaBuysResult tests/unit/test_curation_adapter.py::TestParseIso -v`
Expected: all FAIL with `ImportError`.

- [ ] **Step 3: Add imports + helper + dataclass**

In `src/adapters/curation/adapter.py`, ensure `dataclass` is imported at the top:

```python
from dataclasses import dataclass
```

Also ensure these are imported from `src.core.schemas` (add to existing import block):

```python
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    CreateMediaBuySuccess,
    DeliveryTotals,
    GetMediaBuysMediaBuy,  # NEW
    GetMediaBuysPackage,  # NEW
    MediaPackage,
    PackagePerformance,
    Principal,
    ReportingPeriod,
    UpdateMediaBuyResponse,
    UpdateMediaBuySuccess,
)
```

Then, after the `ACTION_TO_ADCP_STATUS` dict (around line 63), add:

```python
@dataclass
class ListMediaBuysResult:
    """Result of CurationAdapter.list_media_buys().

    Attributes:
        media_buys: Mapped AdCP media buys (one per sale in the result set).
        truncated: True if the fetch-all loop hit the safety cap before
            exhausting pages. The caller appends a soft errors[] entry so
            clients see the signal.
        total_fetched: Number of sales actually converted into media buys.
    """

    media_buys: list["GetMediaBuysMediaBuy"]
    truncated: bool
    total_fetched: int


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO8601 string into a datetime, or return None.

    Handles both ``2026-04-09T12:34:56Z`` and ``2026-04-09T12:34:56+00:00``.
    """
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestListMediaBuysResult tests/unit/test_curation_adapter.py::TestParseIso -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adapters/curation/adapter.py tests/unit/test_curation_adapter.py
git commit -m "feat(curation): add ListMediaBuysResult dataclass and _parse_iso helper"
```

---

## Task 2.7: Add `_sale_to_media_buy` converter — failing tests

**Files:**
- Modify: `tests/unit/test_curation_adapter.py`

- [ ] **Step 1: Add test fixtures + failing tests**

Append to `tests/unit/test_curation_adapter.py`:

```python
# ── _sale_to_media_buy Converter Tests ────────────────────────────────────


SAMPLE_SALE_DICT = {
    "sale_id": "sale-abc-123",
    "buyer_ref": "buyer-1",
    "buyer_campaign_ref": "camp-9",
    "segments": [
        {"segment_id": "seg-red"},
        {"segment_id": "seg-blue"},
    ],
    "activations": [],
    "pricing": {
        "pricing_model": "cpm",
        "currency": "USD",
        "floor_price": 2.50,
        "fixed_price": None,
    },
    "deal_type": "curated",
    "platform_id": "magnite",
    "dsps": [],
    "ad_format_types": None,
    "start_time": "2026-04-01T00:00:00Z",
    "end_time": "2026-04-30T23:59:59Z",
    "brand": None,
    "budget": 1000.0,
    "status": "active",
    "created_at": "2026-03-29T10:00:00Z",
    "updated_at": "2026-03-30T15:00:00Z",
}


def _make_adapter():
    """Helper: build a CurationAdapter instance for unit tests."""
    from src.adapters.curation.adapter import CurationAdapter
    from src.core.schemas import Principal

    p = Principal(principal_id="p1", name="p", platform_mappings={})
    return CurationAdapter(
        config={
            "sales_service_url": "http://sales.test",
            "catalog_service_url": "http://catalog.test",
            "activation_service_url": "http://activation.test",
        },
        principal=p,
        tenant_id="t1",
    )


class TestSaleToMediaBuy:
    def test_single_sale_with_two_segments_produces_two_packages(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_SALE_DICT)

        assert mb.media_buy_id == "sale-abc-123"
        assert mb.buyer_ref == "buyer-1"
        assert mb.buyer_campaign_ref == "camp-9"
        assert mb.status == "active"
        assert mb.currency == "USD"
        assert mb.total_budget == 1000.0
        assert len(mb.packages) == 2

    def test_package_ids_use_segment_id(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_SALE_DICT)
        pkg_ids = [pkg.package_id for pkg in mb.packages]
        assert pkg_ids == ["seg-red", "seg-blue"]

    def test_package_product_id_matches_package_id(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_SALE_DICT)
        for pkg in mb.packages:
            assert pkg.package_id == pkg.product_id

    def test_package_bid_price_from_sale_floor_price(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_SALE_DICT)
        for pkg in mb.packages:
            assert pkg.bid_price == 2.50

    def test_package_prefers_fixed_price_over_floor_price(self):
        adapter = _make_adapter()
        sale = {**SAMPLE_SALE_DICT, "pricing": {
            "pricing_model": "cpm", "currency": "USD",
            "floor_price": 2.50, "fixed_price": 5.00,
        }}
        mb = adapter._sale_to_media_buy(sale)
        for pkg in mb.packages:
            assert pkg.bid_price == 5.00

    def test_package_budget_is_none(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_SALE_DICT)
        for pkg in mb.packages:
            assert pkg.budget is None

    def test_package_buyer_ref_from_sale(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_SALE_DICT)
        for pkg in mb.packages:
            assert pkg.buyer_ref == "buyer-1"

    def test_package_times_from_sale(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_SALE_DICT)
        for pkg in mb.packages:
            assert pkg.start_time is not None
            assert pkg.start_time.year == 2026
            assert pkg.start_time.month == 4
            assert pkg.end_time is not None
            assert pkg.end_time.month == 4
            assert pkg.end_time.day == 30

    def test_zero_segments_yields_empty_packages(self):
        adapter = _make_adapter()
        sale = {**SAMPLE_SALE_DICT, "segments": []}
        mb = adapter._sale_to_media_buy(sale)
        assert mb.packages == []
        assert mb.media_buy_id == "sale-abc-123"

    def test_segment_without_id_is_skipped(self):
        adapter = _make_adapter()
        sale = {**SAMPLE_SALE_DICT, "segments": [
            {"segment_id": "seg-red"},
            {},  # missing segment_id
            {"segment_id": "seg-blue"},
        ]}
        mb = adapter._sale_to_media_buy(sale)
        assert [pkg.package_id for pkg in mb.packages] == ["seg-red", "seg-blue"]

    def test_status_maps_through_sale_status_dict(self):
        adapter = _make_adapter()
        sale = {**SAMPLE_SALE_DICT, "status": "canceled"}
        mb = adapter._sale_to_media_buy(sale)
        # SALE_STATUS_TO_ADCP["canceled"] == "completed"
        assert mb.status == "completed"

    def test_unknown_status_defaults_to_pending_activation(self):
        adapter = _make_adapter()
        sale = {**SAMPLE_SALE_DICT, "status": "weirdstate"}
        mb = adapter._sale_to_media_buy(sale)
        assert mb.status == "pending_activation"

    def test_missing_pricing_yields_none_bid_price(self):
        adapter = _make_adapter()
        sale = {**SAMPLE_SALE_DICT, "pricing": None}
        mb = adapter._sale_to_media_buy(sale)
        for pkg in mb.packages:
            assert pkg.bid_price is None
        assert mb.currency == "USD"  # default

    def test_missing_budget_yields_zero(self):
        adapter = _make_adapter()
        sale = {**SAMPLE_SALE_DICT, "budget": None}
        mb = adapter._sale_to_media_buy(sale)
        assert mb.total_budget == 0.0

    def test_per_segment_pricing_override_forward_compat(self):
        """Forward-compatibility: if segment has pricing, it wins."""
        adapter = _make_adapter()
        sale = {**SAMPLE_SALE_DICT, "segments": [
            {"segment_id": "seg-red", "pricing": {
                "fixed_price": 9.99, "currency": "USD",
            }},
            {"segment_id": "seg-blue"},  # uses sale-level
        ]}
        mb = adapter._sale_to_media_buy(sale)
        assert mb.packages[0].bid_price == 9.99
        assert mb.packages[1].bid_price == 2.50  # sale-level floor
```

- [ ] **Step 2: Run the tests, expect failures**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestSaleToMediaBuy -v`
Expected: all FAIL with `AttributeError: 'CurationAdapter' object has no attribute '_sale_to_media_buy'`.

---

## Task 2.8: Implement `_sale_to_media_buy`

**Files:**
- Modify: `src/adapters/curation/adapter.py`

- [ ] **Step 1: Add the method to `CurationAdapter`**

Add to `CurationAdapter` class in `src/adapters/curation/adapter.py` (place after the `update_media_buy_performance_index` method, before module-level helpers):

```python
    # ── Sale → AdCP media buy converter ────────────────────────────────

    def _sale_to_media_buy(self, sale: dict) -> GetMediaBuysMediaBuy:
        """Convert a curation SaleResponse dict to an AdCP GetMediaBuysMediaBuy.

        Mapping rules (see spec §5.5):
        - One sale segment → one GetMediaBuysPackage
        - package_id = product_id = segment.segment_id
        - bid_price: segment.pricing.fixed_price → segment.pricing.floor_price
          → sale.pricing.fixed_price → sale.pricing.floor_price → None
        - budget: always None at the package level (no per-package budget
          concept in curation sales)
        - status: SALE_STATUS_TO_ADCP mapping, fallback pending_activation
        """
        sale_id = sale["sale_id"]
        sale_pricing = sale.get("pricing") or {}
        currency = sale_pricing.get("currency", "USD")

        packages: list[GetMediaBuysPackage] = []
        for seg in sale.get("segments") or []:
            segment_id = seg.get("segment_id")
            if not segment_id:
                continue

            # Per-segment pricing override (forward-compat), else sale-level
            seg_pricing = seg.get("pricing") or sale_pricing
            bid_price = seg_pricing.get("fixed_price") or seg_pricing.get("floor_price")

            packages.append(
                GetMediaBuysPackage(
                    package_id=segment_id,
                    buyer_ref=sale.get("buyer_ref"),
                    budget=None,
                    bid_price=float(bid_price) if bid_price is not None else None,
                    product_id=segment_id,
                    start_time=_parse_iso(sale.get("start_time")),
                    end_time=_parse_iso(sale.get("end_time")),
                    paused=None,
                    creative_approvals=None,
                    snapshot=None,
                    snapshot_unavailable_reason=None,
                )
            )

        return GetMediaBuysMediaBuy(
            media_buy_id=sale_id,
            buyer_ref=sale.get("buyer_ref"),
            buyer_campaign_ref=sale.get("buyer_campaign_ref"),
            status=SALE_STATUS_TO_ADCP.get(sale.get("status", ""), "pending_activation"),
            currency=currency,
            total_budget=float(sale.get("budget") or 0.0),
            packages=packages,
            created_at=_parse_iso(sale.get("created_at")),
            updated_at=_parse_iso(sale.get("updated_at")),
        )
```

- [ ] **Step 2: Run the converter tests**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestSaleToMediaBuy -v`
Expected: 15 PASS.

- [ ] **Step 3: Commit**

```bash
git add src/adapters/curation/adapter.py tests/unit/test_curation_adapter.py
git commit -m "feat(curation): add _sale_to_media_buy converter

Maps curation SaleResponse dicts to AdCP GetMediaBuysMediaBuy objects.
One segment per package; bid_price falls back through per-segment →
sale-level → None; empty segments produce empty packages; unknown
statuses default to pending_activation."
```

---

## Task 2.9: Add `CurationAdapter.list_media_buys()` — failing tests

**Files:**
- Modify: `tests/unit/test_curation_adapter.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/test_curation_adapter.py`:

```python
# ── CurationAdapter.list_media_buys Tests ─────────────────────────────────


def _make_adapter_with_cap(cap: int = 500):
    """Helper: build an adapter with a custom max_media_buys_per_list cap."""
    from src.adapters.curation.adapter import CurationAdapter
    from src.core.schemas import Principal

    p = Principal(principal_id="p1", name="p", platform_mappings={})
    return CurationAdapter(
        config={
            "sales_service_url": "http://sales.test",
            "catalog_service_url": "http://catalog.test",
            "activation_service_url": "http://activation.test",
            "max_media_buys_per_list": cap,
        },
        principal=p,
        tenant_id="t1",
    )


def _sale_stub(sale_id: str, status: str = "active", buyer_ref: str = "buyer-1") -> dict:
    """Build a minimal valid sale dict for the converter."""
    return {
        "sale_id": sale_id,
        "buyer_ref": buyer_ref,
        "buyer_campaign_ref": None,
        "segments": [{"segment_id": f"seg-{sale_id}"}],
        "activations": [],
        "pricing": {"pricing_model": "cpm", "currency": "USD", "floor_price": 1.0},
        "deal_type": "curated",
        "platform_id": "magnite",
        "dsps": [],
        "ad_format_types": None,
        "start_time": "2026-04-01T00:00:00Z",
        "end_time": "2026-04-30T23:59:59Z",
        "brand": None,
        "budget": 100.0,
        "status": status,
        "created_at": "2026-03-29T10:00:00Z",
        "updated_at": "2026-03-30T15:00:00Z",
    }


class TestListMediaBuys:
    def test_empty_result(self):
        adapter = _make_adapter_with_cap()
        with patch.object(adapter._sales, "list_sales") as mock_list:
            mock_list.return_value = {"items": [], "next_cursor": None}
            result = adapter.list_media_buys()

        assert result.media_buys == []
        assert result.truncated is False
        assert result.total_fetched == 0

    def test_single_page_result(self):
        adapter = _make_adapter_with_cap()
        with patch.object(adapter._sales, "list_sales") as mock_list:
            mock_list.return_value = {
                "items": [_sale_stub("s1"), _sale_stub("s2")],
                "next_cursor": None,
            }
            result = adapter.list_media_buys()

        assert result.total_fetched == 2
        assert result.truncated is False
        assert [mb.media_buy_id for mb in result.media_buys] == ["s1", "s2"]

    def test_paginates_across_multiple_pages(self):
        adapter = _make_adapter_with_cap()
        with patch.object(adapter._sales, "list_sales") as mock_list:
            mock_list.side_effect = [
                {"items": [_sale_stub("s1"), _sale_stub("s2")], "next_cursor": "c1"},
                {"items": [_sale_stub("s3")], "next_cursor": None},
            ]
            result = adapter.list_media_buys()

        assert result.total_fetched == 3
        assert result.truncated is False
        assert [mb.media_buy_id for mb in result.media_buys] == ["s1", "s2", "s3"]
        # Verify cursor was passed on second call
        assert mock_list.call_args_list[1].kwargs["cursor"] == "c1"

    def test_truncates_at_cap(self):
        adapter = _make_adapter_with_cap(cap=2)
        with patch.object(adapter._sales, "list_sales") as mock_list:
            mock_list.return_value = {
                "items": [_sale_stub("s1"), _sale_stub("s2")],
                "next_cursor": "more",
            }
            result = adapter.list_media_buys()

        assert result.total_fetched == 2
        assert result.truncated is True

    def test_not_truncated_when_exactly_at_cap_and_no_more_pages(self):
        adapter = _make_adapter_with_cap(cap=2)
        with patch.object(adapter._sales, "list_sales") as mock_list:
            mock_list.return_value = {
                "items": [_sale_stub("s1"), _sale_stub("s2")],
                "next_cursor": None,
            }
            result = adapter.list_media_buys()

        assert result.total_fetched == 2
        assert result.truncated is False

    def test_cap_of_one_returns_one_item_and_signals_truncation(self):
        adapter = _make_adapter_with_cap(cap=1)
        with patch.object(adapter._sales, "list_sales") as mock_list:
            mock_list.return_value = {
                "items": [_sale_stub("s1")],
                "next_cursor": "more",
            }
            result = adapter.list_media_buys()

        assert result.total_fetched == 1
        assert result.truncated is True

    def test_passes_sale_ids_to_client(self):
        adapter = _make_adapter_with_cap()
        with patch.object(adapter._sales, "list_sales") as mock_list:
            mock_list.return_value = {"items": [], "next_cursor": None}
            adapter.list_media_buys(sale_ids=["s1", "s2"])

        assert mock_list.call_args.kwargs["sale_ids"] == ["s1", "s2"]

    def test_passes_buyer_refs_to_client(self):
        adapter = _make_adapter_with_cap()
        with patch.object(adapter._sales, "list_sales") as mock_list:
            mock_list.return_value = {"items": [], "next_cursor": None}
            adapter.list_media_buys(buyer_refs=["b1"])

        assert mock_list.call_args.kwargs["buyer_refs"] == ["b1"]

    def test_passes_statuses_to_client(self):
        adapter = _make_adapter_with_cap()
        with patch.object(adapter._sales, "list_sales") as mock_list:
            mock_list.return_value = {"items": [], "next_cursor": None}
            adapter.list_media_buys(statuses=["active", "paused"])

        assert mock_list.call_args.kwargs["statuses"] == ["active", "paused"]

    def test_page_size_respects_remaining_cap(self):
        """When cap-remaining < page_size, the adapter asks for fewer items."""
        adapter = _make_adapter_with_cap(cap=150)
        with patch.object(adapter._sales, "list_sales") as mock_list:
            # First call: return full page of 100, with next_cursor
            # Second call: should request only 50 more
            mock_list.side_effect = [
                {"items": [_sale_stub(f"s{i}") for i in range(100)], "next_cursor": "c1"},
                {"items": [_sale_stub(f"t{i}") for i in range(50)], "next_cursor": None},
            ]
            result = adapter.list_media_buys()

        assert result.total_fetched == 150
        assert result.truncated is False
        # Second call asked for 50 (remaining cap)
        assert mock_list.call_args_list[1].kwargs["limit"] == 50
```

- [ ] **Step 2: Run the tests, expect failures**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestListMediaBuys -v`
Expected: all FAIL with `AttributeError: 'CurationAdapter' object has no attribute 'list_media_buys'`.

---

## Task 2.10: Implement `CurationAdapter.list_media_buys()`

**Files:**
- Modify: `src/adapters/curation/adapter.py`

- [ ] **Step 1: Add the method to `CurationAdapter`**

Add to the `CurationAdapter` class (place before `_sale_to_media_buy`):

```python
    # ── List media buys (sales → AdCP with pagination + cap) ───────────

    def list_media_buys(
        self,
        *,
        sale_ids: list[str] | None = None,
        buyer_refs: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> ListMediaBuysResult:
        """Fetch sales from the Sales service and map to AdCP media buys.

        Paginates the sales service up to ``self._max_media_buys_per_list``.
        Signals truncation via the returned dataclass so callers can surface
        a soft ``errors[]`` entry to clients.

        Args:
            sale_ids: Filter to specific sale IDs. When set, the sales
                service uses batch_get and does not paginate (single call).
            buyer_refs: Filter to specific buyer references.
            statuses: Filter to specific curation sale statuses (not AdCP
                statuses — caller must translate via ADCP_STATUS_TO_SALE_STATUSES).

        Returns:
            ListMediaBuysResult with the mapped media buys and a truncation flag.
        """
        cap = self._max_media_buys_per_list
        page_size = min(100, cap)  # sales service hard max is 100
        cursor: str | None = None
        all_sales: list[dict] = []
        truncated = False

        while True:
            remaining = cap - len(all_sales)
            if remaining <= 0:
                # We're at or above cap. If there was a cursor from the last
                # iteration, there's more data we're skipping.
                truncated = cursor is not None
                break

            page = self._sales.list_sales(
                sale_ids=sale_ids,
                buyer_refs=buyer_refs,
                statuses=statuses,
                limit=min(page_size, remaining),
                cursor=cursor,
            )
            items = page.get("items") or []
            all_sales.extend(items)
            cursor = page.get("next_cursor")

            if not cursor:
                # Exhausted
                break
            if len(all_sales) >= cap:
                # Filled the cap and there's more → truncated
                truncated = True
                break

        media_buys = [self._sale_to_media_buy(s) for s in all_sales]
        return ListMediaBuysResult(
            media_buys=media_buys,
            truncated=truncated,
            total_fetched=len(media_buys),
        )
```

- [ ] **Step 2: Run the list_media_buys tests**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestListMediaBuys -v`
Expected: 10 PASS.

- [ ] **Step 3: Run the full curation adapter test file to catch any regression**

Run: `uv run pytest tests/unit/test_curation_adapter.py -v`
Expected: all tests pass (existing + new).

- [ ] **Step 4: Commit**

```bash
git add src/adapters/curation/adapter.py tests/unit/test_curation_adapter.py
git commit -m "feat(curation): add CurationAdapter.list_media_buys with cap + truncation

Paginates the curation sales service up to max_media_buys_per_list,
converts each sale to an AdCP GetMediaBuysMediaBuy, and signals
truncation via ListMediaBuysResult.truncated so callers can surface a
soft errors[] entry. Page size respects remaining-cap budget so the
last page never overruns. sale_ids path exits after one call (sales
service returns next_cursor=None for batch_get)."
```

---

## Task 2.11: Add `_get_media_buys_impl_curation` helper — failing tests

**Files:**
- Modify: `tests/unit/test_get_media_buys.py` (append to end of file)

- [ ] **Step 1: Read the existing test file to match its mocking style**

Run: `sed -n '1,40p' tests/unit/test_get_media_buys.py`
Expected: shows imports and a test class at the top.

- [ ] **Step 2: Append failing tests**

Append to `tests/unit/test_get_media_buys.py`:

```python
# ── Curation early-return branch tests ───────────────────────────────────


class TestGetMediaBuysCurationEarlyReturn:
    """When adapter_manages_own_persistence is True, _impl delegates to
    adapter.list_media_buys() instead of querying Postgres."""

    def _make_identity(self):
        from tests.factories import PrincipalFactory

        return PrincipalFactory.make_identity(tenant_id="t-curation", principal_id="p1")

    def _make_result(self, count: int = 0, truncated: bool = False):
        from src.adapters.curation.adapter import ListMediaBuysResult
        from src.core.schemas import GetMediaBuysMediaBuy

        mbs = [
            GetMediaBuysMediaBuy(
                media_buy_id=f"s{i}",
                buyer_ref="buyer-1",
                buyer_campaign_ref=None,
                status="active",
                currency="USD",
                total_budget=100.0,
                packages=[],
                created_at=None,
                updated_at=None,
            )
            for i in range(count)
        ]
        return ListMediaBuysResult(
            media_buys=mbs,
            truncated=truncated,
            total_fetched=count,
        )

    def test_curation_tenant_calls_adapter_list_media_buys(self):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        identity = self._make_identity()

        mock_adapter = MagicMock()
        mock_adapter.list_media_buys.return_value = self._make_result(count=2)
        mock_adapter._max_media_buys_per_list = 500

        with (
            patch(
                "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                return_value=True,
            ),
            patch(
                "src.core.tools.media_buy_list.get_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "src.core.tools.media_buy_list.get_principal_object",
                return_value=MagicMock(principal_id="p1"),
            ),
        ):
            response = _get_media_buys_impl(
                req=GetMediaBuysRequest(),
                identity=identity,
            )

        assert len(response.media_buys) == 2
        mock_adapter.list_media_buys.assert_called_once()

    def test_curation_tenant_translates_single_status_filter(self):
        from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        identity = self._make_identity()
        mock_adapter = MagicMock()
        mock_adapter.list_media_buys.return_value = self._make_result()
        mock_adapter._max_media_buys_per_list = 500

        with (
            patch(
                "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                return_value=True,
            ),
            patch(
                "src.core.tools.media_buy_list.get_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "src.core.tools.media_buy_list.get_principal_object",
                return_value=MagicMock(principal_id="p1"),
            ),
        ):
            _get_media_buys_impl(
                req=GetMediaBuysRequest(status_filter=MediaBuyStatus.active),
                identity=identity,
            )

        kwargs = mock_adapter.list_media_buys.call_args.kwargs
        assert kwargs["statuses"] == ["active"]

    def test_curation_tenant_default_status_is_active(self):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        identity = self._make_identity()
        mock_adapter = MagicMock()
        mock_adapter.list_media_buys.return_value = self._make_result()
        mock_adapter._max_media_buys_per_list = 500

        with (
            patch(
                "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                return_value=True,
            ),
            patch(
                "src.core.tools.media_buy_list.get_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "src.core.tools.media_buy_list.get_principal_object",
                return_value=MagicMock(principal_id="p1"),
            ),
        ):
            _get_media_buys_impl(req=GetMediaBuysRequest(), identity=identity)

        kwargs = mock_adapter.list_media_buys.call_args.kwargs
        assert kwargs["statuses"] == ["active"]

    def test_curation_tenant_translates_completed_to_multiple_sale_statuses(self):
        from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        identity = self._make_identity()
        mock_adapter = MagicMock()
        mock_adapter.list_media_buys.return_value = self._make_result()
        mock_adapter._max_media_buys_per_list = 500

        with (
            patch(
                "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                return_value=True,
            ),
            patch(
                "src.core.tools.media_buy_list.get_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "src.core.tools.media_buy_list.get_principal_object",
                return_value=MagicMock(principal_id="p1"),
            ),
        ):
            _get_media_buys_impl(
                req=GetMediaBuysRequest(status_filter=MediaBuyStatus.completed),
                identity=identity,
            )

        kwargs = mock_adapter.list_media_buys.call_args.kwargs
        # completed → ["completed", "canceled"]
        assert set(kwargs["statuses"]) == {"completed", "canceled"}

    def test_curation_tenant_passes_media_buy_ids_as_sale_ids(self):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        identity = self._make_identity()
        mock_adapter = MagicMock()
        mock_adapter.list_media_buys.return_value = self._make_result()
        mock_adapter._max_media_buys_per_list = 500

        with (
            patch(
                "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                return_value=True,
            ),
            patch(
                "src.core.tools.media_buy_list.get_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "src.core.tools.media_buy_list.get_principal_object",
                return_value=MagicMock(principal_id="p1"),
            ),
        ):
            _get_media_buys_impl(
                req=GetMediaBuysRequest(media_buy_ids=["s1", "s2"]),
                identity=identity,
            )

        kwargs = mock_adapter.list_media_buys.call_args.kwargs
        assert kwargs["sale_ids"] == ["s1", "s2"]

    def test_curation_tenant_passes_buyer_refs(self):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        identity = self._make_identity()
        mock_adapter = MagicMock()
        mock_adapter.list_media_buys.return_value = self._make_result()
        mock_adapter._max_media_buys_per_list = 500

        with (
            patch(
                "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                return_value=True,
            ),
            patch(
                "src.core.tools.media_buy_list.get_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "src.core.tools.media_buy_list.get_principal_object",
                return_value=MagicMock(principal_id="p1"),
            ),
        ):
            _get_media_buys_impl(
                req=GetMediaBuysRequest(buyer_refs=["b1", "b2"]),
                identity=identity,
            )

        kwargs = mock_adapter.list_media_buys.call_args.kwargs
        assert kwargs["buyer_refs"] == ["b1", "b2"]

    def test_curation_tenant_truncation_appends_errors_entry(self):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        identity = self._make_identity()
        mock_adapter = MagicMock()
        mock_adapter.list_media_buys.return_value = self._make_result(
            count=500, truncated=True
        )
        mock_adapter._max_media_buys_per_list = 500

        with (
            patch(
                "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                return_value=True,
            ),
            patch(
                "src.core.tools.media_buy_list.get_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "src.core.tools.media_buy_list.get_principal_object",
                return_value=MagicMock(principal_id="p1"),
            ),
        ):
            response = _get_media_buys_impl(
                req=GetMediaBuysRequest(),
                identity=identity,
            )

        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0]["code"] == "results_truncated"
        assert "500" in response.errors[0]["message"]

    def test_curation_tenant_include_snapshot_sets_unsupported(self):
        from src.core.schemas import (
            GetMediaBuysMediaBuy,
            GetMediaBuysPackage,
            GetMediaBuysRequest,
            SnapshotUnavailableReason,
        )
        from src.adapters.curation.adapter import ListMediaBuysResult
        from src.core.tools.media_buy_list import _get_media_buys_impl

        identity = self._make_identity()
        mock_adapter = MagicMock()
        mock_adapter.list_media_buys.return_value = ListMediaBuysResult(
            media_buys=[
                GetMediaBuysMediaBuy(
                    media_buy_id="s1",
                    buyer_ref="b",
                    buyer_campaign_ref=None,
                    status="active",
                    currency="USD",
                    total_budget=0.0,
                    packages=[
                        GetMediaBuysPackage(
                            package_id="seg1",
                            buyer_ref="b",
                            budget=None,
                            bid_price=None,
                            product_id="seg1",
                            start_time=None,
                            end_time=None,
                            paused=None,
                            creative_approvals=None,
                            snapshot=None,
                            snapshot_unavailable_reason=None,
                        )
                    ],
                    created_at=None,
                    updated_at=None,
                )
            ],
            truncated=False,
            total_fetched=1,
        )
        mock_adapter._max_media_buys_per_list = 500

        with (
            patch(
                "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                return_value=True,
            ),
            patch(
                "src.core.tools.media_buy_list.get_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "src.core.tools.media_buy_list.get_principal_object",
                return_value=MagicMock(principal_id="p1"),
            ),
        ):
            response = _get_media_buys_impl(
                req=GetMediaBuysRequest(),
                identity=identity,
                include_snapshot=True,
            )

        pkg = response.media_buys[0].packages[0]
        assert pkg.snapshot_unavailable_reason == SnapshotUnavailableReason.SNAPSHOT_UNSUPPORTED
```

- [ ] **Step 3: Add required imports at top of test file**

Ensure `tests/unit/test_get_media_buys.py` imports at the top include:

```python
from unittest.mock import MagicMock, patch
```

Skip if already imported.

- [ ] **Step 4: Run the tests, expect failures**

Run: `uv run pytest tests/unit/test_get_media_buys.py::TestGetMediaBuysCurationEarlyReturn -v`
Expected: all 8 FAIL — `adapter_manages_own_persistence` not imported in `media_buy_list.py`, or branch doesn't exist yet.

---

## Task 2.12: Implement early-return branch + `_get_media_buys_impl_curation`

**Files:**
- Modify: `src/core/tools/media_buy_list.py`

- [ ] **Step 1: Update imports at top of `media_buy_list.py`**

In `src/core/tools/media_buy_list.py`, update the import block to add `adapter_manages_own_persistence` and `ADCP_STATUS_TO_SALE_STATUSES`:

Replace the existing `from src.core.helpers.adapter_helpers import get_adapter` line with:

```python
from src.core.helpers.adapter_helpers import adapter_manages_own_persistence, get_adapter
```

Add this import near the other `src.adapters` imports (or create one if none exist — the file already has `from src.core.helpers.adapter_helpers import get_adapter` so adapter imports are permitted):

```python
from src.adapters.curation.adapter import ADCP_STATUS_TO_SALE_STATUSES
```

- [ ] **Step 2: Add the early-return block in `_get_media_buys_impl`**

In `src/core/tools/media_buy_list.py`, locate `_get_media_buys_impl` (around line 78). After the `tenant_id: str = tenant["tenant_id"]` line (around line 117), insert:

```python
    # ── Curation tenant early return ──────────────────────────────────
    # Adapters with manages_own_persistence=True bypass Postgres entirely.
    # We delegate the listing to adapter.list_media_buys() and wrap the
    # result in GetMediaBuysResponse. This path is used by CurationAdapter,
    # which stores media buys as sales in the external curation_sales service.
    if adapter_manages_own_persistence(tenant):
        testing_ctx_obj = identity.testing_context
        adapter = get_adapter(
            principal,
            dry_run=testing_ctx_obj.dry_run if testing_ctx_obj else False,
            testing_context=testing_ctx_obj,
        )
        return _get_media_buys_impl_curation(
            req=req,
            adapter=adapter,
            include_snapshot=include_snapshot,
        )
    # ── End curation early return ─────────────────────────────────────

```

- [ ] **Step 3: Add the `_get_media_buys_impl_curation` helper**

Append to `src/core/tools/media_buy_list.py` (after the existing helper functions at the bottom):

```python
def _get_media_buys_impl_curation(
    *,
    req: GetMediaBuysRequest,
    adapter: Any,
    include_snapshot: bool,
) -> GetMediaBuysResponse:
    """Curation-tenant path: delegate to adapter.list_media_buys().

    Translates AdCP filters to curation filters, calls the adapter, wraps
    the result in a GetMediaBuysResponse, and appends a soft truncation
    error entry if the adapter hit its safety cap.
    """
    # Translate AdCP status_filter → curation sale statuses (lossy inverse)
    adcp_statuses = _resolve_status_filter(req.status_filter)
    sale_statuses: list[str] = []
    for adcp_status in adcp_statuses:
        # adcp_status is a MediaBuyStatus enum; .value is the string name
        sale_statuses.extend(ADCP_STATUS_TO_SALE_STATUSES.get(adcp_status.value, []))

    result = adapter.list_media_buys(
        sale_ids=req.media_buy_ids,
        buyer_refs=req.buyer_refs,
        statuses=sale_statuses or None,
    )

    errors: list[Any] = []
    if result.truncated:
        cap = getattr(adapter, "_max_media_buys_per_list", 500)
        errors.append(
            {
                "code": "results_truncated",
                "message": (
                    f"Result set exceeded cap of {cap}; "
                    f"{result.total_fetched} media buys returned. "
                    f"Narrow filters to see more."
                ),
            }
        )
        logger.warning(
            "Curation get_media_buys truncated at cap=%d (total_fetched=%d)",
            cap,
            result.total_fetched,
        )

    # CurationAdapter does not support realtime reporting; when the caller
    # requested snapshots, mark every package as unsupported so clients see
    # the signal instead of silent None.
    if include_snapshot:
        for mb in result.media_buys:
            for pkg in mb.packages:
                pkg.snapshot_unavailable_reason = SnapshotUnavailableReason.SNAPSHOT_UNSUPPORTED

    return GetMediaBuysResponse(
        media_buys=result.media_buys,
        errors=errors or None,
        context=req.context,
    )
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/unit/test_get_media_buys.py::TestGetMediaBuysCurationEarlyReturn -v`
Expected: all 8 PASS.

- [ ] **Step 5: Run the full `test_get_media_buys.py` file to verify no Postgres-path regression**

Run: `uv run pytest tests/unit/test_get_media_buys.py -v`
Expected: all tests pass — existing Postgres tests unaffected by the new early return.

- [ ] **Step 6: Run the structural guards**

Run: `uv run pytest tests/unit/test_architecture_no_model_dump_in_impl.py tests/unit/test_architecture_repository_pattern.py -v`
Expected: PASS. If the `from src.adapters.curation.adapter import ADCP_STATUS_TO_SALE_STATUSES` import trips a guard, move it to a lazy in-function import inside `_get_media_buys_impl_curation` and re-run.

- [ ] **Step 7: Commit**

```bash
git add src/core/tools/media_buy_list.py tests/unit/test_get_media_buys.py
git commit -m "feat(media_buy_list): curation tenant early-return branch

Delegates get_media_buys to CurationAdapter.list_media_buys() when the
tenant's adapter has manages_own_persistence=True. Translates AdCP
status filters through ADCP_STATUS_TO_SALE_STATUSES, surfaces the
adapter's truncation flag as a soft errors[] entry, and marks all
package snapshots as SNAPSHOT_UNSUPPORTED when include_snapshot=True
(CurationAdapter does not support realtime reporting)."
```

---

## Task 2.13: Add admin UI form field

**Files:**
- Modify: `templates/adapters/curation/connection_config.html`

- [ ] **Step 1: Locate the pricing/limits section**

Run: `grep -n 'pricing_multiplier\|max_suggested_cpm' templates/adapters/curation/connection_config.html`
Expected: line numbers for existing numeric inputs.

- [ ] **Step 2: Add the new form field near existing pricing fields**

Insert this block adjacent to the other numeric inputs in `templates/adapters/curation/connection_config.html`:

```html
<div class="form-group">
  <label for="max_media_buys_per_list">Max media buys per list call</label>
  <input type="number"
         id="max_media_buys_per_list"
         name="max_media_buys_per_list"
         value="{{ config.get('max_media_buys_per_list', 500) }}"
         min="1"
         max="5000"
         step="1"
         class="form-control">
  <small class="form-text text-muted">
    Safety cap on <code>get_media_buys</code> responses for curation tenants.
    Results beyond this are truncated with a warning in the response
    <code>errors</code> field. Default 500.
  </small>
</div>
```

- [ ] **Step 3: Render the template manually to check for syntax errors**

Run: `uv run python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('templates'))
tpl = env.get_template('adapters/curation/connection_config.html')
print(tpl.render(config={'max_media_buys_per_list': 250, 'sales_service_url': 'http://x', 'catalog_service_url': 'http://y', 'activation_service_url': 'http://z'})[:500])
"`
Expected: renders HTML without error (template content printed, no stack trace).

- [ ] **Step 4: Commit**

```bash
git add templates/adapters/curation/connection_config.html
git commit -m "feat(admin): add max_media_buys_per_list field to Pubx Curation UI

Exposes the new CurationConnectionConfig field as a numeric input in
the Pubx Curation admin section. Existing config_json save handler
persists it automatically — no blueprint changes needed."
```

---

## Task 2.14: Integration test with mocked HTTP

**Files:**
- Create: `tests/integration/test_curation_get_media_buys.py`

This test uses the `responses` library to mock the curation sales service at the HTTP level, exercising the full stack from `_get_media_buys_impl` → `CurationAdapter` → `SalesClient` → HTTP.

- [ ] **Step 1: Verify `responses` is available**

Run: `uv run python -c "import responses; print(responses.__version__)"`
Expected: prints a version (library is already a dev dep). If ImportError, add it: `uv add --dev responses`.

- [ ] **Step 2: Create the integration test file**

Create `tests/integration/test_curation_get_media_buys.py` with:

```python
"""End-to-end integration tests for get_media_buys on curation tenants.

Mocks the curation sales service at the HTTP level via `responses` so the
test doesn't need a running curation_sales container. Exercises:
    _get_media_buys_impl → CurationAdapter → SalesClient → HTTP
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import responses


SALES_BASE = "http://sales.test"


def _sale_payload(sale_id: str, status: str = "active") -> dict:
    return {
        "sale_id": sale_id,
        "buyer_ref": "buyer-1",
        "buyer_campaign_ref": None,
        "segments": [{"segment_id": f"seg-{sale_id}"}],
        "activations": [],
        "pricing": {
            "pricing_model": "cpm",
            "currency": "USD",
            "floor_price": 2.5,
        },
        "deal_type": "curated",
        "platform_id": "magnite",
        "dsps": [],
        "ad_format_types": None,
        "start_time": "2026-04-01T00:00:00Z",
        "end_time": "2026-04-30T23:59:59Z",
        "brand": None,
        "budget": 1000.0,
        "status": status,
        "created_at": "2026-03-29T10:00:00Z",
        "updated_at": "2026-03-30T15:00:00Z",
    }


@pytest.mark.requires_db
class TestCurationGetMediaBuysEndToEnd:
    """Full-stack integration: _impl → adapter → client → (mocked) HTTP."""

    def _patch_tenant(self):
        """Patch resolved-identity to simulate a curation tenant."""
        from tests.factories import PrincipalFactory

        identity = PrincipalFactory.make_identity(
            tenant_id="t-curation",
            principal_id="p1",
        )
        # Tenant dict must include adapter_type so adapter_manages_own_persistence
        # picks the curation branch
        identity.tenant["adapter_type"] = "curation"
        return identity

    def _build_adapter(self, cap: int = 500):
        from src.adapters.curation.adapter import CurationAdapter
        from src.core.schemas import Principal

        p = Principal(principal_id="p1", name="p", platform_mappings={})
        return CurationAdapter(
            config={
                "sales_service_url": SALES_BASE,
                "catalog_service_url": "http://catalog.test",
                "activation_service_url": "http://activation.test",
                "max_media_buys_per_list": cap,
            },
            principal=p,
            tenant_id="t-curation",
        )

    @responses.activate
    def test_list_happy_path_single_page(self, integration_db):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        responses.add(
            responses.GET,
            f"{SALES_BASE}/api/v1/sales",
            json={
                "items": [_sale_payload("s1"), _sale_payload("s2")],
                "next_cursor": None,
            },
            status=200,
        )

        identity = self._patch_tenant()
        adapter = self._build_adapter()

        with (
            patch(
                "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                return_value=True,
            ),
            patch(
                "src.core.tools.media_buy_list.get_adapter",
                return_value=adapter,
            ),
            patch(
                "src.core.tools.media_buy_list.get_principal_object",
                return_value=type("P", (), {"principal_id": "p1"})(),
            ),
        ):
            response = _get_media_buys_impl(
                req=GetMediaBuysRequest(),
                identity=identity,
            )

        assert len(response.media_buys) == 2
        assert response.media_buys[0].media_buy_id == "s1"
        assert response.errors is None

    @responses.activate
    def test_list_pagination_follows_cursor(self, integration_db):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        # Page 1: cursor "c1"
        responses.add(
            responses.GET,
            f"{SALES_BASE}/api/v1/sales",
            json={
                "items": [_sale_payload("s1"), _sale_payload("s2")],
                "next_cursor": "c1",
            },
            status=200,
        )
        # Page 2: no more
        responses.add(
            responses.GET,
            f"{SALES_BASE}/api/v1/sales",
            json={
                "items": [_sale_payload("s3")],
                "next_cursor": None,
            },
            status=200,
        )

        identity = self._patch_tenant()
        adapter = self._build_adapter()

        with (
            patch(
                "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                return_value=True,
            ),
            patch(
                "src.core.tools.media_buy_list.get_adapter",
                return_value=adapter,
            ),
            patch(
                "src.core.tools.media_buy_list.get_principal_object",
                return_value=type("P", (), {"principal_id": "p1"})(),
            ),
        ):
            response = _get_media_buys_impl(
                req=GetMediaBuysRequest(),
                identity=identity,
            )

        assert len(response.media_buys) == 3
        assert response.errors is None

    @responses.activate
    def test_list_truncation_surfaces_errors_entry(self, integration_db):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        responses.add(
            responses.GET,
            f"{SALES_BASE}/api/v1/sales",
            json={
                "items": [_sale_payload(f"s{i}") for i in range(2)],
                "next_cursor": "more",  # claim there's more data
            },
            status=200,
        )

        identity = self._patch_tenant()
        adapter = self._build_adapter(cap=2)

        with (
            patch(
                "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                return_value=True,
            ),
            patch(
                "src.core.tools.media_buy_list.get_adapter",
                return_value=adapter,
            ),
            patch(
                "src.core.tools.media_buy_list.get_principal_object",
                return_value=type("P", (), {"principal_id": "p1"})(),
            ),
        ):
            response = _get_media_buys_impl(
                req=GetMediaBuysRequest(),
                identity=identity,
            )

        assert len(response.media_buys) == 2
        assert response.errors is not None
        assert response.errors[0]["code"] == "results_truncated"

    @responses.activate
    def test_list_sale_ids_filter_passes_through(self, integration_db):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        responses.add(
            responses.GET,
            f"{SALES_BASE}/api/v1/sales",
            json={
                "items": [_sale_payload("s1"), _sale_payload("s3")],
                "next_cursor": None,
            },
            status=200,
        )

        identity = self._patch_tenant()
        adapter = self._build_adapter()

        with (
            patch(
                "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                return_value=True,
            ),
            patch(
                "src.core.tools.media_buy_list.get_adapter",
                return_value=adapter,
            ),
            patch(
                "src.core.tools.media_buy_list.get_principal_object",
                return_value=type("P", (), {"principal_id": "p1"})(),
            ),
        ):
            response = _get_media_buys_impl(
                req=GetMediaBuysRequest(media_buy_ids=["s1", "s3"]),
                identity=identity,
            )

        assert len(response.media_buys) == 2
        # Verify the outgoing HTTP request included sale_ids
        assert len(responses.calls) == 1
        assert "sale_ids=s1" in responses.calls[0].request.url
        assert "sale_ids=s3" in responses.calls[0].request.url
```

- [ ] **Step 3: Run the integration tests**

Run: `scripts/run-test.sh tests/integration/test_curation_get_media_buys.py -x -v`
Expected: all 4 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_curation_get_media_buys.py
git commit -m "test(curation): add end-to-end integration tests for get_media_buys

Exercises the full stack _impl → CurationAdapter → SalesClient → HTTP,
mocking the curation sales service via the responses library. Covers
single-page happy path, cursor pagination, truncation at cap, and
sale_ids filter pass-through."
```

---

## Task 2.15: Run full quality gates + final regression check

**Files:** none (verification task)

- [ ] **Step 1: Run formatting + linting + mypy + unit tests**

Run: `make quality`
Expected: all pass. Fix any ruff/black/mypy issues before proceeding.

- [ ] **Step 2: Run the curation-adapter test file in full**

Run: `uv run pytest tests/unit/test_curation_adapter.py -v`
Expected: all tests pass (existing + newly added).

- [ ] **Step 3: Run the get_media_buys test file in full**

Run: `uv run pytest tests/unit/test_get_media_buys.py -v`
Expected: all tests pass (existing Postgres-path + new curation tests).

- [ ] **Step 4: Run the integration test**

Run: `scripts/run-test.sh tests/integration/test_curation_get_media_buys.py -x -v`
Expected: all 4 tests pass.

- [ ] **Step 5: Run the structural architecture guards**

Run: `uv run pytest tests/unit/test_architecture_no_model_dump_in_impl.py tests/unit/test_architecture_repository_pattern.py tests/unit/test_adcp_contract.py -v`
Expected: PASS. Critically check that the curation branch in `media_buy_list.py` does not trip the transport-agnostic `_impl` guard or the repository-pattern guard.

- [ ] **Step 6: Entity-scoped test run for `media_buy`**

Run: `make test-entity ENTITY=media_buy`
Expected: all media_buy-tagged tests pass (across unit + integration).

---

## Task 2.16: Manual staging smoke test

**Files:** none (manual verification)

**Prerequisite:** Phase 1 (`curation_sales`) PR must be merged and deployed to staging.

- [ ] **Step 1: Configure the staging tenant**

Navigate to the admin UI on staging (`https://salesagent.staging.pbxai.com/admin/`), open the default tenant's Pubx Curation section, verify the `Max media buys per list call` field is visible and defaulting to 500. Leave it at 500 unless there's a specific reason to change.

- [ ] **Step 2: Call get_media_buys via MCP CLI**

Run:

```bash
uvx adcp https://salesagent.staging.pbxai.com/mcp/ --auth <real-token> get_media_buys '{}'
```

Expected: returns a non-empty `media_buys` array (assuming there are sales in the curation_sales service for the tenant). No errors, no truncation warning unless the total exceeds 500.

- [ ] **Step 3: Call with status filter**

Run:

```bash
uvx adcp https://salesagent.staging.pbxai.com/mcp/ --auth <real-token> get_media_buys '{"status_filter": "active"}'
```

Expected: returns only active media buys.

- [ ] **Step 4: Call with media_buy_ids filter**

Take one sale_id from the previous response and run:

```bash
uvx adcp https://salesagent.staging.pbxai.com/mcp/ --auth <real-token> get_media_buys '{"media_buy_ids": ["<sale_id>"]}'
```

Expected: returns exactly that one media buy.

- [ ] **Step 5: Mark the beads task complete**

If all smoke tests pass:

```bash
bd update salesagent-ckh --notes="Implemented and smoke-tested on staging. Cross-repo PR curation-sales-001 deployed first. All filter combinations working end-to-end."
bd close salesagent-ckh
```

---

## Task 2.17: File follow-up beads tasks

**Files:** none (beads CLI)

- [ ] **Step 1: File `curation-sales-002` — `buyer_ref-index` GSI**

```bash
bd create \
  --title="Add buyer_ref-index GSI to curation_sales SaleModel" \
  --description="TODO-014 follow-up from salesagent-ckh. Currently, filtering sales by buyer_ref requires a full DynamoDB scan because there's no GSI on buyer_ref. This is acceptable at single-tenant staging scale but becomes a scaling cliff as sales volume grows. Add a GSI on buyer_ref to make filter queries O(1) instead of O(n)." \
  --type=task \
  --priority=3
```

- [ ] **Step 2: File `salesagent-???` — AdCP pagination for get_media_buys**

```bash
bd create \
  --title="Add pagination fields to GetMediaBuysRequest/Response when AdCP spec supports it" \
  --description="Follow-up from salesagent-ckh. The current implementation uses a safety cap (default 500) with soft truncation instead of pagination because AdCP's GetMediaBuysRequest/Response have no pagination fields today. When adcp 3.6+ ships with pagination, replace the cap approach with proper cursor-based pagination that flows through to the curation sales service's existing cursor." \
  --type=task \
  --priority=3
```

- [ ] **Step 3: Update `docs/curation-todos.md`**

Add these follow-up task IDs to the references in `docs/curation-todos.md` under TODO-001.

---

# Self-review

## Spec coverage check

Walking through each spec section:

- **§1 Goal + non-goals** — Covered. Plan scope matches spec.
- **§2 Fetch-all with safety cap** — Task 2.1 (config field), Task 2.10 (cap loop in `list_media_buys`), Task 2.12 (truncation error entry).
- **§3 Cross-repo sequencing** — Phase 1 / Phase 2 split; Phase 1 listed as prerequisite for the integration test and staging smoke test.
- **§4 curation_sales changes** — Tasks 1.1 through 1.8 cover route, params model, repo dispatch, all three filter types, combined filters, regression check, PR.
- **§5 salesagent changes**:
  - §5.1 `SalesClient.list_sales()` — Tasks 2.3, 2.4
  - §5.2 `CurationConnectionConfig.max_media_buys_per_list` — Task 2.1
  - §5.3 reverse map + `ListMediaBuysResult` — Tasks 2.5, 2.6
  - §5.4 `list_media_buys` method — Tasks 2.9, 2.10
  - §5.5 `_sale_to_media_buy` converter — Tasks 2.7, 2.8
  - §5.6 admin UI — Task 2.13
  - §5.7 `_impl` early return + helper — Tasks 2.11, 2.12
- **§6 Test plan** — Covered task-by-task. Unit tests for adapter: Tasks 2.3–2.10. Unit tests for `_impl`: Tasks 2.11–2.12. Integration test: Task 2.14. curation_sales tests: Tasks 1.3–1.7.
- **§7 Files touched** — All 12 files referenced in tasks.
- **§9 Risks** — Guard check in Task 2.12 Step 6 mitigates the structural-guard risk. The scan-based buyer_refs risk is deferred to a follow-up task (§11 / Task 2.17).
- **§10 Rollout** — Task 2.16 covers admin UI config and MCP CLI smoke tests.
- **§11 Follow-ups** — Task 2.17 files the two beads tasks.

## Placeholder scan

No "TBD", no "implement later", no "similar to Task N". Every code block is complete. Every command has expected output.

One knowingly deferred item: `salesagent-???` in Task 2.17 Step 2 — this is intentional because beads assigns its own ID. The task description makes it clear that's a beads-assigned placeholder.

## Type consistency check

- `ListMediaBuysResult` — same fields everywhere: `media_buys`, `truncated`, `total_fetched`
- `list_media_buys()` kwargs — same names everywhere: `sale_ids`, `buyer_refs`, `statuses`
- `ADCP_STATUS_TO_SALE_STATUSES` — same signature `dict[str, list[str]]` everywhere
- `_sale_to_media_buy` vs `_parse_iso` — consistent method / module-level split
- `SalesClient.list_sales()` kwargs match `CurationAdapter.list_media_buys()` kwargs plus `limit` and `cursor`
- `max_media_buys_per_list` — same attribute `self._max_media_buys_per_list` everywhere

All consistent.
