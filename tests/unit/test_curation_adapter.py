"""Tests for the CurationAdapter and its supporting modules.

Tests cover:
- Segment-to-Product conversion
- HTTP client base class and construction
- Adapter registration and configuration
- CurationAdapter interface compliance
- CurationAdapter method behavior (with mocked HTTP)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

# ── Segment Converter Tests ────────────────────────────────────────────


SAMPLE_SEGMENT = {
    "name": "Premium Apple Prime",
    "segment_id": "seg-uuid-123",
    "description": "Apple device users in the US and UK during prime-time hours",
    "rule": {"cel_rule": "country IN ['US','GB'] && platform IN ['ios','macos'] && hour >= 18 && hour <= 23"},
    "rule_type": "CEL",
    "metadata": {
        "estimation": {
            "avg_daily_cpm": 0.59,
            "avg_daily_impressions": 41000000,
            "total_impressions_7d": 287000000,
            "unique_sites": 1315,
            "lookback_days": 7,
            "days_with_data": 14,
            "sampling_applied": True,
            "error": None,
            "estimated_at": "2026-02-19T00:00:00Z",
        },
        "signals_used": ["country", "platform", "hour"],
        "domains": ["pubx.ai RON"],
    },
    "version": 2,
    "status": "prod",
    "owner": "DS-Team",
    "created_at": "2026-02-20T13:17:56Z",
    "updated_at": "2026-04-06T11:03:34Z",
    "schema_hash": "abc123",
}


MINIMAL_SEGMENT = {
    "name": "minimal_seg",
    "description": "A minimal segment",
}

NON_VIABLE_SEGMENT = {
    "name": "broken_segment",
    "segment_id": "seg-broken",
    "description": "Segment with estimation error and zero impressions",
    "rule": {"cel_rule": "interests in ['sports']"},
    "rule_type": "CEL",
    "metadata": {
        "estimation": {
            "avg_daily_cpm": 0,
            "avg_daily_impressions": 0,
            "total_impressions_7d": 0,
            "error": "Signal 'interests' requires ConceptX data (not available in v1)",
        }
    },
    "version": 1,
    "status": "prod",
    "owner": "admin",
}


class TestSegmentToProduct:
    def test_converts_segment_to_valid_product(self):
        from src.adapters.curation.segment_converter import segment_to_product

        product = segment_to_product(SAMPLE_SEGMENT)

        assert product.product_id == "seg-uuid-123"
        assert product.name == "Premium Apple Prime"
        assert "Apple device users" in product.description
        assert str(product.delivery_type.value) == "non_guaranteed"
        assert len(product.pricing_options) == 1
        assert len(product.publisher_properties) == 1

    def test_pricing_uses_floor_from_config(self):
        from src.adapters.curation.segment_converter import segment_to_product

        product = segment_to_product(SAMPLE_SEGMENT, pricing_floor_cpm=0.25)

        po = product.pricing_options[0].root
        assert po.floor_price == 0.25

    def test_pricing_floor_is_capped_at_two_decimals(self):
        from src.adapters.curation.segment_converter import segment_to_product

        product = segment_to_product(SAMPLE_SEGMENT, pricing_floor_cpm=0.999)

        po = product.pricing_options[0].root
        assert po.floor_price == 0.99

    def test_price_guidance_from_estimation(self):
        from src.adapters.curation.segment_converter import segment_to_product

        product = segment_to_product(SAMPLE_SEGMENT, pricing_multiplier=5.0, pricing_max_suggested_cpm=10.0)

        po = product.pricing_options[0].root
        assert po.price_guidance is not None
        assert po.price_guidance.p50 == 2.95  # multiplied: 0.59 * 5.0 = 2.95
        assert po.price_guidance.recommended == 2.95

    def test_countries_extracted_from_cel(self):
        from src.adapters.curation.segment_converter import segment_to_product

        product = segment_to_product(SAMPLE_SEGMENT)

        assert sorted(product.countries) == ["GB", "US"]

    def test_device_types_extracted_from_cel(self):
        from src.adapters.curation.segment_converter import segment_to_product

        product = segment_to_product(SAMPLE_SEGMENT)

        assert product.device_types is not None
        assert "desktop" in product.device_types
        assert "mobile" in product.device_types

    def test_forecast_from_estimation(self):
        from src.adapters.curation.segment_converter import segment_to_product

        product = segment_to_product(SAMPLE_SEGMENT)

        assert product.forecast is not None
        assert len(product.forecast.points) == 1
        assert product.forecast.points[0].metrics.impressions.mid == 41000000.0

    def test_ext_contains_metadata(self):
        from src.adapters.curation.segment_converter import segment_to_product

        product = segment_to_product(SAMPLE_SEGMENT)

        assert product.ext is not None
        ext = product.ext if isinstance(product.ext, dict) else product.ext.model_dump()
        assert ext["signals_used"] == ["country", "platform", "hour"]
        assert ext["domains"] == ["pubx.ai RON"]
        assert ext["unique_sites"] == 1315

    def test_non_viable_segment_returns_none(self):
        from src.adapters.curation.segment_converter import segment_to_product

        result = segment_to_product(NON_VIABLE_SEGMENT)
        assert result is None

    def test_minimal_segment_uses_fallback_name(self):
        from src.adapters.curation.segment_converter import segment_to_product

        product = segment_to_product(MINIMAL_SEGMENT)

        assert product.product_id == "minimal_seg"
        assert product.name == "minimal_seg"

    def test_segments_to_products_filters_non_viable(self):
        from src.adapters.curation.segment_converter import segments_to_products

        segments = [SAMPLE_SEGMENT, NON_VIABLE_SEGMENT, MINIMAL_SEGMENT]
        products = segments_to_products(segments)

        assert len(products) == 2
        ids = {p.product_id for p in products}
        assert "seg-uuid-123" in ids
        assert "minimal_seg" in ids

    def test_segments_to_products_empty_list(self):
        from src.adapters.curation.segment_converter import segments_to_products

        products = segments_to_products([])
        assert products == []

    def test_product_has_required_adcp_fields(self):
        from src.adapters.curation.segment_converter import segment_to_product

        product = segment_to_product(SAMPLE_SEGMENT)
        dump = product.model_dump()

        assert "product_id" in dump
        assert "name" in dump
        assert "description" in dump
        assert "format_ids" in dump
        assert "delivery_type" in dump
        assert "pricing_options" in dump
        assert "publisher_properties" in dump

    def test_product_has_channels(self):
        from src.adapters.curation.segment_converter import segment_to_product

        product = segment_to_product(SAMPLE_SEGMENT)
        assert product.channels is not None
        assert len(product.channels) == 1
        assert str(product.channels[0].value) == "display"


# ── CEL Parser Tests ───────────────────────────────────────────────────


class TestCelParsers:
    def test_extract_countries_in_list(self):
        from src.adapters.curation.segment_converter import _extract_countries_from_cel

        assert _extract_countries_from_cel("country IN ['US','GB']") == ["US", "GB"]

    def test_extract_countries_equality(self):
        from src.adapters.curation.segment_converter import _extract_countries_from_cel

        assert _extract_countries_from_cel("country == 'SE'") == ["SE"]

    def test_extract_countries_none(self):
        from src.adapters.curation.segment_converter import _extract_countries_from_cel

        assert _extract_countries_from_cel("hour >= 18") == []

    def test_extract_devices_from_platform(self):
        from src.adapters.curation.segment_converter import _extract_device_types_from_cel

        result = _extract_device_types_from_cel("platform IN ['ios','macos']")
        assert "mobile" in result
        assert "desktop" in result

    def test_extract_devices_from_device_type(self):
        from src.adapters.curation.segment_converter import _extract_device_types_from_cel

        result = _extract_device_types_from_cel("device_type == 'mobile'")
        assert result == ["mobile"]

    def test_extract_devices_none(self):
        from src.adapters.curation.segment_converter import _extract_device_types_from_cel

        assert _extract_device_types_from_cel("country == 'US' && hour >= 9") == []


# ── Config Tests ───────────────────────────────────────────────────────


class TestCurationConfig:
    def test_default_config(self):
        from src.adapters.curation.config import CurationConnectionConfig

        config = CurationConnectionConfig()

        assert config.pricing_multiplier == 5.0
        assert config.pricing_floor_cpm == 0.1
        assert config.http_timeout_seconds == 30.0

    def test_config_from_env(self):
        from src.adapters.curation.config import CurationConnectionConfig

        with patch.dict("os.environ", {"CURATION_CATALOG_URL": "http://catalog:9000"}):
            config = CurationConnectionConfig()
            assert config.catalog_service_url == "http://catalog:9000"


# ── HTTP Client Tests ──────────────────────────────────────────────────


class TestCurationHttpClient:
    def test_base_client_strips_trailing_slash(self):
        from src.adapters.curation.http_client import CurationHttpClient

        client = CurationHttpClient("http://localhost:8000/")
        assert client._base_url == "http://localhost:8000"

    def test_base_client_raises_not_found_on_404(self):
        from src.adapters.curation.http_client import CurationHttpClient
        from src.core.exceptions import AdCPNotFoundError

        client = CurationHttpClient("http://localhost:8000")
        with patch.object(client, "_get_client") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_get.return_value.request.return_value = mock_response

            with pytest.raises(AdCPNotFoundError):
                client._request("GET", "/missing")

    def test_base_client_raises_adapter_error_on_500(self):
        import httpx

        from src.adapters.curation.http_client import CurationHttpClient
        from src.core.exceptions import AdCPAdapterError

        client = CurationHttpClient("http://localhost:8000")
        with patch.object(client, "_get_client") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=mock_response
            )
            mock_get.return_value.request.return_value = mock_response

            with pytest.raises(AdCPAdapterError, match="Curation service error"):
                client._request("GET", "/broken")


class TestCatalogClient:
    def test_inherits_base_client(self):
        from src.adapters.curation.catalog_client import CatalogClient
        from src.adapters.curation.http_client import CurationHttpClient

        client = CatalogClient("http://localhost:8000", timeout=10)
        assert isinstance(client, CurationHttpClient)
        assert client._base_url == "http://localhost:8000"
        assert client._timeout == 10


class TestSalesClient:
    def test_inherits_base_client(self):
        from src.adapters.curation.http_client import CurationHttpClient
        from src.adapters.curation.sales_client import SalesClient

        client = SalesClient("http://localhost:8001", timeout=15)
        assert isinstance(client, CurationHttpClient)


class TestActivationClient:
    def test_inherits_base_client(self):
        from src.adapters.curation.activation_client import ActivationClient
        from src.adapters.curation.http_client import CurationHttpClient

        client = ActivationClient("http://localhost:8002")
        assert isinstance(client, CurationHttpClient)


# ── Adapter Registration Tests ─────────────────────────────────────────


class TestAdapterRegistration:
    def test_curation_in_registry(self):
        from src.adapters import ADAPTER_REGISTRY

        assert "curation" in ADAPTER_REGISTRY

    def test_curation_adapter_class(self):
        from src.adapters import ADAPTER_REGISTRY
        from src.adapters.curation import CurationAdapter

        assert ADAPTER_REGISTRY["curation"] is CurationAdapter

    def test_curation_in_available_adapters(self):
        from src.core.main import AVAILABLE_ADAPTERS

        assert "curation" in AVAILABLE_ADAPTERS


# ── Adapter Interface Tests ────────────────────────────────────────────


def _make_adapter(**config_overrides):
    """Create a CurationAdapter with mocked principal for unit tests."""
    from src.adapters.curation.adapter import CurationAdapter

    principal = MagicMock()
    principal.principal_id = "test-principal"
    principal.get_adapter_id = MagicMock(return_value="curation-id")

    config = {
        "catalog_service_url": "http://catalog:8000",
        "sales_service_url": "http://sales:8001",
        "activation_service_url": "http://activation:8002",
        **config_overrides,
    }

    return CurationAdapter(config, principal, dry_run=False, tenant_id="test-tenant")


class TestCurationAdapterInterface:
    @pytest.fixture
    def adapter(self):
        return _make_adapter()

    def test_manages_own_persistence(self, adapter):
        assert adapter.manages_own_persistence is True

    def test_adapter_name(self, adapter):
        assert adapter.adapter_name == "curation"

    def test_supported_pricing_models(self, adapter):
        assert adapter.get_supported_pricing_models() == {"cpm"}

    def test_no_creative_methods(self, adapter):
        assert not hasattr(adapter, "add_creative_assets")
        assert not hasattr(adapter, "associate_creatives")

    def test_extends_tool_provider_not_ad_server_adapter(self, adapter):
        from src.adapters.base import AdServerAdapter, ToolProvider

        assert isinstance(adapter, ToolProvider)
        assert not isinstance(adapter, AdServerAdapter)

    def test_performance_index_noop(self, adapter):
        result = adapter.update_media_buy_performance_index("mb-1", [])
        assert result is True

    def test_default_channels(self, adapter):
        assert "display" in adapter.default_channels

    def test_capabilities(self, adapter):
        assert adapter.capabilities.supported_pricing_models == ["cpm"]
        assert adapter.capabilities.supports_inventory_sync is False

    def test_get_product_catalog_returns_products(self, adapter):
        adapter._catalog.fetch_all_segments = MagicMock(return_value=[SAMPLE_SEGMENT])

        products = adapter.get_product_catalog("test-tenant")

        assert products is not None
        assert len(products) == 1
        assert products[0].product_id == "seg-uuid-123"
        adapter._catalog.fetch_all_segments.assert_called_once_with()

    def test_get_product_catalog_empty(self, adapter):
        adapter._catalog.fetch_all_segments = MagicMock(return_value=[])

        products = adapter.get_product_catalog("test-tenant")

        assert products == []


class TestCurationAdapterCreateMediaBuy:
    def test_create_media_buy_success(self):
        adapter = _make_adapter()

        adapter._sales.create_sale = MagicMock(return_value={"sale_id": "sale-123"})
        adapter._activation.create_activation = MagicMock(
            return_value={"activations": [{"activation_id": "act-1", "deal_id": "deal-abc", "status": "active"}]}
        )
        adapter._sales.update_sale = MagicMock(return_value={})

        from src.core.schemas import CreateMediaBuyRequest, CreateMediaBuySuccess, MediaPackage

        packages = [
            MediaPackage(
                package_id="pkg-1",
                name="Test Package",
                delivery_type="non_guaranteed",
                cpm=1.0,
                impressions=1000,
                format_ids=[],
                product_id="seg-uuid-123",
            )
        ]

        request = MagicMock(spec=CreateMediaBuyRequest)
        request.buyer_ref = "test-buyer"
        request.ext = None
        request.budget = None
        request.packages = []

        result = adapter.create_media_buy(request, packages, datetime.now(UTC), datetime.now(UTC), None)

        assert isinstance(result, CreateMediaBuySuccess)
        assert result.media_buy_id == "sale-123"
        assert adapter._sales.create_sale.call_count == 1
        assert adapter._activation.create_activation.call_count == 1

    def test_create_media_buy_activation_failure_returns_paused(self):
        adapter = _make_adapter()

        adapter._sales.create_sale = MagicMock(return_value={"sale_id": "sale-456"})
        # CurationHttpClient._request translates httpx failures into
        # AdCPAdapterError before they reach the adapter, so simulate that
        # shape here instead of a bare Exception (which would indicate a bug).
        from src.core.exceptions import AdCPAdapterError

        adapter._activation.create_activation = MagicMock(side_effect=AdCPAdapterError("Activation down"))

        from src.core.schemas import CreateMediaBuyRequest, CreateMediaBuySuccess, MediaPackage

        packages = [
            MediaPackage(
                package_id="pkg-1",
                name="P",
                delivery_type="non_guaranteed",
                cpm=1.0,
                impressions=0,
                format_ids=[],
                product_id="seg-1",
            )
        ]

        request = MagicMock(spec=CreateMediaBuyRequest)
        request.buyer_ref = "buyer"
        request.ext = None
        request.budget = None
        request.packages = []

        result = adapter.create_media_buy(request, packages, datetime.now(UTC), datetime.now(UTC), None)

        assert isinstance(result, CreateMediaBuySuccess)
        assert result.media_buy_id == "sale-456"


class TestCurationAdapterCheckStatus:
    def test_check_status_maps_correctly(self):
        adapter = _make_adapter()
        adapter._sales.get_sale = MagicMock(return_value={"sale_id": "sale-1", "status": "active", "buyer_ref": "b1"})

        result = adapter.check_media_buy_status("sale-1", datetime.now(UTC))
        assert result.status == "active"
        assert result.buyer_ref == "b1"

    def test_check_status_maps_pending(self):
        adapter = _make_adapter()
        adapter._sales.get_sale = MagicMock(
            return_value={"sale_id": "sale-2", "status": "pending_approval", "buyer_ref": "b2"}
        )

        result = adapter.check_media_buy_status("sale-2", datetime.now(UTC))
        assert result.status == "pending_activation"


class TestCurationAdapterUpdateMediaBuy:
    def test_pause_media_buy_maps_to_paused_status(self):
        adapter = _make_adapter()
        adapter._sales.update_sale = MagicMock(return_value={})

        result = adapter.update_media_buy("sale-1", "buyer-1", "pause_media_buy", None, None, datetime.now(UTC))

        assert result.status == "paused"
        assert result.buyer_ref == "buyer-1"
        adapter._sales.update_sale.assert_called_once_with("sale-1", {"status": "paused"})

    def test_resume_media_buy_maps_to_active_status(self):
        adapter = _make_adapter()
        adapter._sales.update_sale = MagicMock(return_value={})

        result = adapter.update_media_buy("sale-1", "buyer-1", "resume_media_buy", None, None, datetime.now(UTC))

        assert result.status == "active"
        adapter._sales.update_sale.assert_called_once_with("sale-1", {"status": "active"})

    def test_buyer_ref_is_forwarded(self):
        adapter = _make_adapter()
        adapter._sales.update_sale = MagicMock(return_value={})

        result = adapter.update_media_buy("sale-1", "my-buyer-ref", "pause_media_buy", None, None, datetime.now(UTC))
        assert result.buyer_ref == "my-buyer-ref"

    def test_budget_only_update_reads_current_sale_status(self):
        """action='update' with budget must not lie — response should mirror
        the sale's actual status, not default to 'active'."""
        adapter = _make_adapter()
        adapter._sales.update_sale = MagicMock(return_value={})
        adapter._sales.get_sale = MagicMock(return_value={"status": "paused"})

        result = adapter.update_media_buy("sale-1", "buyer-1", "update", None, 5000, datetime.now(UTC))

        assert result.status == "paused"
        adapter._sales.update_sale.assert_called_once_with("sale-1", {"budget": 5000.0})
        adapter._sales.get_sale.assert_called_once_with("sale-1")

    def test_budget_only_update_tolerates_get_sale_failure(self):
        """If the post-update GET fails, fall back to pending_activation rather
        than crashing — the write already succeeded."""
        from src.core.exceptions import AdCPAdapterError

        adapter = _make_adapter()
        adapter._sales.update_sale = MagicMock(return_value={})
        adapter._sales.get_sale = MagicMock(side_effect=AdCPAdapterError("service down"))

        result = adapter.update_media_buy("sale-1", "buyer-1", "update", None, 5000, datetime.now(UTC))

        assert result.status == "pending_activation"


