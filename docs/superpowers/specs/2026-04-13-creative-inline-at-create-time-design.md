# Creative Inline at Create Time — Design Spec

## Goal

Get buyer creative details (HTML/VAST tags) into the sale record at `create_media_buy` time so the activation service can upload them to GAM. Eliminates the need for `sync_creatives` for curation tenants.

## Context

The buyer collects HTML/VAST creative snippets from the user before calling `create_media_buy`. Currently these are sent via a separate `sync_creatives` call, which we skip for curation tenants. The AdCP spec supports inline `creatives` in the package request.

## Changes

### 1. Buyer side (curation_buyer)

In `campaign-tools.ts`, include creatives in the `create_media_buy` package request:

```js
{
  product_id: "seg-xxx",
  budget: 20000,
  pricing_option_id: "cpm_usd_auction_xxx",
  creatives: [
    {
      creative_id: "cre-slug-1-1",
      name: "Creative 1",
      format_id: {agent_url: "https://creative.adcontextprotocol.org", id: "display_banner_300x250"},
      snippet: "<img src='...' />",
      snippet_type: "html",
    }
  ]
}
```

### 2. Salesagent side (adapter)

Fix `_build_creative_assignments` in `src/adapters/curation/adapter.py` to map AdCP `snippet` field to `tag` (which the activation service expects):

```python
# Map snippet → tag for activation service compatibility
tag = getattr(c, "tag", None) or getattr(c, "snippet", None)
```

No other salesagent changes needed — `_build_creative_assignments` already reads `orig_pkg.creatives` and maps `creative_id`, `format_id`, `name`, `status`.

### 3. No sync_creatives change

Keep the existing skip for curation tenants. If creatives are included at create time, sync_creatives is not needed.

## Files Changed

| File | Repo | Change |
|------|------|--------|
| `app/(chat)/api/curation/chat/campaign-tools.ts` | curation_buyer | Add `creatives` array to create_media_buy packages |
| `src/adapters/curation/adapter.py` | salesagent | Map `snippet` → `tag` in `_build_creative_assignments` |

## Out of Scope

- sync_creatives proper implementation for curation (kept as skip with TODO)
- Creative validation/approval workflow for curation
- Creative library storage in Postgres for curation tenants
