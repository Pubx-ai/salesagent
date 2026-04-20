"""Status mapping tables between curation-service sale statuses and AdCP statuses.

Lives outside ``adapter.py`` so generic tools (``media_buy_list``) can import the
AdCP→curation mapping without dragging in the full ``CurationAdapter`` class
and violating the transport-agnostic boundary for curation-owned knowledge.
"""

from __future__ import annotations

SALE_STATUS_TO_ADCP: dict[str, str] = {
    "pending_approval": "pending_activation",
    "pending_activation": "pending_activation",
    "active": "active",
    "paused": "paused",
    "completed": "completed",
    "failed": "failed",
    "rejected": "failed",
    "canceled": "completed",
}

# Inverse of SALE_STATUS_TO_ADCP (lossy — multiple curation statuses per AdCP status).
ADCP_STATUS_TO_SALE_STATUSES: dict[str, list[str]] = {
    "pending_activation": ["pending_approval", "pending_activation"],
    "active": ["active"],
    "paused": ["paused"],
    "completed": ["completed", "canceled"],
    "failed": ["failed", "rejected"],
}

# AdCP update_media_buy action strings → resulting AdCP status. Matches the
# action vocabulary used by the media_buy_update tool and every other adapter
# in this repo (GAM, Broadstreet, Kevel, Mock) — not the short "pause"/"resume"
# names that were never emitted by the tool layer.
ACTION_TO_ADCP_STATUS: dict[str, str] = {
    "pause_media_buy": "paused",
    "resume_media_buy": "active",
    "pause_package": "paused",
    "resume_package": "active",
}

# Subset of ACTION_TO_ADCP_STATUS that maps to a curation-service sale status
# update; "update" (budget-only) and package-scoped actions don't change
# sale-level status.
ACTION_TO_SALE_STATUS: dict[str, str] = {
    "pause_media_buy": "paused",
    "resume_media_buy": "active",
}