# ── Base Adapter Hooks Tests ───────────────────────────────────────────


class TestAdapterBaseHooks:
    def test_manages_own_persistence_default_false(self):
        from src.adapters.mock_ad_server import MockAdServer

        principal = MagicMock()
        principal.principal_id = "test"
        principal.get_adapter_id = MagicMock(return_value=None)

        adapter = MockAdServer({}, principal, tenant_id="test")
        assert adapter.manages_own_persistence is False

    def test_get_product_catalog_default_none(self):
        from src.adapters.mock_ad_server import MockAdServer

        principal = MagicMock()
        principal.principal_id = "test"
        principal.get_adapter_id = MagicMock(return_value=None)

        adapter = MockAdServer({}, principal, tenant_id="test")
        assert adapter.get_product_catalog("test") is None


# ── Shared Helper Tests ────────────────────────────────────────────────


class TestAdapterManagesOwnPersistence:
    def test_curation_tenant_returns_true(self):
        from src.core.helpers.adapter_helpers import adapter_manages_own_persistence

        tenant = {"tenant_id": "t1", "ad_server": {"adapter": "curation"}}
        assert adapter_manages_own_persistence(tenant) is True

    def test_mock_tenant_returns_false(self):
        from src.core.helpers.adapter_helpers import adapter_manages_own_persistence

        tenant = {"tenant_id": "t1", "ad_server": {"adapter": "mock"}}
        assert adapter_manages_own_persistence(tenant) is False

    def test_missing_ad_server_returns_false(self):
        from src.core.helpers.adapter_helpers import adapter_manages_own_persistence

        tenant = {"tenant_id": "t1"}
        assert adapter_manages_own_persistence(tenant) is False

    def test_string_ad_server_config(self):
        from src.core.helpers.adapter_helpers import adapter_manages_own_persistence

        tenant = {"tenant_id": "t1", "ad_server": "curation"}
        assert adapter_manages_own_persistence(tenant) is True


