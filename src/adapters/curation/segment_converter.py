"""Convert Curation Catalog segments to AdCP Product schema objects.

Maps segment fields to AdCP Product fields so buyer agents see standard
products while the underlying data comes from the Catalog service.

Enrichment extracts:
- Countries and device types from CEL rules (for filtering)
- Delivery forecast from estimation metadata (impressions, CPM)
- Price guidance from historical CPM data
- Signals, domains, and owner into ext for AI ranking context
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from typing import Any

from adcp import CpmPricingOption
from adcp.types.generated_poc.core.delivery_forecast import DeliveryForecast
from adcp.types.generated_poc.core.forecast_point import ForecastPoint, Metrics
from adcp.types.generated_poc.core.forecast_range import ForecastRange
from adcp.types.generated_poc.core.format_id import FormatId
from adcp.types.generated_poc.core.pricing_option import PricingOption
from adcp.types.generated_poc.core.publisher_property_selector import PublisherPropertySelector
from adcp.types.generated_poc.pricing_options.price_guidance import PriceGuidance

from src.core.schemas.product import Product

logger = logging.getLogger(__name__)

DEFAULT_PUBLISHER_DOMAIN = "pubx.ai"
DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

# Maps platform values from CEL rules to AdCP device_type values
_PLATFORM_TO_DEVICE: dict[str, str] = {
    "ios": "mobile",
    "android": "mobile",
    "macos": "desktop",
    "windows": "desktop",
    "linux": "desktop",
    "mobile": "mobile",
    "desktop": "desktop",
    "tablet": "tablet",
    "ctv": "ctv",
}


def _normalize_domain(raw: str) -> str | None:
    """Extract a bare domain from a raw domain string.

    Handles: "https://www.example.com/", "example.com"
    Skips run-of-network entries like "pubx.ai RON" (not a single domain).
    Returns lowercase bare domain or None if invalid/skipped.
    """
    from urllib.parse import urlparse

    if " RON" in raw:
        return None

    cleaned = raw.strip().rstrip("/")
    if not cleaned:
        return None

    if cleaned.startswith(("http://", "https://")):
        parsed = urlparse(cleaned)
        hostname = parsed.hostname or ""
    else:
        hostname = cleaned.split("/")[0]

    hostname = hostname.lower().strip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]

    return hostname if hostname and "." in hostname else None


def _round_currency(value: float) -> float:
    return math.floor(value * 100) / 100


def _extract_countries_from_cel(cel_rule: str) -> list[str]:
    """Extract ISO country codes from a CEL rule string.

    Handles patterns like:
    - country == 'US'
    - country IN ['US','GB']
    - country in ['AU', 'NZ']
    """
    in_match = re.search(r"country\s+(?:IN|in)\s+\[([^\]]+)\]", cel_rule)
    if in_match:
        return re.findall(r"'([A-Z]{2})'", in_match.group(1))

    eq_match = re.search(r"country\s*==\s*'([A-Z]{2})'", cel_rule)
    if eq_match:
        return [eq_match.group(1)]

    return []


def _extract_device_types_from_cel(cel_rule: str) -> list[str]:
    """Extract device types from CEL rule by parsing device_type and platform signals.

    Handles patterns like:
    - device_type == 'mobile'
    - platform IN ['ios','macos']
    - platform == 'android'
    """
    devices: set[str] = set()

    for field in ("device_type", "platform"):
        in_match = re.search(rf"{field}\s+(?:IN|in)\s+\[([^\]]+)\]", cel_rule)
        if in_match:
            values = re.findall(r"'(\w+)'", in_match.group(1))
            for v in values:
                mapped = _PLATFORM_TO_DEVICE.get(v.lower(), v.lower())
                devices.add(mapped)

        eq_match = re.search(rf"{field}\s*==\s*'(\w+)'", cel_rule)
        if eq_match:
            v = eq_match.group(1)
            mapped = _PLATFORM_TO_DEVICE.get(v.lower(), v.lower())
            devices.add(mapped)

    return sorted(devices)


FORMAT_TYPE_TO_ID_PREFIX: dict[str, str] = {
    "banner": "display",
    "video": "video",
    "native": "native",
    "audio": "audio",
}


def _build_format_ids(metadata: dict[str, Any], agent_url: str) -> list[FormatId]:
    """Build FormatId list from inventory_formats metadata.

    Creates one FormatId per format type + size combination.
    Falls back to a generic display_banner if no inventory_formats present.
    """
    inventory_formats = metadata.get("inventory_formats", {})
    if not inventory_formats:
        return [FormatId(id="display_banner", agent_url=agent_url)]

    format_ids: list[FormatId] = []
    for fmt_type, fmt_data in inventory_formats.items():
        prefix = FORMAT_TYPE_TO_ID_PREFIX.get(fmt_type, fmt_type)
        sizes = fmt_data.get("sizes", []) if isinstance(fmt_data, dict) else []

        if not sizes:
            format_ids.append(FormatId(id=f"{prefix}_{fmt_type}", agent_url=agent_url))
            continue

        for size in sizes:
            parts = str(size).split("x")
            if len(parts) == 2:
                try:
                    w, h = int(parts[0]), int(parts[1])
                    format_ids.append(FormatId(id=f"{prefix}_{size}", agent_url=agent_url, width=w, height=h))
                    continue
                except ValueError:
                    pass
            format_ids.append(FormatId(id=f"{prefix}_{size}", agent_url=agent_url))

    return format_ids or [FormatId(id="display_banner", agent_url=agent_url)]


def _build_forecast(estimation: dict[str, Any]) -> DeliveryForecast | None:
    """Build an AdCP DeliveryForecast from catalog estimation data."""
    avg_daily = estimation.get("avg_daily_impressions")
    if not avg_daily or not isinstance(avg_daily, (int, float)) or avg_daily <= 0:
        return None

    total_7d = estimation.get("total_impressions_7d", 0)
    low = int(avg_daily * 0.7) if avg_daily else None
    high = int(avg_daily * 1.3) if avg_daily else None

    forecast_point = ForecastPoint(
        budget=1000.0,
        metrics=Metrics(
            impressions=ForecastRange(
                low=float(low) if low else None,
                mid=float(avg_daily),
                high=float(high) if high else None,
            ),
        ),
    )

    estimated_at_str = estimation.get("estimated_at")
    generated_at = None
    if estimated_at_str:
        try:
            generated_at = datetime.fromisoformat(estimated_at_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    return DeliveryForecast(
        points=[forecast_point],
        method="estimate",
        currency="USD",
        forecast_range_unit="daily",
        generated_at=generated_at,
    )


def _build_price_guidance(
    estimation: dict[str, Any],
    *,
    floor_cpm: float,
    multiplier: float,
    max_suggested_cpm: float,
) -> PriceGuidance | None:
    """Build PriceGuidance from historical CPM data."""
    avg_cpm = estimation.get("avg_daily_cpm")
    if not avg_cpm or not isinstance(avg_cpm, (int, float)) or avg_cpm <= 0:
        return None

    recommended = _round_currency(min(float(avg_cpm) * multiplier, max_suggested_cpm))
    return PriceGuidance(floor=floor_cpm, recommended=recommended, p50=float(avg_cpm))


def _build_ext(segment: dict[str, Any]) -> dict[str, Any]:
    """Build the ext (extension) object with metadata for AI ranking and buyer context."""
    metadata = segment.get("metadata", {}) or {}
    estimation = metadata.get("estimation", {}) or {}
    ext: dict[str, Any] = {}

    signals_used = metadata.get("signals_used")
    if signals_used:
        ext["signals_used"] = signals_used

    domains = metadata.get("domains")
    if domains:
        ext["domains"] = domains

    unique_sites = estimation.get("unique_sites")
    if unique_sites and isinstance(unique_sites, (int, float)) and unique_sites > 0:
        ext["unique_sites"] = int(unique_sites)

    return ext or None  # type: ignore[return-value]


def _is_viable_segment(segment: dict[str, Any]) -> bool:
    """Check if a segment has viable data (not an error with zero impressions)."""
    metadata = segment.get("metadata", {}) or {}
    estimation = metadata.get("estimation", {}) or {}

    has_error = bool(estimation.get("error"))
    total_impressions = estimation.get("total_impressions_7d", 0) or 0
    avg_daily = estimation.get("avg_daily_impressions", 0) or 0

    if has_error and total_impressions == 0 and avg_daily == 0:
        return False

    return True


def segment_to_product(
    segment: dict[str, Any],
    *,
    pricing_multiplier: float = 5.0,
    pricing_floor_cpm: float = 0.1,
    pricing_max_suggested_cpm: float = 10.0,
    publisher_domain: str = DEFAULT_PUBLISHER_DOMAIN,
    agent_url: str = DEFAULT_AGENT_URL,
) -> Product | None:
    """Convert a single Catalog segment response dict to an AdCP Product.

    Returns None for segments that are not viable (estimation error + zero impressions).
    """
    if not _is_viable_segment(segment):
        logger.debug("Skipping non-viable segment '%s' (estimation error, zero impressions)", segment.get("name"))
        return None

    segment_id = segment.get("segment_id") or segment.get("name", "unknown")
    name = segment.get("name", "Unknown Segment")
    description = segment.get("description", "")

    metadata = segment.get("metadata", {}) or {}
    estimation = metadata.get("estimation", {}) or {}
    cel_rule = (segment.get("rule", {}) or {}).get("cel_rule", "")

    countries = _extract_countries_from_cel(cel_rule)
    device_types = _extract_device_types_from_cel(cel_rule)

    floor_price = _round_currency(pricing_floor_cpm)

    price_guidance = _build_price_guidance(
        estimation,
        floor_cpm=pricing_floor_cpm,
        multiplier=pricing_multiplier,
        max_suggested_cpm=pricing_max_suggested_cpm,
    )

    cpm = CpmPricingOption(
        pricing_option_id=f"cpm_usd_auction_{segment_id}",
        pricing_model="cpm",
        currency="USD",
        floor_price=floor_price,
        **({"price_guidance": price_guidance} if price_guidance else {}),
    )

    forecast = _build_forecast(estimation)
    ext = _build_ext(segment)

    # Use domains from segment metadata, fall back to config default
    segment_domains = metadata.get("domains", []) or []
    is_ron = any(" RON" in d for d in segment_domains)
    pub_properties = []

    if is_ron:
        pub_properties.append(
            PublisherPropertySelector.model_validate({"selection_type": "all", "publisher_domain": publisher_domain})
        )
        if ext is None:
            ext = {}
        ext["run_of_network"] = True
    else:
        for domain in segment_domains:
            cleaned = _normalize_domain(domain)
            if cleaned:
                pub_properties.append(
                    PublisherPropertySelector.model_validate({"selection_type": "all", "publisher_domain": cleaned})
                )

    if not pub_properties:
        pub_properties.append(
            PublisherPropertySelector.model_validate({"selection_type": "all", "publisher_domain": publisher_domain})
        )

    return Product(
        product_id=segment_id,
        name=name,
        description=description or f"Audience segment: {name}",
        publisher_properties=pub_properties,
        format_ids=_build_format_ids(metadata, agent_url),
        delivery_type="non_guaranteed",
        pricing_options=[PricingOption(root=cpm)],
        delivery_measurement={"provider": "curation"},
        channels=["display"],
        countries=countries or None,
        device_types=device_types or None,
        forecast=forecast,
        ext=ext,
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

    Segments that fail conversion or are non-viable are logged and skipped.
    """
    products: list[Product] = []
    skipped = 0
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
            if product is not None:
                products.append(product)
            else:
                skipped += 1
        except Exception:
            seg_name = seg.get("name", "unknown")
            logger.exception("Failed to convert segment '%s' to Product, skipping", seg_name)
            skipped += 1

    if skipped:
        logger.info("Skipped %d non-viable or failed segments out of %d total", skipped, len(segments))
    return products
