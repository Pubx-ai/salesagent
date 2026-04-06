"""Convert Curation Catalog segments to AdCP Product schema objects.

Maps segment fields to AdCP Product fields so buyer agents see standard
products while the underlying data comes from the Catalog service.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from adcp import CpmPricingOption
from adcp.types.generated_poc.core.format_id import FormatId
from adcp.types.generated_poc.core.pricing_option import PricingOption
from adcp.types.generated_poc.core.publisher_property_selector import PublisherPropertySelector

from src.core.schemas.product import Product

logger = logging.getLogger(__name__)

DEFAULT_PUBLISHER_DOMAIN = "curation.local"
DEFAULT_AGENT_URL = "https://curation.local"


def _round_currency(value: float) -> float:
    return math.floor(value * 100) / 100


def _compute_floor_price(
    segment: dict[str, Any],
    *,
    floor_cpm: float,
) -> float:
    """Compute the CPM floor price for a segment.

    Uses the configured floor_cpm directly. Per-segment pricing adjustments
    are handled at the Sales/Activation level, not at catalog discovery time.
    """
    return _round_currency(floor_cpm)


def segment_to_product(
    segment: dict[str, Any],
    *,
    pricing_multiplier: float = 5.0,
    pricing_floor_cpm: float = 0.1,
    pricing_max_suggested_cpm: float = 10.0,
    publisher_domain: str = DEFAULT_PUBLISHER_DOMAIN,
    agent_url: str = DEFAULT_AGENT_URL,
) -> Product:
    """Convert a single Catalog segment response dict to an AdCP Product.

    Args:
        segment: SegmentResponse dict from the Catalog service.
        pricing_multiplier: Multiplier applied to base CPM.
        pricing_floor_cpm: Minimum floor CPM.
        pricing_max_suggested_cpm: Cap on suggested CPM.
        publisher_domain: Domain for publisher_properties.
        agent_url: Agent URL for format_ids.

    Returns:
        An AdCP-compliant Product object.
    """
    segment_id = segment.get("segment_id") or segment.get("name", "unknown")
    name = segment.get("name", "Unknown Segment")
    description = segment.get("description", "")

    floor_price = _compute_floor_price(segment, floor_cpm=pricing_floor_cpm)

    cpm = CpmPricingOption(
        pricing_option_id=f"cpm_usd_auction_{segment_id}",
        pricing_model="cpm",
        currency="USD",
        floor_price=floor_price,
    )

    return Product(
        product_id=segment_id,
        name=name,
        description=description or f"Audience segment: {name}",
        publisher_properties=[
            PublisherPropertySelector.model_validate({"selection_type": "all", "publisher_domain": publisher_domain})
        ],
        format_ids=[FormatId(id="display_banner", agent_url=agent_url)],
        delivery_type="non_guaranteed",
        pricing_options=[PricingOption(root=cpm)],
        delivery_measurement={"provider": "curation"},
        channels=["display"],
    )


def segments_to_products(
    segments: list[dict[str, Any]],
    *,
    pricing_multiplier: float = 5.0,
    pricing_floor_cpm: float = 0.1,
    pricing_max_suggested_cpm: float = 10.0,
    publisher_domain: str = DEFAULT_PUBLISHER_DOMAIN,
    agent_url: str = DEFAULT_AGENT_URL,
) -> list[Product]:
    """Convert a list of Catalog segment dicts to AdCP Products.

    Segments that fail conversion are logged and skipped.
    """
    products: list[Product] = []
    for seg in segments:
        try:
            product = segment_to_product(
                seg,
                pricing_multiplier=pricing_multiplier,
                pricing_floor_cpm=pricing_floor_cpm,
                pricing_max_suggested_cpm=pricing_max_suggested_cpm,
                publisher_domain=publisher_domain,
                agent_url=agent_url,
            )
            products.append(product)
        except Exception:
            seg_name = seg.get("name", "unknown")
            logger.exception("Failed to convert segment '%s' to Product, skipping", seg_name)
    return products
