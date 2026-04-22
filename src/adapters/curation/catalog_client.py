"""HTTP client for the Curation Catalog service.

Fetches segment definitions from the Catalog REST API.
Catalog is a passive data source -- read-only from this client's perspective.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from src.adapters.curation.http_client import CurationHttpClient

logger = logging.getLogger(__name__)

MAX_PAGES = 100
PAGE_LIMIT = 40
ALLOWED_STATUSES = ("prod",)


class CatalogClient(CurationHttpClient):
    """Synchronous HTTP client for the Curation Catalog service.

    Maintains a per-instance ``_segment_cache`` populated by
    :meth:`fetch_all_segments`. Subsequent :meth:`fetch_segment_by_id` calls
    hit the cache first, which collapses the N+1 GET pattern in
    ``CurationAdapter.create_media_buy`` when the caller opportunistically
    warms the cache with a single bulk page walk.
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        super().__init__(base_url, timeout)
        self._segment_cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _segment_cache_keys(segment: dict[str, Any]) -> list[str]:
        """Return every identifier under which ``segment`` may be looked up.

        ``CurationAdapter`` derives a product's ``product_id`` from
        ``segment_id`` if present, otherwise ``name``; either can show up
        later in :meth:`fetch_segment_by_id`, so we cache under both.
        """
        keys: list[str] = []
        seg_id = segment.get("segment_id")
        if isinstance(seg_id, str) and seg_id:
            keys.append(seg_id)
        name = segment.get("name")
        if isinstance(name, str) and name and name not in keys:
            keys.append(name)
        return keys

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

        Returns a flat list of segment dicts and seeds ``_segment_cache`` so a
        subsequent :meth:`fetch_segment_by_id` is served without a network
        round-trip.
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

        for seg in all_segments:
            for key in self._segment_cache_keys(seg):
                self._segment_cache[key] = seg

        logger.info("Fetched %d segments from catalog in %d pages", len(all_segments), page_count)
        return all_segments

    def fetch_segment_by_id(self, segment_id: str) -> dict[str, Any]:
        """Fetch a single segment by its ID.

        Consults ``_segment_cache`` first; on miss, issues a GET and caches
        the response. ``segment_id`` is URL-encoded so names containing
        slashes, spaces, or other reserved characters resolve to the correct
        path instead of accidentally hitting a different endpoint or
        returning 404.
        """
        cached = self._segment_cache.get(segment_id)
        if cached is not None:
            return cached

        encoded = quote(segment_id, safe="")
        result = self._request("GET", f"/segments/{encoded}")

        self._segment_cache[segment_id] = result
        for key in self._segment_cache_keys(result):
            self._segment_cache.setdefault(key, result)

        return result
