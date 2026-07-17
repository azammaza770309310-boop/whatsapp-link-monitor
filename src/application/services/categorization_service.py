"""Categorization service for WhatsApp links."""
from __future__ import annotations

from typing import Dict

from src.domain.entities import Link, LinkCategory


class CategorizationService:
    """Service for categorizing WhatsApp links."""

    CATEGORY_LABELS: Dict[LinkCategory, str] = {
        LinkCategory.GROUP_INVITE: "👥 دعوة مجموعة",
        LinkCategory.CHANNEL: "📢 قناة",
        LinkCategory.DIRECT_CHAT: "💬 دردشة مباشرة",
        LinkCategory.MESSAGE_LINK: "📩 رابط رسالة",
        LinkCategory.API_SEND: "📤 إرسال API",
        LinkCategory.QR_CODE: "📱 رمز QR",
        LinkCategory.SHORT_LINK: "🔗 رابط مختصر",
        LinkCategory.OTHER: "❓ أخرى",
    }

    CATEGORY_EMOJI: Dict[LinkCategory, str] = {
        LinkCategory.GROUP_INVITE: "👥",
        LinkCategory.CHANNEL: "📢",
        LinkCategory.DIRECT_CHAT: "💬",
        LinkCategory.MESSAGE_LINK: "📩",
        LinkCategory.API_SEND: "📤",
        LinkCategory.QR_CODE: "📱",
        LinkCategory.SHORT_LINK: "🔗",
        LinkCategory.OTHER: "❓",
    }

    @classmethod
    def get_label(cls, category: LinkCategory) -> str:
        """Get human-readable label for a category."""
        return cls.CATEGORY_LABELS.get(category, str(category.value))

    @classmethod
    def get_emoji(cls, category: LinkCategory) -> str:
        """Get emoji for a category."""
        return cls.CATEGORY_EMOJI.get(category, "❓")

    @classmethod
    def format_link_display(cls, link: Link) -> str:
        """Format a link for display in Telegram."""
        emoji = cls.get_emoji(link.category)
        label = cls.get_label(link.category)
        title = link.title or link.url
        return f"{emoji} {label}\n   🔗 {link.url}\n   📝 {title}"
