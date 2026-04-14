# Creative Inline at Create Time — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Include buyer creative tags (HTML/VAST) in the `create_media_buy` package request so they're recorded in the sale record for GAM activation — eliminating the need for a separate `sync_creatives` call.

**Architecture:** Two changes: (1) buyer includes `creatives` array in each package at create_media_buy time, (2) salesagent adapter maps AdCP `snippet` field to `tag` for activation service compatibility.

**Tech Stack:** TypeScript (curation_buyer), Python (salesagent), pytest

---

## File Structure

| File | Repo | Change |
|------|------|--------|
| `app/(chat)/api/curation/chat/campaign-tools.ts` | curation_buyer | Add `creatives` to create_media_buy packages |
| `src/adapters/curation/adapter.py` | salesagent | Map `snippet` → `tag` in `_build_creative_assignments` |
| `tests/unit/test_curation_adapter.py` | salesagent | Test creative snippet mapping |

---

### Task 1: Salesagent — map snippet → tag in _build_creative_assignments

The AdCP spec uses `snippet` for HTML/VAST content, but the activation service reads `tag`. Fix the mapping.

**Files:**
- Modify: `src/adapters/curation/adapter.py:821`
- Test: `tests/unit/test_curation_adapter.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_curation_adapter.py`:

```python
class TestBuildCreativeAssignments:
    """_build_creative_assignments maps AdCP creative objects to sale creative_assignments."""

    def test_snippet_mapped_to_tag(self):
        """AdCP creatives use 'snippet' field; activation service expects 'tag'."""
        from unittest.mock import MagicMock

        from src.adapters.curation.adapter import _build_creative_assignments
        from src.core.schemas import MediaPackage

        pkg = MagicMock(spec=MediaPackage)
        pkg.creative_ids = None

        orig_pkg = MagicMock()
        orig_pkg.creatives = [
            MagicMock(
                creative_id="cre-1",
                name="Test Creative",
                snippet='<img src="https://cdn.example.com/banner.jpg" />',
                tag=None,
                format_id={"id": "display_banner_300x250", "agent_url": "https://creative.adcontextprotocol.org"},
                status=None,
                assets=None,
            )
        ]

        result = _build_creative_assignments(pkg, orig_pkg)

        assert len(result) == 1
        assert result[0]["creative_id"] == "cre-1"
        assert result[0]["tag"] == '<img src="https://cdn.example.com/banner.jpg" />'
        assert result[0]["format_id"] == "display_banner_300x250"
        assert result[0]["name"] == "Test Creative"

    def test_tag_field_takes_precedence_over_snippet(self):
        """If both tag and snippet exist, tag wins (backward compat)."""
        from unittest.mock import MagicMock

        from src.adapters.curation.adapter import _build_creative_assignments
        from src.core.schemas import MediaPackage

        pkg = MagicMock(spec=MediaPackage)
        pkg.creative_ids = None

        orig_pkg = MagicMock()
        orig_pkg.creatives = [
            MagicMock(
                creative_id="cre-2",
                name="Tagged Creative",
                snippet="<p>snippet</p>",
                tag="<p>tag wins</p>",
                format_id={"id": "display_banner_728x90", "agent_url": "https://creative.adcontextprotocol.org"},
                status=None,
                assets=None,
            )
        ]

        result = _build_creative_assignments(pkg, orig_pkg)

        assert result[0]["tag"] == "<p>tag wins</p>"

    def test_no_creatives_returns_empty(self):
        """No creatives on either pkg or orig_pkg returns empty list."""
        from unittest.mock import MagicMock

        from src.adapters.curation.adapter import _build_creative_assignments
        from src.core.schemas import MediaPackage

        pkg = MagicMock(spec=MediaPackage)
        pkg.creative_ids = None

        orig_pkg = MagicMock()
        orig_pkg.creatives = None

        result = _build_creative_assignments(pkg, orig_pkg)

        assert result == []

    def test_snippet_type_passed_through(self):
        """snippet_type is included when present."""
        from unittest.mock import MagicMock

        from src.adapters.curation.adapter import _build_creative_assignments
        from src.core.schemas import MediaPackage

        pkg = MagicMock(spec=MediaPackage)
        pkg.creative_ids = None

        orig_pkg = MagicMock()
        orig_pkg.creatives = [
            MagicMock(
                creative_id="cre-3",
                name="HTML Creative",
                snippet="<div>ad</div>",
                snippet_type="html",
                tag=None,
                format_id={"id": "display_banner_300x250", "agent_url": "https://creative.adcontextprotocol.org"},
                status=None,
                assets=None,
            )
        ]

        result = _build_creative_assignments(pkg, orig_pkg)

        assert result[0]["tag"] == "<div>ad</div>"
        assert result[0]["snippet_type"] == "html"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestBuildCreativeAssignments -x -v`
