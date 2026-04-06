"""HTTP client for the Curation Activation service.

Triggers SSP deal creation (e.g. Magnite PMP deals) from sale records.
"""

from __future__ import annotations

import logging
from typing import Any

from src.adapters.curation.http_client import CurationHttpClient

logger = logging.getLogger(__name__)


class ActivationClient(CurationHttpClient):
    """Synchronous HTTP client for the Curation Activation service."""

    def create_activation(self, activation_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new activation for a sale.

        The activation service will create PMP deals on the SSP
        and return activation results (possibly partial success via 207).

        Returns:
            ActivationCreateResult dict with 'activations' and optional 'errors'.
        """
        return self._request("POST", "/activations", json=activation_data, accept_statuses=(201, 207))

    def get_activations_for_sale(self, sale_id: str) -> dict[str, Any]:
        """List activations filtered by sale_id."""
        return self._request("GET", "/activations", params={"sale_id": sale_id})

    def get_activation_by_deal_id(self, deal_id: str) -> dict[str, Any]:
        """Look up an activation by its SSP deal ID."""
        return self._request("GET", f"/deals/{deal_id}")