# ── Helper Functions Tests ─────────────────────────────────────────────


class TestHelperFunctions:
    def test_extract_pricing_defaults(self):
        from src.adapters.curation.adapter import _extract_pricing

        result = _extract_pricing(None)
        assert result["currency"] == "USD"
        # No package pricing → no implicit floor; downstream Sales service
        # treats this as "no floor", which is the explicit signal we want.
        assert result["floor_price"] is None
        assert result["fixed_price"] is None

    def test_extract_pricing_from_info(self):
        from src.adapters.curation.adapter import _extract_pricing

        info = {"pkg_1": {"currency": "EUR", "rate": 2.5, "is_fixed": False, "bid_price": 2.5}}
        result = _extract_pricing(info)
        assert result["currency"] == "EUR"
        assert result["floor_price"] == 2.5

    def test_extract_dsps_from_ext_returns_none_when_absent(self):
        from src.adapters.curation.adapter import _extract_dsps_from_ext

        req = MagicMock()
        req.ext = None
        result = _extract_dsps_from_ext(req)
        assert result is None

    def test_extract_dsps_from_ext_returns_list_when_present(self):
        from src.adapters.curation.adapter import _extract_dsps_from_ext

        req = MagicMock()
        req.ext = {"dsps": [{"seat_id": "SA-123", "dsp_name": "StackAdapt"}]}
        result = _extract_dsps_from_ext(req)
        assert result is not None
        assert len(result) == 1
        assert result[0]["seat_id"] == "SA-123"


