"""Tests for ValidationService."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.domain.entities import Link, LinkStatus
from src.application.services.validation_service import ValidationService


@pytest.fixture
def validation_service():
    link_repo = AsyncMock()
    return ValidationService(link_repo, timeout=5)


@pytest.mark.asyncio
async def test_validate_non_checkable_link(validation_service):
    """Direct chat links (wa.me/phone) are not checkable."""
    result = await validation_service.validate_link("https://wa.me/967777777")
    assert result.is_valid is True
    assert result.status == LinkStatus.ACTIVE
    assert result.reason == "not_checkable"


@pytest.mark.asyncio
async def test_validate_group_invite_not_checkable_returns_active(validation_service):
    """Non-checkable URLs return ACTIVE without HTTP request."""
    result = await validation_service.validate_link("https://wa.me/967777777")
    assert result.is_valid is True


@pytest.mark.asyncio
async def test_validate_batch_empty(validation_service):
    results = await validation_service.validate_batch([])
    assert results == []
