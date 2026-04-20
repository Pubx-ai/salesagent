"""Contract tests for ToolProvider/AdServerAdapter base hooks.

These tests pin behavior that every adapter inherits unless explicitly overridden.
"""

from __future__ import annotations

from src.adapters.base import ToolProvider


class TestOnConfigSavedDefault:
    """on_config_saved is a classmethod hook. The base implementation is a
    no-op so existing adapters (GAM, Kevel, Broadstreet, Mock) need no
    changes; only adapters that need post-save provisioning override it."""

    def test_is_classmethod(self):
        # It must be callable on the class without an instance — adapter
        # configs are saved before any instance is constructed.
        assert callable(ToolProvider.on_config_saved)

    def test_default_returns_none(self):
        result = ToolProvider.on_config_saved("tenant_abc")
        assert result is None

    def test_default_does_not_raise_on_missing_tenant(self):
        # Base hook must tolerate any tenant_id without side effects.
        ToolProvider.on_config_saved("nonexistent_tenant")
        ToolProvider.on_config_saved("")

    def test_subclass_without_override_inherits_noop(self):
        class Dummy(ToolProvider):
            adapter_name = "dummy"

            def create_media_buy(self, *a, **kw): ...
            def check_media_buy_status(self, *a, **kw): ...
            def get_media_buy_delivery(self, *a, **kw): ...
            def update_media_buy(self, *a, **kw): ...
            def update_media_buy_performance_index(self, *a, **kw): ...

        assert Dummy.on_config_saved("any_tenant") is None
