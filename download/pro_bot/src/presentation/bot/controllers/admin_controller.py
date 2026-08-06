"""Admin controller - handles admin operations."""
from __future__ import annotations

import json
import os
from typing import Optional

from telethon import Button, events

from src.application.services.backup_service import BackupService
from src.application.services.link_service import LinkService
from src.application.services.validation_service import ValidationService
from src.core.exceptions import AuthorizationError
from src.core.logging import get_logger
from src.domain.entities import User
from src.domain.repositories import IUserRepository
from src.presentation.bot.keyboards import admin_panel, back_to_main

logger = get_logger(__name__)


class AdminController:
    """Handles admin commands and callbacks."""

    def __init__(
        self,
        link_service: LinkService,
        user_repo: IUserRepository,
        validation_service: ValidationService,
        backup_service: BackupService,
    ) -> None:
        self._link_service = link_service
        self._user_repo = user_repo
        self._validation_service = validation_service
        self._backup_service = backup_service

    def _check_admin(self, user: User) -> None:
        if not user.is_admin:
            raise AuthorizationError(f"User {user.telegram_id} is not admin")

    async def handle_callback(self, event: events.CallbackQuery.Event) -> bool:
        """Handle admin callback queries."""
        data = event.data.decode("utf-8")
        sender = await event.get_sender()
        user = await self._user_repo.get_by_telegram_id(sender.id)

        if user is None or not user.is_admin:
            if data.startswith("admin_"):
                await event.answer("غير مصرح", alert=True)
                return True
            return False

        if data == "admin_panel":
            await event.edit(
                "⚙️ لوحة الإدارة\n\nاختر العملية:",
                buttons=admin_panel(),
            )
            return True

        if data == "admin_stats":
            stats = await self._link_service.get_stats()
            users = await self._user_repo.count()
            text = (
                "📊 إحصائيات مفصلة\n\n"
                f"📥 إجمالي الروابط: {stats['total_links']}\n"
                f"✅ نشطة: {stats['active']}\n"
                f"❌ منتهية: {stats['expired']}\n"
                f"🚫 ملغاة: {stats['revoked']}\n"
                f"⏳ غير متحقق: {stats['unverified']}\n"
                f"👥 المستخدمون: {users}\n"
            )
            if stats.get("by_category"):
                text += "\n📋 حسب الفئة:\n"
                for cat, count in stats["by_category"].items():
                    text += f"   • {cat}: {count}\n"
            await event.edit(text, buttons=admin_panel())
            return True

        if data == "admin_validate":
            await event.answer("جاري التحقق...")
            result = await self._validation_service.validate_expired(limit=50)
            text = (
                "🔄 التحقق من الروابط\n\n"
                f"✅ تم التحقق: {result['checked']}\n"
                f"❌ منتهية: {result['expired']}\n"
                f"🚫 ملغاة: {result['revoked']}\n"
                f"✓ صالحة: {result['still_valid']}"
            )
            await event.edit(text, buttons=admin_panel())
            return True

        if data == "admin_backup":
            await event.answer("جاري النسخ الاحتياطي...")
            backup = await self._backup_service.create_backup(created_by=str(sender.id))
            if backup:
                text = (
                    "💾 النسخ الاحتياطي\n\n"
                    f"📂 المسار: {backup.file_path}\n"
                    f"📊 الحجم: {backup.file_size} بايت\n"
                    f"📥 الروابط: {backup.link_count}\n"
                    f"👥 المستخدمون: {backup.user_count}"
                )
            else:
                text = "❌ فشل النسخ الاحتياطي"
            await event.edit(text, buttons=admin_panel())
            return True

        if data == "admin_export":
            links = await self._link_service.export_all()
            data_export = {
                "version": 1,
                "links": [
                    {
                        "url": l.url,
                        "category": l.category.value,
                        "status": l.status.value,
                        "title": l.title,
                    }
                    for l in links
                ],
            }
            file_path = f"export_{sender.id}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data_export, f, ensure_ascii=False, indent=2)
            await event.reply(
                f"📤 تم التصدير ({len(links)} رابط)",
                file=file_path,
            )
            try:
                os.remove(file_path)
            except Exception:
                pass
            await event.edit("📤 تم التصدير", buttons=admin_panel())
            return True

        if data == "admin_users":
            admins = await self._user_repo.list_admins()
            total = await self._user_repo.count()
            text = (
                f"👥 المستخدمون\n\n"
                f"إجمالي المستخدمين: {total}\n\n"
                "المدراء:\n"
            )
            for admin in admins[:10]:
                name = admin.first_name or "Unknown"
                username = f"@{admin.username}" if admin.username else ""
                text += f"• {name} {username} ({admin.role.value})\n"
            await event.edit(text, buttons=admin_panel())
            return True

        return False
