#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram WhatsApp Link Monitor - FIXED VERSION

إصلاحات رئيسية:
1. إنشاء العميل وتسجيل المعالجات مرة واحدة فقط (خارج حلقة إعادة الاتصال)
2. استخدام connect() بدل start() عند إعادة الاتصال
3. تراجع أسي حقيقي لإعادة الاتصال
4. معالجة loop.add_signal_handler على أندرويد (Pydroid 3)
5. إعادة محاولة الإرسال عند FloodWaitError
6. آلية Keep-Alive لمراقبة الاتصال
7. حماية أفضل من الأخطاء الصامتة
"""

import asyncio
import logging
import os
import re
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

import aiosqlite
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import Message, User, Chat, Channel

# -------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------

SESSIONS_DIR = "sessions"
DATA_DIR = "data"
LOGS_DIR = "logs"
DB_FILE = os.path.join(DATA_DIR, "links.db")
LOG_FILE = os.path.join(LOGS_DIR, "app.log")
DEFAULT_LOG_LEVEL = "INFO"
MAX_MESSAGE_LENGTH = 500

WHATSAPP_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:chat\.whatsapp\.com|whatsapp\.com/channel)[^\s]*",
    re.IGNORECASE
)

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------


class AccountConfig:
    def __init__(self, api_id: int, api_hash: str, phone: str, enabled: bool):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.enabled = enabled

    def __repr__(self) -> str:
        return f"AccountConfig(phone={self.phone}, enabled={self.enabled})"


class Config:
    def __init__(
        self,
        accounts: List[AccountConfig],
        channel_id: int,
        channel_username: Optional[str],
        channel_link: Optional[str],
        log_level: str,
    ):
        self.accounts = accounts
        self.channel_id = channel_id
        self.channel_username = channel_username
        self.channel_link = channel_link
        self.log_level = log_level


def load_config() -> Config:
    load_dotenv(dotenv_path='accounts.env')

    accounts = []
    for i in range(1, 4):
        enabled = os.getenv(f"ENABLE_ACCOUNT_{i}", "false").lower() == "true"
        if not enabled:
            continue
        api_id = os.getenv(f"ACCOUNT_{i}_API_ID")
        api_hash = os.getenv(f"ACCOUNT_{i}_API_HASH")
        phone = os.getenv(f"ACCOUNT_{i}_PHONE")
        if not api_id or not api_hash or not phone:
            logging.warning(
                f"Account {i} is enabled but missing API_ID, API_HASH, or PHONE. Skipping."
            )
            continue
        accounts.append(
            AccountConfig(
                api_id=int(api_id),
                api_hash=api_hash,
                phone=phone,
                enabled=True,
            )
        )

    channel_id_str = os.getenv("CHANNEL_ID")
    if not channel_id_str:
        raise ValueError("CHANNEL_ID is required.")
    channel_id = int(channel_id_str)

    channel_username = os.getenv("CHANNEL_USERNAME")
    channel_link = os.getenv("CHANNEL_LINK")
    log_level = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL)

    return Config(
        accounts=accounts,
        channel_id=channel_id,
        channel_username=channel_username,
        channel_link=channel_link,
        log_level=log_level,
    )


# -------------------------------------------------------------------
# Logging Setup
# -------------------------------------------------------------------


def setup_logging(log_level: str) -> None:
    log_level_upper = log_level.upper()
    level = getattr(logging, log_level_upper, logging.INFO)

    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # منع تكرار الـ handlers عند إعادة الاستدعاء
    if not root_logger.handlers:
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)

    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


# -------------------------------------------------------------------
# Database Manager
# -------------------------------------------------------------------


class DatabaseManager:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()  # حماية من الكتابة المتزامنة

    async def _ensure_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self.db_path, timeout=30.0)
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA busy_timeout=30000")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    async def init_db(self) -> None:
        conn = await self._ensure_conn()
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS forwarded_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT NOT NULL,
                link_key TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_link_key ON forwarded_links (link_key)")
        await conn.commit()

    async def insert_link(self, link: str) -> bool:
        async with self._lock:  # تسلسل عمليات الكتابة
            conn = await self._ensure_conn()
            normalized = self._normalize_link(link)
            try:
                await conn.execute(
                    "INSERT OR IGNORE INTO forwarded_links (link, link_key) VALUES (?, ?)",
                    (link, normalized),
                )
                await conn.commit()
                cursor = await conn.execute("SELECT changes()")
                changes = await cursor.fetchone()
                return changes[0] > 0
            except aiosqlite.Error as e:
                logging.error(f"Database error while inserting link: {e}")
                return False

    @staticmethod
    def _normalize_link(link: str) -> str:
        return link.lower().rstrip("/")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None


# -------------------------------------------------------------------
# WhatsApp Link Extractor
# -------------------------------------------------------------------


class LinkExtractor:
    @staticmethod
    def extract_links(text: str) -> List[str]:
        if not text:
            return []
        matches = WHATSAPP_LINK_PATTERN.findall(text)
        seen = set()
        unique = []
        for link in matches:
            norm = DatabaseManager._normalize_link(link)
            if norm not in seen:
                seen.add(norm)
                unique.append(link)
        return unique


# -------------------------------------------------------------------
# Message Formatter
# -------------------------------------------------------------------


class MessageFormatter:
    @staticmethod
    def format(
        group_name: str,
        sender_name: str,
        message_date: datetime,
        links: List[str],
        message_text: str,
    ) -> str:
        if len(message_text) > MAX_MESSAGE_LENGTH:
            message_text = message_text[:MAX_MESSAGE_LENGTH] + "..."

        date_str = message_date.strftime("%Y-%m-%d %H:%M:%S")
        links_text = "\n".join(f"• {link}" for link in links)

        return (
            "📥 رابط واتساب جديد\n\n"
            f"👥 المجموعة: {group_name}\n"
            f"👤 المرسل: {sender_name}\n"
            f"🕒 التاريخ: {date_str}\n\n"
            f"🔗 الرابط:\n{links_text}\n\n"
            f"💬 الرسالة الأصلية:\n{message_text}"
        )


# -------------------------------------------------------------------
# Account Monitor (FIXED)
# -------------------------------------------------------------------


class AccountMonitor:
    def __init__(
        self,
        account_config: AccountConfig,
        channel_id: int,
        db_manager: DatabaseManager,
    ):
        self.config = account_config
        self.channel_id = channel_id
        self.db = db_manager
        self.client: Optional[TelegramClient] = None
        self.task: Optional[asyncio.Task] = None
        self._running = False
        self._handlers_registered = False  # منع التسجيل المكرر
        self._send_lock = asyncio.Lock()   # تسلسل عمليات الإرسال
        self._session_authenticated = False  # تتبع حالة المصادقة
        self._keep_alive_task: Optional[asyncio.Task] = None

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        await self._process_message(event)

    async def _on_message_edited(self, event: events.MessageEdited.Event) -> None:
        await self._process_message(event)

    async def _process_message(self, event) -> None:
        try:
            message: Message = event.message
            if not message or not message.text:
                return

            chat = await event.get_chat()
            sender = await event.get_sender()

            group_name = self._get_chat_name(chat)
            sender_name = self._get_sender_name(sender)

            links = LinkExtractor.extract_links(message.text)
            if not links:
                return

            new_links = []
            for link in links:
                try:
                    inserted = await self.db.insert_link(link)
                except Exception as db_err:
                    logging.error(f"DB insert error for {link}: {db_err}")
                    # نعتبره جديد لعدم فقدان الرابط
                    inserted = True
                if inserted:
                    new_links.append(link)
                else:
                    logging.debug(f"Duplicate link ignored: {link}")

            if not new_links:
                return

            formatted = MessageFormatter.format(
                group_name=group_name,
                sender_name=sender_name,
                message_date=message.date,
                links=new_links,
                message_text=message.text,
            )

            # إرسال مع إعادة المحاولة عند FloodWait
            await self._send_with_retry(formatted)
            logging.info(
                f"Forwarded {len(new_links)} new link(s) from {group_name} by {sender_name}"
            )

        except FloodWaitError as e:
            logging.warning(f"Flood wait in process: {e}. Sleeping {e.seconds}s")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"Error processing message: {e}", exc_info=True)

    async def _send_with_retry(self, text: str, max_retries: int = 3) -> None:
        """إرسال الرسالة مع إعادة المحاولة عند FloodWait أو أخطاء الشبكة."""
        async with self._send_lock:
            for attempt in range(1, max_retries + 1):
                try:
                    await self.client.send_message(self.channel_id, text)
                    return
                except FloodWaitError as e:
                    logging.warning(
                        f"FloodWait on send (attempt {attempt}/{max_retries}): "
                        f"sleeping {e.seconds}s"
                    )
                    await asyncio.sleep(e.seconds + 1)
                except (RPCError, OSError, ConnectionError) as e:
                    wait = min(10 * attempt, 60)
                    logging.warning(
                        f"Send error (attempt {attempt}/{max_retries}): {e}. "
                        f"Retrying in {wait}s"
                    )
                    await asyncio.sleep(wait)
            logging.error(f"Failed to send message after {max_retries} attempts.")

    @staticmethod
    def _get_chat_name(chat) -> str:
        if hasattr(chat, "title") and chat.title:
            return chat.title
        if hasattr(chat, "first_name"):
            name = chat.first_name or ""
            if hasattr(chat, "last_name") and chat.last_name:
                name += f" {chat.last_name}"
            return name.strip() or "Private"
        return "Unknown Group"

    @staticmethod
    def _get_sender_name(sender) -> str:
        if not sender:
            return "Unknown"
        if hasattr(sender, "first_name"):
            name = sender.first_name or ""
            if hasattr(sender, "last_name") and sender.last_name:
                name += f" {sender.last_name}"
            return name.strip() or getattr(sender, "username", "") or "Unknown"
        return getattr(sender, "username", "Unknown") or "Unknown"

    def _create_client(self) -> TelegramClient:
        """إنشاء عميل جديد (يُستدعى مرة واحدة عند البداية)."""
        session_path = os.path.join(SESSIONS_DIR, f"account_{self.config.phone}")
        client = TelegramClient(
            session_path,
            self.config.api_id,
            self.config.api_hash,
            connection_retries=None,  # محاولات لا نهائية على مستوى الاتصال
            retry_delay=5,
            request_retries=5,
            auto_reconnect=True,       # السماح لـ Telethon بإعادة الاتصال تلقائياً
            sequential_updates=False,
        )
        return client

    def _register_handlers(self) -> None:
        """تسجيل معالجات الأحداث مرة واحدة فقط."""
        if self._handlers_registered:
            return
        self.client.add_event_handler(
            self._on_new_message,
            events.NewMessage(incoming=True),
        )
        self.client.add_event_handler(
            self._on_message_edited,
            events.MessageEdited(incoming=True),
        )
        self._handlers_registered = True
        logging.info(f"Handlers registered for {self.config.phone}")

    async def _run_client(self) -> None:
        """حلقة رئيسية محصنة بأخطاء مع تراجع أسي حقيقي."""
        # إنشاء العميل مرة واحدة فقط
        self.client = self._create_client()

        # تسجيل المعالجات مرة واحدة قبل البدء
        self._register_handlers()

        backoff = 5      # البداية بـ 5 ثوانٍ
        max_backoff = 600  # الحد الأقصى 10 دقائق

        while self._running:
            try:
                if not self.client.is_connected():
                    logging.info(f"Connecting client for {self.config.phone}...")
                    # start() للجلسة الأولى (يتكفل بالمصادقة)، ثم connect() للمرات التالية
                    if not self._session_authenticated:
                        await self.client.start(phone=self.config.phone)
                        self._session_authenticated = True
                    else:
                        await self.client.connect()
                        if not await self.client.is_user_authorized():
                            logging.warning(
                                f"Session not authorized for {self.config.phone}, "
                                "re-running start()"
                            )
                            await self.client.start(phone=self.config.phone)
                    logging.info(f"Client connected for {self.config.phone}")
                    backoff = 5  # إعادة التعيين بعد نجاح الاتصال

                # تشغيل حتى الانقطاع
                await self.client.run_until_disconnected()

            except FloodWaitError as e:
                logging.warning(f"FloodWaitError: sleeping {e.seconds}s")
                await asyncio.sleep(e.seconds + 1)
            except (RPCError, ConnectionError, OSError) as e:
                logging.error(f"Client error for {self.config.phone}: {e}")
            except asyncio.CancelledError:
                logging.info(f"Client task cancelled for {self.config.phone}")
                raise
            except Exception as e:
                logging.error(
                    f"Unexpected error for {self.config.phone}: {e}",
                    exc_info=True,
                )
            finally:
                # التأكد من فصل الاتصال قبل إعادة المحاولة
                if self.client and self.client.is_connected():
                    try:
                        await self.client.disconnect()
                    except Exception:
                        pass

            if not self._running:
                break

            # تراجع أسي حقيقي
            logging.info(f"Reconnecting in {backoff}s (exponential backoff)...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    # سمة لتتبع ما إذا تمت المصادقة على الجلسة
    # (تم نقلها إلى __init__)

    async def _keep_alive(self) -> None:
        """مهمة خفية تتأكد من أن الاتصال حي كل 60 ثانية."""
        while self._running:
            try:
                await asyncio.sleep(60)
                if self.client and not self.client.is_connected():
                    logging.warning(
                        f"Keep-alive detected disconnection for {self.config.phone}"
                    )
                    # محاولة إعادة الاتصال غير المتزامن
                    try:
                        await self.client.connect()
                    except Exception as e:
                        logging.error(f"Keep-alive reconnect failed: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Keep-alive error: {e}")

    async def start(self) -> None:
        self._running = True
        self.task = asyncio.create_task(self._run_client())
        # تشغيل مهمة المراقبة الخفية
        self._keep_alive_task = asyncio.create_task(self._keep_alive())

    async def stop(self) -> None:
        self._running = False
        if self.client and self.client.is_connected():
            try:
                await self.client.disconnect()
            except Exception:
                pass
        for t_attr in ("task", "_keep_alive_task"):
            t = getattr(self, t_attr, None)
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass


# -------------------------------------------------------------------
# Main Entry Point (FIXED)
# -------------------------------------------------------------------


async def main() -> None:
    try:
        config = load_config()
    except ValueError as e:
        # التهيئة المبكرة للطباعة قبل إعداد السجل
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    setup_logging(config.log_level)

    logging.info("Starting Telegram WhatsApp Link Monitor (FIXED VERSION)")

    Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

    db = DatabaseManager()
    await db.init_db()

    monitors = []
    for acc_config in config.accounts:
        if not acc_config.enabled:
            continue
        logging.info(f"Setting up account: {acc_config.phone}")
        monitor = AccountMonitor(acc_config, config.channel_id, db)
        monitors.append(monitor)
        await monitor.start()

    if not monitors:
        logging.error("No enabled accounts found. Exiting.")
        await db.close()
        sys.exit(1)

    logging.info("All monitors started. Monitoring...")

    shutdown_event = asyncio.Event()

    def signal_handler():
        logging.info("Shutdown signal received. Stopping monitors...")
        shutdown_event.set()

    # معالجة الإشارات بشكل آمن على كل المنصات (بما فيها أندرويد/Pydroid 3)
    loop = asyncio.get_event_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except (NotImplementedError, RuntimeError, ValueError) as e:
                logging.warning(f"add_signal_handler failed for {sig}: {e}")
                # محاولة استخدام المسار الاحتياطي
                import signal as _sig
                try:
                    _sig.signal(sig, lambda *_: signal_handler())
                except Exception:
                    pass
    except Exception as e:
        logging.warning(f"Signal handler setup failed entirely: {e}")

    # انتظار إشارة الإيقاف
    await shutdown_event.wait()

    logging.info("Stopping monitors...")
    for monitor in monitors:
        await monitor.stop()

    await db.close()
    logging.info("Application stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Interrupted by user.")
    except Exception as e:
        logging.critical(f"Unhandled exception: {e}", exc_info=True)