# ── Status Mapping Tests ──────────────────────────────────────────────


class TestStatusMapping:
    def test_sale_status_to_adcp(self):
        from src.adapters.curation.status_mapping import SALE_STATUS_TO_ADCP

        assert SALE_STATUS_TO_ADCP["active"] == "active"
        assert SALE_STATUS_TO_ADCP["pending_activation"] == "pending_activation"
        assert SALE_STATUS_TO_ADCP["paused"] == "paused"
        assert SALE_STATUS_TO_ADCP["completed"] == "completed"
        assert SALE_STATUS_TO_ADCP["failed"] == "failed"

    def test_sale_status_covers_all_states(self):
        from src.adapters.curation.status_mapping import SALE_STATUS_TO_ADCP

        assert "canceled" in SALE_STATUS_TO_ADCP
        assert "rejected" in SALE_STATUS_TO_ADCP
        assert SALE_STATUS_TO_ADCP["canceled"] == "completed"
        assert SALE_STATUS_TO_ADCP["rejected"] == "failed"

    def test_action_to_adcp_status(self):
        from src.adapters.curation.status_mapping import ACTION_TO_ADCP_STATUS

        # Keys MUST match the action strings emitted by media_buy_update
        # (same vocabulary as GAM / Kevel / Broadstreet adapters). Earlier
        # revisions used short names like "pause" / "resume" / "cancel" that
        # were never actually passed by the tool layer, so the mapping was
        # effectively dead.
        assert ACTION_TO_ADCP_STATUS["pause_media_buy"] == "paused"
        assert ACTION_TO_ADCP_STATUS["resume_media_buy"] == "active"
        assert ACTION_TO_ADCP_STATUS["pause_package"] == "paused"
        assert ACTION_TO_ADCP_STATUS["resume_package"] == "active"

    def test_action_to_sale_status_excludes_budget_only(self):
        from src.adapters.curation.status_mapping import ACTION_TO_SALE_STATUS

        # Only campaign-level pause/resume trigger a sale-status PATCH.
        # "update" (budget-only) and package-scoped actions must not show up
        # here — they should leave the sale's status untouched.
        assert "pause_media_buy" in ACTION_TO_SALE_STATUS
        assert "resume_media_buy" in ACTION_TO_SALE_STATUS
        assert "update" not in ACTION_TO_SALE_STATUS
        assert "update_package_budget" not in ACTION_TO_SALE_STATUS


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

    def test_list_sales_returns_raw_dict(self):
        from src.adapters.curation.sales_client import SalesClient

        client = SalesClient(base_url="http://test")
        expected = {"items": [{"sale_id": "s1"}], "next_cursor": "next"}
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = expected
            result = client.list_sales()

        assert result == expected


# ── ActivationClient Simplified Payload Tests ────────────────────────────


