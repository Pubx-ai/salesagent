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

ACTION_TO_ADCP_STATUS: dict[str, str] = {
    "pause": "paused",
    "resume": "active",
    "cancel": "completed",
}
