"""Datetime helpers shared across the curation adapter package.

Lives next to the adapter so both ``adapter.py`` (strict) and
``segment_converter.py`` (best-effort on optional fields) can route through
one parser with a single ``replace("Z", "+00:00")`` workaround — Python
3.10's ``datetime.fromisoformat`` rejects the trailing ``Z`` literal.
"""

from __future__ import annotations

from datetime import datetime


def parse_iso(value: str | None, *, safe: bool = False) -> datetime | None:
    """Parse an ISO8601 string into a datetime, or return ``None``.

    Handles both ``2026-04-09T12:34:56Z`` and ``2026-04-09T12:34:56+00:00``.

    Args:
        value: The ISO8601 string, or ``None``.
        safe: When ``True``, malformed strings yield ``None`` instead of
            raising. The strict default matches the contract historically
            used by ``adapter.py`` for sale serialization; ``safe=True``
            matches ``segment_converter`` where optional estimation
            timestamps shouldn't crash product conversion.

    Returns:
        The parsed ``datetime``, or ``None`` if ``value`` is falsy (or
        malformed with ``safe=True``).

    Raises:
        ValueError | AttributeError: On malformed input when ``safe=False``.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        if safe:
            return None
        raise
