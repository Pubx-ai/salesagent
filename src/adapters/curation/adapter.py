"""CurationAdapter -- bridges ToolProvider to external curation services.

This adapter is the sole source of truth for curation tenants.
No data is persisted in the Prebid Sales Agent PostgreSQL database.

Extends ToolProvider directly (not AdServerAdapter) because curation
is not an ad server -- it manages audience segments, sale records,
and SSP deal activations via external HTTP services.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from adcp.types.aliases import Package as ResponsePackage
from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

from src.adapters.base import (
    AdapterCapabilities,
    BaseConnectionConfig,
    ToolProvider,
)
from src.adapters.curation.activation_client import ActivationClient
from src.adapters.curation.catalog_client import CatalogClient
from src.adapters.curation.config import CurationConnectionConfig
from src.adapters.curation.sales_client import SalesClient
from src.adapters.curation.segment_converter import DEFAULT_PUBLISHER_DOMAIN, segments_to_products
from src.core.exceptions import AdCPAdapterError
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    CreateMediaBuySuccess,
    DeliveryTotals,
    GetMediaBuysMediaBuy,
    GetMediaBuysPackage,
    MediaPackage,
    PackagePerformance,
    Principal,
    ReportingPeriod,
    UpdateMediaBuyResponse,
    UpdateMediaBuySuccess,
)
from src.core.schemas.product import Product

logger = logging.getLogger(__name__)

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

ACTION_TO_ADCP_STATUS = {
    "pause": "paused",
    "resume": "active",
    "cancel": "completed",
}


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

    media_buys: list[GetMediaBuysMediaBuy]
    truncated: bool
    total_fetched: int


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO8601 string into a datetime, or return None.

    Handles both ``2026-04-09T12:34:56Z`` and ``2026-04-09T12:34:56+00:00``.
    """
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class CurationAdapter(ToolProvider):
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

    connection_config_class: type[BaseConnectionConfig] | None = CurationConnectionConfig

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
        creative_engine: Any = None,
        tenant_id: str | None = None,
    ):
        if not tenant_id:
            raise ValueError("tenant_id is required for CurationAdapter initialization.")

        self.config = config
        self.principal = principal
        self.dry_run = dry_run
        self.tenant_id = tenant_id

        self.manual_approval_required = config.get("manual_approval_required", False)
        self.manual_approval_operations: set[str] = set(config.get("manual_approval_operations", []))

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
        self._publisher_domain = config.get("publisher_domain") or DEFAULT_PUBLISHER_DOMAIN
        self._mock_activation = conn.mock_activation
        self._max_media_buys_per_list = conn.max_media_buys_per_list

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

    # ── Create media buy (sale + activation) ───────────────────────────

    def create_media_buy(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> CreateMediaBuyResponse:
        """Create a sale + activation in curation services."""
        ext_dict = _ext_as_dict(request)
        use_deal = ext_dict.get("sale_type") == "deal" or bool(_extract_dsps_from_ext(request))

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
        """Build a campaign-type sale payload for the Sales service."""
        brand = getattr(request, "brand", None)
        brand_domain = ""
        if brand:
            brand_domain = getattr(brand, "domain", "") or ""

        segments: list[dict[str, Any]] = []
        for pkg in packages:
            pricing_info_dict = (package_pricing_info or {}).get(pkg.package_id, {})
            rate = pricing_info_dict.get("rate") or pricing_info_dict.get("bid_price")
            currency = pricing_info_dict.get("currency", "USD")

            ad_format_types: list[str] = []
            for fmt in getattr(pkg, "format_ids", None) or []:
                fmt_id = fmt.get("id") if isinstance(fmt, dict) else getattr(fmt, "id", None)
                if fmt_id:
                    ad_format_types.append(str(fmt_id))

            segments.append(
                {
                    "segment_id": pkg.product_id,
                    "package_id": pkg.product_id,
                    "product_id": pkg.product_id,
                    "domains": [],
                    "ad_format_types": ad_format_types,
                    "budget": float(pkg.budget) if pkg.budget else None,
                    "pricing_info": {"rate": float(rate), "currency": currency} if rate else None,
                    "creative_assignments": [],
                    "publishers": [],
                }
            )

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
        """Build a deal-type sale payload for the Sales service."""
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

    def _activate_sale(self, sale_id: str, sale_data: dict[str, Any]) -> str | None:
        """Activate a sale via the Activation service, or mock it.

        Returns an activation identifier on success, None on failure.
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
                activation_record: dict[str, Any] = {
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
        self._sales.get_sale(media_buy_id)

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
        - start_time/end_time: passed as raw ISO strings (schema uses `str | None`)
        - created_at/updated_at: parsed to datetime (schema uses `datetime | None`)
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
                    start_time=sale.get("start_time"),
                    end_time=sale.get("end_time"),
                    paused=None,
                    creative_approvals=None,
                    snapshot=None,
                    snapshot_unavailable_reason=None,
                )
            )

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


def _ext_as_dict(request: CreateMediaBuyRequest) -> dict[str, Any]:
    """Normalize request.ext to a plain dict.

    ``ext`` may be a dict (from raw dicts in tests) or an adcp
    ``ExtensionObject`` (Pydantic model with extra fields in
    ``model_extra``).  This helper returns a plain dict in both cases.
    """
    ext = getattr(request, "ext", None)
    if ext is None:
        return {}
    if isinstance(ext, dict):
        return ext
    # ExtensionObject — extra fields are stored in model_extra
    extras = getattr(ext, "model_extra", None) or {}
    # Also include explicitly declared fields
    try:
        declared = ext.model_dump()
    except Exception:
        declared = {}
    return {**declared, **extras}


def _extract_dsps_from_ext(request: CreateMediaBuyRequest) -> list[dict[str, Any]] | None:
    """Extract DSP configuration from request.ext, or None if absent."""
    ext_dict = _ext_as_dict(request)
    dsps_from_ext = ext_dict.get("dsps")
    if dsps_from_ext and isinstance(dsps_from_ext, list):
        return dsps_from_ext
    return None
