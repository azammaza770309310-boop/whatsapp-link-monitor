#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram WhatsApp Link Monitor - v5 (COMMANDS + INCREMENTAL SCAN)

المميزات الجديدة في v5:
1. أزرار أوامر تيليجرام للتحكم الكامل:
   /help               — قائمة الأوامر
   /status             — حالة البوت والإحصائيات
   /scan_week          — مسح آخر 7 أيام (متزايد)
   /scan_month         — مسح آخر 30 يوم (متزايد)
   /scan_60            — مسح آخر 60 يوم (متزايد)
   /scan_90            — مسح آخر 90 يوم (متزايد)
   /scan_full          — مسح كامل بدون حد أيام
   /scan_stop          — إيقاف المسح الحالي
   /last_scan          — تاريخ آخر مسح لكل حساب
   /reset_scan         — إعادة تعيين سجل المسح (يفحص كل شيء من جديد)

2. المسح المتزايد الذكي (Incremental Scan):
   - يحفظ آخر تاريخ مسح لكل محادثة في جدول scan_state
   - عند طلب /scan_month مرة ثانية، يفحص فقط الفترة الجديدة
     (مثلاً: لو طلبته بعد أسبوع، يفحص آخر 7 أيام فقط بدلاً من 30 يوم كاملة)
   - يقلل الضغط على تيليجرام بشكل كبير
   - يستثني القناة الوجهة نفسها

3. حماية الأوامر:
   - فقط الأوامر القادمة من القناة الوجهة (CHANNEL_ID) تُنفّذ
   - أو من المالك المحدد في OWNER_USERNAME (اختياري)

4. منع المسح المتزامن:
   - لو مسح قيد التنفيذ، يرفض الطلب ويعلم المستخدم

5. ردود فورية:
   - عند بدء المسح: "بدأ المسح..."
   - أثناء المسح: لا إزعاج
   - عند الانتهاء: ملخص إحصائي

6. يحتفظ بكل مميزات v2-v4.
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

# خريطة أوامر المسح → عدد الأيام (None = بلا حد)
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


