"""Shared test fixtures for curation adapter and integration tests."""

from __future__ import annotations

# Common deal-type sale fields used across unit and integration tests.
_DEAL_SALE_DEFAULTS: dict[str, object] = {
    "deal_type": "curated",
    "platform_id": "magnite",
    "dsps": [],
    "ad_format_types": None,
    "start_time": "2026-04-01T00:00:00Z",
    "end_time": "2026-04-30T23:59:59Z",
    "brand": None,
    "created_at": "2026-03-29T10:00:00Z",
    "updated_at": "2026-03-30T15:00:00Z",
}


def make_deal_sale(
    sale_id: str,
    *,
    status: str = "active",
    buyer_ref: str = "buyer-1",
    budget: float = 1000.0,
    floor_price: float = 2.50,
    fixed_price: float | None = None,
    segments: list[dict] | None = None,
    **overrides: object,
) -> dict:
    """Build a deal-type sale dict for tests.

    Both unit and integration tests use this so the fixture shape stays
    consistent and doesn't trigger the code-duplication ratchet.
    """
    return {
        **_DEAL_SALE_DEFAULTS,
        "sale_id": sale_id,
        "sale_type": "deal",
        "buyer_ref": buyer_ref,
        "buyer_campaign_ref": None,
        "segments": segments if segments is not None else [{"segment_id": f"seg-{sale_id}"}],
        "activations": [],
        "pricing": {
            "pricing_model": "cpm",
            "currency": "USD",
            "floor_price": floor_price,
            "fixed_price": fixed_price,
        },
        "budget": budget,
        "status": status,
        **overrides,
    }
