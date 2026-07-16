#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram WhatsApp Link Monitor - v6 (DUAL CLIENT - POWERFUL EDITION)

المعمارية:
- user_client: حساب المستخدم (+967770309310) - يراقب كل المجموعات/القنوات
- bot_client: بوت تيليجرام (BOT_TOKEN) - يرسل الروابط + يرد على الأوامر
- DB: قاعدة بيانات مشتركة

المميزات:
1. فصل هوية المراقبة عن هوية الإرسال (احترافي وآمن)
2. البوت يراقب كل مجموعات حسابك (حتى لو البوت ليس عضواً)
3. الرسائل في القناة تظهر باسم البوت
4. أوامر كاملة: /help, /status, /scan_week, /scan_month, /scan_60, /scan_90,
   /scan_full, /scan_stop, /last_scan, /reset_scan
5. مسح تاريخي متزايد ذكي
6. فلترة الروابط المنتهية (HTTP check)
7. إعادة اتصال تلقائي مع تراجع أسي
8. دعم بروكسي اختياري
9. حماية من FloodWait
10. سجلات مفصلة
"""

import asyncio
import logging
import os
import re
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, Dict

import aiohttp
import aiosqlite
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession
from telethon.tl.types import Message

# لإضافة خادم HTTP خفيف (Web Service mode)
from aiohttp import web

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
    r"""
    (?:https?://)?
    (?:
        chat\.whatsapp\.com
      | whatsapp\.com/channel
      | whatsapp\.com/contact
      | wa\.me
      | api\.whatsapp\.com
      | l\.whatsapp\.com
    )
    [^\s<>"'\)\]]*
    """,
    re.IGNORECASE | re.VERBOSE,
)

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

EXPIRABLE_TYPES = ("chat.whatsapp.com/", "wa.me/message/", "api.whatsapp.com/message")

SCAN_COMMANDS: Dict[str, Optional[int]] = {
    "/scan_week": 7,
    "/scan_month": 30,
    "/scan_60": 60,
    "/scan_90": 90,
    "/scan_full": None,
}


# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------


class Config:
    def __init__(self):
        # User account (for monitoring)
        self.api_id: int = int(os.getenv("API_ID", "0"))
        self.api_hash: str = os.getenv("API_HASH", "")
        self.phone: str = os.getenv("PHONE", "")

        # Bot (for posting)
        self.bot_token: str = os.getenv("BOT_TOKEN", "")

        # Destination channel
        self.channel_id: int = int(os.getenv("CHANNEL_ID", "0"))

        # Owner (optional - restricts commands to specific user)
        self.owner_id: Optional[int] = None
        owner_id_str = os.getenv("OWNER_ID", "")
        if owner_id_str:
            try:
                self.owner_id = int(owner_id_str)
            except ValueError:
                pass

        # Logging
        self.log_level: str = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL)

        # Expired link checking
        self.check_expired: bool = os.getenv("CHECK_EXPIRED", "true").lower() == "true"
        self.http_timeout: int = int(os.getenv("HTTP_TIMEOUT", "6"))

        # History scan
        self.history_max_per_chat: int = int(os.getenv("HISTORY_MAX_PER_CHAT", "500"))
        self.history_batch_size: int = max(1, min(int(os.getenv("HISTORY_BATCH_SIZE", "5")), 20))
        self.history_skip_channel_posts: bool = (
            os.getenv("HISTORY_SKIP_CHANNEL_POSTS", "false").lower() == "true"
        )
        self.startup_scan_days: Optional[int] = None
        ssd = os.getenv("STARTUP_SCAN_DAYS", "")
        if ssd and ssd.lower() not in ("none", "null", ""):
            try:
                self.startup_scan_days = int(ssd)
            except ValueError:
                pass

        # String Session (for cloud deployment - Render/Railway)
        self.user_session_string: Optional[str] = os.getenv("USER_SESSION_STRING", "") or None
        if self.user_session_string:
            logging.info("Using StringSession for user account (cloud mode)")

        # Proxy (optional)
        self.proxy: Optional[tuple] = None
        proxy_str = os.getenv("PROXY", "")
        if proxy_str:
            try:
                # Format: socks5:host:port or http:host:port
                parts = proxy_str.split(":")
                if len(parts) == 3:
                    proxy_type = parts[0].lower()
                    import socks
                    type_map = {
                        "socks5": socks.SOCKS5,
                        "socks4": socks.SOCKS4,
                        "http": socks.HTTP,
                    }
                    if proxy_type in type_map:
                        self.proxy = (type_map[proxy_type], parts[1], int(parts[2]))
            except Exception as e:
                logging.warning(f"Failed to parse proxy: {e}")

    def validate(self) -> List[str]:
        errors = []
        if not self.api_id:
            errors.append("API_ID is required")
        if not self.api_hash:
            errors.append("API_HASH is required")
        if not self.phone:
            errors.append("PHONE is required")
        if not self.bot_token:
            errors.append("BOT_TOKEN is required")
        if not self.channel_id:
            errors.append("CHANNEL_ID is required")
        return errors


def load_config() -> Config:
    load_dotenv(dotenv_path='accounts.env')
    cfg = Config()
    errors = cfg.validate()
    if errors:
        for e in errors:
            print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    return cfg


# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------


def setup_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS forwarded_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT NOT NULL,
                link_key TEXT NOT NULL UNIQUE,
                link_type TEXT,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_link_key ON forwarded_links (link_key)")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS expired_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link_key TEXT NOT NULL UNIQUE,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_state (
                chat_id INTEGER NOT NULL,
                chat_name TEXT,
                last_scanned_at TIMESTAMP NOT NULL,
                last_scanned_message_date TIMESTAMP NOT NULL,
                PRIMARY KEY (chat_id)
            )
        """)
        await conn.commit()
        cursor = await conn.execute("SELECT link_key FROM expired_links")
        rows = await cursor.fetchall()
        self._expired_cache = {r[0] for r in rows}
        logging.info(f"Loaded {len(self._expired_cache)} expired links from DB cache")

    async def is_known_expired(self, link: str) -> bool:
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

    async def insert_link(self, link: str, source: str = "live") -> bool:
        async with self._lock:
            conn = await self._ensure_conn()
            normalized = self._normalize_link(link)
            link_type = self._detect_link_type(link)
            try:
                await conn.execute(
                    "INSERT OR IGNORE INTO forwarded_links (link, link_key, link_type, source) "
                    "VALUES (?, ?, ?, ?)",
                    (link, normalized, link_type, source),
                )
                await conn.commit()
                cursor = await conn.execute("SELECT changes()")
                changes = await cursor.fetchone()
                return changes[0] > 0
            except aiosqlite.Error as e:
                logging.error(f"Database error while inserting link: {e}")
                return False

    async def get_last_scan_date(self, chat_id: int) -> Optional[datetime]:
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT last_scanned_message_date FROM scan_state WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        if row and row[0]:
            try:
                return datetime.fromisoformat(row[0])
            except Exception:
                return None
        return None

    async def update_scan_state(
        self, chat_id: int, chat_name: str, last_msg_date: datetime
    ) -> None:
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                """
                INSERT INTO scan_state
                    (chat_id, chat_name, last_scanned_at, last_scanned_message_date)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    chat_name = excluded.chat_name,
                    last_scanned_at = excluded.last_scanned_at,
                    last_scanned_message_date = excluded.last_scanned_message_date
                """,
                (chat_id, chat_name, datetime.now().isoformat(), last_msg_date.isoformat()),
            )
            await conn.commit()

    async def reset_scan_state(self) -> int:
        async with self._lock:
            conn = await self._ensure_conn()
            cursor = await conn.execute("DELETE FROM scan_state")
            await conn.commit()
            return cursor.rowcount

    async def count_links(self, source: Optional[str] = None) -> int:
        conn = await self._ensure_conn()
        if source:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM forwarded_links WHERE source = ?", (source,)
            )
        else:
            cursor = await conn.execute("SELECT COUNT(*) FROM forwarded_links")
        row = await cursor.fetchone()
        return row[0] if row else 0

    @staticmethod
    def _normalize_link(link: str) -> str:
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
            link = link.rstrip(".,;:!?)]}>\"'")
            norm = DatabaseManager._normalize_link(link)
            if norm not in seen:
                seen.add(norm)
                unique.append(link)
        return unique


