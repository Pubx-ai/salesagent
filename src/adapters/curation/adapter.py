"""CurationAdapter -- bridges AdServerAdapter to external curation services.

This adapter is the sole source of truth for curation tenants.
No data is persisted in the Prebid Sales Agent PostgreSQL database.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.adapters.base import (
    AdapterCapabilities,
    AdServerAdapter,
    BaseProductConfig,
    CreativeEngineAdapter,
    TargetingCapabilities,
)
from src.adapters.curation.activation_client import ActivationClient
from src.adapters.curation.catalog_client import CatalogClient
from src.adapters.curation.config import CurationConnectionConfig
from src.adapters.curation.sales_client import SalesClient
from src.adapters.curation.segment_converter import segments_to_products
from src.core.exceptions import AdCPAdapterError
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AssetStatus,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    DeliveryTotals,
    MediaPackage,
    PackagePerformance,
    Principal,
    ReportingPeriod,
    UpdateMediaBuyResponse,
    UpdateMediaBuySuccess,
)
from src.core.schemas.product import Product

logger = logging.getLogger(__name__)

ACTIVATION_STATUS_TO_ADCP = {
    "pending": "pending_activation",
    "active": "active",
    "paused": "paused",
    "error": "failed",
    "inactive": "completed",
}

SALE_STATUS_TO_ADCP = {
    "pending_approval": "pending_activation",
    "pending_activation": "pending_activation",
    "active": "active",
    "paused": "paused",
    "completed": "completed",
    "failed": "failed",
    "rejected": "failed",
    "canceled": "completed",
}

ACTION_TO_ADCP_STATUS = {
    "pause": "paused",
    "resume": "active",
    "cancel": "completed",
}


class CurationAdapter(AdServerAdapter):
    """Adapter bridging AdCP tools to external curation services.

    Instead of managing line items on an ad server, this adapter:
    - Fetches audience segments from a Catalog service (as products)
    - Creates sale records in a Sales service (as media buys)
    - Triggers SSP deal activation via an Activation service
    """

    adapter_name = "curation"
    manages_own_persistence = True

    default_channels = ["display"]
    default_delivery_measurement = {"provider": "curation"}

    connection_config_class = CurationConnectionConfig
    product_config_class: type[BaseProductConfig] | None = None

    capabilities = AdapterCapabilities(
        supports_inventory_sync=False,
        supports_inventory_profiles=False,
        inventory_entity_label="Segments",
        supports_custom_targeting=False,
        supports_geo_targeting=False,
        supports_dynamic_products=False,
        supported_pricing_models=["cpm"],
        supports_webhooks=False,
        supports_realtime_reporting=False,
    )

    def __init__(
        self,
        config: dict[str, Any],
        principal: Principal,
        dry_run: bool = False,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        super().__init__(config, principal, dry_run, creative_engine, tenant_id)

        conn = CurationConnectionConfig(
            **{k: v for k, v in config.items() if k in CurationConnectionConfig.model_fields}
        )
        timeout = conn.http_timeout_seconds

        self._catalog = CatalogClient(conn.catalog_service_url, timeout=timeout)
        self._sales = SalesClient(conn.sales_service_url, timeout=timeout)
        self._activation = ActivationClient(conn.activation_service_url, timeout=timeout)

        self._pricing_multiplier = conn.pricing_multiplier
        self._pricing_floor_cpm = conn.pricing_floor_cpm
        self._pricing_max_suggested_cpm = conn.pricing_max_suggested_cpm
        self._publisher_domain = config.get("publisher_domain", "curation.local")

    # ── Product catalog ────────────────────────────────────────────────

    def get_product_catalog(self, tenant_id: str) -> list[Product] | None:
        """Fetch segments from Catalog and return as AdCP Products."""
        segments = self._catalog.fetch_all_segments()
        return segments_to_products(
            segments,
            pricing_multiplier=self._pricing_multiplier,
            pricing_floor_cpm=self._pricing_floor_cpm,
            pricing_max_suggested_cpm=self._pricing_max_suggested_cpm,
            publisher_domain=self._publisher_domain,
        )

    # ── Pricing ────────────────────────────────────────────────────────

    def get_supported_pricing_models(self) -> set[str]:
        return {"cpm"}

    def get_targeting_capabilities(self) -> TargetingCapabilities:
        return TargetingCapabilities(geo_countries=False)

    # ── Create media buy ───────────────────────────────────────────────

    def create_media_buy(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> CreateMediaBuyResponse:
        """Create a sale + activation in curation services."""
        segment_refs = [{"segment_id": pkg.product_id} for pkg in packages]
        pricing_info = _extract_pricing(package_pricing_info)
        dsps = _extract_dsps(request)

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

        # Step 1: Create sale
        sale_resp = self._sales.create_sale(sale_data)
        sale_id = sale_resp.get("sale_id")
        if not sale_id:
            raise AdCPAdapterError("Sales service did not return a sale_id")
        logger.info("Created sale %s in Sales service", sale_id)

        # Step 2: Trigger activation
        ssp_deal_id: str | None = None
        try:
            activation_price = pricing_info.get("fixed_price") or pricing_info.get("floor_price") or 0
            act_result = self._activation.create_activation(
                {
                    "sale_id": sale_id,
                    "ssp_name": "magnite",
                    "start_date": start_time.isoformat(),
                    "end_date": end_time.isoformat(),
                    "price": {"amount": activation_price, "currency": pricing_info.get("currency", "USD")},
                }
            )

            activations = act_result.get("activations", [])
            if activations:
                ssp_deal_id = activations[0].get("deal_id")
                logger.info("Activation created: deal_id=%s", ssp_deal_id)
        except Exception:
            logger.exception("Activation failed for sale %s, deal will be pending_activation", sale_id)

        # Step 3: Update sale status if activation succeeded
        if ssp_deal_id:
            try:
                self._sales.update_sale(
                    sale_id,
                    {
                        "status": "active",
                        "activations": [
                            {
                                "activation_id": f"act-{sale_id}",
                                "ssp_name": "magnite",
                                "dsp_name": ", ".join(d.get("dsp_name", "") for d in dsps if d.get("dsp_name")),
                                "deal_id": ssp_deal_id,
                                "status": "active",
                            }
                        ],
                    },
                )
            except Exception:
                logger.warning("Failed to update sale %s status after activation", sale_id, exc_info=True)

        return self._build_create_success(
            request,
            sale_id,
            packages,
            paused=ssp_deal_id is None,
        )

    # ── Check status ───────────────────────────────────────────────────

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        sale = self._sales.get_sale(media_buy_id)
        raw_status = sale.get("status", "pending_activation")
        adcp_status = SALE_STATUS_TO_ADCP.get(raw_status, "pending_activation")

        return CheckMediaBuyStatusResponse(
            media_buy_id=media_buy_id,
            buyer_ref=sale.get("buyer_ref", ""),
            status=adcp_status,
        )

    # ── Get delivery ───────────────────────────────────────────────────

    def get_media_buy_delivery(
        self,
        media_buy_id: str,
        date_range: ReportingPeriod,
        today: datetime,
    ) -> AdapterGetMediaBuyDeliveryResponse:
        sale = self._sales.get_sale(media_buy_id)
        raw_status = sale.get("status", "pending_activation")
        adcp_status = SALE_STATUS_TO_ADCP.get(raw_status, "pending_activation")

        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id=media_buy_id,
            reporting_period=date_range,
            currency="USD",
            totals=DeliveryTotals(
                impressions=0.0,
                spend=0.0,
                clicks=None,
                video_completions=None,
            ),
            by_package=[],
        )

    # ── Update media buy ───────────────────────────────────────────────

    def update_media_buy(
        self,
        media_buy_id: str,
        buyer_ref: str,
        action: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> UpdateMediaBuyResponse:
        update_data: dict[str, Any] = {}

        if action == "pause":
            update_data["status"] = "paused"
        elif action == "resume":
            update_data["status"] = "active"
        elif action == "cancel":
            update_data["status"] = "canceled"

        if budget is not None:
            update_data["budget"] = float(budget)

        if update_data:
            self._sales.update_sale(media_buy_id, update_data)

        adcp_status = ACTION_TO_ADCP_STATUS.get(action, "active")
        return UpdateMediaBuySuccess(
            media_buy_id=media_buy_id,
            buyer_ref=buyer_ref,
            status=adcp_status,
        )

    # ── Performance index (no-op for curation) ─────────────────────────

    def update_media_buy_performance_index(
        self, media_buy_id: str, package_performance: list[PackagePerformance]
    ) -> bool:
        return True

    # ── Creative management (not applicable for curation deals) ────────

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        return []

    def associate_creatives(self, line_item_ids: list[str], platform_creative_ids: list[str]) -> list[dict[str, Any]]:
        return []


def _extract_pricing(package_pricing_info: dict[str, dict] | None) -> dict[str, Any]:
    """Extract pricing from the first package's pricing info."""
    if not package_pricing_info:
        return {"currency": "USD", "floor_price": 0.5}

    first = next(iter(package_pricing_info.values()), {})
    return {
        "currency": first.get("currency", "USD"),
        "floor_price": first.get("bid_price") or first.get("rate"),
        "fixed_price": first.get("rate") if first.get("is_fixed") else None,
    }


def _extract_dsps(request: CreateMediaBuyRequest) -> list[dict[str, Any]]:
    """Extract DSP configuration from the request, with sensible defaults."""
    ext = getattr(request, "ext", None) or {}
    dsps_from_ext = ext.get("dsps") if isinstance(ext, dict) else None

    if dsps_from_ext and isinstance(dsps_from_ext, list):
        return dsps_from_ext

    return [{"seat_id": "default", "dsp_name": "Default DSP"}]
