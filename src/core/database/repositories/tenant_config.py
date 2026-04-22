"""Tenant config repository -- tenant-scoped read access for configuration models.

Provides access to PublisherPartner and AdapterConfig for _impl functions
that need tenant-level configuration data without calling get_db_session().

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

beads: salesagent-9y0
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import AdapterConfig, PublisherPartner, Tenant


class TenantConfigRepository:
    """Tenant-scoped read access for configuration models.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def get_tenant(self) -> Tenant | None:
        """Get the tenant record."""
        stmt = select(Tenant).filter_by(tenant_id=self._tenant_id)
        return self._session.scalars(stmt).first()

    def list_publisher_partners(self) -> list[PublisherPartner]:
        """Get all publisher partners for the tenant."""
        stmt = select(PublisherPartner).filter_by(tenant_id=self._tenant_id)
        return list(self._session.scalars(stmt).all())

    def list_publisher_domains(self) -> list[str]:
        """Get sorted list of publisher domain strings for the tenant."""
        partners = self.list_publisher_partners()
        return sorted([p.publisher_domain for p in partners])

    def get_adapter_config(self) -> AdapterConfig | None:
        """Get the adapter configuration for the tenant, or None if not configured."""
        stmt = select(AdapterConfig).filter_by(tenant_id=self._tenant_id)
        return self._session.scalars(stmt).first()

    def seed_ranking_prompt_if_unset(self, prompt: str) -> bool:
        """Write ``prompt`` to the tenant's ``product_ranking_prompt`` column
        if the current value is null or empty. Idempotent.

        Commits the session when a write happens. Callers that want to
        orchestrate a larger transaction should open their own session and
        commit themselves instead of using this helper.

        Args:
            prompt: The prompt text to seed.

        Returns:
            True if the prompt was seeded, False if a non-empty value was
            preserved or the tenant row doesn't exist.
        """
        tenant = self.get_tenant()
        if tenant is None:
            return False
        if tenant.product_ranking_prompt:
            return False
        tenant.product_ranking_prompt = prompt
        self._session.commit()
        return True
