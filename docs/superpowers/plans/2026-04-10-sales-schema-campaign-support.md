# Sales Schema Campaign Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the curation adapter to create campaign-type sales by default, with simplified activation and campaign-aware converters.

**Architecture:** Campaign-default with deal fallback. The adapter builds campaign payloads unless the buyer explicitly requests a deal (via `request.ext`). The activation client is simplified to send only `{sale_id}` — the activation service routes internally based on sale_type. The `_sale_to_media_buy` converter handles both campaign and deal segment shapes.

**Tech Stack:** Python 3.12, httpx, Pydantic v2, pytest, unittest.mock

---

## File Structure

| File | Role | Change |
|------|------|--------|
| `src/adapters/curation/activation_client.py` | HTTP client for activation service | Simplify `create_activation()` to accept `sale_id: str` |
| `src/adapters/curation/sales_client.py` | HTTP client for sales service | Add `sale_type` param to `list_sales()` |
| `src/adapters/curation/adapter.py` | Main adapter logic | Campaign payload, activation parsing, converter update |
| `tests/unit/test_curation_adapter.py` | Unit tests | New test classes for all changes |

---

### Task 1: ActivationClient — simplify to sale_id-only payload

The activation service now accepts just `{"sale_id": "..."}` and routes internally based on `sale_type`. The current client sends extra fields (ssp_name, start_date, end_date, price) that are no longer needed.

**Files:**
- Modify: `src/adapters/curation/activation_client.py`
- Test: `tests/unit/test_curation_adapter.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_curation_adapter.py`, after the existing `TestSalesClientListSales` class:

```python
class TestActivationClientSimplified:
    """ActivationClient.create_activation sends only sale_id."""

    def test_create_activation_sends_sale_id_only(self):
        from src.adapters.curation.activation_client import ActivationClient

        client = ActivationClient(base_url="http://test")
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = {"activations": [], "errors": None}
            client.create_activation("sale-123")

        mock_request.assert_called_once_with(
            "POST",
            "/activations",
            json={"sale_id": "sale-123"},
            accept_statuses=(201, 207),
        )

    def test_create_activation_returns_full_response(self):
        from src.adapters.curation.activation_client import ActivationClient

        client = ActivationClient(base_url="http://test")
        expected = {
            "activations": [{"activation_id": "act_abc", "ssp_name": "gam", "status": "active"}],
            "errors": None,
        }
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = expected
            result = client.create_activation("sale-123")

        assert result == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestActivationClientSimplified -x -v`
Expected: FAIL — `create_activation` currently takes a dict, not a string

- [ ] **Step 3: Implement the change**

Replace `create_activation` in `src/adapters/curation/activation_client.py`:

```python
class ActivationClient(CurationHttpClient):
    """Synchronous HTTP client for the Curation Activation service."""

    def create_activation(self, sale_id: str) -> dict[str, Any]:
        """Trigger activation for a sale.

        The activation service fetches the sale internally and routes
        based on sale_type (campaign → GAM, deal → Magnite).

        Returns:
            ActivationCreateResult dict with 'activations' and optional 'errors'.
        """
        return self._request(
            "POST", "/activations", json={"sale_id": sale_id}, accept_statuses=(201, 207)
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestActivationClientSimplified -x -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/adapters/curation/activation_client.py tests/unit/test_curation_adapter.py
git commit -m "refactor(curation): simplify ActivationClient to sale_id-only payload"
```

---

### Task 2: SalesClient — add sale_type filter to list_sales

The sales service supports `sale_type` as a query parameter. Adding it to `list_sales()` lets us filter campaigns vs deals.

**Files:**
- Modify: `src/adapters/curation/sales_client.py`
- Test: `tests/unit/test_curation_adapter.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_curation_adapter.py`, in the existing `TestSalesClientListSales` class:

```python
    def test_list_sales_passes_sale_type(self):
        from src.adapters.curation.sales_client import SalesClient

        client = SalesClient(base_url="http://test")
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = {"items": [], "next_cursor": None}
            client.list_sales(sale_type="campaign", limit=20)

        mock_request.assert_called_once_with(
            "GET",
            "/api/v1/sales",
            params={"limit": 20, "sale_type": "campaign"},
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestSalesClientListSales::test_list_sales_passes_sale_type -x -v`
Expected: FAIL — `list_sales()` doesn't accept `sale_type`

- [ ] **Step 3: Implement the change**

In `src/adapters/curation/sales_client.py`, add `sale_type` parameter to `list_sales()`:

```python
    def list_sales(
        self,
        *,
        status: str | None = None,
        statuses: list[str] | None = None,
        sale_ids: list[str] | None = None,
        buyer_refs: list[str] | None = None,
        sale_type: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
```

And add to the params dict, before the return:

