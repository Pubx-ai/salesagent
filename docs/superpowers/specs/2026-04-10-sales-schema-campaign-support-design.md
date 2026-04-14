# Sales Schema Campaign Support — Design Spec

## Goal

Update the salesagent curation adapter to use the new campaign-type sale schema when interacting with the curation sales service. Default `sale_type` is `"campaign"` unless the buyer explicitly requests `"deal"`.

## Context

The curation sales service now supports a discriminator pattern with `sale_type: "deal" | "campaign"`. Campaign sales carry richer segment data (package_id, product_id, domains, ad_format_types, budget, pricing_info, creative_assignments, publishers), a required `campaign_meta` block, and campaign-specific activation records (GAM details). The salesagent currently hardcodes all sales as deal-type.

The curation activation service has been updated to accept a simplified request (`{sale_id}`) and routes internally based on `sale_type` — GAM for campaigns, Magnite for deals. The response includes structured metadata with GAM order/line-item details for campaigns.

## Architecture

**Campaign-default with deal fallback.** The adapter builds campaign payloads by default. If the buyer provides deal-specific signals (`request.ext.get("sale_type") == "deal"` or `request.ext.get("dsps")` is non-empty), the existing deal path is used.

Key constraint: `segment_id == package_id` — these are always the same value.

## Changes by Component

### 1. Sale Creation (`create_media_buy`)

**Campaign payload construction:**
```python
{
    "sale_type": "campaign",
    "buyer_ref": request.buyer_ref,
    "buyer_campaign_ref": request.buyer_ref,
    "campaign_meta": {
        "order_name": f"{request.brand.domain}-{request.buyer_ref}",
        "media_buy_id": ""  # left empty; sale_id serves this purpose
    },
    "segments": [
        {
            "segment_id": pkg.product_id,
            "package_id": pkg.product_id,
            "product_id": pkg.product_id,
            "domains": <from catalog product publisher_properties>,
            "ad_format_types": <from catalog product format_ids → strings>,
            "budget": pkg.budget,
            "pricing_info": {"rate": pkg.bid_price, "currency": "USD"},
            "creative_assignments": <from pkg.creative_ids if available, else []>,
            "publishers": [],  # empty; activation service handles GAM resolution
            "frequency_cap": <from pkg.targeting_overlay if present>
        }
    ],
    "brand": {"domain": request.brand.domain},
    "budget": total_budget,
    "start_time": ISO string,
    "end_time": ISO string
}
```

**Deal fallback** (unchanged existing path): triggered when `request.ext.get("sale_type") == "deal"` or `request.ext.get("dsps")` is non-empty.

**Catalog enrichment**: Look up each package's product to extract `publisher_properties` → `domains` and `format_ids` → `ad_format_types`. The product data is available from the adapter's product catalog.

### 2. Activation Flow

**Rename** `_activate_deal()` → `_activate_sale()`.

**Simplified request:**
```python
POST /activations
{"sale_id": "..."}
```

**Response parsing** — branch on `ssp_name`:

Campaign (`ssp_name == "gam"`):
```python
{
    "activation_id": "act_xxx",
    "ssp_name": "gam",
    "metadata": {
        "activation_target": "GAM",
        "gam_network_code": "117107141",
        "gam_order_id": "4037456353",
        "segments": [
            {"package_id": "...", "gam_line_item_id": "...", "gam_line_item_status": "ready"}
        ],
        "creative_ids": [...]
    },
    "status": "active"
}
```

Deal (`ssp_name == "magnite"`) — existing shape with `deal_id`.

**Sale update after activation:**

Campaign activation record:
```python
{
    "status": "active",
    "activations": [{
        "activation_id": resp["activation_id"],
        "activation_target": resp["metadata"]["activation_target"],
        "gam_network_code": resp["metadata"]["gam_network_code"],
        "gam_order_id": resp["metadata"].get("gam_order_id"),
        "segments": resp["metadata"].get("segments", []),
        "status": resp["status"]
    }]
}
```

Deal activation record: unchanged from existing code.

**Mock activation**: When `mock_activation=True` and sale_type is campaign, return campaign-shaped mock data.

**Error handling**: Same pattern — if activation fails, log error, return packages as `paused=True`.

### 3. Converter (`_sale_to_media_buy`)

**Detection**: Check `sale.get("sale_type")` — `"campaign"` uses campaign parsing, `"deal"` or missing uses existing deal parsing.

**Campaign segment → GetMediaBuysPackage mapping:**
```
package_id    ← segment["package_id"]
product_id    ← segment["product_id"]
budget        ← segment["budget"]
bid_price     ← segment["pricing_info"]["rate"]
start_time    ← sale["start_time"]
end_time      ← sale["end_time"]
status        ← SALE_STATUS_TO_ADCP[sale["status"]]
```

Deal segment parsing: unchanged.

### 4. Status Mapping Updates

Add to `SALE_STATUS_TO_ADCP`:
```
"pending_approval" → "pending_activation"
```

Update `ADCP_STATUS_TO_SALE_STATUSES`:
```
"pending_activation" → ["pending_approval", "pending_activation"]
```

### 5. SalesClient

- `list_sales()`: Add optional `sale_type` parameter (passed as query param).
- `create_sale()`, `get_sale()`, `update_sale()`: No signature changes — payload structure handled at adapter level.

### 6. ActivationClient

- `create_activation()`: Simplify payload to `{"sale_id": sale_id}`. Remove `ssp_name`, `start_date`, `end_date`, `price` parameters.
- Keep accepting 201 and 207 status codes.
- Return the full response dict for adapter to parse metadata.

### 7. Other Tool Methods

- `check_media_buy_status()`: Minor — new `pending_approval` status handled by updated mapping.
- `update_media_buy()`: No changes — status and budget updates work identically for both types.
- `list_media_buys()`: Delegates to updated `_sale_to_media_buy()`. No additional changes.

## Files Changed

| File | Change |
|------|--------|
| `src/adapters/curation/adapter.py` | Campaign payload in `create_media_buy`, rename `_activate_deal` → `_activate_sale`, update activation parsing, update `_sale_to_media_buy` for campaign segments, update status maps, update mock activation |
| `src/adapters/curation/sales_client.py` | Add `sale_type` param to `list_sales()` |
| `src/adapters/curation/activation_client.py` | Simplify payload to `{sale_id}` |
| `tests/unit/test_curation_adapter.py` | Campaign fixtures, activation tests, converter tests |
| `tests/integration/test_curation_get_media_buys.py` | Campaign response fixtures |

## Out of Scope

- `gam_network_code` population in segments (handled by activation service)
- Creative upload/validation flow changes
- Admin UI template changes
- New config fields
- Catalog service changes
