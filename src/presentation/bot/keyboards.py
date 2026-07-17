"""Inline keyboard definitions."""
from __future__ import annotations

from telethon import Button

from src.domain.entities import LinkCategory


def main_menu(is_admin: bool = False):
    """Main menu keyboard."""
    buttons = [
        [Button.inline("📤 إرسال رابط", b"submit_link")],
        [Button.inline("🔍 بحث", b"search"),
         Button.inline("📊 إحصائيات", b"stats")],
        [Button.inline("📋 آخر الروابط", b"recent_links")],
        [Button.inline("❓ المساعدة", b"help")],
    ]
    if is_admin:
        buttons.append([Button.inline("⚙️ لوحة الإدارة", b"admin_panel")])
    return buttons


def admin_panel():
    """Admin panel keyboard."""
    return [
        [Button.inline("📊 إحصائيات مفصلة", b"admin_stats"),
         Button.inline("👥 المستخدمون", b"admin_users")],
        [Button.inline("🔄 تحقق من الروابط", b"admin_validate"),
         Button.inline("💾 نسخ احتياطي", b"admin_backup")],
        [Button.inline("📤 تصدير", b"admin_export"),
         Button.inline("📥 استيراد", b"admin_import")],
        [Button.inline("🔙 القائمة الرئيسية", b"main_menu")],
    ]


def category_filter():
    """Category filter keyboard."""
    buttons = []
    categories = [
        (LinkCategory.GROUP_INVITE, "👥 المجموعات"),
        (LinkCategory.CHANNEL, "📢 القنوات"),
        (LinkCategory.DIRECT_CHAT, "💬 الدردشات"),
    ]
    for cat, label in categories:
        buttons.append([Button.inline(label, f"cat_{cat.value}")])
    buttons.append([Button.inline("🔙 رجوع", b"main_menu")])
    return buttons


def back_to_main():
    """Back to main menu button."""
    return [[Button.inline("🔙 القائمة الرئيسية", b"main_menu")]]
