"""HTTP client for the Curation Catalog service.

Fetches segment definitions from the Catalog REST API.
Catalog is a passive data source -- read-only from this client's perspective.
"""

from __future__ import annotations

import logging
from typing import Any

from src.adapters.curation.http_client import CurationHttpClient

logger = logging.getLogger(__name__)

MAX_PAGES = 100
PAGE_LIMIT = 40
ALLOWED_STATUSES = ("prod",)


class CatalogClient(CurationHttpClient):
    """Synchronous HTTP client for the Curation Catalog service."""

    def fetch_segments(
        self,
        *,
        status: tuple[str, ...] = ALLOWED_STATUSES,
        limit: int = PAGE_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a single page of segments.

        Returns the raw JSON response dict with 'items' and 'next_cursor'.
        """
        params: dict[str, Any] = {"limit": limit, "status": list(status)}
        if cursor:
            params["cursor"] = cursor

        return self._request("GET", "/segments", params=params)

    def fetch_all_segments(
        self,
        *,
        status: tuple[str, ...] = ALLOWED_STATUSES,
    ) -> list[dict[str, Any]]:
        """Fetch all segments across all pages.

        Returns a flat list of segment dicts.
        """
        all_segments: list[dict[str, Any]] = []
        cursor: str | None = None
        page_count = 0

        while page_count < MAX_PAGES:
            data = self.fetch_segments(status=status, cursor=cursor)
            items = data.get("items", [])
            all_segments.extend(items)
            cursor = data.get("next_cursor")
            page_count += 1

            logger.debug(
                "Fetched catalog page %d: %d items, total so far: %d, has_more: %s",
                page_count,
                len(items),
                len(all_segments),
                bool(cursor),
            )

            if not cursor:
                break

        if page_count >= MAX_PAGES and cursor:
            logger.warning(
                "fetch_all_segments hit max page limit (%d), results may be incomplete. Total: %d",
                MAX_PAGES,
                len(all_segments),
            )

        logger.info("Fetched %d segments from catalog in %d pages", len(all_segments), page_count)
        return all_segments

    def fetch_segment_by_id(self, segment_id: str) -> dict[str, Any]:
        """Fetch a single segment by its ID (the segment name, which is the PK)."""
        return self._request("GET", f"/segments/{segment_id}")