# -------------------------------------------------------------------
# Expired Checker
# -------------------------------------------------------------------


class ExpiredChecker:
    def __init__(self, db: DatabaseManager, timeout: int = 6):
        self.db = db
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
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
        l = link.lower()
        return any(t in l for t in EXPIRABLE_TYPES)

    async def is_expired(self, link: str) -> Tuple[bool, str]:
        if not self.is_checkable(link):
            return False, ""
        if await self.db.is_known_expired(link):
            return True, "cached_expired"
        if await self.db.is_known_valid(link):
            return False, ""

        url = link.strip()
        if not url.startswith("http"):
            url = "https://" + url

        try:
            session = await self._get_session()
            async with session.get(url, allow_redirects=True, ssl=False) as resp:
                if resp.status == 404:
                    await self.db.mark_expired(link)
                    return True, "http_404"
                if resp.status >= 500:
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
                    await self.db.mark_valid(link)
                    return False, ""
                return False, ""
        except asyncio.TimeoutError:
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
    def format_live(group_name, sender_name, message_date, links, message_text):
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

    @staticmethod
    def format_history_batch(batch):
        lines = ["📚 روابط تاريخية مسحوبة من الأرشيف", ""]
        for i, (link, mdate, group_name, sender_name) in enumerate(batch, 1):
            date_str = mdate.strftime("%Y-%m-%d")
            short_group = group_name[:30] + "…" if len(group_name) > 30 else group_name
            lines.append(f"{i}. 🔗 {link}")
            lines.append(f"   📅 {date_str} | 👥 {short_group} | 👤 {sender_name}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def format_scan_summary(total_scanned, total_links, new_links, expired_skipped,
                            chats_scanned, period_desc, duration_sec):
        return (
            "📊 ملخص المسح التاريخي\n\n"
            f"📅 الفترة: {period_desc}\n"
            f"💬 المحادثات المفحوصة: {chats_scanned}\n"
            f"🔍 الرسائل المفحوصة: {total_scanned}\n"
            f"🔗 إجمالي الروابط: {total_links}\n"
            f"✅ روابط جديدة: {new_links}\n"
            f"❌ روابط منتهية تم تخطيها: {expired_skipped}\n"
            f"⏱️ المدة: {duration_sec:.1f} ثانية\n"
        )

    @staticmethod
    def format_help():
        return (
            "🤖 أوامر بوت سحب روابط واتساب v6\n\n"
            "📌 أوامر المسح التاريخي:\n"
            "• /scan_week — مسح آخر 7 أيام\n"
            "• /scan_month — مسح آخر 30 يوم\n"
            "• /scan_60 — مسح آخر 60 يوم\n"
            "• /scan_90 — مسح آخر 90 يوم\n"
            "• /scan_full — مسح كامل\n"
            "• /scan_stop — إيقاف المسح\n"
            "• /last_scan — آخر مسح لكل محادثة\n"
            "• /reset_scan — إعادة تعيين سجل المسح\n\n"
            "📌 أوامر عامة:\n"
            "• /status — حالة البوت\n"
            "• /help — هذه القائمة\n\n"
            "ℹ️ المسح متزايد: يفحص فقط الفترة الجديدة."
        )

    @staticmethod
    def format_status(live_links, history_links, expired_count, scan_running, scan_progress=""):
        return (
            "📊 حالة البوت v6\n\n"
            f"📥 روابط حية مسحوبة: {live_links}\n"
            f"📚 روابط تاريخية مسحوبة: {history_links}\n"
            f"❌ روابط منتهية مكتشفة: {expired_count}\n"
            f"🔄 المسح التاريخي: "
            + (
                "قيد التنفيذ"
                + (f" ({scan_progress})" if scan_progress else "")
                if scan_running
                else "متوقف"
            )
            + "\n"
        )


