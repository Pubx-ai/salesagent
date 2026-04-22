"""Curation adapter admin endpoints.

Curation-specific admin endpoints live here instead of
``src/admin/blueprints/adapters.py`` so the general adapters blueprint
stays close to upstream prebid/salesagent shape. Rebase of the general
blueprint becomes conflict-free; curation edits happen only in this file.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from src.adapters.curation.http_client import CurationHttpClient
from src.admin.utils import require_tenant_access
from src.core.security.url_validator import check_url_ssrf

logger = logging.getLogger(__name__)

curation_bp = Blueprint("curation", __name__)


@curation_bp.route("/api/tenant/<tenant_id>/adapters/curation/test-connection", methods=["POST"])
@require_tenant_access()
def test_curation_connection(tenant_id, **kwargs):
    """Test connectivity to all three curation services."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        catalog_url = data.get("catalog_service_url")
        sales_url = data.get("sales_service_url")
        activation_url = data.get("activation_service_url")

        if not catalog_url:
            return jsonify({"success": False, "error": "catalog_service_url is required"}), 400

        for url, label in (
            (catalog_url, "catalog_service_url"),
            (sales_url, "sales_service_url"),
            (activation_url, "activation_service_url"),
        ):
            if not url:
                continue
            is_safe, ssrf_error = check_url_ssrf(url)
            if not is_safe:
                err = f"{label}: {ssrf_error}"
                logger.warning(
                    "Curation connection test rejected for tenant_id=%s: %s",
                    tenant_id,
                    err,
                )
                return jsonify({"success": False, "error": err}), 400

        results: dict = {}
        segment_count: int | None = None

        # All three probes share the same "status < 500 = reachable" criterion,
        # which is what admin connectivity tests actually verify. We route
        # them through CurationHttpClient so the admin probe and the runtime
        # adapter share one connection pool and one transport implementation.
        catalog_status, catalog_body = CurationHttpClient(catalog_url, timeout=10).probe(
            "/segments", params={"limit": 1}
        )
        results["catalog"] = catalog_status
        if catalog_status == "ok" and isinstance(catalog_body, dict):
            items = catalog_body.get("items")
            if isinstance(items, list):
                segment_count = len(items)

        if sales_url:
            sales_status, _ = CurationHttpClient(sales_url, timeout=10).probe("/api/v1/sales", params={"limit": 1})
            results["sales"] = sales_status

        if activation_url:
            activation_status, _ = CurationHttpClient(activation_url, timeout=10).probe("/health")
            results["activation"] = activation_status

        all_ok = all(v == "ok" for v in results.values())
        if not all_ok:
            logger.info(
                "Curation test-connection incomplete for tenant_id=%s: %s",
                tenant_id,
                results,
            )
        return jsonify(
            {
                "success": all_ok,
                "services": results,
                "segment_count": segment_count,
                **({"error": "Some services unreachable"} if not all_ok else {}),
            }
        )

    except Exception as e:
        logger.error(f"Curation connection test failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