class AccountConfig:
    def __init__(self, api_id: int, api_hash: str, phone: str, enabled: bool):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.enabled = enabled


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
        history_scan: bool = False,
        history_days: int = 30,
        history_max_per_chat: int = 500,
        history_batch_size: int = 5,
        history_skip_channel_posts: bool = False,
        owner_username: Optional[str] = None,
        startup_scan_days: Optional[int] = None,
    ):
        self.accounts = accounts
        self.channel_id = channel_id
        self.channel_username = channel_username
        self.channel_link = channel_link
        self.log_level = log_level
        self.check_expired = check_expired
        self.http_timeout = http_timeout
        self.history_scan = history_scan
        self.history_days = history_days
        self.history_max_per_chat = history_max_per_chat
        self.history_batch_size = history_batch_size
        self.history_skip_channel_posts = history_skip_channel_posts
        self.owner_username = owner_username
        # أيام المسح عند بدء التشغيل (None = لا مسح، 0 = استخدم history_days)
        self.startup_scan_days = startup_scan_days


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

    history_scan = os.getenv("HISTORY_SCAN", "false").lower() == "true"
    history_days = min(int(os.getenv("HISTORY_DAYS", "30")), 90)
    history_max_per_chat = int(os.getenv("HISTORY_MAX_PER_CHAT", "500"))
    history_batch_size = max(1, min(int(os.getenv("HISTORY_BATCH_SIZE", "5")), 20))
    history_skip_channel_posts = (
        os.getenv("HISTORY_SKIP_CHANNEL_POSTS", "false").lower() == "true"
    )

    owner_username = os.getenv("OWNER_USERNAME")
    if owner_username:
        owner_username = owner_username.lstrip("@")

    startup_scan_days_str = os.getenv("STARTUP_SCAN_DAYS")
    startup_scan_days: Optional[int] = None
    if startup_scan_days_str and startup_scan_days_str.lower() not in ("none", "null", ""):
        startup_scan_days = int(startup_scan_days_str)

    return Config(
        accounts=accounts,
        channel_id=channel_id,
        channel_username=channel_username,
        channel_link=channel_link,
        log_level=log_level,
        check_expired=check_expired,
        http_timeout=http_timeout,
        history_scan=history_scan,
        history_days=history_days,
        history_max_per_chat=history_max_per_chat,
        history_batch_size=history_batch_size,
        history_skip_channel_posts=history_skip_channel_posts,
        owner_username=owner_username,
        startup_scan_days=startup_scan_days,
    )


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
        # جدول جديد: سجل آخر مسح لكل (حساب + محادثة)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_state (
                phone TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                chat_name TEXT,
                last_scanned_at TIMESTAMP NOT NULL,
                last_scanned_message_date TIMESTAMP NOT NULL,
                PRIMARY KEY (phone, chat_id)
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

    # ---- إدارة سجل المسح ----

    async def get_last_scan_date(self, phone: str, chat_id: int) -> Optional[datetime]:
        """يُرجع تاريخ آخر رسالة تم فحصها في محادثة معينة لحساب معين."""
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT last_scanned_message_date FROM scan_state WHERE phone = ? AND chat_id = ?",
            (phone, chat_id),
        )
        row = await cursor.fetchone()
        if row and row[0]:
            try:
                return datetime.fromisoformat(row[0])
            except Exception:
                return None
        return None

    async def update_scan_state(
        self, phone: str, chat_id: int, chat_name: str, last_msg_date: datetime
    ) -> None:
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                """
                INSERT INTO scan_state
                    (phone, chat_id, chat_name, last_scanned_at, last_scanned_message_date)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(phone, chat_id) DO UPDATE SET
                    chat_name = excluded.chat_name,
                    last_scanned_at = excluded.last_scanned_at,
                    last_scanned_message_date = excluded.last_scanned_message_date
                """,
                (
                    phone,
                    chat_id,
                    chat_name,
                    datetime.now().isoformat(),
                    last_msg_date.isoformat(),
                ),
            )
            await conn.commit()

    async def reset_scan_state(self, phone: Optional[str] = None) -> int:
        """يعيد تعيين سجل المسح. يُرجع عدد الصفوف المحذوفة."""
        async with self._lock:
            conn = await self._ensure_conn()
            if phone:
                cursor = await conn.execute(
                    "DELETE FROM scan_state WHERE phone = ?", (phone,)
                )
            else:
                cursor = await conn.execute("DELETE FROM scan_state")
            await conn.commit()
            return cursor.rowcount

    async def get_scan_summary(self) -> List[Tuple[str, str, str]]:
        """يُرجع ملخص آخر مسح لكل (حساب، محادثة)."""
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT phone, chat_name, last_scanned_at FROM scan_state "
            "ORDER BY last_scanned_at DESC LIMIT 20"
        )
        return await cursor.fetchall()

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
# Expired Link Checker
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
    def format_live(
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

    @staticmethod
    def format_history_batch(
        batch: List[Tuple[str, datetime, str, str]]
    ) -> str:
        lines = ["📚 روابط تاريخية مسحوبة من الأرشيف", ""]
        for i, (link, mdate, group_name, sender_name) in enumerate(batch, 1):
            date_str = mdate.strftime("%Y-%m-%d")
            short_group = group_name[:30] + "…" if len(group_name) > 30 else group_name
            lines.append(f"{i}. 🔗 {link}")
            lines.append(f"   📅 {date_str} | 👥 {short_group} | 👤 {sender_name}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def format_scan_summary(
        total_scanned: int,
        total_links: int,
        new_links: int,
        expired_skipped: int,
        chats_scanned: int,
        period_desc: str,
        duration_sec: float,
    ) -> str:
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
    def format_help() -> str:
        return (
            "🤖 أوامر بوت سحب روابط واتساب\n\n"
            "📌 أوامر المسح التاريخي:\n"
            "• /scan_week — مسح آخر 7 أيام\n"
            "• /scan_month — مسح آخر 30 يوم\n"
            "• /scan_60 — مسح آخر 60 يوم\n"
            "• /scan_90 — مسح آخر 90 يوم\n"
            "• /scan_full — مسح كامل (كل المحفوظ)\n"
            "• /scan_stop — إيقاف المسح الحالي\n"
            "• /last_scan — عرض آخر مسح لكل محادثة\n"
            "• /reset_scan — إعادة تعيين سجل المسح\n\n"
            "📌 أوامر عامة:\n"
            "• /status — حالة البوت والإحصائيات\n"
            "• /help — هذه القائمة\n\n"
            "ℹ️ المسح متزايد: عند تكرار الأمر، يفحص البوت\n"
            "   فقط الفترة الجديدة منذ آخر مسح لتقليل الضغط."
        )

    @staticmethod
    def format_status(
        monitors_count: int,
        live_links: int,
        history_links: int,
        expired_count: int,
        scan_running: bool,
        scan_progress: str = "",
    ) -> str:
        return (
            "📊 حالة البوت\n\n"
            f"👤 الحسابات النشطة: {monitors_count}\n"
            f"📥 روابط حية مسحوبة: {live_links}\n"
            f"📚 روابط تاريخية مسحوبة: {history_links}\n"
            f"❌ روابط منتهية مكتشفة: {expired_count}\n"
            f"🔄 المسح التاريخي: {'قيد التنفيذ' + (f' ({scan_progress})' if scan_progress else '') if scan_running else 'متوقف'}\n"
        )


# -------------------------------------------------------------------
# History Scanner (مع المسح المتزايد)
# -------------------------------------------------------------------


class HistoryScanner:
    """يفحص الرسائل التاريخية مع دعم المسح المتزايد."""

    def __init__(
        self,
        client: TelegramClient,
        db: DatabaseManager,
        expired_checker: Optional[ExpiredChecker],
        channel_id: int,
        days_back: Optional[int],
        max_per_chat: int,
        batch_size: int,
        skip_channel_posts: bool,
        send_lock: asyncio.Lock,
        phone: str,
        incremental: bool = True,
        progress_callback=None,
    ):
        self.client = client
        self.db = db
        self.expired_checker = expired_checker
        self.channel_id = channel_id
        self.days_back = days_back
        self.max_per_chat = max_per_chat
        self.batch_size = batch_size
        self.skip_channel_posts = skip_channel_posts
        self.send_lock = send_lock
        self.phone = phone
        self.incremental = incremental
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
        """
        نفّذ المسح. يُرجع وصف الفترة الممسوحة فعلياً (للملخص).
        """
        start_time = datetime.now()

        # تحديد الحد الأقصى للفترة
        if self.days_back is not None:
            hard_cutoff = datetime.now() - timedelta(days=self.days_back)
        else:
            hard_cutoff = None  # لا حد (scan_full)

        # تحديد الحد الأدنى (آخر مسح) - للمسح المتزايد
        # نأخذ أحدث last_scanned_message_date عبر كل محادثات هذا الحساب
        soft_cutoff = None
        if self.incremental:
            try:
                conn = await self.db._ensure_conn()
                cursor = await conn.execute(
                    "SELECT MAX(last_scanned_message_date) FROM scan_state WHERE phone = ?",
                    (self.phone,),
                )
                row = await cursor.fetchone()
                if row and row[0]:
                    soft_cutoff = datetime.fromisoformat(row[0])
                    logging.info(
                        f"[HISTORY {self.phone}] Incremental mode: skipping messages "
                        f"older than last scan ({soft_cutoff})"
                    )
            except Exception as e:
                logging.warning(f"Could not load soft_cutoff: {e}")

        # الفعلي نأخذ الأحدث بين hard_cutoff و soft_cutoff
        # (أي رسالة أحدث من كليهما)
        # نريد: نبدأ من رسائل أحدث من effective_cutoff
        if hard_cutoff and soft_cutoff:
            effective_cutoff = max(hard_cutoff, soft_cutoff)  # الأحدث
        else:
            effective_cutoff = hard_cutoff or soft_cutoff

        # وصف الفترة للملخص
        if effective_cutoff:
            days_actual = (datetime.now() - effective_cutoff).days
            period_desc = f"آخر {days_actual} يوم (متزايد)"
        else:
            period_desc = "كامل (بدون حد أيام)"

        logging.info(
            f"[HISTORY {self.phone}] Starting scan. Period: {period_desc}"
        )

        # جلب المحادثات
        try:
            dialogs = await self.client.get_dialogs()
        except FloodWaitError as e:
            logging.warning(f"[HISTORY {self.phone}] FloodWait get_dialogs: {e.seconds}s")
            await asyncio.sleep(e.seconds + 1)
            return period_desc
        except Exception as e:
            logging.error(f"[HISTORY {self.phone}] get_dialogs error: {e}")
            return period_desc

        logging.info(f"[HISTORY {self.phone}] Found {len(dialogs)} dialogs")

        for idx, dialog in enumerate(dialogs, 1):
            if self._is_cancelled():
                logging.info(f"[HISTORY {self.phone}] Scan cancelled by user")
                break

            # تخطي القناة الوجهة
            try:
                if dialog.id == self.channel_id:
                    continue
            except Exception:
                pass

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
                await self._scan_chat(
                    dialog, effective_cutoff, chat_name
                )
            except FloodWaitError as e:
                logging.warning(
                    f"[HISTORY {self.phone}] FloodWait scanning {chat_name}: "
                    f"{e.seconds}s"
                )
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                logging.error(
                    f"[HISTORY {self.phone}] Error scanning {chat_name}: {e}"
                )

            await asyncio.sleep(0.3)

        duration = (datetime.now() - start_time).total_seconds()

        # إرسال ملخص
        await self._send_summary(period_desc, duration)
        logging.info(
            f"[HISTORY {self.phone}] Done. Scanned {self.total_scanned} msgs, "
            f"found {self.total_links} links, {self.new_links} new, "
            f"{self.expired_skipped} expired. Duration: {duration:.1f}s"
        )
        return period_desc

    async def _scan_chat(self, dialog, effective_cutoff, chat_name: str) -> None:
        batch: List[Tuple[str, datetime, str, str]] = []
        scanned_in_chat = 0
        last_msg_date: Optional[datetime] = None

        # للمسح المتزايد: نحصل على آخر مسح لهذه المحادثة
        chat_specific_cutoff = effective_cutoff
        if self.incremental:
            try:
                last_scan_date = await self.db.get_last_scan_date(
                    self.phone, dialog.id
                )
                if last_scan_date:
                    # نأخذ الأحدث بين effective_cutoff و last_scan_date
                    if chat_specific_cutoff:
                        chat_specific_cutoff = max(chat_specific_cutoff, last_scan_date)
                    else:
                        chat_specific_cutoff = last_scan_date
            except Exception as e:
                logging.warning(f"Could not get last scan date for chat {dialog.id}: {e}")

        try:
            # iter_messages مع offset_date يبدأ من الأحدث ويرجع للقدم
            async for message in self.client.iter_messages(
                dialog,
                offset_date=None,
                reverse=False,
                limit=self.max_per_chat,
            ):
                if self._is_cancelled():
                    break

                # تحويل التاريخ (telethon يرجع tz-aware)
                try:
                    mdate = message.date.replace(tzinfo=None) if message.date else None
                except Exception:
                    mdate = None

                if mdate and chat_specific_cutoff and mdate < chat_specific_cutoff:
                    # وصلنا لما قبل تاريخ آخر مسح، توقف
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

                # اسم المرسل
                try:
                    sender = await message.get_sender()
                    sender_name = AccountMonitor._get_sender_name(sender)
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
            logging.error(f"[HISTORY {self.phone}] iter_messages error: {e}")
            # حتى عند الخطأ، نحدث scan_state بما وصلنا له
            if last_msg_date:
                try:
                    await self.db.update_scan_state(
                        self.phone, dialog.id, chat_name, last_msg_date
                    )
                except Exception:
                    pass
            return

        # إرسال ما تبقى
        if batch:
            await self._send_batch(batch)

        # تحديث scan_state
        if last_msg_date:
            try:
                await self.db.update_scan_state(
                    self.phone, dialog.id, chat_name, last_msg_date
                )
            except Exception as e:
                logging.error(f"Failed to update scan_state: {e}")

        self.chats_scanned += 1

    async def _send_batch(self, batch: List[Tuple[str, datetime, str, str]]) -> None:
        formatted = MessageFormatter.format_history_batch(batch)
        async with self.send_lock:
            for attempt in range(1, 4):
                try:
                    await self.client.send_message(self.channel_id, formatted)
                    return
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
                except (RPCError, OSError, ConnectionError) as e:
                    wait = min(10 * attempt, 60)
                    await asyncio.sleep(wait)
            logging.error(f"[HISTORY {self.phone}] Failed to send batch after 3 attempts.")

    async def _send_summary(self, period_desc: str, duration_sec: float) -> None:
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
        async with self.send_lock:
            try:
                await self.client.send_message(self.channel_id, formatted)
            except Exception as e:
                logging.error(f"[HISTORY {self.phone}] Failed to send summary: {e}")


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
        config: Optional[Config] = None,
    ):
        self.config = account_config
        self.channel_id = channel_id
        self.db = db_manager
        self.expired_checker = expired_checker
        self.global_config = config
        self.client: Optional[TelegramClient] = None
        self.task: Optional[asyncio.Task] = None
        self._running = False
        self._handlers_registered = False
        self._send_lock = asyncio.Lock()
        self._session_authenticated = False
        self._keep_alive_task: Optional[asyncio.Task] = None

        # حالة المسح
        self._current_scanner: Optional[HistoryScanner] = None
        self._current_scan_task: Optional[asyncio.Task] = None
        self._scan_progress: str = ""

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
                f"[LIVE {self.config.phone}] Found {len(all_links)} link(s) in {group_name}"
            )

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
                f"[LIVE {self.config.phone}] Forwarded {len(new_links)} new link(s) "
                f"from {group_name}"
            )

        except FloodWaitError as e:
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
                    await asyncio.sleep(e.seconds + 1)
                except (RPCError, OSError, ConnectionError) as e:
                    wait = min(10 * attempt, 60)
                    await asyncio.sleep(wait)
            logging.error(f"Failed to send after {max_retries} attempts.")

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

        # معالجات الرسائل العادية
        self.client.add_event_handler(
            self._on_new_message,
            events.NewMessage(incoming=True),
        )
        self.client.add_event_handler(
            self._on_message_edited,
            events.MessageEdited(incoming=True),
        )

        # معالج الأوامر (يتلقى الرسائل الواردة من القناة الوجهة فقط)
        self.client.add_event_handler(
            self._on_command,
            events.NewMessage(
                incoming=True,
                chats=self.channel_id,
                pattern=r"^/[a-zA-Z_]+",
            ),
        )
        self._handlers_registered = True
        logging.info(f"Handlers registered for {self.config.phone} (including commands)")

    async def _on_command(self, event) -> None:
        """معالج أوامر المسح المرسلة إلى القناة الوجهة."""
        try:
            text = (event.message.text or "").strip()
            parts = text.split()
            if not parts:
                return
            cmd = parts[0].lower()
            args = parts[1:]

            # التحقق من المالك (إن كان محدداً)
            if self.global_config and self.global_config.owner_username:
                try:
                    sender = await event.get_sender()
                    sender_username = getattr(sender, "username", "") or ""
                    if sender_username.lower() != self.global_config.owner_username.lower():
                        # تجاهل صامت - ليس المالك
                        return
                except Exception:
                    pass

            logging.info(f"[CMD {self.config.phone}] Received: {cmd} {args}")

            if cmd == "/help":
                await self._reply(event, MessageFormatter.format_help())

            elif cmd == "/status":
                live = await self.db.count_links("live")
                hist = await self.db.count_links("history")
                expired_count = len(self.db._expired_cache)
                await self._reply(
                    event,
                    MessageFormatter.format_status(
                        monitors_count=1,  # هذا الحساب
                        live_links=live,
                        history_links=hist,
                        expired_count=expired_count,
                        scan_running=self.is_scan_running(),
                        scan_progress=self._scan_progress,
                    ),
                )

            elif cmd in SCAN_COMMANDS:
                days = SCAN_COMMANDS[cmd]
                await self._start_scan_command(event, days, cmd)

            elif cmd == "/scan_stop":
                if self.is_scan_running():
                    self.stop_scan()
                    await self._reply(event, "⏹️ تم إرسال إشارة إيقاف المسح. سيتوقف قريباً.")
                else:
                    await self._reply(event, "ℹ️ لا يوجد مسح قيد التنفيذ حالياً.")

            elif cmd == "/last_scan":
                rows = await self.db.get_scan_summary()
                if not rows:
                    await self._reply(event, "ℹ️ لا يوجد سجل مسح سابق.")
                else:
                    lines = ["📋 آخر مسح لكل محادثة:", ""]
                    for phone, chat_name, last_at in rows[:15]:
                        try:
                            dt = datetime.fromisoformat(last_at).strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            dt = last_at
                        chat_short = (chat_name or "Unknown")[:25]
                        lines.append(f"• {phone} | {chat_short} | {dt}")
                    if len(rows) > 15:
                        lines.append(f"\n... و {len(rows) - 15} محادثة أخرى")
                    await self._reply(event, "\n".join(lines))

            elif cmd == "/reset_scan":
                deleted = await self.db.reset_scan_state(self.config.phone)
                logging.info(f"[CMD] Reset scan_state for {self.config.phone}: {deleted} rows")
                await self._reply(
                    event,
                    f"✅ تم إعادة تعيين سجل المسح.\n"
                    f"حُذف {deleted} سجل.\n"
                    f"الآن عند طلب /scan_month سيبدأ من الصفر.",
                )

            else:
                await self._reply(
                    event,
                    f"❓ أمر غير معروف: {cmd}\nاكتب /help لعرض الأوامر.",
                )

        except Exception as e:
            logging.error(f"Command handler error: {e}", exc_info=True)
            try:
                await self._reply(event, f"❌ خطأ في تنفيذ الأمر: {e}")
            except Exception:
                pass

    async def _reply(self, event, text: str) -> None:
        """يرد على الأمر في نفس المحادثة (القناة الوجهة)."""
        try:
            await event.reply(text)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
            try:
                await event.reply(text)
            except Exception as e2:
                logging.error(f"Reply failed after FloodWait: {e2}")
        except Exception as e:
            logging.error(f"Reply failed: {e}")

    async def _start_scan_command(self, event, days: Optional[int], cmd_name: str) -> None:
        """بدء مسح استجابةً لأمر تيليجرام."""
        if self.is_scan_running():
            await self._reply(
                event,
                "⚠️ يوجد مسح قيد التنفيذ بالفعل.\n"
                "انتظر اكتماله أو أرسل /scan_stop لإيقافه.",
            )
            return

        days_desc = f"{days} يوم" if days else "كامل (بدون حد)"
        await self._reply(
            event,
            f"🚀 بدء المسح التاريخي ({cmd_name})\n"
            f"📅 الفترة المطلوبة: {days_desc}\n"
            f"🔄 المسح متزايد: لن يفحص إلا الفترة الجديدة فقط.\n"
            f"⏳ جاري الجلب...",
        )

        # بدء المسح في الخلفية
        self._current_scan_task = asyncio.create_task(self._run_history_scan(days))
        # مهمة لتنظيف المرجع
        def _cleanup(t):
            self._current_scan_task = None
            self._scan_progress = ""
        self._current_scan_task.add_done_callback(_cleanup)

    def is_scan_running(self) -> bool:
        return self._current_scan_task is not None and not self._current_scan_task.done()

    def stop_scan(self) -> None:
        if self._current_scanner:
            self._current_scanner.cancel()

    async def _run_history_scan(self, days: Optional[int]) -> None:
        """نفّذ مسح تاريخي بأمر مستخدم."""
        try:
            await asyncio.sleep(2)  # إمهار قصير
            if not self.client or not self.client.is_connected():
                logging.warning(f"[HISTORY {self.config.phone}] Client not connected")
                return

            def progress(idx, total, chat_name):
                self._scan_progress = f"{idx}/{total}: {chat_name[:20]}"

            self._current_scanner = HistoryScanner(
                client=self.client,
                db=self.db,
                expired_checker=self.expired_checker,
                channel_id=self.channel_id,
                days_back=days,
                max_per_chat=self.global_config.history_max_per_chat,
                batch_size=self.global_config.history_batch_size,
                skip_channel_posts=self.global_config.history_skip_channel_posts,
                send_lock=self._send_lock,
                phone=self.config.phone,
                incremental=True,
                progress_callback=progress,
            )
            await self._current_scanner.scan()
        except asyncio.CancelledError:
            logging.info(f"[HISTORY {self.config.phone}] Scan task cancelled")
        except Exception as e:
            logging.error(f"[HISTORY {self.config.phone}] Scan error: {e}", exc_info=True)
        finally:
            self._current_scanner = None

    async def _run_startup_scan(self) -> None:
        """نفّذ المسح عند بدء التشغيل إن كان مفعّلاً."""
        try:
            await asyncio.sleep(5)
            if not self.client or not self.client.is_connected():
                return

            days = self.global_config.startup_scan_days
            if days is None:
                return  # لا مسح

            logging.info(
                f"[STARTUP {self.config.phone}] Running startup scan: {days} days"
            )

            self._current_scanner = HistoryScanner(
                client=self.client,
                db=self.db,
                expired_checker=self.expired_checker,
                channel_id=self.channel_id,
                days_back=days,
                max_per_chat=self.global_config.history_max_per_chat,
                batch_size=self.global_config.history_batch_size,
                skip_channel_posts=self.global_config.history_skip_channel_posts,
                send_lock=self._send_lock,
                phone=self.config.phone,
                incremental=True,
            )
            await self._current_scanner.scan()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"[STARTUP {self.config.phone}] Scan error: {e}", exc_info=True)
        finally:
            self._current_scanner = None

    async def _run_client(self) -> None:
        self.client = self._create_client()
        self._register_handlers()

        backoff = 5
        max_backoff = 600
        startup_scan_done = False

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
                            await self.client.start(phone=self.config.phone)
                    logging.info(f"Client connected for {self.config.phone}")
                    backoff = 5

                    # بدء مسح بدء التشغيل
                    if (
                        self.global_config
                        and self.global_config.startup_scan_days is not None
                        and not startup_scan_done
                    ):
                        startup_scan_done = True
                        asyncio.create_task(self._run_startup_scan())

                await self.client.run_until_disconnected()

            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except (RPCError, ConnectionError, OSError) as e:
                logging.error(f"Client error for {self.config.phone}: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logging.error(f"Unexpected error for {self.config.phone}: {e}", exc_info=True)
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
        self.stop_scan()
        if self.client and self.client.is_connected():
            try:
                await self.client.disconnect()
            except Exception:
                pass
        for t_attr in ("task", "_keep_alive_task", "_current_scan_task"):
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
    logging.info("=== Starting Telegram WhatsApp Link Monitor v5 ===")
    logging.info(
        f"Expired check: {'ON' if config.check_expired else 'OFF'} | "
        f"Startup scan: "
        f"{config.startup_scan_days if config.startup_scan_days is not None else 'OFF'}"
    )

    Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

    db = DatabaseManager()
    await db.init_db()

    expired_checker: Optional[ExpiredChecker] = None
    if config.check_expired:
        expired_checker = ExpiredChecker(db, timeout=config.http_timeout)

    monitors = []
    for acc_config in config.accounts:
        if not acc_config.enabled:
            continue
        logging.info(f"Setting up account: {acc_config.phone}")
        monitor = AccountMonitor(
            acc_config, config.channel_id, db, expired_checker, config=config
        )
        monitors.append(monitor)
        await monitor.start()

    if not monitors:
        logging.error("No enabled accounts found. Exiting.")
        await db.close()
        if expired_checker:
            await expired_checker.close()
        sys.exit(1)

    logging.info(
        f"All {len(monitors)} monitor(s) started. "
        f"Live monitoring active. Send /help to the channel for commands."
    )

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