# -------------------------------------------------------------------
# History Scanner
# -------------------------------------------------------------------


class HistoryScanner:
    def __init__(
        self,
        user_client: TelegramClient,
        bot_client: TelegramClient,
        db: DatabaseManager,
        expired_checker: Optional[ExpiredChecker],
        channel_id: int,
        days_back: Optional[int],
        max_per_chat: int,
        batch_size: int,
        skip_channel_posts: bool,
        progress_callback=None,
    ):
        self.user_client = user_client
        self.bot_client = bot_client
        self.db = db
        self.expired_checker = expired_checker
        self.channel_id = channel_id
        self.days_back = days_back
        self.max_per_chat = max_per_chat
        self.batch_size = batch_size
        self.skip_channel_posts = skip_channel_posts
        self.progress_callback = progress_callback

        self.total_scanned = 0
        self.total_links = 0
        self.new_links = 0
        self.expired_skipped = 0
        self.chats_scanned = 0
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _is_cancelled(self) -> bool:
        return self._cancelled

    async def scan(self) -> str:
        start_time = datetime.now()

        if self.days_back is not None:
            hard_cutoff = datetime.now() - timedelta(days=self.days_back)
        else:
            hard_cutoff = None

        soft_cutoff = None
        try:
            conn = await self.db._ensure_conn()
            cursor = await conn.execute(
                "SELECT MAX(last_scanned_message_date) FROM scan_state"
            )
            row = await cursor.fetchone()
            if row and row[0]:
                soft_cutoff = datetime.fromisoformat(row[0])
                logging.info(f"[SCAN] Incremental: skipping messages before {soft_cutoff}")
        except Exception as e:
            logging.warning(f"Could not load soft_cutoff: {e}")

        if hard_cutoff and soft_cutoff:
            effective_cutoff = max(hard_cutoff, soft_cutoff)
        else:
            effective_cutoff = hard_cutoff or soft_cutoff

        if effective_cutoff:
            days_actual = (datetime.now() - effective_cutoff).days
            period_desc = f"آخر {days_actual} يوم (متزايد)"
        else:
            period_desc = "كامل (بدون حد أيام)"

        logging.info(f"[SCAN] Starting. Period: {period_desc}")

        try:
            dialogs = await self.user_client.get_dialogs()
        except FloodWaitError as e:
            logging.warning(f"[SCAN] FloodWait get_dialogs: {e.seconds}s")
            await asyncio.sleep(e.seconds + 1)
            return period_desc
        except Exception as e:
            logging.error(f"[SCAN] get_dialogs error: {e}")
            return period_desc

        logging.info(f"[SCAN] Found {len(dialogs)} dialogs")

        for idx, dialog in enumerate(dialogs, 1):
            if self._is_cancelled():
                logging.info("[SCAN] Cancelled by user")
                break

            if dialog.id == self.channel_id:
                continue

            if self.skip_channel_posts:
                try:
                    if dialog.is_channel:
                        continue
                except Exception:
                    pass

            chat_name = dialog.name or "Unknown"
            if self.progress_callback:
                try:
                    self.progress_callback(idx, len(dialogs), chat_name)
                except Exception:
                    pass

            try:
                await self._scan_chat(dialog, effective_cutoff, chat_name)
            except FloodWaitError as e:
                logging.warning(f"[SCAN] FloodWait scanning {chat_name}: {e.seconds}s")
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                logging.error(f"[SCAN] Error scanning {chat_name}: {e}")

            await asyncio.sleep(0.3)

        duration = (datetime.now() - start_time).total_seconds()
        await self._send_summary(period_desc, duration)
        logging.info(
            f"[SCAN] Done. Scanned {self.total_scanned} msgs, "
            f"found {self.total_links} links, {self.new_links} new, "
            f"{self.expired_skipped} expired. Duration: {duration:.1f}s"
        )
        return period_desc

    async def _scan_chat(self, dialog, effective_cutoff, chat_name: str) -> None:
        batch: List[Tuple[str, datetime, str, str]] = []
        scanned_in_chat = 0
        last_msg_date: Optional[datetime] = None

        chat_specific_cutoff = effective_cutoff
        if chat_specific_cutoff is None:
            try:
                last_scan_date = await self.db.get_last_scan_date(dialog.id)
                if last_scan_date:
                    chat_specific_cutoff = last_scan_date
            except Exception as e:
                logging.warning(f"Could not get last scan date for chat {dialog.id}: {e}")

        try:
            async for message in self.user_client.iter_messages(
                dialog,
                offset_date=None,
                reverse=False,
                limit=self.max_per_chat,
            ):
                if self._is_cancelled():
                    break

                try:
                    mdate = message.date.replace(tzinfo=None) if message.date else None
                except Exception:
                    mdate = None

                if mdate and chat_specific_cutoff and mdate < chat_specific_cutoff:
                    break

                self.total_scanned += 1
                scanned_in_chat += 1

                if mdate and (last_msg_date is None or mdate > last_msg_date):
                    last_msg_date = mdate

                if not message or not message.text:
                    continue

                links = LinkExtractor.extract_links(message.text)
                if not links:
                    continue

                try:
                    sender = await message.get_sender()
                    sender_name = Monitor._get_sender_name(sender)
                except Exception:
                    sender_name = "Unknown"

                for link in links:
                    self.total_links += 1

                    if self.expired_checker is not None:
                        is_exp, reason = await self.expired_checker.is_expired(link)
                        if is_exp:
                            self.expired_skipped += 1
                            continue

                    try:
                        inserted = await self.db.insert_link(link, source="history")
                    except Exception as db_err:
                        logging.error(f"DB insert error: {db_err}")
                        inserted = True

                    if inserted:
                        self.new_links += 1
                        batch.append((link, mdate or datetime.now(), chat_name, sender_name))

                        if len(batch) >= self.batch_size:
                            await self._send_batch(batch)
                            batch = []

        except FloodWaitError:
            raise
        except Exception as e:
            logging.error(f"[SCAN] iter_messages error: {e}")
            if last_msg_date:
                try:
                    await self.db.update_scan_state(dialog.id, chat_name, last_msg_date)
                except Exception:
                    pass
            return

        if batch:
            await self._send_batch(batch)

        if last_msg_date:
            try:
                await self.db.update_scan_state(dialog.id, chat_name, last_msg_date)
            except Exception as e:
                logging.error(f"Failed to update scan_state: {e}")

        self.chats_scanned += 1

    async def _send_batch(self, batch):
        formatted = MessageFormatter.format_history_batch(batch)
        for attempt in range(1, 4):
            try:
                await self.bot_client.send_message(self.channel_id, formatted)
                return
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except (RPCError, OSError, ConnectionError) as e:
                wait = min(10 * attempt, 60)
                await asyncio.sleep(wait)
        logging.error("[SCAN] Failed to send batch after 3 attempts.")

    async def _send_summary(self, period_desc: str, duration_sec: float):
        if self.new_links == 0 and self.total_scanned == 0 and self.chats_scanned == 0:
            return
        formatted = MessageFormatter.format_scan_summary(
            total_scanned=self.total_scanned,
            total_links=self.total_links,
            new_links=self.new_links,
            expired_skipped=self.expired_skipped,
            chats_scanned=self.chats_scanned,
            period_desc=period_desc,
            duration_sec=duration_sec,
        )
        try:
            await self.bot_client.send_message(self.channel_id, formatted)
        except Exception as e:
            logging.error(f"[SCAN] Failed to send summary: {e}")


