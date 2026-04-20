"""Unit tests for media_buy_create helper functions.

Tests the helper functions used in media buy creation, particularly
format specification retrieval, creative validation, status determination,
and URL extraction.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import AdCPValidationError
from src.core.tools.media_buy_create import _get_format_spec_sync


def _make_curation_adapter():
    """Build a minimal CurationAdapter for exercising the inline-creative validator."""
    from src.adapters.curation.adapter import CurationAdapter

    principal = MagicMock()
    principal.principal_id = "test-principal"
    principal.get_adapter_id = MagicMock(return_value="curation-id")

    return CurationAdapter(
        {
            "catalog_service_url": "http://catalog:8000",
            "sales_service_url": "http://sales:8001",
            "activation_service_url": "http://activation:8002",
        },
        principal,
        dry_run=False,
        tenant_id="test-tenant",
    )


def _validate_inline_creatives_for_external_adapter(req):
    """Delegate to CurationAdapter._validate_inline_creatives.

    The function of that name used to live in src/core/tools/media_buy_create.py
    but was hoisted onto the adapter as part of the curation isolation refactor.
    The existing test cases still exercise the same invariants — we just go
    through the adapter now.
    """
    _make_curation_adapter()._validate_inline_creatives(req)


class TestGetFormatSpecSync:
    """Test synchronous format specification retrieval."""

    def test_successful_format_retrieval(self):
        """Test successful format spec retrieval with mocked registry."""
        # Create mock format spec
        mock_format_spec = MagicMock()
        mock_format_spec.format_id.id = "display_300x250_image"
        mock_format_spec.name = "Medium Rectangle - Image"

        # Mock the registry to avoid HTTP calls
        mock_registry = MagicMock()
        mock_registry.get_format = AsyncMock(return_value=mock_format_spec)

        with patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry):
            format_spec = _get_format_spec_sync("https://creative.adcontextprotocol.org", "display_300x250_image")
            assert format_spec is not None
            assert format_spec.format_id.id == "display_300x250_image"
            assert format_spec.name == "Medium Rectangle - Image"

    def test_unknown_format_returns_none(self):
        """Test that unknown format returns None."""
        # Mock registry returning None for unknown format
        mock_registry = MagicMock()
        mock_registry.get_format = AsyncMock(return_value=None)

        with patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry):
            format_spec = _get_format_spec_sync("https://creative.adcontextprotocol.org", "unknown_format_xyz")
            assert format_spec is None

    def test_exception_returns_none(self):
        """Test that exceptions are caught and None is returned."""
        mock_registry = MagicMock()
        mock_registry.get_format = AsyncMock(side_effect=Exception("Network error"))

        with patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry):
            format_spec = _get_format_spec_sync("https://creative.adcontextprotocol.org", "display_300x250_image")
            assert format_spec is None


def _req(packages):
    """Minimal stand-in for CreateMediaBuyRequest — the validator only reads
    ``req.packages`` and each package's attributes, so we don't need the full
    Pydantic schema here."""
    return SimpleNamespace(packages=packages)


class TestValidateInlineCreativesForExternalAdapter:
    """Fail-fast check for inline creatives on the curation / adapter-managed path.

    The Postgres path runs creatives through process_and_upload_package_creatives
    which enforces this shape; the curation path skips that, so this helper
    is the only validation between the buyer and the external Sales service.
    """

    def test_passes_when_no_packages(self):
        _validate_inline_creatives_for_external_adapter(_req(None))
        _validate_inline_creatives_for_external_adapter(_req([]))

    def test_passes_when_no_inline_creatives(self):
        pkg = SimpleNamespace(creatives=None, creative_ids=["cid-1"])
        _validate_inline_creatives_for_external_adapter(_req([pkg]))

    def test_passes_with_library_reference(self):
        creative = SimpleNamespace(creative_id="cid-1", format_id={"id": "display_300x250"})
        pkg = SimpleNamespace(creatives=[creative], creative_ids=None)
        _validate_inline_creatives_for_external_adapter(_req([pkg]))

    def test_passes_with_inline_tag(self):
        creative = SimpleNamespace(
            creative_id=None,
            format_id={"id": "display_300x250"},
            tag="<img src='ad.png' />",
            snippet=None,
            assets=None,
        )
        pkg = SimpleNamespace(creatives=[creative], creative_ids=None)
        _validate_inline_creatives_for_external_adapter(_req([pkg]))

    def test_passes_with_snippet_in_assets(self):
        creative = SimpleNamespace(
            creative_id=None,
            format_id={"id": "display_300x250"},
            tag=None,
            snippet=None,
            assets={"snippet": "<div>ad</div>"},
        )
        pkg = SimpleNamespace(creatives=[creative], creative_ids=None)
        _validate_inline_creatives_for_external_adapter(_req([pkg]))

    def test_rejects_missing_format_id(self):
        creative = SimpleNamespace(creative_id="cid-1", format_id=None)
        pkg = SimpleNamespace(creatives=[creative], creative_ids=None)

        with pytest.raises(AdCPValidationError) as excinfo:
            _validate_inline_creatives_for_external_adapter(_req([pkg]))

        assert "packages[0].creatives[0].format_id" in str(excinfo.value)

    def test_rejects_no_id_and_no_content(self):
        creative = SimpleNamespace(
            creative_id=None,
            format_id={"id": "display_300x250"},
            tag=None,
            snippet=None,
            assets=None,
        )
        pkg = SimpleNamespace(creatives=[creative], creative_ids=None)

        with pytest.raises(AdCPValidationError) as excinfo:
            _validate_inline_creatives_for_external_adapter(_req([pkg]))

        assert "packages[0].creatives[0]" in str(excinfo.value)
        assert "creative_id" in str(excinfo.value)

    def test_points_at_correct_coordinate_on_second_package(self):
        good = SimpleNamespace(
            creative_id="cid-ok",
            format_id={"id": "display_300x250"},
        )
        bad = SimpleNamespace(creative_id=None, format_id=None)
        pkg0 = SimpleNamespace(creatives=[good], creative_ids=None)
        pkg1 = SimpleNamespace(creatives=[good, bad], creative_ids=None)

        with pytest.raises(AdCPValidationError) as excinfo:
            _validate_inline_creatives_for_external_adapter(_req([pkg0, pkg1]))

        assert "packages[1].creatives[1]" in str(excinfo.value)
