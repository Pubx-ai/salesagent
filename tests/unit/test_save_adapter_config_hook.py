"""Verify save_adapter_config dispatches on_config_saved to the adapter class.

This is the wiring that lets CurationAdapter.on_config_saved seed the default
ranking prompt when an operator saves a curation adapter config. The test uses
a dummy adapter registered temporarily so we don't depend on real adapter
side effects.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from flask import Flask


def _unwrap(fn):
    """Strip decorator wrappers (require_tenant_access, log_admin_action)."""
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class TestSaveAdapterConfigDispatchesHook:
    def test_dispatches_on_config_saved_after_commit(self):
        from src.adapters import ADAPTER_REGISTRY
        from src.adapters.base import ToolProvider

        # Capture the tenant_id passed to on_config_saved
        observed: dict = {"tenant_id": None}

        class _Probe(ToolProvider):
            adapter_name = "_probe"

            def create_media_buy(self, *a, **kw): ...
            def check_media_buy_status(self, *a, **kw): ...
            def get_media_buy_delivery(self, *a, **kw): ...
            def update_media_buy(self, *a, **kw): ...
            def update_media_buy_performance_index(self, *a, **kw): ...

            @classmethod
            def on_config_saved(cls, tenant_id: str) -> None:
                observed["tenant_id"] = tenant_id

        # Register the probe in the registry for the duration of this test
        original = ADAPTER_REGISTRY.get("_probe")
        ADAPTER_REGISTRY["_probe"] = _Probe
        try:
            from src.admin.blueprints.adapters import save_adapter_config

            inner = _unwrap(save_adapter_config)

            # Stub the DB session layer so we only exercise the hook dispatch
            with patch("src.admin.blueprints.adapters.get_db_session") as mock_db:
                fake_session = MagicMock()
                fake_session.scalars.return_value.first.return_value = None
                mock_db.return_value.__enter__.return_value = fake_session

                # Build a minimal Flask app + request context to call the view
                app = Flask(__name__)
                with app.test_request_context(
                    "/api/tenant/t1/adapter-config",
                    method="POST",
                    json={"adapter_type": "_probe", "config": {}},
                ):
                    inner(tenant_id="t1")
        finally:
            if original is None:
                ADAPTER_REGISTRY.pop("_probe", None)
            else:
                ADAPTER_REGISTRY["_probe"] = original

        assert observed["tenant_id"] == "t1"

    def test_unknown_adapter_type_does_not_crash_hook_dispatch(self):
        """If the adapter_type isn't in the registry, save still completes."""
        from src.admin.blueprints.adapters import save_adapter_config

        inner = _unwrap(save_adapter_config)

        with patch("src.admin.blueprints.adapters.get_db_session") as mock_db:
            fake_session = MagicMock()
            fake_session.scalars.return_value.first.return_value = None
            mock_db.return_value.__enter__.return_value = fake_session

            app = Flask(__name__)
            with app.test_request_context(
                "/api/tenant/t1/adapter-config",
                method="POST",
                json={"adapter_type": "nonexistent_adapter", "config": {}},
            ):
                # Should not raise — missing adapter class is tolerated
                inner(tenant_id="t1")
