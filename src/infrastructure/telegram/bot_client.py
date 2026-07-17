"""Telegram bot client wrapper with auto-reconnect."""
from __future__ import annotations

import asyncio
from typing import Optional

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError

from src.core.config import TelegramConfig
from src.core.exceptions import TelegramError
from src.core.logging import get_logger

logger = get_logger(__name__)


class BotClient:
    """Wrapper around Telethon's TelegramClient with auto-reconnect."""

    def __init__(self, config: TelegramConfig) -> None:
        self._config = config
        self._client: Optional[TelegramClient] = None
        self._running = False
        self._reconnect_task: Optional[asyncio.Task] = None

    @property
    def client(self) -> TelegramClient:
        if self._client is None:
            raise TelegramError("Bot client not started")
        return self._client

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected()

    def add_event_handler(self, callback, event=None) -> None:
        """Register an event handler."""
        if self._client is None:
            raise TelegramError("Cannot add handler before start()")
        self._client.add_event_handler(callback, event)

    async def start(self) -> None:
        """Start the bot client."""
        if not self._config.bot_token:
            raise TelegramError("BOT_TOKEN is required")

        import os
        os.makedirs(self._config.session_dir, exist_ok=True)
        session_path = os.path.join(self._config.session_dir, "bot")

        self._client = TelegramClient(
            session_path,
            self._config.api_id or 0,
            self._config.api_hash or "",
            connection_retries=self._config.connection_retries,
            retry_delay=self._config.retry_delay,
            request_retries=self._config.request_retries,
            auto_reconnect=self._config.auto_reconnect,
            sequential_updates=False,
        )

        try:
            await self._client.start(bot_token=self._config.bot_token)
            me = await self._client.get_me()
            logger.info(
                "Bot connected",
                extra={
                    "extra_data": {
                        "username": me.username,
                        "name": me.first_name,
                    }
                },
            )
        except Exception as e:
            raise TelegramError(f"Failed to start bot: {e}") from e

    async def run_until_disconnected(self) -> None:
        """Run the client until disconnected."""
        if self._client is None:
            raise TelegramError("Bot not started")
        await self._client.run_until_disconnected()

    async def send_message(self, entity, message: str, **kwargs):
        """Send a message via the bot."""
        try:
            return await self.client.send_message(entity, message, **kwargs)
        except FloodWaitError as e:
            logger.warning(
                "FloodWait on send_message",
                extra={"extra_data": {"seconds": e.seconds}},
            )
            await asyncio.sleep(e.seconds + 1)
            return await self.client.send_message(entity, message, **kwargs)
        except (RPCError, OSError, ConnectionError) as e:
            logger.error(f"Send message failed: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect the bot."""
        if self._client and self._client.is_connected():
            await self._client.disconnect()
            logger.info("Bot disconnected")
