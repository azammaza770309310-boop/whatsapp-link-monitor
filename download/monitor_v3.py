#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram WhatsApp Link Monitor - v3 (ALL LINK TYPES + EXPIRED FILTER)

المميزات الجديدة في v3:
1. سحب جميع أنواع روابط واتساب:
   - chat.whatsapp.com/<invite>          (دعوة مجموعة)
   - whatsapp.com/channel/<id>           (قناة)
   - wa.me/<phone>                       (دردشة مباشرة)
   - wa.me/message/<code>                (رابط رسالة)
   - api.whatsapp.com/send?phone=...     (إرسال مباشر)
   - api.whatsapp.com/message?...        (رسالة API)
   - api.whatsapp.com/q?code=...         (رمز QR)
   - wa.me/p/<phone>                     (ملف شخصي)

2. فلترة الروابط المنتهية:
   - فحص HTTP فعلي عبر aiohttp لكل رابط دعوة/رسالة
   - كشف الصفحات التي تحتوي على كلمات "revoked/expired/invalid"
   - تخزين مؤقت للروابط المنتهية في جدول expired_links
   - مهلة 6 ثوانٍ لكل فحص حتى لا يعلق البوت
   - عند فشل الفحص (شبكة)، نُرسل الرابط ولا نفقده