class TestActivationClientSimplified:
    """ActivationClient.create_activation sends only {sale_id: ...}."""

    def test_create_activation_sends_sale_id_only(self):
        from src.adapters.curation.activation_client import ActivationClient

        client = ActivationClient(base_url="http://test")
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = {"activations": [{"deal_id": "d1"}]}
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
        expected = {"activations": [{"deal_id": "d1"}], "errors": []}
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = expected
            result = client.create_activation("sale-123")

        assert result == expected


# ── ADCP_STATUS_TO_SALE_STATUSES Tests ────────────────────────────────────


class TestAdcpToSaleStatusReverseMap:
    """Reverse mapping of AdCP MediaBuyStatus values to curation sale statuses."""

    def test_pending_activation_maps_to_both_pending_states(self):
        from src.adapters.curation.status_mapping import ADCP_STATUS_TO_SALE_STATUSES

        assert ADCP_STATUS_TO_SALE_STATUSES["pending_activation"] == [
            "pending_approval",
            "pending_activation",
        ]

    def test_active_maps_to_single_active(self):
        from src.adapters.curation.status_mapping import ADCP_STATUS_TO_SALE_STATUSES

        assert ADCP_STATUS_TO_SALE_STATUSES["active"] == ["active"]

    def test_completed_maps_to_completed_and_canceled(self):
        from src.adapters.curation.status_mapping import ADCP_STATUS_TO_SALE_STATUSES

        assert ADCP_STATUS_TO_SALE_STATUSES["completed"] == ["completed", "canceled"]

    def test_failed_maps_to_failed_and_rejected(self):
        from src.adapters.curation.status_mapping import ADCP_STATUS_TO_SALE_STATUSES

        assert ADCP_STATUS_TO_SALE_STATUSES["failed"] == ["failed", "rejected"]

    def test_reverse_map_covers_all_forward_mapping_values(self):
        from src.adapters.curation.status_mapping import (
            ADCP_STATUS_TO_SALE_STATUSES,
            SALE_STATUS_TO_ADCP,
        )

        forward_adcp_values = set(SALE_STATUS_TO_ADCP.values())
        reverse_keys = set(ADCP_STATUS_TO_SALE_STATUSES.keys())
        assert forward_adcp_values == reverse_keys, (
            "Every AdCP status in SALE_STATUS_TO_ADCP.values() must be a key "
            "in ADCP_STATUS_TO_SALE_STATUSES, and vice versa."
        )


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
        from src.adapters.curation._dt import parse_iso

        result = parse_iso("2026-04-09T12:34:56Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 9
        assert result.hour == 12

    def test_parses_plus_offset(self):
        from src.adapters.curation._dt import parse_iso

        result = parse_iso("2026-04-09T12:34:56+00:00")
        assert result is not None
        assert result.year == 2026

    def test_returns_none_for_none(self):
        from src.adapters.curation._dt import parse_iso

        assert parse_iso(None) is None

    def test_returns_none_for_empty_string(self):
        from src.adapters.curation._dt import parse_iso

        assert parse_iso("") is None

    def test_raises_on_malformed_strict(self):
        import pytest

        from src.adapters.curation._dt import parse_iso

        with pytest.raises(ValueError):
            parse_iso("not-a-date")

    def test_returns_none_on_malformed_safe(self):
        from src.adapters.curation._dt import parse_iso

        assert parse_iso("not-a-date", safe=True) is None


# ── _sale_to_media_buy Converter Tests ────────────────────────────────────


from tests.helpers.curation_fixtures import make_deal_sale

SAMPLE_SALE_DICT = make_deal_sale(
    "sale-abc-123",
    buyer_ref="buyer-1",
    segments=[{"segment_id": "seg-red"}, {"segment_id": "seg-blue"}],
    buyer_campaign_ref="camp-9",
)


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
        assert mb.status.value == "active"
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
        sale = {
            **SAMPLE_SALE_DICT,
            "pricing": {
                "pricing_model": "cpm",
                "currency": "USD",
                "floor_price": 2.50,
                "fixed_price": 5.00,
            },
        }
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
            assert pkg.start_time == "2026-04-01T00:00:00Z"
            assert pkg.end_time == "2026-04-30T23:59:59Z"

    def test_zero_segments_yields_empty_packages(self):
        adapter = _make_adapter()
        sale = {**SAMPLE_SALE_DICT, "segments": []}
        mb = adapter._sale_to_media_buy(sale)
        assert mb.packages == []
        assert mb.media_buy_id == "sale-abc-123"

    def test_segment_without_id_is_skipped(self):
        adapter = _make_adapter()
        sale = {
            **SAMPLE_SALE_DICT,
            "segments": [
                {"segment_id": "seg-red"},
                {},  # missing segment_id
                {"segment_id": "seg-blue"},
            ],
        }
        mb = adapter._sale_to_media_buy(sale)
        assert [pkg.package_id for pkg in mb.packages] == ["seg-red", "seg-blue"]

    def test_status_maps_through_sale_status_dict(self):
        adapter = _make_adapter()
        sale = {**SAMPLE_SALE_DICT, "status": "canceled"}
        mb = adapter._sale_to_media_buy(sale)
        # SALE_STATUS_TO_ADCP["canceled"] == "completed"
        assert mb.status.value == "completed"

    def test_unknown_status_defaults_to_pending_activation(self):
        adapter = _make_adapter()
        sale = {**SAMPLE_SALE_DICT, "status": "weirdstate"}
        mb = adapter._sale_to_media_buy(sale)
        assert mb.status.value == "pending_activation"

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
        sale = {
            **SAMPLE_SALE_DICT,
            "segments": [
                {
                    "segment_id": "seg-red",
                    "pricing": {
                        "fixed_price": 9.99,
                        "currency": "USD",
                    },
                },
                {"segment_id": "seg-blue"},  # uses sale-level
            ],
        }
        mb = adapter._sale_to_media_buy(sale)
        assert mb.packages[0].bid_price == 9.99
        assert mb.packages[1].bid_price == 2.50  # sale-level floor

    def test_media_buy_created_at_updated_at_as_datetime(self):
        """created_at / updated_at are parsed into datetime objects."""
        from datetime import datetime

        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_SALE_DICT)
        assert isinstance(mb.created_at, datetime)
        assert mb.created_at.year == 2026
        assert mb.created_at.month == 3
        assert mb.created_at.day == 29
        assert isinstance(mb.updated_at, datetime)
        assert mb.updated_at.day == 30


# ── Campaign _sale_to_media_buy Converter Tests ─────────────────────────────


