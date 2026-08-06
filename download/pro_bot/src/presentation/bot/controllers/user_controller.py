"""User controller - handles regular user interactions."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from telethon import Button, events

from src.application.services.categorization_service import CategorizationService
from src.application.services.link_service import LinkService
from src.application.services.rate_limiter import RateLimiter
from src.core.exceptions import RateLimitExceededError, ValidationError
from src.core.logging import get_logger
from src.domain.entities import User, UserRole
from src.domain.repositories import IUserRepository
from src.presentation.bot.keyboards import back_to_main, main_menu

logger = get_logger(__name__)


class UserController:
    """Handles regular user commands and callbacks."""

    def __init__(
        self,
        link_service: LinkService,
        user_repo: IUserRepository,
        rate_limiter: RateLimiter,
        admin_ids: list,
        channel_id: int,
    ) -> None:
        self._link_service = link_service
        self._user_repo = user_repo
        self._rate_limiter = rate_limiter
        self._admin_ids = admin_ids
        self._channel_id = channel_id

    async def _get_or_create_user(
        self,
        telegram_id: int,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
    ) -> User:
        """Get user from DB or create new."""
        user = await self._user_repo.get_by_telegram_id(telegram_id)
        if user is None:
            role = UserRole.SUPER_ADMIN if telegram_id in self._admin_ids else UserRole.USER
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                role=role,
            )
            user = await self._user_repo.save(user)
            logger.info(
                "New user registered",
                extra={
                    "extra_data": {
                        "telegram_id": telegram_id,
                        "username": username,
                    }
                },
            )
        else:
            # Update activity
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            await self._user_repo.save(user)
        return user

    async def handle_start(self, event: events.NewMessage.Event) -> None:
        """Handle /start command."""
        sender = await event.get_sender()
        user = await self._get_or_create_user(
            sender.id,
            getattr(sender, "username", None),
            getattr(sender, "first_name", None),
            getattr(sender, "last_name", None),
        )

        name = user.first_name or "صديقي"
        is_admin = user.is_admin
        welcome = (
            f"🤖 أهلاً {name}!\n\n"
            "📋 هذا البوت يدير روابط واتساب.\n\n"
            "📌 ماذا أستطيع أن أفعل؟\n"
            "• إرسال رابط واتساب جديد\n"
            "• البحث في الروابط المحفوظة\n"
            "• عرض الإحصائيات\n\n"
            "اختر من القائمة:"
        )
        await event.reply(welcome, buttons=main_menu(is_admin))

    async def handle_message(self, event: events.NewMessage.Event) -> None:
        """Handle regular messages - check for links."""
        text = (event.message.text or "").strip()
        if not text or text.startswith("/"):
            return

        sender = await event.get_sender()
        user = await self._get_or_create_user(
            sender.id,
            getattr(sender, "username", None),
            getattr(sender, "first_name", None),
            getattr(sender, "last_name", None),
        )

        if user.is_blocked:
            return

        # Rate limit check
        try:
            await self._rate_limiter.check(user.telegram_id)
        except RateLimitExceededError:
            await event.reply(
                "⚠️ تجاوزت الحد المسموح من الطلبات.\n"
                "الرجاء المحاولة بعد دقيقة."
            )
            return

        # Check if it contains a WhatsApp link
        links = self._link_service.extract_links(text)
        if not links:
            await event.reply(
                "لم أجد رابط واتساب في رسالتك.\n\n"
                "أرسل رابطاً يبدأ بـ:\n"
                "• chat.whatsapp.com\n"
                "• wa.me\n"
                "• whatsapp.com/channel",
                buttons=back_to_main(),
            )
            return

        # Submit each link
        results = []
        for url in links:
            try:
                link, is_duplicate = await self._link_service.submit_link(
                    url=url,
                    submitted_by=user.id,
                    submitted_by_name=user.first_name,
                    message_text=text,
                )
                if is_duplicate:
                    results.append(f"⚠️ مكرر: {url}")
                else:
                    emoji = CategorizationService.get_emoji(link.category)
                    label = CategorizationService.get_label(link.category)
                    results.append(f"✅ {emoji} {label}\n   {url}")
            except ValidationError as e:
                results.append(f"❌ {url}: {e}")

        # Publish to channel if not duplicate
        new_links = [r for r in results if r.startswith("✅")]
        if new_links and self._channel_id:
            try:
                channel_msg = (
                    "📥 روابط واتساب جديدة\n\n"
                    + "\n\n".join(new_links)
                    + f"\n\n👤 المُرسل: {user.first_name or 'مستخدم'}"
                )
                await event.client.send_message(self._channel_id, channel_msg)
            except Exception as e:
                logger.error(f"Failed to publish to channel: {e}")

        response = (
            "📋 النتائج:\n\n"
            + "\n\n".join(results)
        )
        await event.reply(response, buttons=back_to_main())

    async def handle_callback(self, event: events.CallbackQuery.Event) -> bool:
        """Handle callback queries. Returns True if handled."""
        data = event.data.decode("utf-8")
        sender = await event.get_sender()
        user = await self._get_or_create_user(
            sender.id,
            getattr(sender, "username", None),
            getattr(sender, "first_name", None),
            getattr(sender, "last_name", None),
        )

        if data == "main_menu":
            await event.edit(
                f"🤖 أهلاً {user.first_name or 'صديقي'}!\n\nاختر من القائمة:",
                buttons=main_menu(user.is_admin),
            )
            return True

        if data == "help":
            help_text = (
                "❓ المساعدة\n\n"
                "📌 الأوامر المتاحة:\n"
                "• /start - القائمة الرئيسية\n"
                "• /help - هذه الرسالة\n"
                "• /stats - الإحصائيات\n"
                "• /recent - آخر الروابط\n\n"
                "📌 كيف أرسل رابطاً؟\n"
                "أرسل رابط واتساب مباشرة في الدردشة.\n\n"
                "📌 الروابط المدعومة:\n"
                "• chat.whatsapp.com (مجموعات)\n"
                "• wa.me (دردشة مباشرة)\n"
                "• whatsapp.com/channel (قنوات)"
            )
            await event.edit(help_text, buttons=back_to_main())
            return True

        if data == "stats":
            stats = await self._link_service.get_stats()
            text = (
                "📊 الإحصائيات\n\n"
                f"📥 إجمالي الروابط: {stats['total_links']}\n"
                f"✅ نشطة: {stats['active']}\n"
                f"❌ منتهية: {stats['expired']}\n"
                f"🚫 ملغاة: {stats['revoked']}\n"
                f"⏳ غير متحقق: {stats['unverified']}\n"
                f"👥 المستخدمون: {stats['total_users']}\n"
            )
            if stats.get("by_category"):
                text += "\n📋 حسب الفئة:\n"
                for cat, count in stats["by_category"].items():
                    text += f"   • {cat}: {count}\n"
            await event.edit(text, buttons=back_to_main())
            return True

        if data == "recent_links":
            links = await self._link_service.list_links(limit=10)
            if not links:
                await event.edit(
                    "ℹ️ لا توجد روابط محفوظة بعد.",
                    buttons=back_to_main(),
                )
                return True
            text = "📋 آخر 10 روابط:\n\n"
            for i, link in enumerate(links, 1):
                emoji = CategorizationService.get_emoji(link.category)
                text += f"{i}. {emoji} {link.url}\n"
                if link.submitted_by_name:
                    text += f"   👤 {link.submitted_by_name}\n"
                text += "\n"
            await event.edit(text, buttons=back_to_main())
            return True

        if data == "submit_link":
            await event.edit(
                "📤 إرسال رابط واتساب\n\n"
                "أرسل رابط واتساب مباشرة في الدردشة.\n\n"
                "الروابط المدعومة:\n"
                "• chat.whatsapp.com/...\n"
                "• wa.me/...\n"
                "• whatsapp.com/channel/...",
                buttons=back_to_main(),
            )
            return True

        if data == "search":
            await event.edit(
                "🔍 البحث في الروابط\n\n"
                "أرسل كلمة البحث في الدردشة.\n"
                "مثال: مجموعة جامعة",
                buttons=back_to_main(),
            )
            # Mark user as in search mode
            self._search_mode = getattr(self, "_search_mode", set())
            self._search_mode.add(user.telegram_id)
            return True

        # Check if user is in search mode
        search_mode = getattr(self, "_search_mode", set())
        if user.telegram_id in search_mode and not data.startswith("admin_"):
            # This is a search query - handled in handle_message
            return False

        return False