3. الحفاظ على جميع إصلاحات v2 (التراجع الأسي، المعالجات الأحادية، keep-alive).
"""

import asyncio
import logging
import os
import re
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import aiohttp
import aiosqlite
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import Message

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

# -------------------------------------------------------------------
# Regex شامل لجميع روابط واتساب
# -------------------------------------------------------------------

# يلتقط أي رابط يحتوي على whatsapp أو wa.me
WHATSAPP_LINK_PATTERN = re.compile(
    r"""
    (?:https?://)?                  # بروتوكول اختياري
    (?:                             # النطاقات المدعومة:
        chat\.whatsapp\.com         #   دعوة مجموعة
      | whatsapp\.com/channel       #   قناة
      | whatsapp\.com/contact       #   جهة اتصال
      | wa\.me                      #   روابط مباشرة
      | api\.whatsapp\.com          #   API
      | l\.whatsapp\.com            #   روابط مختصرة
    )
    [^\s<>"'\)\]]*                  # بقية المسار
    """,
    re.IGNORECASE | re.VERBOSE,
)

# كلمات دالة على انتهاء الرابط في صفحة واتساب
EXPIRED_MARKERS = [
    "invite link revoked",
    "this group invite link has been revoked",
    "this invite link has expired",
    "link expired",
    "invalid invite link",
    "this group cannot be joined",
    "group has been changed",
    "the link is no longer valid",
    "this link has been revoked",
    "this community invite link has been revoked",
    "page not found",
    "invite link invalid",
]

# أنواع الروابط القابلة للانتهاء (نحتاج فحصها)
EXPIRABLE_TYPES = ("chat.whatsapp.com/", "wa.me/message/", "api.whatsapp.com/message")


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
        check_expired: bool = True,
        http_timeout: int = 6,
    ):
        self.accounts = accounts
        self.channel_id = channel_id
        self.channel_username = channel_username
        self.channel_link = channel_link
        self.log_level = log_level
        self.check_expired = check_expired  # تفعيل/تعطيل فحص الانتهاء
        self.http_timeout = http_timeout


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
    check_expired = os.getenv("CHECK_EXPIRED", "true").lower() == "true"
    http_timeout = int(os.getenv("HTTP_TIMEOUT", "6"))

    return Config(
        accounts=accounts,
        channel_id=channel_id,
        channel_username=channel_username,
        channel_link=channel_link,
        log_level=log_level,
        check_expired=check_expired,
        http_timeout=http_timeout,
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
    if not root_logger.handlers:
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)

    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


# -------------------------------------------------------------------
# Database Manager
# -------------------------------------------------------------------


class DatabaseManager:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        # ذاكرة مؤقتة داخلية للروابط المنتهية (لتسريع الفحص)
        self._expired_cache: set = set()
        self._valid_cache: set = set()

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
        # جدول الروابط المُحوَّلة
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS forwarded_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT NOT NULL,
                link_key TEXT NOT NULL UNIQUE,
                link_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_link_key ON forwarded_links (link_key)")
        # جدول الروابط المنتهية (ذاكرة دائمة)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS expired_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link_key TEXT NOT NULL UNIQUE,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.commit()
        # تحميل الروابط المنتهية إلى الذاكرة المؤقتة
        cursor = await conn.execute("SELECT link_key FROM expired_links")
        rows = await cursor.fetchall()
        self._expired_cache = {r[0] for r in rows}
        logging.info(f"Loaded {len(self._expired_cache)} expired links from DB cache")

    async def is_known_expired(self, link: str) -> bool:
        """فحص سريع من الذاكرة المؤقتة دون DB."""
        return self._normalize_link(link) in self._expired_cache

    async def is_known_valid(self, link: str) -> bool:
        return self._normalize_link(link) in self._valid_cache

    async def mark_expired(self, link: str) -> None:
        normalized = self._normalize_link(link)
        self._expired_cache.add(normalized)
        async with self._lock:
            conn = await self._ensure_conn()
            try:
                await conn.execute(
                    "INSERT OR IGNORE INTO expired_links (link_key) VALUES (?)",
                    (normalized,),
                )
                await conn.commit()
            except aiosqlite.Error as e:
                logging.error(f"DB error marking expired: {e}")

    async def mark_valid(self, link: str) -> None:
        self._valid_cache.add(self._normalize_link(link))

    async def insert_link(self, link: str) -> bool:
        async with self._lock:
            conn = await self._ensure_conn()
            normalized = self._normalize_link(link)
            link_type = self._detect_link_type(link)
            try:
                await conn.execute(
                    "INSERT OR IGNORE INTO forwarded_links (link, link_key, link_type) VALUES (?, ?, ?)",
                    (link, normalized, link_type),
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
        # إزالة البروتوكول، تحويل لأحرف صغيرة، إزالة الشرطة المائلة الأخيرة
        link = link.lower().strip()
        if link.startswith("https://"):
            link = link[8:]
        elif link.startswith("http://"):
            link = link[7:]
        return link.rstrip("/")

    @staticmethod
    def _detect_link_type(link: str) -> str:
        l = link.lower()
        if "chat.whatsapp.com" in l:
            return "group_invite"
        if "/channel" in l:
            return "channel"
        if "/message" in l:
            return "message_link"
        if "wa.me" in l and "/message" not in l:
            return "direct_chat"
        if "api.whatsapp.com/send" in l:
            return "api_send"
        if "api.whatsapp.com/q" in l:
            return "qr_code"
        if "l.whatsapp.com" in l:
            return "short_link"
        return "other"

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None


# -------------------------------------------------------------------
# Link Extractor
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
            # تنظيف بسيط
            link = link.rstrip(".,;:!?)]}>\"'")
            norm = DatabaseManager._normalize_link(link)
            if norm not in seen:
                seen.add(norm)
                unique.append(link)
        return unique


# -------------------------------------------------------------------
# Expired Link Checker
# -------------------------------------------------------------------


class ExpiredChecker:
    """يفحص ما إذا كان رابط واتساب منتهياً عبر طلب HTTP."""

    def __init__(self, db: DatabaseManager, timeout: int = 6):
        self.db = db
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
        # User-Agent لتفادي الحظر
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers=self._headers,
            )
        return self._session

    @staticmethod
    def is_checkable(link: str) -> bool:
        """هل يحتاج هذا الرابط لفحص الانتهاء؟"""
        l = link.lower()
        return any(t in l for t in EXPIRABLE_TYPES)

    async def is_expired(self, link: str) -> Tuple[bool, str]:
        """
        يفحص الرابط ويعيد:
          (True, reason)  → الرابط منتهٍ (لا تُرسله)
          (False, "")     → الرابط صالح أو غير متأكد (أرسله)
        """
        if not self.is_checkable(link):
            return False, ""  # روابط مباشرة/API لا تنتهي عادةً

        # فحص الذاكرة المؤقتة أولاً
        if await self.db.is_known_expired(link):
            return True, "cached_expired"
        if await self.db.is_known_valid(link):
            return False, ""

        # تنظيف الرابط
        url = link.strip()
        if not url.startswith("http"):
            url = "https://" + url

        try:
            session = await self._get_session()
            async with session.get(url, allow_redirects=True, ssl=False) as resp:
                # الحالة 404 → منتهٍ/غير موجود
                if resp.status == 404:
                    await self.db.mark_expired(link)
                    return True, f"http_404"
                if resp.status >= 500:
                    # خطأ سيرفر → لا نعرف، نفترض صالح لعدم الفقدان
                    logging.warning(f"Server error {resp.status} for {link}, assuming valid")
                    return False, ""

                if resp.status == 200:
                    try:
                        text = await resp.text(errors="ignore")
                    except Exception:
                        return False, ""

                    text_lower = text.lower()
                    for marker in EXPIRED_MARKERS:
                        if marker in text_lower:
                            await self.db.mark_expired(link)
                            return True, f"marker:{marker[:30]}"

                    # وصلنا هنا → الرابط صالح
                    await self.db.mark_valid(link)
                    return False, ""

                # أي حالة أخرى (3xx, 4xx) → نفترض صالح
                return False, ""

        except asyncio.TimeoutError:
            logging.warning(f"Timeout checking {link}, assuming valid (forwarding)")
            return False, ""
        except aiohttp.ClientError as e:
            logging.warning(f"Network error checking {link}: {e}, forwarding anyway")
            return False, ""
        except Exception as e:
            logging.error(f"Unexpected error checking {link}: {e}")
            return False, ""

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


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
# Account Monitor
# -------------------------------------------------------------------


class AccountMonitor:
    def __init__(
        self,
        account_config: AccountConfig,
        channel_id: int,
        db_manager: DatabaseManager,
        expired_checker: Optional[ExpiredChecker] = None,
    ):
        self.config = account_config
        self.channel_id = channel_id
        self.db = db_manager
        self.expired_checker = expired_checker
        self.client: Optional[TelegramClient] = None
        self.task: Optional[asyncio.Task] = None
        self._running = False
        self._handlers_registered = False
        self._send_lock = asyncio.Lock()
        self._session_authenticated = False
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

            all_links = LinkExtractor.extract_links(message.text)
            if not all_links:
                return

            logging.info(
                f"[{self.config.phone}] Found {len(all_links)} link(s) in {group_name}"
            )

            # فلترة الروابط المنتهية (إن كان الفاحص مفعّلاً)
            valid_links: List[str] = []
            for link in all_links:
                if self.expired_checker is not None:
                    is_exp, reason = await self.expired_checker.is_expired(link)
                    if is_exp:
                        logging.info(f"Skipping expired link: {link} ({reason})")
                        continue
                valid_links.append(link)

            if not valid_links:
                logging.info(f"All {len(all_links)} link(s) expired, nothing to forward")
                return

            # إدراج في DB وإرسال الجديد فقط
            new_links = []
            for link in valid_links:
                try:
                    inserted = await self.db.insert_link(link)
                except Exception as db_err:
                    logging.error(f"DB insert error for {link}: {db_err}")
                    inserted = True  # افتراض جديد لعدم الفقدان
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

            await self._send_with_retry(formatted)
            logging.info(
                f"[{self.config.phone}] Forwarded {len(new_links)} new link(s) "
                f"from {group_name} by {sender_name}"
            )

        except FloodWaitError as e:
            logging.warning(f"Flood wait in process: {e}. Sleeping {e.seconds}s")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"Error processing message: {e}", exc_info=True)

    async def _send_with_retry(self, text: str, max_retries: int = 3) -> None:
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
        session_path = os.path.join(SESSIONS_DIR, f"account_{self.config.phone}")
        return TelegramClient(
            session_path,
            self.config.api_id,
            self.config.api_hash,
            connection_retries=None,
            retry_delay=5,
            request_retries=5,
            auto_reconnect=True,
            sequential_updates=False,
        )

    def _register_handlers(self) -> None:
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
        self.client = self._create_client()
        self._register_handlers()

        backoff = 5
        max_backoff = 600

        while self._running:
            try:
                if not self.client.is_connected():
                    logging.info(f"Connecting client for {self.config.phone}...")
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
                    backoff = 5

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
                if self.client and self.client.is_connected():
                    try:
                        await self.client.disconnect()
                    except Exception:
                        pass

            if not self._running:
                break

            logging.info(f"Reconnecting in {backoff}s (exponential backoff)...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    async def _keep_alive(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(60)
                if self.client and not self.client.is_connected():
                    logging.warning(
                        f"Keep-alive detected disconnection for {self.config.phone}"
                    )
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
# Main
# -------------------------------------------------------------------


async def main() -> None:
    try:
        config = load_config()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    setup_logging(config.log_level)
    logging.info("=== Starting Telegram WhatsApp Link Monitor v3 ===")
    logging.info(f"Expired link checking: {'ENABLED' if config.check_expired else 'DISABLED'}")

    Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

    db = DatabaseManager()
    await db.init_db()

    # إنشاء فاحص الروابط المنتهية إن كان مفعّلاً
    expired_checker: Optional[ExpiredChecker] = None
    if config.check_expired:
        expired_checker = ExpiredChecker(db, timeout=config.http_timeout)

    monitors = []
    for acc_config in config.accounts:
        if not acc_config.enabled:
            continue
        logging.info(f"Setting up account: {acc_config.phone}")
        monitor = AccountMonitor(
            acc_config, config.channel_id, db, expired_checker
        )
        monitors.append(monitor)
        await monitor.start()

    if not monitors:
        logging.error("No enabled accounts found. Exiting.")
        await db.close()
        if expired_checker:
            await expired_checker.close()
        sys.exit(1)

    logging.info(f"All {len(monitors)} monitor(s) started. Monitoring all WhatsApp link types...")

    shutdown_event = asyncio.Event()

    def signal_handler():
        logging.info("Shutdown signal received. Stopping monitors...")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except (NotImplementedError, RuntimeError, ValueError) as e:
                logging.warning(f"add_signal_handler failed for {sig}: {e}")
                try:
                    signal.signal(sig, lambda *_: signal_handler())
                except Exception:
                    pass
    except Exception as e:
        logging.warning(f"Signal handler setup failed entirely: {e}")

    await shutdown_event.wait()

    logging.info("Stopping monitors...")
    for monitor in monitors:
        await monitor.stop()

    if expired_checker:
        await expired_checker.close()
    await db.close()
    logging.info("Application stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Interrupted by user.")
    except Exception as e:
        logging.critical(f"Unhandled exception: {e}", exc_info=True)
