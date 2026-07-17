"""Tests for CategorizationService."""
from src.application.services.categorization_service import CategorizationService
from src.domain.entities import Link, LinkCategory


def test_get_label_group_invite():
    label = CategorizationService.get_label(LinkCategory.GROUP_INVITE)
    assert "مجموعة" in label


def test_get_label_channel():
    label = CategorizationService.get_label(LinkCategory.CHANNEL)
    assert "قناة" in label


def test_get_emoji():
    emoji = CategorizationService.get_emoji(LinkCategory.GROUP_INVITE)
    assert emoji == "👥"


def test_format_link_display():
    link = Link(
        url="https://chat.whatsapp.com/ABC123",
        category=LinkCategory.GROUP_INVITE,
        title="Test Group",
    )
    display = CategorizationService.format_link_display(link)
    assert "👥" in display
    assert "ABC123" in display
    assert "Test Group" in display
