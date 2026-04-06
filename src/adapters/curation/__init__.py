"""Curation adapter package.

Bridges Prebid Sales Agent AdCP tools to external curation services
(Catalog, Sales, Activation) without storing data in PostgreSQL.
"""

from src.adapters.curation.adapter import CurationAdapter

__all__ = ["CurationAdapter"]