SAMPLE_CAMPAIGN_SALE_DICT = {
    "sale_id": "sale-camp-001",
    "sale_type": "campaign",
    "buyer_ref": "buyer-1",
    "buyer_campaign_ref": "buyer-1",
    "campaign_meta": {"order_name": "acme.com-buyer-1", "media_buy_id": ""},
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
    def test_campaign_produces_correct_package_count(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_CAMPAIGN_SALE_DICT)
        assert len(mb.packages) == 2

    def test_campaign_package_ids_from_segment(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_CAMPAIGN_SALE_DICT)
        pkg_ids = [pkg.package_id for pkg in mb.packages]
        assert pkg_ids == ["seg-aaa", "seg-bbb"]

    def test_campaign_product_ids_from_segment(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_CAMPAIGN_SALE_DICT)
        product_ids = [pkg.product_id for pkg in mb.packages]
        assert product_ids == ["seg-aaa", "seg-bbb"]

    def test_campaign_budget_from_segment(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_CAMPAIGN_SALE_DICT)
        budgets = [pkg.budget for pkg in mb.packages]
        assert budgets == [5000.0, 3000.0]

    def test_campaign_bid_price_from_pricing_info_rate(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_CAMPAIGN_SALE_DICT)
        bid_prices = [pkg.bid_price for pkg in mb.packages]
        assert bid_prices == [2.50, 4.00]

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
            "segments": [
                {
                    "segment_id": "seg-no-pricing",
                    "package_id": "seg-no-pricing",
                    "product_id": "seg-no-pricing",
                    "budget": 1000.0,
                    # no pricing_info key
                },
            ],
        }
        mb = adapter._sale_to_media_buy(sale)
        assert len(mb.packages) == 1
        assert mb.packages[0].bid_price is None

    def test_deal_sale_still_works_unchanged(self):
        adapter = _make_adapter()
        mb = adapter._sale_to_media_buy(SAMPLE_SALE_DICT)
        assert mb.media_buy_id == "sale-abc-123"
        assert len(mb.packages) == 2
        pkg_ids = [pkg.package_id for pkg in mb.packages]
        assert pkg_ids == ["seg-red", "seg-blue"]
        for pkg in mb.packages:
            assert pkg.budget is None
            assert pkg.bid_price == 2.50

    def test_campaign_with_no_sale_type_treated_as_deal(self):
        adapter = _make_adapter()
        sale = {
            **SAMPLE_CAMPAIGN_SALE_DICT,
            "sale_type": None,  # explicitly no sale_type
            # Has campaign-style segments but should be treated as deal
            "pricing": {"currency": "USD", "floor_price": 1.00},
        }
        mb = adapter._sale_to_media_buy(sale)
        # Deal parsing uses segment_id only, budget is always None
        for pkg in mb.packages:
            assert pkg.budget is None


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
    return make_deal_sale(sale_id, status=status, buyer_ref=buyer_ref, budget=100.0)


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


# ── CurationAdapter.create_media_buy Campaign Payload Tests ──────────────


