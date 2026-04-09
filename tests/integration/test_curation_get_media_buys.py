"""End-to-end integration tests for get_media_buys on curation tenants.

Mocks the curation sales service at the HTTP level via httpx.MockTransport
(built into httpx — no extra dependency). Exercises the full stack:
    _get_media_buys_impl → CurationAdapter → SalesClient → HTTP
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest

# Capture the real httpx.Client at import time so _make_mock_client_factory
# can call it without being intercepted by the patch that replaces
# src.adapters.curation.http_client.httpx.Client during tests.
_REAL_HTTPX_CLIENT = httpx.Client


SALES_BASE = "http://sales.test"


def _sale_payload(sale_id: str, status: str = "active") -> dict:
    return {
        "sale_id": sale_id,
        "buyer_ref": "buyer-1",
        "buyer_campaign_ref": None,
        "segments": [{"segment_id": f"seg-{sale_id}"}],
        "activations": [],
        "pricing": {
            "pricing_model": "cpm",
            "currency": "USD",
            "floor_price": 2.5,
        },
        "deal_type": "curated",
        "platform_id": "magnite",
        "dsps": [],
        "ad_format_types": None,
        "start_time": "2026-04-01T00:00:00Z",
        "end_time": "2026-04-30T23:59:59Z",
        "brand": None,
        "budget": 1000.0,
        "status": status,
        "created_at": "2026-03-29T10:00:00Z",
        "updated_at": "2026-03-30T15:00:00Z",
    }


def _make_mock_client_factory(handler: Callable[[httpx.Request], httpx.Response]):
    """Build a callable that replaces httpx.Client construction with one that
    uses a MockTransport wired to the given handler.

    Uses _REAL_HTTPX_CLIENT (captured at module import) so this factory is
    not re-intercepted by the httpx.Client patch, which would cause infinite
    recursion.
    """
    transport = httpx.MockTransport(handler)

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return _REAL_HTTPX_CLIENT(*args, **kwargs)

    return _factory


@pytest.mark.requires_db
class TestCurationGetMediaBuysEndToEnd:
    """Full-stack integration: _impl → adapter → client → (mocked) HTTP."""

    def _make_identity(self):
        from tests.factories import PrincipalFactory

        identity = PrincipalFactory.make_identity(
            tenant_id="t-curation",
            principal_id="p1",
        )
        identity.tenant["adapter_type"] = "curation"
        return identity

    def _build_adapter(self, cap: int = 500):
        from src.adapters.curation.adapter import CurationAdapter
        from src.core.schemas import Principal

        p = Principal(principal_id="p1", name="p", platform_mappings={})
        return CurationAdapter(
            config={
                "sales_service_url": SALES_BASE,
                "catalog_service_url": "http://catalog.test",
                "activation_service_url": "http://activation.test",
                "max_media_buys_per_list": cap,
            },
            principal=p,
            tenant_id="t-curation",
        )

    def test_list_happy_path_single_page(self, integration_db):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(
                200,
                json={
                    "items": [_sale_payload("s1"), _sale_payload("s2")],
                    "next_cursor": None,
                },
            )

        identity = self._make_identity()

        with patch(
            "src.adapters.curation.http_client.httpx.Client",
            side_effect=_make_mock_client_factory(handler),
        ):
            adapter = self._build_adapter()

            with (
                patch(
                    "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                    return_value=True,
                ),
                patch(
                    "src.core.tools.media_buy_list.get_adapter",
                    return_value=adapter,
                ),
                patch(
                    "src.core.tools.media_buy_list.get_principal_object",
                    return_value=type("P", (), {"principal_id": "p1"})(),
                ),
            ):
                response = _get_media_buys_impl(
                    req=GetMediaBuysRequest(),
                    identity=identity,
                )

        assert len(response.media_buys) == 2
        assert response.media_buys[0].media_buy_id == "s1"
        assert response.errors is None
        assert len(captured_requests) == 1
        assert "/api/v1/sales" in str(captured_requests[0].url)

    def test_list_pagination_follows_cursor(self, integration_db):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        captured_requests: list[httpx.Request] = []
        responses_iter = iter(
            [
                httpx.Response(
                    200,
                    json={
                        "items": [_sale_payload("s1"), _sale_payload("s2")],
                        "next_cursor": "c1",
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "items": [_sale_payload("s3")],
                        "next_cursor": None,
                    },
                ),
            ]
        )

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return next(responses_iter)

        identity = self._make_identity()

        with patch(
            "src.adapters.curation.http_client.httpx.Client",
            side_effect=_make_mock_client_factory(handler),
        ):
            adapter = self._build_adapter()

            with (
                patch(
                    "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                    return_value=True,
                ),
                patch(
                    "src.core.tools.media_buy_list.get_adapter",
                    return_value=adapter,
                ),
                patch(
                    "src.core.tools.media_buy_list.get_principal_object",
                    return_value=type("P", (), {"principal_id": "p1"})(),
                ),
            ):
                response = _get_media_buys_impl(
                    req=GetMediaBuysRequest(),
                    identity=identity,
                )

        assert len(response.media_buys) == 3
        assert response.errors is None
        assert len(captured_requests) == 2
        assert "cursor=c1" in str(captured_requests[1].url)

    def test_list_truncation_surfaces_errors_entry(self, integration_db):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [_sale_payload(f"s{i}") for i in range(2)],
                    "next_cursor": "more",
                },
            )

        identity = self._make_identity()

        with patch(
            "src.adapters.curation.http_client.httpx.Client",
            side_effect=_make_mock_client_factory(handler),
        ):
            adapter = self._build_adapter(cap=2)

            with (
                patch(
                    "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                    return_value=True,
                ),
                patch(
                    "src.core.tools.media_buy_list.get_adapter",
                    return_value=adapter,
                ),
                patch(
                    "src.core.tools.media_buy_list.get_principal_object",
                    return_value=type("P", (), {"principal_id": "p1"})(),
                ),
            ):
                response = _get_media_buys_impl(
                    req=GetMediaBuysRequest(),
                    identity=identity,
                )

        assert len(response.media_buys) == 2
        assert response.errors is not None
        assert response.errors[0]["code"] == "results_truncated"

    def test_list_sale_ids_filter_passes_through(self, integration_db):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl

        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(
                200,
                json={
                    "items": [_sale_payload("s1"), _sale_payload("s3")],
                    "next_cursor": None,
                },
            )

        identity = self._make_identity()

        with patch(
            "src.adapters.curation.http_client.httpx.Client",
            side_effect=_make_mock_client_factory(handler),
        ):
            adapter = self._build_adapter()

            with (
                patch(
                    "src.core.tools.media_buy_list.adapter_manages_own_persistence",
                    return_value=True,
                ),
                patch(
                    "src.core.tools.media_buy_list.get_adapter",
                    return_value=adapter,
                ),
                patch(
                    "src.core.tools.media_buy_list.get_principal_object",
                    return_value=type("P", (), {"principal_id": "p1"})(),
                ),
            ):
                response = _get_media_buys_impl(
                    req=GetMediaBuysRequest(media_buy_ids=["s1", "s3"]),
                    identity=identity,
                )

        assert len(response.media_buys) == 2
        assert len(captured_requests) == 1
        url_str = str(captured_requests[0].url)
        assert "sale_ids=s1" in url_str
        assert "sale_ids=s3" in url_str
