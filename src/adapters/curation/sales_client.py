"""HTTP client for the Curation Sales service.

Manages sale records -- the persistent store for media buy / deal records.
"""

from __future__ import annotations

import logging
from typing import Any

from src.adapters.curation.http_client import CurationHttpClient

logger = logging.getLogger(__name__)


class SalesClient(CurationHttpClient):
    """Synchronous HTTP client for the Curation Sales service."""

    def create_sale(self, sale_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new sale record.

        Returns:
            SaleCreateResponse dict with sale_id, status, created_at.
        """
        return self._request("POST", "/api/v1/sales", json=sale_data)

    def get_sale(self, sale_id: str) -> dict[str, Any]:
        """Fetch a sale by its ID.

        Returns:
            Full SaleResponse dict.
        """
        return self._request("GET", f"/api/v1/sales/{sale_id}")

    def update_sale(self, sale_id: str, update_data: dict[str, Any]) -> dict[str, Any]:
        """Patch a sale record.

        Returns:
            Updated sale response dict.
        """
        return self._request("PATCH", f"/api/v1/sales/{sale_id}", json=update_data)

    def list_sales(
        self,
        *,
        status: str | None = None,
        statuses: list[str] | None = None,
        sale_ids: list[str] | None = None,
        buyer_refs: list[str] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List sales with optional filters. Returns a single page.

        Args:
            status: Legacy single-value status filter.
            statuses: Multi-value status filter (wins over ``status`` if both set).
            sale_ids: Filter to specific sale IDs (primary-key lookup).
            buyer_refs: Filter to specific buyer references.
            limit: Max items per page (sales service max is 100).
            cursor: Opaque pagination cursor from a prior response.

        Returns:
            dict with keys ``items`` (list of sale dicts) and ``next_cursor``
            (str or None when no more pages).
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        if statuses:
            params["statuses"] = statuses
        if sale_ids:
            params["sale_ids"] = sale_ids
        if buyer_refs:
            params["buyer_refs"] = buyer_refs
        return self._request("GET", "/api/v1/sales", params=params)
