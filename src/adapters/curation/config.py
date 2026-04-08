"""Configuration for curation services integration."""

from __future__ import annotations

import os

from pydantic import Field

from src.adapters.base import BaseConnectionConfig


class CurationConnectionConfig(BaseConnectionConfig):
    """Connection config for Curation adapter.

    Service URLs and pricing guidance parameters are configurable
    via adapter config (stored in AdapterConfig table) or env vars.
    """

    catalog_service_url: str = Field(
        default_factory=lambda: os.getenv("CURATION_CATALOG_URL", "http://localhost:8000"),
        description="Base URL for the Curation Catalog service",
    )
    sales_service_url: str = Field(
        default_factory=lambda: os.getenv("CURATION_SALES_URL", "http://localhost:8001"),
        description="Base URL for the Curation Sales service",
    )
    activation_service_url: str = Field(
        default_factory=lambda: os.getenv("CURATION_ACTIVATION_URL", "http://localhost:8002"),
        description="Base URL for the Curation Activation service",
    )
    pricing_multiplier: float = Field(
        default_factory=lambda: float(os.getenv("CURATION_PRICING_MULTIPLIER", "5")),
        description="Multiplier applied to base CPM from catalog estimation data",
    )
    pricing_floor_cpm: float = Field(
        default_factory=lambda: float(os.getenv("CURATION_PRICING_FLOOR_CPM", "0.1")),
        description="Minimum floor CPM for all segments",
    )
    pricing_max_suggested_cpm: float = Field(
        default_factory=lambda: float(os.getenv("CURATION_PRICING_MAX_SUGGESTED_CPM", "10")),
        description="Cap on the suggested CPM after multiplier",
    )
    mock_activation: bool = Field(
        default_factory=lambda: os.getenv("CURATION_MOCK_ACTIVATION", "false").lower() == "true",
        description="When true, skip real activation service and return mock deal IDs",
    )
    http_timeout_seconds: float = Field(
        default=30.0,
        description="HTTP request timeout for curation service calls",
    )
