"""Private helpers for building CurationAdapter request payloads.

Extracted from ``adapter.py`` to keep the class file focused on the
CurationAdapter class itself. These functions are pure with respect to
their arguments -- they don't touch adapter state -- so they're safe to
call as free functions.
"""

from __future__ import annotations

from typing import Any

from src.core.schemas import CreateMediaBuyRequest, MediaPackage


def _extract_pricing(package_pricing_info: dict[str, dict] | None) -> dict[str, Any]:
    """Extract pricing from the first package's pricing info.

    Falls back to ``floor_price=None`` when no package pricing is supplied so
    that the Sales service receives an explicit "no floor" signal rather than
    an arbitrary $0.50 CPM that has no relationship to the buyer's intent.
    """
    if not package_pricing_info:
        return {"currency": "USD", "floor_price": None, "fixed_price": None}

    first = next(iter(package_pricing_info.values()), {})
    return {
        "currency": first.get("currency", "USD"),
        "floor_price": first.get("bid_price") or first.get("rate"),
        "fixed_price": first.get("rate") if first.get("is_fixed") else None,
    }


def _ext_as_dict(request: CreateMediaBuyRequest) -> dict[str, Any]:
    """Normalize request.ext to a plain dict.

    ``ext`` may be a dict (from raw dicts in tests) or an adcp
    ``ExtensionObject`` (Pydantic model with extra fields in
    ``model_extra``).  This helper returns a plain dict in both cases.
    """
    ext = getattr(request, "ext", None)
    if ext is None:
        return {}
    if isinstance(ext, dict):
        return ext
    # ExtensionObject — extra fields are stored in model_extra
    extras = getattr(ext, "model_extra", None) or {}
    # Also include explicitly declared fields
    try:
        declared = ext.model_dump()
    except Exception:
        declared = {}
    return {**declared, **extras}


def _dsps_from_ext_dict(ext_dict: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Return the DSP list from an already-normalized ``ext`` dict, or None."""
    dsps = ext_dict.get("dsps")
    if dsps and isinstance(dsps, list):
        return dsps
    return None


def _extract_dsps_from_ext(request: CreateMediaBuyRequest) -> list[dict[str, Any]] | None:
    """Extract DSP configuration from ``request.ext`` , or None if absent."""
    return _dsps_from_ext_dict(_ext_as_dict(request))


def _build_creative_assignments(pkg: MediaPackage, orig_pkg: Any | None) -> list[dict[str, Any]]:
    """Build creative_assignments array for a campaign segment.

    Prefers full creative objects from the original request package (which have
    format_id, agent_url, tag, status, name) over bare creative_ids (which only
    have the ID string).

    AdCP spec: packages can carry ``creatives`` (inline full objects) or
    ``creative_assignments`` (references to library creatives). Both paths
    are mapped to the sales service's creative_assignments array.
    """
    assignments: list[dict[str, Any]] = []

    # Path 1: Full creative objects from request package (has tag, format_id, etc.)
    if orig_pkg:
        creatives = getattr(orig_pkg, "creatives", None)
        if creatives:
            for c in creatives:
                entry: dict[str, Any] = {}
                entry["creative_id"] = getattr(c, "creative_id", None) or ""
                fmt = getattr(c, "format_id", None)
                if fmt:
                    raw_id = fmt.get("id") if isinstance(fmt, dict) else getattr(fmt, "id", str(fmt))
                    entry["format_id"] = str(raw_id) if raw_id else str(fmt)
                name = getattr(c, "name", None)
                if name:
                    entry["name"] = str(name)
                # TODO(pubx): Hack for demo — reads snippet from assets dict
                # because the Pydantic model rejects root-level snippet fields.
                # Proper implementation should use sync_creatives with a curation
                # adapter path that updates the sale record directly.
                # Priority: root "tag" → root "snippet" → assets.snippet
                assets_raw = getattr(c, "assets", None)
                assets_dict: dict[str, Any] = {}
                if assets_raw:
                    assets_dict = (
                        assets_raw
                        if isinstance(assets_raw, dict)
                        else (assets_raw.model_dump(mode="json") if hasattr(assets_raw, "model_dump") else {})
                    )

                tag = getattr(c, "tag", None) or getattr(c, "snippet", None) or assets_dict.get("snippet")
                if tag:
                    entry["tag"] = tag
                snippet_type = getattr(c, "snippet_type", None) or assets_dict.get("snippet_type")
                if snippet_type:
                    entry["snippet_type"] = snippet_type
                status = getattr(c, "status", None)
                if status:
                    entry["status"] = str(status.value) if hasattr(status, "value") else str(status)
                agent_url = None
                if fmt:
                    agent_url = fmt.get("agent_url") if isinstance(fmt, dict) else getattr(fmt, "agent_url", None)
                if agent_url:
                    entry["agent_url"] = str(agent_url)
                assignments.append(entry)
            return assignments

    # Path 2: Bare creative_ids from MediaPackage (ID-only references)
    pkg_creative_ids = getattr(pkg, "creative_ids", None)
    if pkg_creative_ids:
        for cid in pkg_creative_ids:
            assignments.append({"creative_id": cid})

    return assignments
