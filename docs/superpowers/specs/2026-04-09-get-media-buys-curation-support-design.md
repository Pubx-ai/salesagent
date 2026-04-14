# get_media_buys — Curation Tenant Support (Design)

**Date:** 2026-04-09
**Beads:** `salesagent-ckh` (TODO-001 from `docs/curation-todos.md`)
**Priority:** P1
**Status:** design approved, pending implementation
**Branch:** `feature/pubx-integration`

---

## 1. Goal

Make `get_media_buys` return real results for curation tenants instead of `[]`.
Curation media buys live exclusively in the external `curation_sales` service
(`manages_own_persistence = True`), so the current Postgres-only path yields
an empty list. This spec wires `get_media_buys` to `GET /api/v1/sales` via
the existing `CurationAdapter` using the established early-return pattern
(already used by `get_media_buy_delivery`).

### Non-goals

- Delivery metrics on the response — `CurationAdapter.supports_realtime_reporting = False` stays. Snapshots are always reported as `SNAPSHOT_UNSUPPORTED`.
- AdCP schema evolution — no new fields on `GetMediaBuysRequest`/`Response`. The spec currently has no pagination; this work uses a safety cap instead. File a follow-up when adcp 3.6+ lands.
- Audit logging on `list_media_buys` — covered by TODO-005 / `salesagent-8zz`.
- Multi-tenant isolation in the curation services — covered by TODO-009 / `salesagent-585`.
- Buyer-ref GSI on the sales service — covered by a new follow-up (see §11).
- Local sync of curation media buys to Postgres — covered by TODO-010 / `salesagent-7cl`.

---

## 2. Central design decision: fetch-all with safety cap

AdCP `GetMediaBuysRequest` has **no pagination fields** today. The curation
sales service has **only cursor pagination**. Three options were considered:

- **(A) Fetch-all with safety cap** — iterate sales service pages up to a cap; return a flat list; signal truncation via `errors[]`. *Chosen.*
- (B) Extend local `GetMediaBuysRequest`/`Response` schema with pagination now. Rejected — invents ahead of AdCP spec; asymmetric with the Postgres path.
- (C) Require `media_buy_ids` or `buyer_refs` filter for curation tenants. Rejected — breaks the common "show me all my media buys" use case.

Rationale for (A): matches the existing Postgres fetch-all semantics, zero
schema churn, protects the response size via cap, and degrades gracefully with
a soft `errors[]` signal (buyers still receive partial results). The cap is
admin-UI configurable per tenant (default 500).

---

## 3. Cross-repo scope + sequencing

Two coordinated changesets in two repos:

| Order | Repo | Change | Rationale |
|---|---|---|---|
| 1 | `curation_sales` | Extend `GET /api/v1/sales` with `sale_ids`, `buyer_refs`, `statuses` list filters | Must deploy first so salesagent integration tests pass |
| 2 | `salesagent` | `SalesClient.list_sales()`, `CurationAdapter.list_media_buys()`, `_get_media_buys_impl` early return, admin UI field | Unit tests (mock `SalesClient`) can land in parallel with step 1 |

Salesagent unit tests can be written and pass immediately — they mock the
sales client at the adapter boundary. Integration tests against a live
`curation_sales` container require step 1 merged + deployed.

---

## 4. `curation_sales` changes

### 4.1 Route — `src/routes/sales.py`

Extend `list_sales` to accept repeatable list query params. Keep singular
`status` for backward compatibility; `statuses` (plural) wins if both are
provided.

```python
@router.get("", response_model=SaleListResponse)
async def list_sales(
    status: str | None = Query(None, description="Filter by single status (legacy)"),
    statuses: list[str] | None = Query(None, description="Filter by multiple statuses"),
    sale_ids: list[str] | None = Query(None, description="Filter by sale IDs"),
    buyer_refs: list[str] | None = Query(None, description="Filter by buyer references"),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None),
) -> SaleListResponse:
    params = SaleListParams(
        status=status, statuses=statuses,
        sale_ids=sale_ids, buyer_refs=buyer_refs,
        limit=limit, cursor=cursor,
    )
    return get_sale_repository().list(params)
```