class TestCreateMediaBuyCampaignPayload:
    """Tests for the campaign-vs-deal payload construction in create_media_buy."""

    def _make_request(self, buyer_ref="buyer-1", brand_domain="example.com", ext=None, budget=None):
        from src.core.schemas import CreateMediaBuyRequest

        kwargs = {
            "buyer_ref": buyer_ref,
            "brand": {"domain": brand_domain},
            "start_time": "asap",
            "end_time": "2026-06-01T00:00:00Z",
        }
        if ext is not None:
            kwargs["ext"] = ext
        if budget is not None:
            kwargs["budget"] = budget
        return CreateMediaBuyRequest(**kwargs)

    def _make_packages(self, count=1, with_budget=True, with_formats=False):
        from adcp.types import FormatId as LibraryFormatId

        from src.core.schemas import MediaPackage

        pkgs = []
        for i in range(count):
            fmt_ids = []
            if with_formats:
                fmt_ids = [LibraryFormatId(id=f"display_300x250_{i}", agent_url="http://test.local/formats")]
            pkgs.append(
                MediaPackage(
                    package_id=f"pkg-{i}",
                    product_id=f"prod-{i}",
                    name=f"Package {i}",
                    delivery_type="non_guaranteed",
                    cpm=2.0,
                    impressions=10000,
                    format_ids=fmt_ids,
                    budget=500.0 if with_budget else None,
                )
            )
        return pkgs

    def test_default_sale_type_is_campaign(self):
        """No DSPs in ext -> sale_data['sale_type'] == 'campaign'."""
        adapter = _make_adapter()
        request = self._make_request()
        packages = self._make_packages()

        with (
            patch.object(adapter._sales, "create_sale", return_value={"sale_id": "sale-1"}) as mock_create,
            patch.object(  # noqa: E501
                adapter, "_activate_sale", return_value="mock-act-1"
            ),
        ):
            adapter.create_media_buy(
                request, packages, datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)
            )

        sale_data = mock_create.call_args[0][0]
        assert sale_data["sale_type"] == "campaign"

    def test_campaign_payload_has_campaign_meta(self):
        """Campaign payload includes campaign_meta with order_name and empty media_buy_id."""
        adapter = _make_adapter()
        request = self._make_request(buyer_ref="buy-99", brand_domain="acme.com")
        packages = self._make_packages()

        with (
            patch.object(adapter._sales, "create_sale", return_value={"sale_id": "sale-1"}) as mock_create,
            patch.object(  # noqa: E501
                adapter, "_activate_sale", return_value="mock-act-1"
            ),
        ):
            adapter.create_media_buy(
                request, packages, datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)
            )

        sale_data = mock_create.call_args[0][0]
        assert "campaign_meta" in sale_data
        assert sale_data["campaign_meta"]["order_name"] == "acme.com-buy-99"
        assert sale_data["campaign_meta"]["media_buy_id"] == ""

    def test_campaign_segments_have_segment_and_product_ids(self):
        """Each segment has segment_id == product_id == pkg.product_id, no package_id."""
        adapter = _make_adapter()
        request = self._make_request()
        packages = self._make_packages(count=2)

        with (
            patch.object(adapter._sales, "create_sale", return_value={"sale_id": "sale-1"}) as mock_create,
            patch.object(  # noqa: E501
                adapter, "_activate_sale", return_value="mock-act-1"
            ),
        ):
            adapter.create_media_buy(
                request, packages, datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)
            )

        sale_data = mock_create.call_args[0][0]
        segments = sale_data["segments"]
        assert len(segments) == 2
        for i, seg in enumerate(segments):
            expected_id = f"prod-{i}"
            assert seg["segment_id"] == expected_id
            assert seg["product_id"] == expected_id
            assert "package_id" not in seg

    def test_campaign_segment_has_budget_and_pricing_info(self):
        """Segment carries budget from package and pricing_info from package_pricing_info."""
        adapter = _make_adapter()
        request = self._make_request()
        packages = self._make_packages(count=1, with_budget=True)

        pricing_info = {"pkg-0": {"rate": 3.50, "currency": "EUR"}}

        with (
            patch.object(adapter._sales, "create_sale", return_value={"sale_id": "sale-1"}) as mock_create,
            patch.object(  # noqa: E501
                adapter, "_activate_sale", return_value="mock-act-1"
            ),
        ):
            adapter.create_media_buy(
                request,
                packages,
                datetime(2026, 5, 1, tzinfo=UTC),
                datetime(2026, 6, 1, tzinfo=UTC),
                package_pricing_info=pricing_info,
            )

        sale_data = mock_create.call_args[0][0]
        seg = sale_data["segments"][0]
        assert seg["budget"] == 500.0
        assert seg["pricing_info"]["rate"] == 3.50
        assert seg["pricing_info"]["currency"] == "EUR"
        assert seg["pricing_info"]["pricing_model"] == "cpm"

    def test_campaign_segment_has_ad_format_types(self):
        """ad_format_types extracted from package format_ids."""
        adapter = _make_adapter()
        request = self._make_request()
        packages = self._make_packages(count=1, with_formats=True)

        with (
            patch.object(adapter._sales, "create_sale", return_value={"sale_id": "sale-1"}) as mock_create,
            patch.object(  # noqa: E501
                adapter, "_activate_sale", return_value="mock-act-1"
            ),
        ):
            adapter.create_media_buy(
                request, packages, datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)
            )

        sale_data = mock_create.call_args[0][0]
        seg = sale_data["segments"][0]
        assert seg["ad_format_types"] == ["display_300x250_0"]

    def test_campaign_segment_publishers_empty_by_default(self):
        """publishers == [] by default in campaign segments."""
        adapter = _make_adapter()
        request = self._make_request()
        packages = self._make_packages()

        with (
            patch.object(adapter._sales, "create_sale", return_value={"sale_id": "sale-1"}) as mock_create,
            patch.object(  # noqa: E501
                adapter, "_activate_sale", return_value="mock-act-1"
            ),
        ):
            adapter.create_media_buy(
                request, packages, datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)
            )

        sale_data = mock_create.call_args[0][0]
        seg = sale_data["segments"][0]
        assert seg["publishers"] == []

    def test_deal_fallback_when_dsps_in_ext(self):
        """DSPs provided in ext -> deal payload (no campaign_meta, has deal_type)."""
        adapter = _make_adapter()
        request = self._make_request(ext={"dsps": [{"seat_id": "s1", "dsp_name": "DSP One"}]})
        packages = self._make_packages()

        with (
            patch.object(adapter._sales, "create_sale", return_value={"sale_id": "sale-1"}) as mock_create,
            patch.object(  # noqa: E501
                adapter, "_activate_sale", return_value="mock-act-1"
            ),
        ):
            adapter.create_media_buy(
                request, packages, datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)
            )

        sale_data = mock_create.call_args[0][0]
        assert "campaign_meta" not in sale_data
        assert sale_data["deal_type"] == "curated"
        assert sale_data["dsps"] == [{"seat_id": "s1", "dsp_name": "DSP One"}]

    def test_deal_fallback_when_sale_type_deal_in_ext(self):
        """ext.sale_type == 'deal' -> deal payload."""
        adapter = _make_adapter()
        request = self._make_request(ext={"sale_type": "deal"})
        packages = self._make_packages()

        with (
            patch.object(adapter._sales, "create_sale", return_value={"sale_id": "sale-1"}) as mock_create,
            patch.object(  # noqa: E501
                adapter, "_activate_sale", return_value="mock-act-1"
            ),
        ):
            adapter.create_media_buy(
                request, packages, datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)
            )

        sale_data = mock_create.call_args[0][0]
        assert "campaign_meta" not in sale_data
        assert sale_data["deal_type"] == "curated"
        assert "sale_type" not in sale_data


# ── _activate_sale Tests ────────────────────────────────────────────────────