# -------------------------------------------------------------------
# Main Monitor (Dual Client)
# -------------------------------------------------------------------


class Monitor:
    def __init__(self, config: Config, db: DatabaseManager, expired_checker: Optional[ExpiredChecker]):
        self.config = config
        self.db = db
        self.expired_checker = expired_checker

        # User client (monitors groups + listens to commands in channel)
        self.user_client: Optional[TelegramClient] = None
        # Bot client (posts messages to channel)
        self.bot_client: Optional[TelegramClient] = None

        self._running = False
        self._handlers_registered = False
        self._send_lock = asyncio.Lock()

        # Scan state
        self._current_scanner: Optional[HistoryScanner] = None
        self._current_scan_task: Optional[asyncio.Task] = None
        self._scan_progress: str = ""

        self._user_task: Optional[asyncio.Task] = None
        self._bot_task: Optional[asyncio.Task] = None
        self._keep_alive_task: Optional[asyncio.Task] = None

        # Track if startup scan was done
        self._startup_scan_done = False

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

    def _create_user_client(self) -> TelegramClient:
        # في وضع السحابة: استخدم StringSession إن وُجد
        if self.config.user_session_string:
            session = StringSession(self.config.user_session_string)
            logging.info("User client: using StringSession (cloud mode)")
        else:
            # الوضع المحلي: استخدم ملف الجلسة
            session = os.path.join(SESSIONS_DIR, f"user_{self.config.phone}")
            logging.info(f"User client: using file session ({session})")

        client = TelegramClient(
            session,
            self.config.api_id,
            self.config.api_hash,
            connection_retries=None,
            retry_delay=5,
            request_retries=5,
            auto_reconnect=True,
            sequential_updates=False,
            proxy=self.config.proxy,
        )
        return client

    def _create_bot_client(self) -> TelegramClient:
        session_path = os.path.join(SESSIONS_DIR, "bot")
        client = TelegramClient(
            session_path,
            self.config.api_id,
            self.config.api_hash,
            connection_retries=None,
            retry_delay=5,
            request_retries=5,
            auto_reconnect=True,
            sequential_updates=False,
            proxy=self.config.proxy,
        )
        return client

    def _register_handlers(self) -> None:
        if self._handlers_registered:
            return

        # User client: monitor new messages in all chats (incoming + outgoing)
        # outgoing=True ضروري لرؤية رسائلك أنت في القناة (للأوامر)
        self.user_client.add_event_handler(
            self._on_new_message,
            events.NewMessage(incoming=True, outgoing=True),
        )
        # User client: monitor edited messages
        self.user_client.add_event_handler(
            self._on_message_edited,
            events.MessageEdited(incoming=True, outgoing=True),
        )
        # User client: listen for commands in destination channel
        # outgoing=True حتى يرى أوامرك أنت عندما ترسلها من نفس الحساب
        self.user_client.add_event_handler(
            self._on_command,
            events.NewMessage(
                chats=self.config.channel_id,
                pattern=r"^/[a-zA-Z_]+",
                incoming=True,
                outgoing=True,
            ),
        )
        self._handlers_registered = True
        logging.info("Handlers registered (user_client) - sees incoming + outgoing")

    async def _on_new_message(self, event):
        await self._process_message(event)

    async def _on_message_edited(self, event):
        await self._process_message(event)

    async def _process_message(self, event):
        try:
            message: Message = event.message
            if not message or not message.text:
                return

            # Skip messages from the destination channel (they're commands/bot posts)
            chat = await event.get_chat()
            if hasattr(chat, 'id') and chat.id == self.config.channel_id:
                return

            group_name = self._get_chat_name(chat)
            sender = await event.get_sender()
            sender_name = self._get_sender_name(sender)

            all_links = LinkExtractor.extract_links(message.text)
            if not all_links:
                return

            logging.info(f"[LIVE] Found {len(all_links)} link(s) in {group_name}")

            valid_links: List[str] = []
            for link in all_links:
                if self.expired_checker is not None:
                    is_exp, reason = await self.expired_checker.is_expired(link)
                    if is_exp:
                        logging.info(f"[LIVE] Skipping expired: {link} ({reason})")
                        continue
                valid_links.append(link)

            if not valid_links:
                return

            new_links = []
            for link in valid_links:
                try:
                    inserted = await self.db.insert_link(link, source="live")
                except Exception as db_err:
                    logging.error(f"DB insert error for {link}: {db_err}")
                    inserted = True
                if inserted:
                    new_links.append(link)

            if not new_links:
                return

            formatted = MessageFormatter.format_live(
                group_name=group_name,
                sender_name=sender_name,
                message_date=message.date,
                links=new_links,
                message_text=message.text,
            )

            await self._send_with_retry(formatted)
            logging.info(
                f"[LIVE] Forwarded {len(new_links)} new link(s) from {group_name}"
            )

        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"Error processing message: {e}", exc_info=True)

    async def _send_with_retry(self, text: str, max_retries: int = 3):
        """Send via BOT client."""
        async with self._send_lock:
            for attempt in range(1, max_retries + 1):
                try:
                    await self.bot_client.send_message(self.config.channel_id, text)
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
            logging.error(f"Failed to send after {max_retries} attempts.")

    async def _on_command(self, event):
        """Handle /commands in the destination channel (received via user_client)."""
        try:
            text = (event.message.text or "").strip()
            parts = text.split()
            if not parts:
                return
            cmd = parts[0].lower()

            # Owner check (optional)
            if self.config.owner_id:
                sender = await event.get_sender()
                sender_id = getattr(sender, 'id', None)
                if sender_id != self.config.owner_id:
                    return  # silently ignore

            logging.info(f"[CMD] Received: {cmd}")

            # Reply via BOT
            async def reply(text: str):
                try:
                    await self.bot_client.send_message(self.config.channel_id, text)
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
                    try:
                        await self.bot_client.send_message(self.config.channel_id, text)
                    except Exception as e2:
                        logging.error(f"Reply failed after FloodWait: {e2}")
                except Exception as e:
                    logging.error(f"Reply failed: {e}")

            if cmd == "/help":
                await reply(MessageFormatter.format_help())

            elif cmd == "/status":
                live = await self.db.count_links("live")
                hist = await self.db.count_links("history")
                expired_count = len(self.db._expired_cache)
                await reply(MessageFormatter.format_status(
                    live_links=live,
                    history_links=hist,
                    expired_count=expired_count,
                    scan_running=self.is_scan_running(),
                    scan_progress=self._scan_progress,
                ))

            elif cmd in SCAN_COMMANDS:
                days = SCAN_COMMANDS[cmd]
                await self._start_scan_command(days, cmd)

            elif cmd == "/scan_stop":
                if self.is_scan_running():
                    self.stop_scan()
                    await reply("⏹️ تم إرسال إشارة إيقاف المسح.")
                else:
                    await reply("ℹ️ لا يوجد مسح قيد التنفيذ.")

            elif cmd == "/last_scan":
                conn = await self.db._ensure_conn()
                cursor = await conn.execute(
                    "SELECT chat_name, last_scanned_at FROM scan_state "
                    "ORDER BY last_scanned_at DESC LIMIT 15"
                )
                rows = await cursor.fetchall()
                if not rows:
                    await reply("ℹ️ لا يوجد سجل مسح سابق.")
                else:
                    lines = ["📋 آخر مسح لكل محادثة:", ""]
                    for chat_name, last_at in rows:
                        try:
                            dt = datetime.fromisoformat(last_at).strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            dt = last_at
                        chat_short = (chat_name or "Unknown")[:25]
                        lines.append(f"• {chat_short} | {dt}")
                    await reply("\n".join(lines))

            elif cmd == "/reset_scan":
                deleted = await self.db.reset_scan_state()
                await reply(
                    f"✅ تم إعادة تعيين سجل المسح.\nحُذف {deleted} سجل."
                )

            else:
                await reply(f"❓ أمر غير معروف: {cmd}\nاكتب /help لعرض الأوامر.")

        except Exception as e:
            logging.error(f"Command handler error: {e}", exc_info=True)

    async def _start_scan_command(self, days: Optional[int], cmd_name: str):
        if self.is_scan_running():
            await self._send_status(
                "⚠️ يوجد مسح قيد التنفيذ بالفعل.\n"
                "انتظر اكتماله أو أرسل /scan_stop لإيقافه."
            )
            return

        days_desc = f"{days} يوم" if days else "كامل (بدون حد)"
        await self._send_status(
            f"🚀 بدء المسح التاريخي ({cmd_name})\n"
            f"📅 الفترة المطلوبة: {days_desc}\n"
            f"🔄 المسح متزايد: لن يفحص إلا الفترة الجديدة فقط.\n"
            f"⏳ جاري الجلب..."
        )

        self._current_scan_task = asyncio.create_task(self._run_history_scan(days))

        def _cleanup(t):
            self._current_scan_task = None
            self._scan_progress = ""

        self._current_scan_task.add_done_callback(_cleanup)

    async def _send_status(self, text: str):
        try:
            await self.bot_client.send_message(self.config.channel_id, text)
        except Exception as e:
            logging.error(f"Status send failed: {e}")

    def is_scan_running(self) -> bool:
        return self._current_scan_task is not None and not self._current_scan_task.done()

    def stop_scan(self) -> None:
        if self._current_scanner:
            self._current_scanner.cancel()

    async def _run_history_scan(self, days: Optional[int]):
        try:
            await asyncio.sleep(2)

            def progress(idx, total, chat_name):
                self._scan_progress = f"{idx}/{total}: {chat_name[:20]}"

            self._current_scanner = HistoryScanner(
                user_client=self.user_client,
                bot_client=self.bot_client,
                db=self.db,
                expired_checker=self.expired_checker,
                channel_id=self.config.channel_id,
                days_back=days,
                max_per_chat=self.config.history_max_per_chat,
                batch_size=self.config.history_batch_size,
                skip_channel_posts=self.config.history_skip_channel_posts,
                progress_callback=progress,
            )
            await self._current_scanner.scan()
        except asyncio.CancelledError:
            logging.info("[SCAN] Cancelled")
        except Exception as e:
            logging.error(f"[SCAN] Fatal error: {e}", exc_info=True)
        finally:
            self._current_scanner = None

    async def _run_startup_scan(self):
        try:
            await asyncio.sleep(5)
            if not self.user_client or not self.user_client.is_connected():
                return
            if not self.bot_client or not self.bot_client.is_connected():
                return

            days = self.config.startup_scan_days
            if days is None:
                return

            logging.info(f"[STARTUP] Running scan: {days} days")
            self._current_scanner = HistoryScanner(
                user_client=self.user_client,
                bot_client=self.bot_client,
                db=self.db,
                expired_checker=self.expired_checker,
                channel_id=self.config.channel_id,
                days_back=days,
                max_per_chat=self.config.history_max_per_chat,
                batch_size=self.config.history_batch_size,
                skip_channel_posts=self.config.history_skip_channel_posts,
            )
            await self._current_scanner.scan()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"[STARTUP] Scan error: {e}", exc_info=True)
        finally:
            self._current_scanner = None

    async def _run_user_client(self):
        backoff = 5
        max_backoff = 600

        while self._running:
            try:
                if not self.user_client.is_connected():
                    logging.info(f"Connecting user_client for {self.config.phone}...")

                    # في وضع السحابة (StringSession): connect فقط (لا كود)
                    if self.config.user_session_string:
                        await self.user_client.connect()
                        if not await self.user_client.is_user_authorized():
                            logging.error(
                                "StringSession not authorized! Re-export it locally."
                            )
                            return
                        self._user_authenticated = True
                    else:
                        # الوضع المحلي: استخدم start(phone=...)
                        if not self._user_authenticated:
                            await self.user_client.start(phone=self.config.phone)
                            self._user_authenticated = True
                        else:
                            await self.user_client.connect()
                            if not await self.user_client.is_user_authorized():
                                await self.user_client.start(phone=self.config.phone)

                    logging.info("User client connected")
                    backoff = 5

                    if (
                        self.config.startup_scan_days is not None
                        and not self._startup_scan_done
                    ):
                        self._startup_scan_done = True
                        asyncio.create_task(self._run_startup_scan())

                await self.user_client.run_until_disconnected()

            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except (RPCError, ConnectionError, OSError) as e:
                logging.error(f"User client error: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logging.error(f"User client unexpected error: {e}", exc_info=True)
            finally:
                if self.user_client and self.user_client.is_connected():
                    try:
                        await self.user_client.disconnect()
                    except Exception:
                        pass

            if not self._running:
                break

            logging.info(f"User client reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    async def _run_bot_client(self):
        backoff = 5
        max_backoff = 600

        while self._running:
            try:
                if not self.bot_client.is_connected():
                    logging.info("Connecting bot_client...")
                    await self.bot_client.start(bot_token=self.config.bot_token)
                    me = await self.bot_client.get_me()
                    logging.info(
                        f"Bot connected as @{me.username} ({me.first_name})"
                    )
                    backoff = 5

                await self.bot_client.run_until_disconnected()

            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except (RPCError, ConnectionError, OSError) as e:
                logging.error(f"Bot client error: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logging.error(f"Bot client unexpected error: {e}", exc_info=True)
            finally:
                if self.bot_client and self.bot_client.is_connected():
                    try:
                        await self.bot_client.disconnect()
                    except Exception:
                        pass

            if not self._running:
                break

            logging.info(f"Bot client reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    async def _keep_alive(self):
        while self._running:
            try:
                await asyncio.sleep(60)
                if self.user_client and not self.user_client.is_connected():
                    logging.warning("Keep-alive: user_client disconnected")
                    try:
                        await self.user_client.connect()
                    except Exception as e:
                        logging.error(f"Keep-alive user reconnect failed: {e}")
                if self.bot_client and not self.bot_client.is_connected():
                    logging.warning("Keep-alive: bot_client disconnected")
                    try:
                        await self.bot_client.connect()
                    except Exception as e:
                        logging.error(f"Keep-alive bot reconnect failed: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Keep-alive error: {e}")

    async def start(self):
        self._running = True

        # Create clients
        self.user_client = self._create_user_client()
        self.bot_client = self._create_bot_client()

        # Register handlers (user_client)
        self._register_handlers()

        # Start tasks
        self._user_task = asyncio.create_task(self._run_user_client())
        # Wait a bit before starting bot (so user is connected first)
        await asyncio.sleep(3)
        self._bot_task = asyncio.create_task(self._run_bot_client())
        self._keep_alive_task = asyncio.create_task(self._keep_alive())

    async def stop(self):
        self._running = False
        self.stop_scan()
        for client in (self.user_client, self.bot_client):
            if client and client.is_connected():
                try:
                    await client.disconnect()
                except Exception:
                    pass
        for t_attr in ("_user_task", "_bot_task", "_keep_alive_task", "_current_scan_task"):
            t = getattr(self, t_attr, None)
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    # Attribute initialized in __init__? Actually, we need to add it
    _user_authenticated = False


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------


async def health_handler(request):
    """HTTP endpoint لـ Render Web Service - يحافظ على الخدمة مستيقظة"""
    return web.Response(text="✅ Bot is running", status=200)


async def keep_alive_task():
    """إرسال طلب HTTP لنفسه كل 10 دقائق لمنع السكون"""
    app_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("APP_URL")
    if not app_url:
        logging.info("Keep-alive disabled (no RENDER_EXTERNAL_URL)")
        return
    # إزالة الشرطة المائلة الأخيرة إن وجدت
    app_url = app_url.rstrip("/")
    health_url = f"{app_url}/health"
    logging.info(f"Keep-alive enabled: will ping {health_url} every 10 minutes")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await asyncio.sleep(600)  # كل 10 دقائق
                async with session.get(health_url, timeout=10) as resp:
                    logging.debug(f"Keep-alive ping: {resp.status}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.warning(f"Keep-alive failed: {e}")


async def start_http_server():
    """تشغيل خادم HTTP خفيف على PORT (يطلبه Render)"""
    port = int(os.getenv("PORT", "10000"))
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"HTTP server listening on port {port}")
    return runner


async def main():
    config = load_config()
    setup_logging(config.log_level)

    logging.info("=== Starting Telegram WhatsApp Link Monitor v6 (DUAL CLIENT) ===")
    logging.info(f"User account: {config.phone}")
    logging.info(f"Bot token: {config.bot_token[:20]}...")
    logging.info(f"Channel ID: {config.channel_id}")
    logging.info(f"Expired check: {'ON' if config.check_expired else 'OFF'}")
    if config.startup_scan_days is not None:
        logging.info(f"Startup scan: {config.startup_scan_days} days")
    if config.proxy:
        logging.info(f"Proxy: {config.proxy}")

    Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

    db = DatabaseManager()
    await db.init_db()

    expired_checker: Optional[ExpiredChecker] = None
    if config.check_expired:
        expired_checker = ExpiredChecker(db, timeout=config.http_timeout)

    monitor = Monitor(config, db, expired_checker)
    await monitor.start()

    # تشغيل خادم HTTP (مطلوب لـ Render Web Service)
    http_runner = await start_http_server()

    # تشغيل مهمة منع السكون (Keep-alive)
    keep_alive = asyncio.create_task(keep_alive_task())

    logging.info(
        "✅ Monitor started. Live monitoring active. Send /help to the channel for commands."
    )

    shutdown_event = asyncio.Event()

    def signal_handler():
        logging.info("Shutdown signal received...")
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

    logging.info("Stopping monitor...")
    keep_alive.cancel()
    await monitor.stop()

    if expired_checker:
        await expired_checker.close()
    await db.close()
    await http_runner.cleanup()
    logging.info("Application stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Interrupted by user.")
    except Exception as e:
        logging.critical(f"Unhandled exception: {e}", exc_info=True)
