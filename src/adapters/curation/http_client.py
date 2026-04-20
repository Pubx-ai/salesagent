"""Shared base HTTP client for curation service communication.

All curation HTTP clients extend this to avoid duplicating
constructor / client factory boilerplate (DRY invariant).
"""

from __future__ import annotations

import json as _json
import logging
import threading
from typing import Any

import httpx

from src.core.exceptions import AdCPAdapterError, AdCPNotFoundError

logger = logging.getLogger(__name__)

# Process-wide pool of httpx.Client instances keyed by (base_url, timeout).
# Each CurationAdapter instantiation used to build three fresh httpx.Clients,
# defeating connection pooling and eating TCP/TLS handshake cost on every
# request. We now share one client per (base_url, timeout) across adapter
# instances; httpx.Client is thread-safe for concurrent requests, so reusing
# it is strictly better for throughput.
_CLIENT_CACHE: dict[tuple[str, float], httpx.Client] = {}
_CLIENT_CACHE_LOCK = threading.Lock()


def _get_or_create_client(base_url: str, timeout: float) -> httpx.Client:
    key = (base_url, timeout)
    existing = _CLIENT_CACHE.get(key)
    if existing is not None and not existing.is_closed:
        return existing
    with _CLIENT_CACHE_LOCK:
        existing = _CLIENT_CACHE.get(key)
        if existing is not None and not existing.is_closed:
            return existing
        client = httpx.Client(base_url=base_url, timeout=timeout)
        _CLIENT_CACHE[key] = client
        return client


def close_all_cached_clients() -> None:
    """Close every cached httpx.Client. Intended for test teardown / shutdown hooks."""
    with _CLIENT_CACHE_LOCK:
        for client in list(_CLIENT_CACHE.values()):
            if not client.is_closed:
                client.close()
        _CLIENT_CACHE.clear()


class CurationHttpClient:
    """Base synchronous HTTP client for curation services.

    Uses a shared, process-wide ``httpx.Client`` per ``(base_url, timeout)``
    so connection pooling works across adapter instantiations.
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _get_client(self) -> httpx.Client:
        return _get_or_create_client(self._base_url, self._timeout)

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
        """No-op: the underlying httpx.Client is process-wide shared.

        Closing it here would break the next adapter instance using the same
        ``(base_url, timeout)``. Use ``close_all_cached_clients()`` at process
        shutdown / test teardown instead.
        """