class TestActivateSale:
    """Tests for _activate_sale which handles both campaign and deal activation."""

    def test_sends_only_sale_id_to_activation_service(self):
        """create_activation receives just the sale_id string, not a dict."""
        adapter = _make_adapter()
        adapter._activation.create_activation = MagicMock(
            return_value={
                "activations": [{"activation_id": "act-1", "deal_id": "d1", "status": "active"}],
            }
        )
        adapter._sales.update_sale = MagicMock(return_value={})

        sale_data = {"dsps": [{"seat_id": "s1", "dsp_name": "DSP"}]}
        adapter._activate_sale("sale-1", sale_data)

        adapter._activation.create_activation.assert_called_once_with("sale-1")

    def test_campaign_activation_updates_sale_with_gam_record(self):
        """Campaign activation builds a GAM-style activation record."""
        adapter = _make_adapter()
        adapter._activation.create_activation = MagicMock(
            return_value={
                "activations": [
                    {
                        "activation_id": "act-gam-1",
                        "ssp_name": "gam",
                        "status": "active",
                        "metadata": {
                            "activation_target": "GAM",
                            "gam_network_code": "12345",
                            "gam_order_id": "order-99",
                            "segments": [{"id": "seg-1"}],
                        },
                    }
                ],
            }
        )
        adapter._sales.update_sale = MagicMock(return_value={})

        sale_data = {"sale_type": "campaign"}
        result = adapter._activate_sale("sale-c1", sale_data)

        assert result == "act-gam-1"
        update_call = adapter._sales.update_sale.call_args
        assert update_call[0][0] == "sale-c1"
        activation = update_call[0][1]["activations"][0]
        assert activation["activation_target"] == "GAM"
        assert activation["gam_network_code"] == "12345"
        assert activation["gam_order_id"] == "order-99"
        assert activation["segments"] == [{"id": "seg-1"}]
        assert activation["status"] == "active"

    def test_deal_activation_updates_sale_with_deal_record(self):
        """Deal activation builds a Magnite-style activation record."""
        adapter = _make_adapter()
        adapter._activation.create_activation = MagicMock(
            return_value={
                "activations": [
                    {
                        "activation_id": "act-mag-1",
                        "ssp_name": "magnite",
                        "deal_id": "deal-xyz",
                        "status": "active",
                    }
                ],
            }
        )
        adapter._sales.update_sale = MagicMock(return_value={})

        sale_data = {"dsps": [{"seat_id": "s1", "dsp_name": "StackAdapt"}]}
        result = adapter._activate_sale("sale-d1", sale_data)

        assert result == "act-mag-1"
        update_call = adapter._sales.update_sale.call_args
        activation = update_call[0][1]["activations"][0]
        assert activation["ssp_name"] == "magnite"
        assert activation["dsp_name"] == "StackAdapt"
        assert activation["deal_id"] == "deal-xyz"
        assert activation["status"] == "active"

    def test_activation_failure_returns_none(self):
        """AdCPAdapterError from create_activation -> returns None, no update_sale call."""
        from src.core.exceptions import AdCPAdapterError

        adapter = _make_adapter()
        adapter._activation.create_activation = MagicMock(side_effect=AdCPAdapterError("Service down"))
        adapter._sales.update_sale = MagicMock(return_value={})

        sale_data = {"dsps": []}
        result = adapter._activate_sale("sale-fail", sale_data)

        assert result is None
        adapter._sales.update_sale.assert_not_called()

    def test_empty_activations_returns_none(self):
        """Empty activations list -> returns None, no update_sale call."""
        adapter = _make_adapter()
        adapter._activation.create_activation = MagicMock(
            return_value={"activations": []},
        )
        adapter._sales.update_sale = MagicMock(return_value={})

        sale_data = {"dsps": []}
        result = adapter._activate_sale("sale-empty", sale_data)

        assert result is None
        adapter._sales.update_sale.assert_not_called()

    def test_mock_activation_campaign(self):
        """mock_activation=True with campaign sale_type -> mock GAM record."""
        adapter = _make_adapter()
        adapter._mock_activation = True
        adapter._sales.update_sale = MagicMock(return_value={})

        sale_data = {"sale_type": "campaign"}
        result = adapter._activate_sale("sale-mock-c", sale_data)

        assert result is not None
        assert result.startswith("mock-")
        update_call = adapter._sales.update_sale.call_args
        activation = update_call[0][1]["activations"][0]
        assert activation["activation_target"] == "GAM"
        assert activation["gam_network_code"] == "mock-network"
        assert "gam_order_id" in activation
        assert activation["status"] == "active"


class TestBuildCreativeAssignments:
    """Tests for _build_creative_assignments snippet→tag mapping."""

    def test_snippet_mapped_to_tag(self):
        """Creative with snippet and no tag → result has tag from snippet."""
        from src.adapters.curation.adapter import _build_creative_assignments
        from src.core.schemas import MediaPackage

        creative = MagicMock()
        creative.creative_id = "c1"
        creative.format_id = None
        creative.name = None
        creative.tag = None
        creative.snippet = "<img src='ad.png' />"
        creative.snippet_type = None
        creative.assets = None
        creative.status = None

        pkg = MagicMock(spec=MediaPackage)
        pkg.creative_ids = None

        orig_pkg = MagicMock()
        orig_pkg.creatives = [creative]

        result = _build_creative_assignments(pkg, orig_pkg)

        assert len(result) == 1
        assert result[0]["tag"] == "<img src='ad.png' />"

    def test_tag_field_takes_precedence_over_snippet(self):
        """Creative with both tag and snippet → tag wins."""
        from src.adapters.curation.adapter import _build_creative_assignments
        from src.core.schemas import MediaPackage

        creative = MagicMock()
        creative.creative_id = "c2"
        creative.format_id = None
        creative.name = None
        creative.tag = "<p>tag wins</p>"
        creative.snippet = "<p>snippet</p>"
        creative.snippet_type = None
        creative.assets = None
        creative.status = None

        pkg = MagicMock(spec=MediaPackage)
        pkg.creative_ids = None

        orig_pkg = MagicMock()
        orig_pkg.creatives = [creative]

        result = _build_creative_assignments(pkg, orig_pkg)

        assert len(result) == 1
        assert result[0]["tag"] == "<p>tag wins</p>"

    def test_no_creatives_returns_empty(self):
        """No creatives on either pkg or orig_pkg → empty list."""
        from src.adapters.curation.adapter import _build_creative_assignments
        from src.core.schemas import MediaPackage

        pkg = MagicMock(spec=MediaPackage)
        pkg.creative_ids = None

        orig_pkg = MagicMock()
        orig_pkg.creatives = None

        result = _build_creative_assignments(pkg, orig_pkg)

        assert result == []

    def test_snippet_type_passed_through(self):
        """Creative with snippet_type → result includes snippet_type."""
        from src.adapters.curation.adapter import _build_creative_assignments
        from src.core.schemas import MediaPackage

        creative = MagicMock()
        creative.creative_id = "c3"
        creative.format_id = None
        creative.name = None
        creative.tag = None
        creative.snippet = "<div>ad</div>"
        creative.snippet_type = "html"
        creative.assets = None
        creative.status = None

        pkg = MagicMock(spec=MediaPackage)
        pkg.creative_ids = None

        orig_pkg = MagicMock()
        orig_pkg.creatives = [creative]

        result = _build_creative_assignments(pkg, orig_pkg)

        assert len(result) == 1
        assert result[0]["tag"] == "<div>ad</div>"
        assert result[0]["snippet_type"] == "html"


class TestRankingPromptLocation:
    """DEFAULT_CURATION_RANKING_PROMPT moved out of services/ai to live with
    the curation adapter. Keeps the rebase surface small in the shared
    ranking agent module.
    """

    def test_prompt_importable_from_curation_module(self):
        from src.adapters.curation.ranking import DEFAULT_CURATION_RANKING_PROMPT

        assert isinstance(DEFAULT_CURATION_RANKING_PROMPT, str)
        assert "audience matching expert" in DEFAULT_CURATION_RANKING_PROMPT

    def test_prompt_no_longer_exported_from_ranking_agent(self):
        from src.services.ai.agents import ranking_agent

        assert not hasattr(ranking_agent, "DEFAULT_CURATION_RANKING_PROMPT"), (
            "DEFAULT_CURATION_RANKING_PROMPT must live in "
            "src/adapters/curation/ranking.py only — keeping a copy in "
            "services/ai would drift the two."
        )