### 4.2 Params model — `src/models/sale.py`

```python
class SaleListParams(BaseModel):
    status: Optional[str] = None
    statuses: Optional[List[str]] = None
    sale_ids: Optional[List[str]] = None
    buyer_refs: Optional[List[str]] = None
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    cursor: Optional[str] = None
```

### 4.3 Repository dispatch — `src/repositories/sale_repo.py`

Three dispatch branches, ordered by specificity:

1. **`sale_ids` present** → `SaleModel.batch_get(sale_ids)` (primary-key lookup). Post-filter the result in Python for any remaining filters. `next_cursor=None` (sale_ids path doesn't paginate).
2. **Single `status`, no `statuses`, no `buyer_refs` (and no `sale_ids`)** → existing `SaleModel.status_index.query()` GSI path. Unchanged.
3. **Otherwise** → `SaleModel.scan()` + post-filter in Python for `statuses` and/or `buyer_refs`. Cursor still advances via DynamoDB `last_evaluated_key`.

```python
def list(self, params: SaleListParams) -> SaleListResponse:
    last_evaluated_key = self._decode_cursor(params.cursor)

    # 1. sale_ids path — batch_get, bounded, no pagination
    if params.sale_ids:
        items = list(SaleModel.batch_get(params.sale_ids))
        items = self._apply_post_filters(items, params)
        return SaleListResponse(
            items=[self._to_response(s) for s in items[: params.limit]],
            next_cursor=None,
        )

    # 2. Single-status GSI fast path
    if params.status and not params.statuses and not params.buyer_refs:
        query = SaleModel.status_index.query(
            params.status, limit=params.limit, last_evaluated_key=last_evaluated_key,
        )
        return self._build_page_response(query, params)

    # 3. Scan + Python filter (multi-status and/or buyer_refs)
    query = SaleModel.scan(limit=params.limit, last_evaluated_key=last_evaluated_key)
    items = self._apply_post_filters([s for s in query], params)
    return self._build_filtered_page_response(items, query.last_evaluated_key)

def _apply_post_filters(self, items, params):
    if params.buyer_refs:
        items = [s for s in items if s.buyer_ref in params.buyer_refs]
    if params.statuses:
        items = [s for s in items if s.status in params.statuses]
    return items
```

### 4.4 Known DynamoDB caveat — flagged, not a bug

With scan + post-filter, a single paginated page may return fewer than `limit`
items because filters are applied after DynamoDB's scan budget is spent. The
cursor still advances correctly; clients must keep following `next_cursor`
until it's null. This is documented DynamoDB behavior. Tests assert this
contract explicitly.

### 4.5 Tests — `tests/test_sales.py`

- `test_list_by_sale_ids` — create 3 sales, list 2 by ID, assert both returned, `next_cursor` is null
- `test_list_by_buyer_refs` — sales for 2 different buyers, filter to one
- `test_list_by_multiple_statuses` — sales in 3 statuses, filter to 2 of them
- `test_list_combined_filters` — `sale_ids + buyer_refs` → intersection
- `test_list_sale_ids_ignores_cursor` — `sale_ids` path returns `next_cursor=None`
- `test_scan_filter_may_return_fewer_than_limit` — asserts sparse-page behavior is documented

---

## 5. `salesagent` changes

### 5.1 `SalesClient.list_sales()` — new method

**File:** `src/adapters/curation/sales_client.py`

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

    Returns:
        {"items": list[dict], "next_cursor": str | None}
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

Single-page primitive. No pagination loop here — the adapter owns the loop so
it can enforce the cap. `httpx` serializes list-valued params as repeated
query keys automatically.

### 5.2 `CurationConnectionConfig.max_media_buys_per_list` — new field

**File:** `src/adapters/curation/config.py`

```python
max_media_buys_per_list: int = Field(
    default=500,
    ge=1,
    le=5000,
    description="Safety cap on number of sales fetched per get_media_buys call",
)
```

Persisted in `AdapterConfig.config_json`, set via Pubx Curation admin UI
(see §5.6), used by `CurationAdapter.__init__`:

```python
self._max_media_buys_per_list = conn.max_media_buys_per_list
```

### 5.3 Status reverse mapping + `ListMediaBuysResult` dataclass

**File:** `src/adapters/curation/adapter.py`

Add module-level dict alongside existing `SALE_STATUS_TO_ADCP`:

```python
# Inverse of SALE_STATUS_TO_ADCP. One AdCP status may map to multiple curation
# statuses (lossy forward mapping → multi-valued reverse).
ADCP_STATUS_TO_SALE_STATUSES: dict[str, list[str]] = {
    "pending_activation": ["pending_approval", "pending_activation"],
    "active": ["active"],
    "paused": ["paused"],
    "completed": ["completed", "canceled"],
    "failed": ["failed", "rejected"],
}
```

And a result dataclass:

```python
@dataclass
class ListMediaBuysResult:
    media_buys: list[GetMediaBuysMediaBuy]
    truncated: bool
    total_fetched: int
```

### 5.4 `CurationAdapter.list_media_buys()` — new method

**File:** `src/adapters/curation/adapter.py`

```python
def list_media_buys(
    self,
    *,
    sale_ids: list[str] | None = None,
    buyer_refs: list[str] | None = None,
    statuses: list[str] | None = None,  # curation-status strings, not AdCP
) -> ListMediaBuysResult:
    """Fetch sales from curation service and map to AdCP media buys.

    Paginates the sales service up to self._max_media_buys_per_list.
    Signals truncation via the returned dataclass.
    """
    cap = self._max_media_buys_per_list
    page_size = min(100, cap)  # sales service max limit is 100
    cursor: str | None = None
    all_sales: list[dict] = []
    truncated = False

    while True:
        remaining = cap - len(all_sales)
        if remaining <= 0:
            truncated = cursor is not None
            break
        page = self._sales.list_sales(
            sale_ids=sale_ids,
            buyer_refs=buyer_refs,
            statuses=statuses,
            limit=min(page_size, remaining),
            cursor=cursor,
        )
        items = page.get("items", [])
        all_sales.extend(items)
        cursor = page.get("next_cursor")
        if not cursor:
            break
        if len(all_sales) >= cap:
            truncated = True
            break

    media_buys = [self._sale_to_media_buy(s) for s in all_sales]
    return ListMediaBuysResult(
        media_buys=media_buys,
        truncated=truncated,
        total_fetched=len(media_buys),
    )
```

**`sale_ids` edge case:** The sales service's `sale_ids` path returns
`next_cursor=None` and ignores the cap loop naturally — the first iteration
gets everything.

### 5.5 Sale → `GetMediaBuysMediaBuy` converter

**File:** `src/adapters/curation/adapter.py` (or a new
`src/adapters/curation/media_buy_converter.py` if it grows beyond a helper)

```python
def _sale_to_media_buy(self, sale: dict) -> GetMediaBuysMediaBuy:
    """Convert a curation SaleResponse dict to an AdCP GetMediaBuysMediaBuy."""
    sale_id = sale["sale_id"]
    sale_pricing = sale.get("pricing") or {}
    currency = sale_pricing.get("currency", "USD")

    packages: list[GetMediaBuysPackage] = []
    for seg in sale.get("segments", []):
        segment_id = seg.get("segment_id")
        if not segment_id:
            continue
        # Per-segment pricing override if present (forward-compatible),
        # else fall back to sale-level.
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


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    # Handles both "2026-04-09T12:34:56Z" and "2026-04-09T12:34:56+00:00"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
```

**Mapping table** — full field-by-field:

| AdCP field | Source on sale | Notes |
|---|---|---|
| `media_buy_id` | `sale.sale_id` | |
| `buyer_ref` | `sale.buyer_ref` | |
| `buyer_campaign_ref` | `sale.buyer_campaign_ref` | may be null |
| `status` | `SALE_STATUS_TO_ADCP[sale.status]` | fallback `pending_activation` |
| `currency` | `sale.pricing.currency` | default `"USD"` |
| `total_budget` | `sale.budget` | default `0.0` |
| `created_at` | `sale.created_at` (ISO string) | parsed to `datetime` |
| `updated_at` | `sale.updated_at` (ISO string) | parsed to `datetime` |
| `packages[]` | one per `sale.segments[i]` | empty list if no segments |
| `packages[].package_id` | `segment.segment_id` | |
| `packages[].product_id` | `segment.segment_id` | mirrors `create_media_buy` inverse |
| `packages[].buyer_ref` | `sale.buyer_ref` | sale-level; no per-segment buyer_ref today |
| `packages[].budget` | `None` | no per-package budget concept in curation |
| `packages[].bid_price` | `segment.pricing.fixed_price or .floor_price` → fallback `sale.pricing.*` | forward-compatible with future per-segment pricing |
| `packages[].start_time` | `sale.start_time` | sale-level |
| `packages[].end_time` | `sale.end_time` | sale-level, may be null |
| `packages[].paused` | `None` | curation doesn't track per-segment pause |
| `packages[].creative_approvals` | `None` | curation doesn't manage creatives |
| `packages[].snapshot` | `None` | set by `_impl` if `include_snapshot=True` |

### 5.6 Admin UI — `templates/adapters/curation/connection_config.html`

Add one form input in the "Pricing & Limits" section:

```html
<div class="form-group">
  <label for="max_media_buys_per_list">Max media buys per list call</label>
  <input type="number" id="max_media_buys_per_list" name="max_media_buys_per_list"
         value="{{ config.get('max_media_buys_per_list', 500) }}"
         min="1" max="5000" step="1">
  <small class="form-text text-muted">
    Safety cap on get_media_buys responses. Results beyond this are truncated
    with a warning. Default 500.
  </small>
</div>
```

No changes in `src/admin/blueprints/adapters.py` — the existing save handler
persists the full form body into `config_json`, and the Pydantic
`CurationConnectionConfig` validates the new field on load.

### 5.7 `_get_media_buys_impl` early-return — `src/core/tools/media_buy_list.py`

Insert after the principal lookup (around line 117), before opening the
`MediaBuyUoW`:

```python
tenant = identity.tenant
today = datetime.now(UTC).date()
tenant_id: str = tenant["tenant_id"]

# --- CURATION EARLY RETURN -------------------------------------------
if adapter_manages_own_persistence(tenant):
    adapter = get_adapter(
        principal,
        dry_run=testing_ctx.dry_run if testing_ctx else False,
        testing_context=testing_ctx,
    )
    return _get_media_buys_impl_curation(
        req=req, adapter=adapter, include_snapshot=include_snapshot,
    )
# --- END CURATION EARLY RETURN ---------------------------------------

# Existing Postgres path unchanged
with MediaBuyUoW(tenant_id) as uow:
    ...
```

New helper in the same file:

```python
def _get_media_buys_impl_curation(
    *,
    req: GetMediaBuysRequest,
    adapter: Any,
    include_snapshot: bool,
) -> GetMediaBuysResponse:
    """Curation-tenant path: delegate to adapter.list_media_buys()."""
    # Translate AdCP filters → adapter filters
    adcp_statuses = _resolve_status_filter(req.status_filter)
    sale_statuses: list[str] = []
    for adcp_status in adcp_statuses:
        sale_statuses.extend(
            ADCP_STATUS_TO_SALE_STATUSES.get(adcp_status.value, [])
        )

    result = adapter.list_media_buys(
        sale_ids=req.media_buy_ids,
        buyer_refs=req.buyer_refs,
        statuses=sale_statuses or None,
    )

    errors: list[Any] = []
    if result.truncated:
        cap = getattr(adapter, "_max_media_buys_per_list", 500)
        errors.append({
            "code": "results_truncated",
            "message": (
                f"Result set exceeded cap of {cap}; "
                f"{result.total_fetched} media buys returned. "
                f"Narrow filters to see more."
            ),
        })
        logger.warning(
            "Curation get_media_buys truncated at cap=%d (principal=%s)",
            cap, (req.context.model_dump() if req.context else None),
        )

    # include_snapshot is not supported by CurationAdapter today
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

The `ADCP_STATUS_TO_SALE_STATUSES` import is placed at module top of
`media_buy_list.py` alongside the existing `get_adapter` import. The
structural guards permit `src/adapters/*` imports in `_impl` files (since
`get_adapter` is already imported at module level); only `fastmcp`, `a2a`,
`starlette`, and `fastapi` imports are forbidden in `_impl`. Verify during
implementation by running the guard test after making the change.

---

## 6. Test plan

### 6.1 Unit tests — `tests/unit/test_curation_adapter.py` (extend)

```
class TestListMediaBuys:
    test_maps_single_sale_with_two_segments
    test_empty_result
    test_pagination_loops_until_cursor_exhausted
    test_truncates_at_cap_and_sets_truncated_flag
    test_status_reverse_mapping_single
    test_status_reverse_mapping_multi
    test_sale_ids_passes_through_to_client
    test_buyer_refs_passes_through_to_client
    test_sale_with_zero_segments_returns_empty_packages
    test_sale_with_no_pricing_yields_none_bid_price
    test_per_segment_pricing_override  # forward-compat
    test_iso_date_parsing_handles_z_suffix
    test_cap_of_1_returns_one_item_and_signals_truncation
```

All tests mock `SalesClient.list_sales()` at the object boundary — no HTTP.

### 6.2 Unit tests — `tests/unit/test_get_media_buys.py` (extend)

```
class TestGetMediaBuysCurationEarlyReturn:
    test_curation_tenant_calls_adapter_list_media_buys
    test_curation_tenant_translates_adcp_status_filter
    test_curation_tenant_default_status_filter_is_active
    test_curation_tenant_multi_status_filter
    test_curation_tenant_truncated_appends_errors_entry
    test_curation_tenant_include_snapshot_sets_unsupported
    test_curation_tenant_passes_media_buy_ids_as_sale_ids
    test_curation_tenant_passes_buyer_refs
    test_postgres_tenant_unaffected  # regression guard
```

Patches `get_adapter()` and `adapter_manages_own_persistence()` to bypass
Postgres and exercise the early-return branch.

### 6.3 Integration test — new `tests/integration/test_curation_get_media_buys.py`

```
class TestCurationGetMediaBuysEndToEnd:
    test_list_happy_path
    test_list_with_filters
    test_list_pagination
    test_list_truncation
```

Uses `responses` library to mock the sales HTTP endpoint. Exercises the full
stack from `get_media_buys_raw` → `_impl` → `CurationAdapter` →
`SalesClient` → mocked HTTP. Does not require the real `curation_sales`
container in the salesagent test environment.

### 6.4 `curation_sales` tests — `tests/test_sales.py`

(See §4.5.)

---

## 7. Files touched

### `curation_sales` repo

| File | Change |
|---|---|
| `src/routes/sales.py` | Extend `list_sales` signature with 3 new list query params |
| `src/models/sale.py` | Add 3 fields to `SaleListParams` |
| `src/repositories/sale_repo.py` | Dispatch branches + post-filter helper |
| `tests/test_sales.py` | 6 new tests |

### `salesagent` repo

| File | Change |
|---|---|
| `src/adapters/curation/sales_client.py` | `list_sales()` method |
| `src/adapters/curation/config.py` | `max_media_buys_per_list` field |
| `src/adapters/curation/adapter.py` | `ADCP_STATUS_TO_SALE_STATUSES`, `ListMediaBuysResult`, `list_media_buys()`, `_sale_to_media_buy()`, `_parse_iso()`, `self._max_media_buys_per_list` in `__init__` |
| `src/core/tools/media_buy_list.py` | Early-return branch + `_get_media_buys_impl_curation()` helper |
| `templates/adapters/curation/connection_config.html` | New `max_media_buys_per_list` input |
| `tests/unit/test_curation_adapter.py` | `TestListMediaBuys` class (13 tests) |
| `tests/unit/test_get_media_buys.py` | `TestGetMediaBuysCurationEarlyReturn` class (9 tests) |
| `tests/integration/test_curation_get_media_buys.py` | New file (4 tests) |

---

## 8. Open questions

None. All design decisions are locked.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Scan-based `buyer_refs` filter is O(n) on total sales | Acceptable at current single-tenant low-volume scale. Follow-up task to add `buyer_ref-index` GSI. |
| Cap silently truncates large result sets | Soft `errors[]` entry with explicit message. Admin UI exposes the cap so operators can raise it per tenant. Log warning server-side. |
| Multi-status filter forces a scan path | Intentional trade-off vs. multi-GSI-query-merge, which has painful pagination semantics. |
| `sale_ids` + `buyer_refs` combined → batch_get + post-filter may miss the buyer_refs signal | Explicit intersection semantics documented; tests cover the combination. |
| `include_snapshot=True` on curation tenants silently returns nothing useful | `SNAPSHOT_UNSUPPORTED` reason is set explicitly; buyers see the signal. |
| Reverse status map is lossy (`active` → `active`, but `completed` → 2 curation statuses) | Documented in the dict; tests cover both forward and reverse paths. |
| Structural guard may reject `src/adapters/curation` import in `_impl` file | Lazy in-function import avoids module-load coupling; if top-level already allowed for `get_adapter`, prefer that. |
| Cross-repo sequencing: salesagent integration tests need `curation_sales` deployed | Unit tests mock `SalesClient` so salesagent changes are independently mergeable; integration tests run after `curation_sales` deploys to staging. |

---

## 10. Rollout

1. Merge `curation_sales` PR. Deploy to staging.
2. Merge salesagent PR. Unit tests run in CI immediately; integration test runs after staging deploy.
3. Configure `max_media_buys_per_list` per tenant via Admin UI → Pubx Curation (default 500, no action required unless adjustment needed).
4. Manual smoke test on staging: call `get_media_buys` via MCP CLI against the curation tenant, verify real results return.

No migration, no feature flag, no runtime toggle — the new code path activates
automatically for any tenant whose adapter has `manages_own_persistence = True`.

---

## 11. Follow-up beads tasks (to file after design approval)

| Proposed ID | Title | Priority |
|---|---|---|
| `curation-sales-001` | Extend `GET /api/v1/sales` with list filters (the `curation_sales` side of this work) | P1 |
| `curation-sales-002` | Add `buyer_ref-index` GSI to `SaleModel` for efficient filtering at scale | P3 |
| `salesagent-???` | Add pagination fields to `GetMediaBuysRequest`/`Response` once AdCP 3.6+ spec lands | P3 |

---

## 12. References

- Parent TODO: `docs/curation-todos.md` § TODO-001
- Integration summary: `docs/curation-integration-summary.md` § 10
- Reference early-return pattern: `src/core/tools/media_buy_delivery.py:164-208`
- Current `_get_media_buys_impl`: `src/core/tools/media_buy_list.py:78-216`
- AdCP status enum: `adcp.types.generated_poc.enums.media_buy_status.MediaBuyStatus`
- Sales service list endpoint: `curation_sales/src/routes/sales.py:73-81`
- Sales DynamoDB schema: `curation_sales/src/models/sale_dynamodb.py`
