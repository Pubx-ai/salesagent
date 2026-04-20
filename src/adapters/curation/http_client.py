"""Shared base HTTP client for curation service communication.

All curation HTTP clients extend this to avoid duplicating
constructor / client factory boilerplate (DRY invariant).
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any

import httpx

from src.core.exceptions import AdCPAdapterError, AdCPNotFoundError

logger = logging.getLogger(__name__)


class CurationHttpClient:
    """Base synchronous HTTP client for curation services.

    Uses httpx.Client (sync) because AdServerAdapter methods are synchronous.
    A single client instance is reused across calls for connection pooling.
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(base_url=self._base_url, timeout=self._timeout)
        return self._client

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        accept_statuses: tuple[int, ...] = (),
    ) -> dict[str, Any]:
        """Execute an HTTP request with standardized error handling.

        Args:
            method: HTTP method (GET, POST, PATCH, etc.)
            path: URL path relative to base_url.
            json: Request body as dict.
            params: Query parameters.
            accept_statuses: Additional status codes to accept beyond 2xx.

        Returns:
            Parsed JSON response dict.

        Raises:
            AdCPNotFoundError: If the service returns 404.
            AdCPAdapterError: For all other HTTP errors.
        """
        client = self._get_client()
        try:
            resp = client.request(method, path, json=json, params=params)
            # 404 always wins over accept_statuses so the caller can branch on
            # AdCPNotFoundError; other accepted statuses (e.g. 409 conflict the
            # caller wants to inspect) skip raise_for_status() AND skip JSON
            # parsing of the body so we don't blow up on a 204/empty payload.
            if resp.status_code == 404:
                raise AdCPNotFoundError(f"Resource not found: {method} {path}")
            is_success = 200 <= resp.status_code < 300
            if not is_success and resp.status_code not in accept_statuses:
                resp.raise_for_status()
            if resp.status_code == 204 or not resp.content:
                return {}
            try:
                return resp.json()
            except (_json.JSONDecodeError, ValueError) as e:
                if is_success or resp.status_code in accept_statuses:
                    raise AdCPAdapterError(
                        f"Curation service returned non-JSON body on {method} {path}: {e}",
                        recovery="transient",
                    ) from e
                raise
        except AdCPNotFoundError:
            raise
        except httpx.HTTPStatusError as e:
            raise AdCPAdapterError(
                f"Curation service error: {e.response.status_code} on {method} {path}",
                recovery="transient",
            ) from e
        except httpx.HTTPError as e:
            raise AdCPAdapterError(
                f"Curation service connection error: {e}",
                recovery="transient",
            ) from e

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()
