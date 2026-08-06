"""Bot orchestrator - registers handlers and runs the bot."""
from __future__ import annotations

import asyncio
from typing import Optional

from telethon import events

from src.application.services.link_service import LinkService
from src.application.services.rate_limiter import RateLimiter
from src.application.services.validation_service import ValidationService
from src.application.services.backup_service import BackupService
from src.core.logging import get_logger
from src.domain.repositories import ILinkRepository, IUserRepository, ISubmissionRepository, IBackupRepository
from src.infrastructure.telegram.bot_client import BotClient
from src.presentation.bot.controllers.admin_controller import AdminController
from src.presentation.bot.controllers.user_controller import UserController

logger = get_logger(__name__)


class BotOrchestrator:
    """Orchestrates the bot, registering handlers and managing lifecycle."""

    def __init__(
        self,
        bot_client: BotClient,
        link_service: LinkService,
        user_repo: IUserRepository,
        validation_service: ValidationService,
        backup_service: BackupService,
        rate_limiter: RateLimiter,
        admin_ids: list,
        channel_id: int,
    ) -> None:
        self._bot = bot_client
        self._user_controller = UserController(
            link_service=link_service,
            user_repo=user_repo,
            rate_limiter=rate_limiter,
            admin_ids=admin_ids,
            channel_id=channel_id,
        )
        self._admin_controller = AdminController(
            link_service=link_service,
            user_repo=user_repo,
            validation_service=validation_service,
            backup_service=backup_service,
        )
        self._running = False

    def register_handlers(self) -> None:
        """Register all event handlers."""
        # /start command
        self._bot.add_event_handler(
            self._handle_start,
            events.NewMessage(pattern=r"^/start"),
        )
        # /help command
        self._bot.add_event_handler(
            self._handle_help,
            events.NewMessage(pattern=r"^/help"),
        )
        # /stats command
        self._bot.add_event_handler(
            self._handle_stats,
            events.NewMessage(pattern=r"^/stats"),
        )
        # Callback queries (button presses)
        self._bot.add_event_handler(
            self._handle_callback,
            events.CallbackQuery(),
        )
        # Regular messages (link submission, search)
        self._bot.add_event_handler(
            self._handle_message,
            events.NewMessage(func=lambda e: e.is_private and not e.message.text.startswith("/")),
        )
        # Group messages - monitor for links
        self._bot.add_event_handler(
            self._handle_group_message,
            events.NewMessage(func=lambda e: not e.is_private),
        )
        logger.info("Bot handlers registered")

    async def _handle_start(self, event) -> None:
        await self._user_controller.handle_start(event)

    async def _handle_help(self, event) -> None:
        help_text = (
            "❓ المساعدة\n\n"
            "📌 الأوامر:\n"
            "• /start - القائمة الرئيسية\n"
            "• /help - هذه الرسالة\n"
            "• /stats - الإحصائيات\n\n"
            "أرسل رابط واتساب مباشرة لحفظه."
        )
        await event.reply(help_text)

    async def _handle_stats(self, event) -> None:
        stats = await self._user_controller._link_service.get_stats()
        text = (
            "📊 الإحصائيات\n\n"
            f"📥 إجمالي الروابط: {stats['total_links']}\n"
            f"✅ نشطة: {stats['active']}\n"
            f"👥 المستخدمون: {stats['total_users']}"
        )
        await event.reply(text)

    async def _handle_callback(self, event) -> None:
        # Try admin controller first
        handled = await self._admin_controller.handle_callback(event)
        if handled:
            return
        # Fall back to user controller
        await self._user_controller.handle_callback(event)

    async def _handle_message(self, event) -> None:
        await self._user_controller.handle_message(event)

    async def _handle_group_message(self, event) -> None:
        """Monitor group messages for WhatsApp links (bot must be admin)."""
        try:
            text = (event.message.text or "").strip()
            if not text:
                return
            # Extract links
            links = self._user_controller._link_service.extract_links(text)
            if not links:
                return
            # Get sender info
            sender = await event.get_sender()
            chat = await event.get_chat()
            sender_name = (
                getattr(sender, "first_name", None)
                or getattr(sender, "username", None)
                or "Unknown"
            )
            group_name = getattr(chat, "title", None) or "Unknown Group"
            # Submit each link
            for url in links:
                try:
                    link, is_dup = await self._user_controller._link_service.submit_link(
                        url=url,
                        submitted_by=getattr(sender, "id", None),
                        submitted_by_name=sender_name,
                        source_group_id=getattr(chat, "id", None),
                        source_group_name=group_name,
                        message_text=text,
                    )
                    if not is_dup and self._user_controller._channel_id:
                        from src.application.services.categorization_service import CategorizationService
                        emoji = CategorizationService.get_emoji(link.category)
                        label = CategorizationService.get_label(link.category)
                        msg = (
                            f"📥 رابط واتساب جديد\n\n"
                            f"{emoji} {label}\n"
                            f"🔗 {url}\n\n"
                            f"👥 المجموعة: {group_name}\n"
                            f"👤 المرسل: {sender_name}"
                        )
                        try:
                            await event.client.send_message(
                                self._user_controller._channel_id,
                                msg,
                            )
                        except Exception as e:
                            logger.error(f"Failed to publish to channel: {e}")
                except Exception as e:
                    logger.error(f"Failed to submit link from group: {e}")
        except Exception as e:
            logger.error(f"Group message handler error: {e}", exc_info=True)

    async def run(self) -> None:
        """Run the bot until disconnected."""
        self._running = True
        await self._bot.run_until_disconnected()

    async def stop(self) -> None:
        """Stop the bot."""
        self._running = False
        await self._bot.disconnect()
