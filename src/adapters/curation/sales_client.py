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