Expected: FAIL — `snippet` not mapped to `tag`

- [ ] **Step 3: Implement the fix**

In `src/adapters/curation/adapter.py`, update the `_build_creative_assignments` function. Replace:

```python
                # tag may be in assets or as a direct field
                tag = getattr(c, "tag", None)
                if tag:
                    entry["tag"] = tag
```

With:

```python
                # Map tag or snippet → "tag" (activation service expects "tag",
                # AdCP spec uses "snippet" for HTML/VAST content)
                tag = getattr(c, "tag", None) or getattr(c, "snippet", None)
                if tag:
                    entry["tag"] = tag
                snippet_type = getattr(c, "snippet_type", None)
                if snippet_type:
                    entry["snippet_type"] = snippet_type
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestBuildCreativeAssignments -x -v`
Expected: PASS

- [ ] **Step 5: Run all adapter tests**

Run: `uv run pytest tests/unit/test_curation_adapter.py -x -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/adapters/curation/adapter.py tests/unit/test_curation_adapter.py
git commit -m "fix(curation): map AdCP snippet to tag in creative_assignments"
```

---

### Task 2: Buyer — include creatives in create_media_buy packages

Add `creatives` array to each package in the `create_media_buy` call. The buyer already builds creative objects for `sync_creatives` — we reuse the same construction but attach them to the package at creation time.

**Files:**
- Modify: `/Users/hrishikeshjangir/Dev/curation_buyer/app/(chat)/api/curation/chat/campaign-tools.ts:329-335`

- [ ] **Step 1: Build creatives array per package before create_media_buy**

In `campaign-tools.ts`, after the `packages.push(...)` loop (line 336) and before the `create_media_buy` call (line 358), add creative construction per package.

Replace the packages construction block (lines 329-335):

```typescript
    packages.push({
      product_id: li.segmentId,
      pricing_option_id: pricingOptionId,
      budget: li.budget,
      buyer_ref: `pkg-${li.segmentId.slice(-8)}-${i + 1}`,
      targeting_overlay: targetingOverlayFromLineItemPlan(li),
    });
```

With:

```typescript
    // Build inline creatives from creative tags (if any)
    const pkgCreatives: unknown[] = [];
    for (let j = 0; j < li.creativeTags.length; j++) {
      const tag = li.creativeTags[j];
      const segSlug =
        li.segmentId.slice(-10).replace(/^-+|-+$/g, "") || `s${i}`;
      const creativeId = `cre-${segSlug}-${i + 1}-${j + 1}`;
      pkgCreatives.push({
        creative_id: creativeId,
        name: `Creative ${j + 1} for ${li.segmentId}`,
        format_id: syncFormatIdForProduct(li.segmentId, product_format_ids),
        ...buildSnippetCreativePayloadForSync(tag),
      });
    }

    packages.push({
      product_id: li.segmentId,
      pricing_option_id: pricingOptionId,
      budget: li.budget,
      buyer_ref: `pkg-${li.segmentId.slice(-8)}-${i + 1}`,
      targeting_overlay: targetingOverlayFromLineItemPlan(li),
      ...(pkgCreatives.length > 0 ? { creatives: pkgCreatives } : {}),
    });
```

- [ ] **Step 2: Verify build passes**

Run: `cd /Users/hrishikeshjangir/Dev/curation_buyer && npm run build`
Expected: BUILD SUCCESS

- [ ] **Step 3: Commit**

```bash
git add app/(chat)/api/curation/chat/campaign-tools.ts
git commit -m "feat: include creatives in create_media_buy packages for curation"
```

---

### Task 3: Quality gates

- [ ] **Step 1: Run salesagent unit tests**

Run: `cd /Users/hrishikeshjangir/Dev/salesagent && uv run pytest tests/unit/test_curation_adapter.py -v`
Expected: ALL PASS

- [ ] **Step 2: Run salesagent mypy**

Run: `uv run mypy src/adapters/curation/adapter.py --config-file=mypy.ini`
Expected: 0 errors

- [ ] **Step 3: Run salesagent ruff**

Run: `uv run ruff check src/adapters/curation/adapter.py`
Expected: Clean

- [ ] **Step 4: Push both repos**

```bash
cd /Users/hrishikeshjangir/Dev/salesagent && git push
cd /Users/hrishikeshjangir/Dev/curation_buyer && git push
```