```python
        if sale_type:
            params["sale_type"] = sale_type
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestSalesClientListSales -x -v`
Expected: ALL PASS (new test + existing tests unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/adapters/curation/sales_client.py tests/unit/test_curation_adapter.py
git commit -m "feat(curation): add sale_type filter to SalesClient.list_sales"
```

---

### Task 3: Campaign sale payload construction in create_media_buy

The core change: `create_media_buy` builds campaign payloads by default. Falls back to deal only when buyer explicitly provides DSPs or sets `sale_type: "deal"` in `request.ext`.

**Files:**
- Modify: `src/adapters/curation/adapter.py`
- Test: `tests/unit/test_curation_adapter.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_curation_adapter.py`:

```python
class TestCreateMediaBuyCampaignPayload:
    """create_media_buy builds campaign-type sale payloads by default."""

    def _make_request(self, ext=None):
        """Build a minimal CreateMediaBuyRequest for testing."""
        from src.core.schemas import CreateMediaBuyRequest

        return CreateMediaBuyRequest(
            brand={"domain": "acme.com"},
            buyer_ref="buyer-001",
            packages=[
                {
                    "product_id": "seg-abc",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "buyer_ref": "buyer-001",
                    "bid_price": 2.50,
                }
            ],
            start_time="2026-05-01T00:00:00Z",
            end_time="2026-05-31T23:59:59Z",
            ext=ext or {},
        )

    def _make_packages(self):
        from src.core.schemas import MediaPackage

        return [
            MediaPackage(
                package_id="pkg-seg-abc",
                name="Test Package",
                delivery_type="non_guaranteed",
                cpm=2.50,
                format_ids=[{"id": "display_banner_728x90"}],
                buyer_ref="buyer-001",
                product_id="seg-abc",
                budget=5000.0,
            )
        ]

    def test_default_sale_type_is_campaign(self):
        adapter = _make_adapter()
        request = self._make_request()
        packages = self._make_packages()

        with patch.object(adapter._sales, "create_sale") as mock_create, \
             patch.object(adapter, "_activate_sale", return_value="act-123"):
            mock_create.return_value = {"sale_id": "sale-1", "status": "pending_activation"}
            adapter.create_media_buy(
                request, packages,
                datetime(2026, 5, 1, tzinfo=UTC),
                datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
                {"pkg-seg-abc": {"currency": "USD", "bid_price": 2.50, "rate": 2.50}},
            )

        sale_data = mock_create.call_args[0][0]
        assert sale_data["sale_type"] == "campaign"

    def test_campaign_payload_has_campaign_meta(self):
        adapter = _make_adapter()
        request = self._make_request()
        packages = self._make_packages()

        with patch.object(adapter._sales, "create_sale") as mock_create, \
             patch.object(adapter, "_activate_sale", return_value="act-123"):
            mock_create.return_value = {"sale_id": "sale-1", "status": "pending_activation"}
            adapter.create_media_buy(
                request, packages,
                datetime(2026, 5, 1, tzinfo=UTC),
                datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
                {"pkg-seg-abc": {"currency": "USD", "bid_price": 2.50, "rate": 2.50}},
            )

        sale_data = mock_create.call_args[0][0]
        assert "campaign_meta" in sale_data
        assert sale_data["campaign_meta"]["order_name"] == "acme.com-buyer-001"
        assert sale_data["campaign_meta"]["media_buy_id"] == ""

    def test_campaign_segments_have_package_and_product_ids(self):
        adapter = _make_adapter()
        request = self._make_request()
        packages = self._make_packages()

        with patch.object(adapter._sales, "create_sale") as mock_create, \
             patch.object(adapter, "_activate_sale", return_value="act-123"):
            mock_create.return_value = {"sale_id": "sale-1", "status": "pending_activation"}
            adapter.create_media_buy(
                request, packages,
                datetime(2026, 5, 1, tzinfo=UTC),
                datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
                {"pkg-seg-abc": {"currency": "USD", "bid_price": 2.50, "rate": 2.50}},
            )

        sale_data = mock_create.call_args[0][0]
        seg = sale_data["segments"][0]
        assert seg["segment_id"] == "seg-abc"
        assert seg["package_id"] == "seg-abc"
        assert seg["product_id"] == "seg-abc"

    def test_campaign_segment_has_budget_and_pricing_info(self):
        adapter = _make_adapter()
        request = self._make_request()
        packages = self._make_packages()

        with patch.object(adapter._sales, "create_sale") as mock_create, \
             patch.object(adapter, "_activate_sale", return_value="act-123"):
            mock_create.return_value = {"sale_id": "sale-1", "status": "pending_activation"}
            adapter.create_media_buy(
                request, packages,
                datetime(2026, 5, 1, tzinfo=UTC),
                datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
                {"pkg-seg-abc": {"currency": "USD", "bid_price": 2.50, "rate": 2.50}},
            )

        seg = mock_create.call_args[0][0]["segments"][0]
        assert seg["budget"] == 5000.0
        assert seg["pricing_info"] == {"rate": 2.50, "currency": "USD"}

    def test_campaign_segment_has_ad_format_types(self):
        adapter = _make_adapter()
        request = self._make_request()
        packages = self._make_packages()

        with patch.object(adapter._sales, "create_sale") as mock_create, \
             patch.object(adapter, "_activate_sale", return_value="act-123"):
            mock_create.return_value = {"sale_id": "sale-1", "status": "pending_activation"}
            adapter.create_media_buy(
                request, packages,
                datetime(2026, 5, 1, tzinfo=UTC),
                datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
                {"pkg-seg-abc": {"currency": "USD", "bid_price": 2.50, "rate": 2.50}},
            )

        seg = mock_create.call_args[0][0]["segments"][0]
        assert seg["ad_format_types"] == ["display_banner_728x90"]

    def test_campaign_segment_publishers_empty_by_default(self):
        adapter = _make_adapter()
        request = self._make_request()
        packages = self._make_packages()

        with patch.object(adapter._sales, "create_sale") as mock_create, \
             patch.object(adapter, "_activate_sale", return_value="act-123"):
            mock_create.return_value = {"sale_id": "sale-1", "status": "pending_activation"}
            adapter.create_media_buy(
                request, packages,
                datetime(2026, 5, 1, tzinfo=UTC),
                datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
                {"pkg-seg-abc": {"currency": "USD", "bid_price": 2.50, "rate": 2.50}},
            )

        seg = mock_create.call_args[0][0]["segments"][0]
        assert seg["publishers"] == []

    def test_deal_fallback_when_dsps_in_ext(self):
        adapter = _make_adapter()
        ext = {"dsps": [{"seat_id": "seat-1", "dsp_name": "DV360"}]}
        request = self._make_request(ext=ext)
        packages = self._make_packages()

        with patch.object(adapter._sales, "create_sale") as mock_create, \
             patch.object(adapter, "_activate_sale", return_value="deal-id-1"):
            mock_create.return_value = {"sale_id": "sale-1", "status": "pending_activation"}
            adapter.create_media_buy(
                request, packages,
                datetime(2026, 5, 1, tzinfo=UTC),
                datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
                {"pkg-seg-abc": {"currency": "USD", "bid_price": 2.50, "rate": 2.50}},
            )

        sale_data = mock_create.call_args[0][0]
        assert sale_data.get("sale_type") is None or sale_data.get("deal_type") == "curated"
        assert "campaign_meta" not in sale_data

    def test_deal_fallback_when_sale_type_deal_in_ext(self):
        adapter = _make_adapter()
        ext = {"sale_type": "deal", "dsps": [{"seat_id": "s1"}]}
        request = self._make_request(ext=ext)
        packages = self._make_packages()

        with patch.object(adapter._sales, "create_sale") as mock_create, \
             patch.object(adapter, "_activate_sale", return_value="deal-id-1"):
            mock_create.return_value = {"sale_id": "sale-1", "status": "pending_activation"}
            adapter.create_media_buy(
                request, packages,
                datetime(2026, 5, 1, tzinfo=UTC),
                datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
                {"pkg-seg-abc": {"currency": "USD", "bid_price": 2.50, "rate": 2.50}},
            )

        sale_data = mock_create.call_args[0][0]
        assert "campaign_meta" not in sale_data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestCreateMediaBuyCampaignPayload -x -v`
Expected: FAIL — `create_media_buy` currently always builds deal payload, no `_activate_sale` method

- [ ] **Step 3: Implement campaign payload construction**

In `src/adapters/curation/adapter.py`, update `create_media_buy` to branch on sale type:

```python
    def create_media_buy(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> CreateMediaBuyResponse:
        """Create a sale + activation in curation services."""
        ext = getattr(request, "ext", None) or {}
        use_deal = (
            (isinstance(ext, dict) and ext.get("sale_type") == "deal")
            or bool(_extract_dsps_from_ext(request))
        )

        if use_deal:
            sale_data = self._build_deal_sale_data(request, packages, start_time, end_time, package_pricing_info)
        else:
            sale_data = self._build_campaign_sale_data(request, packages, start_time, end_time, package_pricing_info)

        sale_resp = self._sales.create_sale(sale_data)
        sale_id = sale_resp.get("sale_id")
        if not sale_id:
            raise AdCPAdapterError("Sales service did not return a sale_id")
        logger.info("Created sale %s (%s) in Sales service", sale_id, sale_data.get("sale_type", "deal"))

        activation_id = self._activate_sale(sale_id, sale_data)

        pkg_responses = [
            ResponsePackage(buyer_ref=p.buyer_ref or "unknown", package_id=p.package_id, paused=activation_id is None)
            for p in packages
        ]
        creative_deadline = datetime.now(UTC) + timedelta(days=2)

        return CreateMediaBuySuccess(
            buyer_ref=request.buyer_ref or "unknown",
            media_buy_id=sale_id,
            creative_deadline=creative_deadline,
            packages=pkg_responses,
        )

    def _build_campaign_sale_data(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> dict[str, Any]:
        """Build a campaign-type sale payload for the sales service."""
        brand = getattr(request, "brand", None)
        brand_domain = ""
        if brand:
            brand_domain = getattr(brand, "domain", "") or ""

        segments = []
        for pkg in packages:
            pricing_info_dict = (package_pricing_info or {}).get(pkg.package_id, {})
            rate = pricing_info_dict.get("rate") or pricing_info_dict.get("bid_price")
            currency = pricing_info_dict.get("currency", "USD")

            # Extract ad_format_types from package format_ids
            ad_format_types = []
            for fmt in getattr(pkg, "format_ids", None) or []:
                fmt_id = fmt.get("id") if isinstance(fmt, dict) else getattr(fmt, "id", None)
                if fmt_id:
                    ad_format_types.append(str(fmt_id))

            segments.append({
                "segment_id": pkg.product_id,
                "package_id": pkg.product_id,
                "product_id": pkg.product_id,
                "domains": [],
                "ad_format_types": ad_format_types,
                "budget": float(pkg.budget) if pkg.budget else None,
                "pricing_info": {"rate": float(rate), "currency": currency} if rate else None,
                "creative_assignments": [],
                "publishers": [],
            })

        sale_data: dict[str, Any] = {
            "sale_type": "campaign",
            "buyer_ref": request.buyer_ref or "unknown",
            "buyer_campaign_ref": request.buyer_ref or "",
            "campaign_meta": {
                "order_name": f"{brand_domain}-{request.buyer_ref or 'unknown'}",
                "media_buy_id": "",
            },
            "segments": segments,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        }

        if brand_domain:
            sale_data["brand"] = {"domain": brand_domain}

        budget = getattr(request, "budget", None)
        if budget is not None:
            sale_data["budget"] = float(budget)
        else:
            # Sum package budgets as total
            total = sum(float(pkg.budget) for pkg in packages if pkg.budget)
            if total > 0:
                sale_data["budget"] = total

        return sale_data

    def _build_deal_sale_data(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> dict[str, Any]:
        """Build a deal-type sale payload (existing logic, preserved for backward compat)."""
        segment_refs = [{"segment_id": pkg.product_id} for pkg in packages]
        pricing_info = _extract_pricing(package_pricing_info)
        dsps = _extract_dsps_from_ext(request) or [{"seat_id": "default", "dsp_name": "Default DSP"}]

        sale_data: dict[str, Any] = {
            "buyer_ref": request.buyer_ref or "unknown",
            "segments": segment_refs,
            "pricing": {
                "pricing_model": "cpm",
                "currency": pricing_info.get("currency", "USD"),
                "floor_price": pricing_info.get("floor_price"),
                "fixed_price": pricing_info.get("fixed_price"),
            },
            "deal_type": "curated",
            "platform_id": "magnite",
            "dsps": dsps,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        }
        if request.buyer_ref:
            sale_data["buyer_campaign_ref"] = request.buyer_ref

        budget = getattr(request, "budget", None)
        if budget is not None:
            sale_data["budget"] = float(budget)

        return sale_data
```

Also rename `_extract_dsps` to `_extract_dsps_from_ext` (module-level helper) to clarify it only reads from ext:

```python
def _extract_dsps_from_ext(request: CreateMediaBuyRequest) -> list[dict[str, Any]] | None:
    """Extract DSP configuration from request.ext, or None if absent."""
    ext = getattr(request, "ext", None) or {}
    dsps_from_ext = ext.get("dsps") if isinstance(ext, dict) else None

    if dsps_from_ext and isinstance(dsps_from_ext, list):
        return dsps_from_ext

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestCreateMediaBuyCampaignPayload -x -v`
Expected: PASS

- [ ] **Step 5: Run all existing tests to catch regressions**

Run: `uv run pytest tests/unit/test_curation_adapter.py -x -v`
Expected: ALL PASS (existing deal tests still work via deal fallback)

- [ ] **Step 6: Commit**

```bash
git add src/adapters/curation/adapter.py tests/unit/test_curation_adapter.py
git commit -m "feat(curation): campaign-type sale payload with deal fallback"
```

---

### Task 4: Activation flow — rename _activate_deal → _activate_sale

The activation method needs to:
1. Send simplified payload (just sale_id) via updated ActivationClient
2. Parse the response differently for campaign (GAM metadata) vs deal (deal_id)
3. Build the correct activation record shape for the sale update
4. Handle mock activation for both types

**Files:**
- Modify: `src/adapters/curation/adapter.py`
- Test: `tests/unit/test_curation_adapter.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_curation_adapter.py`:

```python
class TestActivateSale:
    """_activate_sale handles both campaign (GAM) and deal (Magnite) responses."""

    def test_sends_only_sale_id_to_activation_service(self):
        adapter = _make_adapter()
        sale_data = {"sale_type": "campaign"}

        with patch.object(adapter._activation, "create_activation") as mock_act, \
             patch.object(adapter._sales, "update_sale"):
            mock_act.return_value = {
                "activations": [{
                    "activation_id": "act_abc",
                    "ssp_name": "gam",
                    "status": "active",
                    "metadata": {
                        "activation_target": "GAM",
                        "gam_network_code": "117107141",
                        "gam_order_id": "4037456353",
                        "segments": [],
                    },
                }],
                "errors": None,
            }
            adapter._activate_sale("sale-1", sale_data)

        mock_act.assert_called_once_with("sale-1")

    def test_campaign_activation_updates_sale_with_gam_record(self):
        adapter = _make_adapter()
        sale_data = {"sale_type": "campaign"}

        with patch.object(adapter._activation, "create_activation") as mock_act, \
             patch.object(adapter._sales, "update_sale") as mock_update:
            mock_act.return_value = {
                "activations": [{
                    "activation_id": "act_abc",
                    "ssp_name": "gam",
                    "status": "active",
                    "metadata": {
                        "activation_target": "GAM",
                        "gam_network_code": "117107141",
                        "gam_order_id": "4037456353",
                        "segments": [{"package_id": "seg-1", "gam_line_item_id": "li-1", "gam_line_item_status": "ready"}],
                    },
                }],
                "errors": None,
            }
            result = adapter._activate_sale("sale-1", sale_data)

        assert result is not None
        update_data = mock_update.call_args[0][1]
        assert update_data["status"] == "active"
        activation = update_data["activations"][0]
        assert activation["activation_target"] == "GAM"
        assert activation["gam_network_code"] == "117107141"
        assert activation["gam_order_id"] == "4037456353"
        assert activation["segments"] == [{"package_id": "seg-1", "gam_line_item_id": "li-1", "gam_line_item_status": "ready"}]

    def test_deal_activation_updates_sale_with_deal_record(self):
        adapter = _make_adapter()
        sale_data = {"sale_type": "deal", "dsps": [{"dsp_name": "DV360"}]}

        with patch.object(adapter._activation, "create_activation") as mock_act, \
             patch.object(adapter._sales, "update_sale") as mock_update:
            mock_act.return_value = {
                "activations": [{
                    "activation_id": "act_xyz",
                    "ssp_name": "magnite",
                    "deal_id": "deal-magnite-123",
                    "status": "active",
                    "metadata": {},
                }],
                "errors": None,
            }
            result = adapter._activate_sale("sale-1", sale_data)

        assert result is not None
        update_data = mock_update.call_args[0][1]
        activation = update_data["activations"][0]
        assert activation["ssp_name"] == "magnite"
        assert activation["deal_id"] == "deal-magnite-123"

    def test_activation_failure_returns_none(self):
        adapter = _make_adapter()
        sale_data = {"sale_type": "campaign"}

        with patch.object(adapter._activation, "create_activation") as mock_act, \
             patch.object(adapter._sales, "update_sale"):
            mock_act.side_effect = Exception("Connection refused")
            result = adapter._activate_sale("sale-1", sale_data)

        assert result is None

    def test_empty_activations_returns_none(self):
        adapter = _make_adapter()
        sale_data = {"sale_type": "campaign"}

        with patch.object(adapter._activation, "create_activation") as mock_act, \
             patch.object(adapter._sales, "update_sale"):
            mock_act.return_value = {"activations": [], "errors": None}
            result = adapter._activate_sale("sale-1", sale_data)

        assert result is None

    def test_mock_activation_campaign(self):
        from src.adapters.curation.adapter import CurationAdapter
        from src.core.schemas import Principal

        p = Principal(principal_id="p1", name="p", platform_mappings={})
        adapter = CurationAdapter(
            config={
                "sales_service_url": "http://sales.test",
                "catalog_service_url": "http://catalog.test",
                "activation_service_url": "http://activation.test",
                "mock_activation": True,
            },
            principal=p,
            tenant_id="t1",
        )
        sale_data = {"sale_type": "campaign"}

        with patch.object(adapter._sales, "update_sale") as mock_update:
            result = adapter._activate_sale("sale-1", sale_data)

        assert result is not None
        assert result.startswith("mock-")
        update_data = mock_update.call_args[0][1]
        assert update_data["status"] == "active"
        activation = update_data["activations"][0]
        assert activation["activation_target"] == "GAM"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestActivateSale -x -v`
Expected: FAIL — `_activate_sale` method doesn't exist yet

- [ ] **Step 3: Implement _activate_sale**

Replace `_activate_deal` with `_activate_sale` in `src/adapters/curation/adapter.py`:

```python
    def _activate_sale(self, sale_id: str, sale_data: dict[str, Any]) -> str | None:
        """Activate a sale via the Activation service, or mock it.

        Returns an activation identifier on success (activation_id for campaigns,
        deal_id for deals), None on failure.
        Updates the sale status in the Sales service if activation succeeds.
        """
        is_campaign = sale_data.get("sale_type") == "campaign"
        activation_id: str | None = None

        if self._mock_activation:
            import uuid

            mock_id = f"mock-{uuid.uuid4().hex[:8]}"
            activation_id = mock_id
            logger.info("Mock activation for sale %s: id=%s", sale_id, mock_id)

            if is_campaign:
                activation_record = {
                    "activation_id": mock_id,
                    "activation_target": "GAM",
                    "gam_network_code": "mock-network",
                    "gam_order_id": f"mock-order-{uuid.uuid4().hex[:6]}",
                    "segments": [],
                    "status": "active",
                }
            else:
                dsps = sale_data.get("dsps") or []
                dsp_label = ", ".join(d.get("dsp_name", "") for d in dsps if d.get("dsp_name"))
                activation_record = {
                    "activation_id": mock_id,
                    "ssp_name": "magnite",
                    "dsp_name": dsp_label,
                    "deal_id": mock_id,
                    "status": "active",
                }
        else:
            try:
                act_result = self._activation.create_activation(sale_id)
                activations = act_result.get("activations") or []

                if not activations:
                    logger.warning("Activation returned no results for sale %s", sale_id)
                    return None

                act_resp = activations[0]
                activation_id = act_resp.get("activation_id")
                metadata = act_resp.get("metadata") or {}

                if is_campaign or act_resp.get("ssp_name") == "gam":
                    activation_record = {
                        "activation_id": activation_id,
                        "activation_target": metadata.get("activation_target", "GAM"),
                        "gam_network_code": metadata.get("gam_network_code", ""),
                        "gam_order_id": metadata.get("gam_order_id"),
                        "segments": metadata.get("segments", []),
                        "status": act_resp.get("status", "active"),
                    }
                else:
                    dsps = sale_data.get("dsps") or []
                    dsp_label = ", ".join(d.get("dsp_name", "") for d in dsps if d.get("dsp_name"))
                    activation_record = {
                        "activation_id": activation_id or f"act-{sale_id}",
                        "ssp_name": act_resp.get("ssp_name", "magnite"),
                        "dsp_name": dsp_label,
                        "deal_id": act_resp.get("deal_id"),
                        "status": act_resp.get("status", "active"),
                    }
                logger.info("Activation created for sale %s: %s", sale_id, activation_id)
            except Exception:
                logger.exception("Activation failed for sale %s", sale_id)
                return None

        # Update sale with activation record
        try:
            self._sales.update_sale(
                sale_id,
                {"status": "active", "activations": [activation_record]},
            )
        except Exception:
            logger.warning("Failed to update sale %s after activation", sale_id, exc_info=True)

        return activation_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestActivateSale -x -v`
Expected: PASS

- [ ] **Step 5: Run all tests to catch regressions**

Run: `uv run pytest tests/unit/test_curation_adapter.py -x -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/adapters/curation/adapter.py tests/unit/test_curation_adapter.py
git commit -m "feat(curation): replace _activate_deal with _activate_sale supporting campaign/deal"
```

---

### Task 5: Converter — _sale_to_media_buy handles campaign segments

The converter needs to detect `sale_type: "campaign"` and parse rich segment data (package_id, product_id, budget, pricing_info) instead of simple `{segment_id}` + root-level pricing.

**Files:**
- Modify: `src/adapters/curation/adapter.py`
- Test: `tests/unit/test_curation_adapter.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_curation_adapter.py`:

```python
SAMPLE_CAMPAIGN_SALE_DICT = {
    "sale_id": "sale-camp-001",
    "sale_type": "campaign",
    "buyer_ref": "buyer-1",
    "buyer_campaign_ref": "buyer-1",
    "campaign_meta": {
        "order_name": "acme.com-buyer-1",
        "media_buy_id": "",
    },
    "segments": [
        {
            "segment_id": "seg-aaa",
            "package_id": "seg-aaa",
            "product_id": "seg-aaa",
            "domains": ["example.com"],
            "ad_format_types": ["display_banner_728x90"],
            "budget": 5000.0,
            "pricing_info": {"rate": 2.50, "currency": "USD"},
            "creative_assignments": [],
            "publishers": [],
        },
        {
            "segment_id": "seg-bbb",
            "package_id": "seg-bbb",
            "product_id": "seg-bbb",
            "domains": [],
            "ad_format_types": ["video_640x480"],
            "budget": 3000.0,
            "pricing_info": {"rate": 4.00, "currency": "USD"},
            "creative_assignments": [],
            "publishers": [{"gam_network_code": "117107141"}],
        },
    ],
    "activations": [],
    "brand": {"domain": "acme.com"},
    "budget": 8000.0,
    "start_time": "2026-05-01T00:00:00Z",
    "end_time": "2026-05-31T23:59:59Z",
    "status": "active",
    "created_at": "2026-04-10T10:00:00Z",
    "updated_at": "2026-04-10T10:00:00Z",
}


class TestSaleToMediaBuyCampaign:
    """_sale_to_media_buy handles campaign-type sales with rich segments."""

    def test_campaign_produces_correct_package_count(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_CAMPAIGN_SALE_DICT)
        assert len(mb.packages) == 2

    def test_campaign_package_ids_from_segment(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_CAMPAIGN_SALE_DICT)
        assert [p.package_id for p in mb.packages] == ["seg-aaa", "seg-bbb"]

    def test_campaign_product_ids_from_segment(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_CAMPAIGN_SALE_DICT)
        assert [p.product_id for p in mb.packages] == ["seg-aaa", "seg-bbb"]

    def test_campaign_budget_from_segment(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_CAMPAIGN_SALE_DICT)
        assert mb.packages[0].budget == 5000.0
        assert mb.packages[1].budget == 3000.0

    def test_campaign_bid_price_from_pricing_info_rate(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_CAMPAIGN_SALE_DICT)
        assert mb.packages[0].bid_price == 2.50
        assert mb.packages[1].bid_price == 4.00

    def test_campaign_total_budget_from_sale(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_CAMPAIGN_SALE_DICT)
        assert mb.total_budget == 8000.0

    def test_campaign_currency_defaults_to_usd(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_CAMPAIGN_SALE_DICT)
        assert mb.currency == "USD"

    def test_campaign_missing_pricing_info_yields_none_bid(self):
        adapter = _make_adapter()
        sale = {
            **SAMPLE_CAMPAIGN_SALE_DICT,
            "segments": [{
                "segment_id": "seg-x",
                "package_id": "seg-x",
                "product_id": "seg-x",
            }],
        }
        mb = adapter._sale_to_media_buy(sale)
        assert mb.packages[0].bid_price is None

    def test_deal_sale_still_works_unchanged(self):
        """Existing deal-type sales are unaffected by campaign branch."""
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_SALE_DICT)
        assert mb.media_buy_id == "sale-abc-123"
        assert len(mb.packages) == 2
        assert mb.packages[0].bid_price == 2.50
        assert mb.packages[0].budget is None  # deal segments have no per-package budget

    def test_campaign_with_no_sale_type_treated_as_deal(self):
        """Missing sale_type defaults to deal parsing (backward compat)."""
        adapter = _make_adapter()
        sale = {**SAMPLE_SALE_DICT}
        sale.pop("sale_type", None)
        mb = adapter._sale_to_media_buy(sale)
        assert mb.packages[0].budget is None  # deal parsing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestSaleToMediaBuyCampaign -x -v`
Expected: FAIL — campaign segments parsed as deal (budget=None, wrong bid_price)

- [ ] **Step 3: Implement campaign branch in converter**

Update `_sale_to_media_buy` in `src/adapters/curation/adapter.py`:

```python
    def _sale_to_media_buy(self, sale: dict) -> GetMediaBuysMediaBuy:
        """Convert a curation SaleResponse dict to an AdCP GetMediaBuysMediaBuy.

        Detects sale_type to handle campaign vs deal segment shapes:
        - Campaign: segments have package_id, product_id, budget, pricing_info
        - Deal: segments have only segment_id; pricing at sale root level
        """
        sale_id = sale["sale_id"]
        is_campaign = sale.get("sale_type") == "campaign"

        if is_campaign:
            packages = self._convert_campaign_segments(sale)
            # Campaigns have no root-level pricing map — currency from first segment
            first_seg = (sale.get("segments") or [{}])[0] if sale.get("segments") else {}
            currency = (first_seg.get("pricing_info") or {}).get("currency", "USD")
        else:
            packages = self._convert_deal_segments(sale)
            sale_pricing = sale.get("pricing") or {}
            currency = sale_pricing.get("currency", "USD")

        adcp_status_str = SALE_STATUS_TO_ADCP.get(sale.get("status", ""), "pending_activation")

        return GetMediaBuysMediaBuy(
            media_buy_id=sale_id,
            buyer_ref=sale.get("buyer_ref"),
            buyer_campaign_ref=sale.get("buyer_campaign_ref"),
            status=MediaBuyStatus(adcp_status_str),
            currency=currency,
            total_budget=float(sale.get("budget") or 0.0),
            packages=packages,
            created_at=_parse_iso(sale.get("created_at")),
            updated_at=_parse_iso(sale.get("updated_at")),
        )

    def _convert_campaign_segments(self, sale: dict) -> list[GetMediaBuysPackage]:
        """Convert campaign segments (rich data) to GetMediaBuysPackage list."""
        packages: list[GetMediaBuysPackage] = []
        for seg in sale.get("segments") or []:
            segment_id = seg.get("segment_id") or seg.get("package_id")
            if not segment_id:
                continue

            pricing_info = seg.get("pricing_info") or {}
            bid_price = pricing_info.get("rate")

            packages.append(
                GetMediaBuysPackage(
                    package_id=seg.get("package_id") or segment_id,
                    buyer_ref=sale.get("buyer_ref"),
                    budget=float(seg["budget"]) if seg.get("budget") is not None else None,
                    bid_price=float(bid_price) if bid_price is not None else None,
                    product_id=seg.get("product_id") or segment_id,
                    start_time=sale.get("start_time"),
                    end_time=sale.get("end_time"),
                    paused=None,
                    creative_approvals=None,
                    snapshot=None,
                    snapshot_unavailable_reason=None,
                )
            )
        return packages

    def _convert_deal_segments(self, sale: dict) -> list[GetMediaBuysPackage]:
        """Convert deal segments (simple {segment_id} + root pricing) to GetMediaBuysPackage list."""
        sale_pricing = sale.get("pricing") or {}
        packages: list[GetMediaBuysPackage] = []
        for seg in sale.get("segments") or []:
            segment_id = seg.get("segment_id")
            if not segment_id:
                continue

            seg_pricing = seg.get("pricing") or sale_pricing
            bid_price = seg_pricing.get("fixed_price") or seg_pricing.get("floor_price")

            packages.append(
                GetMediaBuysPackage(
                    package_id=segment_id,
                    buyer_ref=sale.get("buyer_ref"),
                    budget=None,
                    bid_price=float(bid_price) if bid_price is not None else None,
                    product_id=segment_id,
                    start_time=sale.get("start_time"),
                    end_time=sale.get("end_time"),
                    paused=None,
                    creative_approvals=None,
                    snapshot=None,
                    snapshot_unavailable_reason=None,
                )
            )
        return packages
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestSaleToMediaBuyCampaign -x -v`
Expected: PASS

- [ ] **Step 5: Run ALL converter tests to verify backward compat**

Run: `uv run pytest tests/unit/test_curation_adapter.py::TestSaleToMediaBuy tests/unit/test_curation_adapter.py::TestSaleToMediaBuyCampaign -x -v`
Expected: ALL PASS (existing deal tests + new campaign tests)

- [ ] **Step 6: Commit**

```bash
git add src/adapters/curation/adapter.py tests/unit/test_curation_adapter.py
git commit -m "feat(curation): campaign-aware _sale_to_media_buy converter"
```

---

### Task 6: Quality gates and full test run

Run full quality gates to ensure nothing is broken across the codebase.

**Files:** None (verification only)

- [ ] **Step 1: Run unit tests for the entire curation adapter file**

Run: `uv run pytest tests/unit/test_curation_adapter.py -v`
Expected: ALL PASS

- [ ] **Step 2: Run get_media_buys tests**

Run: `uv run pytest tests/unit/test_get_media_buys.py -v`
Expected: ALL PASS (curation early-return tests should still work)

- [ ] **Step 3: Run integration tests for curation**

Run: `uv run pytest tests/integration/test_curation_get_media_buys.py -v`
Expected: ALL PASS

- [ ] **Step 4: Run mypy on changed files**

Run: `uv run mypy src/adapters/curation/adapter.py src/adapters/curation/activation_client.py src/adapters/curation/sales_client.py --config-file=mypy.ini`
Expected: 0 errors

- [ ] **Step 5: Run ruff lint and format check**

Run: `uv run ruff check src/adapters/curation/ && uv run ruff format --check src/adapters/curation/`
Expected: Clean

- [ ] **Step 6: Run full make quality**

Run: `make quality`
Expected: ALL PASS

- [ ] **Step 7: Commit any lint/format fixes if needed**

```bash
git add -u
git commit -m "style(curation): lint and format fixes"
```
