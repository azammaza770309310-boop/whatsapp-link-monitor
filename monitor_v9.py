#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram Help Requests Monitor - v9 (SIMPLE)
البوت البسيط - يراقب المجموعات التي يُضاف إليها كـ admin

كيف يعمل:
1. تشارك رابط البوت مع أصدقائك: t.me/YourBot
2. كل صديق يضغط Start
3. يضيف البوت لمجموعته الدراسية كـ Admin
4. البوت يراقب المجموعة تلقائياً
5. كل طلب مساعدة يُنشر في قناتك المشتركة

لا حاجة لـ:
- أرقام هواتف
- أكواد تيليجرام
- StringSession
- Pydroid 3
- أي تثبيت

فقط: اضغط Start + أضفني لمجموعتك!
"""

import asyncio
import logging
import os
import re
import signal
import sys
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Set

import aiohttp
import aiosqlite
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import Message, ChatAdminRights
from aiohttp import web

# -------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------

SESSIONS_DIR = "sessions"
DATA_DIR = "data"
LOGS_DIR = "logs"
DB_FILE = os.path.join(DATA_DIR, "help_requests.db")
LOG_FILE = os.path.join(LOGS_DIR, "app.log")
MAX_MESSAGE_LENGTH = 800

# كلمات طلبات المساعدة الدراسية
HELP_KEYWORDS = [
    "ابي", "أبي", "اري", "أريد", "ابغى", "ابغ", "احتاج", "أحتاج",
    "ممكن", "ممكن تساعدني", "ممكن تساعد", "طلبتكم", "طلبكم",
    "مساعدة", "ساعدوني", "ساعدني", "يساعدني", "يساعد", "نبي", "نريد",
    "اسايمنت", "assignment", "اسمنت", "اسمنت",
    "واجب", "واجبات", "homework", "hw",
    "بحث", "ابحاث", "research", "report",
    "عرض", "عرض تقديمي", "presentation", "presentaion",
    "برزنتيشن", "بريزنتيشن", "برزنت",
    "مشروع", "مشروع تخرج", "project",
    "سكليف", "اسكليف", "تصميم",
    "فاينل", "final", "نهائي",
    "كويز", "quiz", "اختبار", "اختبارات",
    "استماع", "محاضرة", "ملخص", "خلاصة",
    "تفسير", "شرح", "توضيح",
    "بمقابل", "مقابل", "بأجر", "باجر", "مدفوع",
    "بدون مقابل", "مجان", "مجاناً", "مجاني", "free",
    "مختص", "خبير", "شاطر", "محترف", "بديع", "مبدع",
    "مين يقدر", "مين يعرف", "مين يساعد",
    "من يعرف", "من يقدر", "من يساعد",
    "ثقة", "بثقة", "موثوق",
    "شطور", "شطورة",
    "لي", "الي", "لى",
    "help", "help me", "ممكن مساعدة",
]

# كلمات سبام
SPAM_KEYWORDS = [
    "buy", "sell", "تخفيض", "عرض خاص", "limited offer",
    "click here", "اضغط هنا", "follow me", "تابعني",
    "اشتراكي", "يوتيوب", "tiktok", "انستقرام",
    "https://t.me/+", "https://telegram.me/joinchat",
    "كورس مجاني", "تطبيق مجاني", "ربح", "كاش", "money",
]

HELP_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(kw) for kw in HELP_KEYWORDS if ' ' not in kw) + r')\b',
    re.IGNORECASE
)


# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------


class Config:
    def __init__(self):
        load_dotenv(dotenv_path='accounts.env')
        self.api_id = int(os.getenv("API_ID", "0") or "0")
        self.api_hash = os.getenv("API_HASH", "") or ""
        self.bot_token = os.getenv("BOT_TOKEN", "") or ""
        self.channel_id = int(os.getenv("CHANNEL_ID", "0") or "0")
        self.owner_id = None
        oid = os.getenv("OWNER_ID", "")
        if oid:
            try:
                self.owner_id = int(oid)
            except:
                pass
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.history_max_per_chat = int(os.getenv("HISTORY_MAX_PER_CHAT", "500"))
        self.history_batch_size = max(1, min(int(os.getenv("HISTORY_BATCH_SIZE", "5")), 20))
        self.startup_scan_days = None
        ssd = os.getenv("STARTUP_SCAN_DAYS", "")
        if ssd and ssd.lower() not in ("none", "null", ""):
            try:
                self.startup_scan_days = int(ssd)
            except:
                pass
        if self.startup_scan_days is None and not ssd:
            self.startup_scan_days = 7
            logging.info("Default startup scan: 7 days for new groups")

    def validate(self):
        errors = []
        if not self.api_id: errors.append("API_ID required")
        if not self.api_hash: errors.append("API_HASH required")
        if not self.bot_token: errors.append("BOT_TOKEN required")
        if not self.channel_id: errors.append("CHANNEL_ID required")
        return errors


def setup_logging(level_name):
    level = getattr(logging, level_name.upper(), logging.INFO)
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(formatter)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(fh)
        root.addHandler(ch)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


# -------------------------------------------------------------------
# Database Manager
# -------------------------------------------------------------------


class DatabaseManager:
    def __init__(self, db_path=DB_FILE):
        self.db_path = db_path
        self._conn = None
        self._lock = asyncio.Lock()

    async def _ensure_conn(self):
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self.db_path, timeout=30.0)
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA busy_timeout=30000")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    async def init_db(self):
        conn = await self._ensure_conn()
        # المجموعات المراقَبة (التي البوت مشرف فيها)
        await conn.execute("""CREATE TABLE IF NOT EXISTS monitored_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL UNIQUE,
            chat_title TEXT,
            chat_type TEXT,
            added_by_user_id INTEGER,
            added_by_name TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            last_scanned_at TIMESTAMP,
            last_scanned_message_date TIMESTAMP)""")
        # طلبات المساعدة المنشورة
        await conn.execute("""CREATE TABLE IF NOT EXISTS forwarded_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_text TEXT NOT NULL,
            message_date TIMESTAMP,
            group_id INTEGER,
            group_name TEXT,
            sender_name TEXT,
            sender_id INTEGER,
            message_link TEXT,
            content_hash TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_content_hash ON forwarded_requests (content_hash)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_group ON forwarded_requests (group_id)")
        # المستخدمون المنضمون
        await conn.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            groups_added INTEGER DEFAULT 0)""")
        await conn.commit()

    async def add_user(self, user_id: int, username: str, first_name: str):
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                """INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)""",
                (user_id, username, first_name))
            await conn.commit()

    async def add_monitored_group(self, chat_id: int, chat_title: str, chat_type: str,
                                    added_by_user_id: int, added_by_name: str) -> bool:
        async with self._lock:
            conn = await self._ensure_conn()
            try:
                await conn.execute(
                    """INSERT OR REPLACE INTO monitored_groups
                    (chat_id, chat_title, chat_type, added_by_user_id, added_by_name, is_active)
                    VALUES (?, ?, ?, ?, ?, 1)""",
                    (chat_id, chat_title, chat_type, added_by_user_id, added_by_name))
                await conn.commit()
                return True
            except Exception as e:
                logging.error(f"Add group error: {e}")
                return False

    async def deactivate_group(self, chat_id: int):
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute("UPDATE monitored_groups SET is_active = 0 WHERE chat_id = ?", (chat_id,))
            await conn.commit()

    async def get_active_groups(self) -> List[Dict]:
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT chat_id, chat_title, last_scanned_message_date FROM monitored_groups WHERE is_active = 1")
        rows = await cursor.fetchall()
        return [{"chat_id": r[0], "chat_title": r[1], "last_scanned": r[2]} for r in rows]

    async def get_last_scan_date(self, chat_id: int):
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT last_scanned_message_date FROM monitored_groups WHERE chat_id = ?", (chat_id,))
        row = await cursor.fetchone()
        if row and row[0]:
            try:
                return datetime.fromisoformat(row[0])
            except:
                return None
        return None

    async def update_scan_state(self, chat_id: int, last_msg_date: datetime):
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                """UPDATE monitored_groups SET last_scanned_at = ?, last_scanned_message_date = ?
                WHERE chat_id = ?""",
                (datetime.now().isoformat(), last_msg_date.isoformat(), chat_id))
            await conn.commit()

    async def insert_request(self, message_text, message_date, group_id, group_name,
                              sender_name, sender_id, message_link=None):
        async with self._lock:
            conn = await self._ensure_conn()
            content_hash = hashlib.md5(message_text[:500].encode()).hexdigest()
            try:
                await conn.execute(
                    """INSERT OR IGNORE INTO forwarded_requests
                    (message_text, message_date, group_id, group_name, sender_name, sender_id, message_link, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (message_text, message_date.isoformat() if message_date else None,
                     group_id, group_name, sender_name, sender_id, message_link, content_hash))
                await conn.commit()
                cursor = await conn.execute("SELECT changes()")
                changes = await cursor.fetchone()
                return changes[0] > 0
            except Exception as e:
                logging.error(f"Insert request error: {e}")
                return False

    async def count_requests(self):
        conn = await self._ensure_conn()
        cursor = await conn.execute("SELECT COUNT(*) FROM forwarded_requests")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def count_groups(self):
        conn = await self._ensure_conn()
        cursor = await conn.execute("SELECT COUNT(*) FROM monitored_groups WHERE is_active = 1")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def count_users(self):
        conn = await self._ensure_conn()
        cursor = await conn.execute("SELECT COUNT(*) FROM users")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None


# -------------------------------------------------------------------
# Help Request Detector
# -------------------------------------------------------------------


class HelpRequestDetector:
    @staticmethod
    def is_help_request(text: str, min_length: int = 15, max_length: int = 2000) -> Tuple[bool, List[str]]:
        if not text:
            return False, []
        text_str = text.strip()
        if len(text_str) < min_length or len(text_str) > max_length:
            return False, []

        # فحص السبام
        text_lower = text_str.lower()
        for spam in SPAM_KEYWORDS:
            if spam.lower() in text_lower:
                return False, []

        # فحص الكلمات المفتاحية
        found_keywords = []
        for kw in HELP_KEYWORDS:
            if ' ' in kw:
                if kw.lower() in text_lower:
                    found_keywords.append(kw)

        single_keywords = [kw for kw in HELP_KEYWORDS if ' ' not in kw]
        if single_keywords:
            pattern = re.compile(r'\b(' + '|'.join(re.escape(k) for k in single_keywords) + r')\b', re.IGNORECASE)
            matches = pattern.findall(text_str)
            found_keywords.extend(matches)

        found_keywords = list(dict.fromkeys(found_keywords))

        if len(found_keywords) >= 1:
            return True, found_keywords
        return False, []


# -------------------------------------------------------------------
# Message Formatter
# -------------------------------------------------------------------


class MessageFormatter:
    @staticmethod
    def format_help_request(group_name, sender_name, message_date, message_text, keywords_found, message_link=None):
        if len(message_text) > MAX_MESSAGE_LENGTH:
            message_text = message_text[:MAX_MESSAGE_LENGTH] + "..."
        date_str = message_date.strftime("%Y-%m-%d %H:%M") if message_date else "غير معروف"
        keywords_str = "، ".join(keywords_found[:5])
        lines = [
            "📚 طلب مساعدة دراسية",
            "",
            f"👥 المجموعة: {group_name}",
            f"👤 المرسل: {sender_name}",
            f"🕒 التاريخ: {date_str}",
            f"🔑 الكلمات: {keywords_str}",
        ]
        if message_link:
            lines.append(f"🔗 الرابط: {message_link}")
        lines.extend(["", "💬 الرسالة:", message_text])
        return "\n".join(lines)

    @staticmethod
    def format_scan_summary(total_scanned, total_found, new_count, chats_scanned, period_desc, duration_sec):
        return (f"📊 ملخص المسح التاريخي\n\n"
                f"📅 الفترة: {period_desc}\n"
                f"💬 المجموعات المفحوصة: {chats_scanned}\n"
                f"🔍 الرسائل المفحوصة: {total_scanned}\n"
                f"📚 طلبات مساعدة موجودة: {total_found}\n"
                f"✅ طلبات جديدة منشورة: {new_count}\n"
                f"⏱️ المدة: {duration_sec:.1f} ثانية\n")

    @staticmethod
    def format_welcome(bot_username):
        return (
            "🤖 أهلاً بك في بوت طلبات المساعدة الدراسية!\n\n"
            "📚 ماذا يفعل هذا البوت؟\n"
            "• يراقب مجموعاتك الدراسية\n"
            "• يكتشف طلبات المساعدة (واجب، بحث، عرض، إلخ)\n"
            "• ينشرها في قناة مشتركة\n\n"
            "🚀 كيف أبدأ؟\n"
            "1️⃣ أضفني لأي مجموعة دراسية\n"
            "2️⃣ ارفعني كـ مشرف (Admin)\n"
            "3️⃣ أعطني صلاحية: قراءة الرسائل + إرسال الرسائل\n"
            "4️⃣ مبروك! سأراقب المجموعة تلقائياً\n\n"
            "💡 بعد إضافتي، سأبدأ بمسح آخر 7 أيام من رسائل المجموعة\n"
            "   وأنشر طلبات المساعدة في القناة المشتركة.\n\n"
            "📌 الأوامر:\n"
            "• /start - هذه الرسالة\n"
            "• /status - حالة البوت\n"
            "• /help - المساعدة"
        )

    @staticmethod
    def format_status(total_requests, groups_count, users_count, scan_running, scan_progress=""):
        return (f"📊 حالة البوت\n\n"
                f"📚 طلبات مساعدة منشورة: {total_requests}\n"
                f"👥 المجموعات المراقَبة: {groups_count}\n"
                f"👤 المستخدمون المنضمون: {users_count}\n"
                f"🔄 المسح التاريخي: "
                + ("قيد التنفيذ" + (f" ({scan_progress})" if scan_progress else "") if scan_running else "متوقف")
                + "\n")

    @staticmethod
    def format_help():
        return (
            "🤖 أوامر البوت\n\n"
            "📌 الأوامر العامة (في الدردشة الخاصة):\n"
            "• /start - رسالة الترحيب\n"
            "• /status - حالة البوت\n"
            "• /help - هذه القائمة\n\n"
            "📌 كيف يعمل؟\n"
            "1. أضف البوت لمجموعتك الدراسية\n"
            "2. ارفعه كـ مشرف (Admin)\n"
            "3. البوت يراقب تلقائياً!\n\n"
            "📌 أوامر المالك (في القناة):\n"
            "• /scan_week - مسح آخر 7 أيام\n"
            "• /scan_month - مسح آخر 30 يوم\n"
            "• /scan_stop - إيقاف المسح"
        )


# -------------------------------------------------------------------
# History Scanner
# -------------------------------------------------------------------


class HistoryScanner:
    def __init__(self, bot_client, db, channel_id, days_back, max_per_chat,
                 progress_callback=None):
        self.bot_client = bot_client
        self.db = db
        self.channel_id = channel_id
        self.days_back = days_back
        self.max_per_chat = max_per_chat
        self.progress_callback = progress_callback
        self.total_scanned = 0
        self.total_found = 0
        self.new_count = 0
        self.chats_scanned = 0
        self._cancelled = False

    def cancel(self): self._cancelled = True

    async def scan(self):
        start = datetime.now()
        if self.days_back is not None:
            cutoff = datetime.now() - timedelta(days=self.days_back)
        else:
            cutoff = None
        period = f"آخر {self.days_back} يوم" if self.days_back else "كامل"
        logging.info(f"[SCAN] Period: {period}")

        groups = await self.db.get_active_groups()
        logging.info(f"[SCAN] {len(groups)} groups to scan")

        for idx, g in enumerate(groups, 1):
            if self._cancelled: break
            if g['chat_id'] == self.channel_id: continue
            try:
                if self.progress_callback:
                    self.progress_callback(idx, len(groups), g['chat_title'])
                await self._scan_group(g, cutoff)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                logging.error(f"[SCAN] Error group {g['chat_id']}: {e}")
            await asyncio.sleep(0.5)

        dur = (datetime.now() - start).total_seconds()
        await self._send_summary(period, dur)
        return period

    async def _scan_group(self, group, cutoff):
        chat_id = group['chat_id']
        chat_title = group['chat_title'] or "Unknown"
        chat_cutoff = cutoff
        if chat_cutoff is None:
            try:
                ls = await self.db.get_last_scan_date(chat_id)
                if ls:
                    chat_cutoff = ls
            except:
                pass

        try:
            async for msg in self.bot_client.iter_messages(chat_id, reverse=False, limit=self.max_per_chat):
                if self._cancelled: break
                try:
                    md = msg.date.replace(tzinfo=None) if msg.date else None
                except:
                    md = None
                if md and chat_cutoff and md < chat_cutoff:
                    break
                self.total_scanned += 1
                if not msg or not msg.text:
                    continue

                is_help, keywords = HelpRequestDetector.is_help_request(msg.text)
                if not is_help:
                    continue
                self.total_found += 1

                try:
                    sender = await msg.get_sender()
                    sn = (sender.first_name or "") if hasattr(sender, 'first_name') else "User"
                    sender_id = sender.id if hasattr(sender, 'id') else None
                except:
                    sn = "User"
                    sender_id = None

                msg_link = None
                try:
                    # للبوتات: استخدام get_messages_link أو بناء الرابط يدوياً
                    entity = await self.bot_client.get_entity(chat_id)
                    msg_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{msg.id}"
                except:
                    pass

                inserted = await self.db.insert_request(
                    msg.text, md, chat_id, chat_title, sn, sender_id, msg_link)
                if inserted:
                    self.new_count += 1
                    # نشر فوري
                    try:
                        formatted = MessageFormatter.format_help_request(
                            chat_title, sn, md, msg.text, keywords, msg_link)
                        await self.bot_client.send_message(self.channel_id, formatted)
                        await asyncio.sleep(0.5)
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 1)
                    except Exception as e:
                        logging.error(f"[SCAN] publish error: {e}")

            # تحديث scan_state
            try:
                await self.db.update_scan_state(chat_id, datetime.now())
            except:
                pass
            self.chats_scanned += 1

        except FloodWaitError:
            raise
        except Exception as e:
            logging.error(f"[SCAN] iter error group {chat_id}: {e}")

    async def _send_summary(self, period, dur):
        if self.new_count == 0 and self.total_scanned == 0:
            return
        f = MessageFormatter.format_scan_summary(
            self.total_scanned, self.total_found, self.new_count,
            self.chats_scanned, period, dur)
        try:
            await self.bot_client.send_message(self.channel_id, f)
        except Exception as e:
            logging.error(f"[SCAN] summary: {e}")


# -------------------------------------------------------------------
# Monitor
# -------------------------------------------------------------------


class Monitor:
    def __init__(self, config, db):
        self.config = config
        self.db = db
        self.bot_client = None
        self._running = False
        self._handlers_registered = False
        self._send_lock = asyncio.Lock()
        self._current_scanner = None
        self._current_scan_task = None
        self._scan_progress = ""
        self._bot_task = None
        self._keep_alive_task = None
        self._startup_scan_done = False

    def _create_bot_client(self):
        sp = os.path.join(SESSIONS_DIR, "bot")
        return TelegramClient(sp, self.config.api_id, self.config.api_hash,
                              connection_retries=None, retry_delay=5, request_retries=5,
                              auto_reconnect=True, sequential_updates=False)

    def _register_handlers(self):
        if self._handlers_registered:
            return

        # 1. رسائل جديدة في كل المجموعات التي البوت عضو فيها
        self.bot_client.add_event_handler(
            self._on_new_message,
            events.NewMessage()
        )

        # 2. البوت أُضيف لمجموعة جديدة
        self.bot_client.add_event_handler(
            self._on_chat_action,
            events.ChatAction()
        )

        # 3. أوامر الدردشة الخاصة (للجميع)
        self.bot_client.add_event_handler(
            self._on_private_command,
            events.NewMessage(func=lambda e: e.is_private and e.message.text and e.message.text.startswith('/'))
        )

        # 4. أوامر القناة (للمالك فقط)
        self.bot_client.add_event_handler(
            self._on_channel_command,
            events.NewMessage(chats=self.config.channel_id, pattern=r"^/[a-zA-Z_]+")
        )

        self._handlers_registered = True
        logging.info("Bot handlers registered (groups + private + channel)")

    async def _on_chat_action(self, event):
        """عند إضافة/إزالة البوت من مجموعة"""
        try:
            if event.user_added or event.user_joined:
                # تحقق إن كان البوت هو المُضاف
                me = await self.bot_client.get_me()
                if event.user_id == me.id:
                    chat = await event.get_chat()
                    chat_id = chat.id
                    chat_title = chat.title or "Unknown"
                    chat_type = "channel" if hasattr(chat, 'broadcast') and chat.broadcast else "group"

                    # من أضاف البوت؟
                    added_by = await event.get_user()
                    added_by_id = added_by.id if added_by else None
                    added_by_name = (added_by.first_name if added_by and hasattr(added_by, 'first_name') else "Unknown")

                    # حفظ في DB
                    added = await self.db.add_monitored_group(
                        chat_id, chat_title, chat_type, added_by_id, added_by_name)
                    if added:
                        logging.info(f"[NEW GROUP] {chat_title} ({chat_id}) by {added_by_name}")
                        # شكر المُضاف
                        try:
                            await event.reply(
                                f"✅ شكراً لإضافتي لمجموعة: {chat_title}\n\n"
                                f"📚 سأبدأ بمراقبة طلبات المساعدة الدراسية هنا.\n"
                                f"🔍 سأقوم بمسح آخر {self.config.startup_scan_days or 7} أيام تلقائياً.\n\n"
                                f"📡 كل طلب مساعدة سيُنشر في قناتنا المشتركة."
                            )
                        except:
                            pass
                        # بدء مسح للمجموعة الجديدة
                        asyncio.create_task(self._scan_new_group(chat_id, chat_title))

            elif event.user_kicked or event.user_left:
                me = await self.bot_client.get_me()
                if event.user_id == me.id:
                    chat = await event.get_chat()
                    await self.db.deactivate_group(chat.id)
                    logging.info(f"[GROUP REMOVED] {chat.title} ({chat.id})")
        except Exception as e:
            logging.error(f"Chat action error: {e}", exc_info=True)

    async def _on_new_message(self, event):
        """معالج رسائل المجموعات"""
        try:
            msg = event.message
            if not msg or not msg.text:
                return
            chat = await event.get_chat()
            if hasattr(chat, 'id') and chat.id == self.config.channel_id:
                return  # تجاهل رسائل القناة الوجهة

            group_name = chat.title or "Unknown"
            sender = await event.get_sender()
            sender_name = (sender.first_name or "User") if hasattr(sender, 'first_name') else "User"
            sender_id = sender.id if hasattr(sender, 'id') else None

            # فحص طلب مساعدة
            is_help, keywords = HelpRequestDetector.is_help_request(msg.text)
            if not is_help:
                return

            # رابط الرسالة
            msg_link = None
            try:
                msg_link = f"https://t.me/c/{str(chat.id).replace('-100', '')}/{msg.id}"
            except:
                pass

            # إدراج في DB
            inserted = await self.db.insert_request(
                msg.text, msg.date.replace(tzinfo=None) if msg.date else datetime.now(),
                chat.id, group_name, sender_name, sender_id, msg_link)
            if not inserted:
                return  # مكرر

            # نشر في القناة
            formatted = MessageFormatter.format_help_request(
                group_name, sender_name, msg.date, msg.text, keywords, msg_link)
            await self._send(formatted)
            logging.info(f"[LIVE] Help request from {group_name}")

        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"New message error: {e}", exc_info=True)

    async def _on_private_command(self, event):
        """أوامر الدردشة الخاصة مع البوت (للجميع)"""
        try:
            text = (event.message.text or "").strip()
            if not text:
                return
            cmd = text.split()[0].lower()
            sender = await event.get_sender()
            sender_id = sender.id if sender else None

            # تسجيل المستخدم
            if sender:
                await self.db.add_user(
                    sender.id,
                    getattr(sender, 'username', None),
                    getattr(sender, 'first_name', None))

            me = await self.bot_client.get_me()
            bot_username = me.username if me.username else "YourBot"

            if cmd == "/start":
                await event.reply(MessageFormatter.format_welcome(bot_username))
            elif cmd == "/status":
                total = await self.db.count_requests()
                groups = await self.db.count_groups()
                users = await self.db.count_users()
                await event.reply(MessageFormatter.format_status(
                    total, groups, users, self.is_scan_running(), self._scan_progress))
            elif cmd == "/help":
                await event.reply(MessageFormatter.format_help())
            elif cmd == "/groups":
                groups = await self.db.get_active_groups()
                if not groups:
                    await event.reply("ℹ️ لا توجد مجموعات مراقَبة بعد.\nأضفني لمجموعتك للبدء!")
                else:
                    lines = [f"👥 المجموعات المراقَبة ({len(groups)}):", ""]
                    for g in groups[:20]:
                        lines.append(f"• {g['chat_title']}")
                    if len(groups) > 20:
                        lines.append(f"\n... و {len(groups)-20} مجموعة أخرى")
                    await event.reply("\n".join(lines))
            else:
                await event.reply(
                    "🤖 أمر غير معروف.\n\nالأوامر:\n• /start - البدء\n• /status - الحالة\n• /help - المساعدة\n• /groups - مجموعاتي المراقَبة"
                )
        except Exception as e:
            logging.error(f"Private command error: {e}", exc_info=True)

    async def _on_channel_command(self, event):
        """أوامر القناة - للمالك فقط"""
        try:
            text = (event.message.text or "").strip()
            if not text:
                return
            cmd = text.split()[0].lower()

            # التحقق من المالك
            if self.config.owner_id:
                sender = await event.get_sender()
                if getattr(sender, 'id', None) != self.config.owner_id:
                    return

            logging.info(f"[CMD-CHANNEL] {cmd}")

            if cmd == "/help":
                await self._send(MessageFormatter.format_help())
            elif cmd == "/status":
                total = await self.db.count_requests()
                groups = await self.db.count_groups()
                users = await self.db.count_users()
                await self._send(MessageFormatter.format_status(
                    total, groups, users, self.is_scan_running(), self._scan_progress))
            elif cmd == "/scan_week":
                await self._start_scan(7, "/scan_week")
            elif cmd == "/scan_month":
                await self._start_scan(30, "/scan_month")
            elif cmd == "/scan_stop":
                if self.is_scan_running():
                    self.stop_scan()
                    await self._send("⏹️ تم إيقاف المسح")
                else:
                    await self._send("ℹ️ لا يوجد مسح قيد التنفيذ")
            elif cmd == "/groups":
                groups = await self.db.get_active_groups()
                if not groups:
                    await self._send("ℹ️ لا توجد مجموعات مراقَبة")
                else:
                    lines = [f"👥 المجموعات المراقَبة ({len(groups)}):", ""]
                    for g in groups:
                        lines.append(f"• {g['chat_title']}")
                    await self._send("\n".join(lines))
            else:
                await self._send(f"❓ أمر غير معروف: {cmd}\nاكتب /help")
        except Exception as e:
            logging.error(f"Channel command error: {e}", exc_info=True)

    async def _send(self, text, retries=3):
        async with self._send_lock:
            for a in range(1, retries + 1):
                try:
                    await self.bot_client.send_message(self.config.channel_id, text)
                    return
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
                except (RPCError, OSError, ConnectionError):
                    await asyncio.sleep(min(10 * a, 60))
            logging.error(f"Failed after {retries} attempts")

    def is_scan_running(self):
        return self._current_scan_task is not None and not self._current_scan_task.done()

    def stop_scan(self):
        if self._current_scanner:
            self._current_scanner.cancel()

    async def _start_scan(self, days, cmd_name):
        if self.is_scan_running():
            await self._send("⚠️ يوجد مسح قيد التنفيذ\nأرسل /scan_stop لإيقافه")
            return
        d = f"{days} يوم" if days else "كامل"
        await self._send(f"🚀 بدء المسح ({cmd_name})\n📅 الفترة: {d}\n⏳ جاري...")
        self._current_scan_task = asyncio.create_task(self._run_scan(days))

        def _c(t):
            self._current_scan_task = None
            self._scan_progress = ""
        self._current_scan_task.add_done_callback(_c)

    async def _run_scan(self, days):
        try:
            await asyncio.sleep(2)
            def p(i, t, name):
                self._scan_progress = f"{i}/{t}: {name[:20]}"
            self._current_scanner = HistoryScanner(
                self.bot_client, self.db, self.config.channel_id,
                days, self.config.history_max_per_chat, p)
            await self._current_scanner.scan()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"Scan error: {e}", exc_info=True)
        finally:
            self._current_scanner = None

    async def _scan_new_group(self, chat_id: int, chat_title: str):
        """مسح مجموعة جديدة بعد إضافتها"""
        try:
            await asyncio.sleep(3)
            logging.info(f"[NEW GROUP SCAN] Starting scan for {chat_title}")
            # فحص الرسائل الأخيرة
            cutoff = datetime.now() - timedelta(days=self.config.startup_scan_days or 7)
            count = 0
            async for msg in self.bot_client.iter_messages(chat_id, reverse=False, limit=self.config.history_max_per_chat):
                try:
                    md = msg.date.replace(tzinfo=None) if msg.date else None
                except:
                    md = None
                if md and md < cutoff:
                    break
                if not msg or not msg.text:
                    continue
                is_help, keywords = HelpRequestDetector.is_help_request(msg.text)
                if not is_help:
                    continue
                try:
                    sender = await msg.get_sender()
                    sn = (sender.first_name or "User") if hasattr(sender, 'first_name') else "User"
                    sender_id = sender.id if hasattr(sender, 'id') else None
                except:
                    sn = "User"
                    sender_id = None
                msg_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{msg.id}"
                inserted = await self.db.insert_request(
                    msg.text, md, chat_id, chat_title, sn, sender_id, msg_link)
                if inserted:
                    count += 1
                    formatted = MessageFormatter.format_help_request(
                        chat_title, sn, md, msg.text, keywords, msg_link)
                    try:
                        await self.bot_client.send_message(self.config.channel_id, formatted)
                        await asyncio.sleep(0.5)
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 1)
                    except:
                        pass
            try:
                await self.db.update_scan_state(chat_id, datetime.now())
            except:
                pass
            logging.info(f"[NEW GROUP SCAN] {chat_title}: found {count} new requests")
        except Exception as e:
            logging.error(f"New group scan error: {e}", exc_info=True)

    async def _run_bot(self):
        backoff = 5
        while self._running:
            try:
                if not self.bot_client.is_connected():
                    logging.info("Connecting bot...")
                    await self.bot_client.start(bot_token=self.config.bot_token)
                    me = await self.bot_client.get_me()
                    logging.info(f"Bot: @{me.username} ({me.first_name})")
                    backoff = 5
                await self.bot_client.run_until_disconnected()
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except (RPCError, ConnectionError, OSError) as e:
                logging.error(f"Bot error: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logging.error(f"Bot unexpected: {e}", exc_info=True)
            finally:
                if self.bot_client and self.bot_client.is_connected():
                    try:
                        await self.bot_client.disconnect()
                    except:
                        pass
            if not self._running:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 600)

    async def _keep_alive(self):
        app_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("APP_URL")
        if not app_url:
            logging.info("Keep-alive disabled (no RENDER_EXTERNAL_URL)")
            return
        app_url = app_url.rstrip("/")
        health_url = f"{app_url}/health"
        logging.info(f"Keep-alive: will ping {health_url}")
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await asyncio.sleep(600)
                    async with session.get(health_url, timeout=10) as r:
                        logging.debug(f"Keep-alive: {r.status}")
                except asyncio.CancelledError:
                    break
                except:
                    pass

    async def start(self):
        self._running = True
        self.bot_client = self._create_bot_client()
        self._register_handlers()
        self._bot_task = asyncio.create_task(self._run_bot())
        await asyncio.sleep(3)
        self._keep_alive_task = asyncio.create_task(self._keep_alive())

    async def stop(self):
        self._running = False
        self.stop_scan()
        if self.bot_client and self.bot_client.is_connected():
            try:
                await self.bot_client.disconnect()
            except:
                pass
        for t in [self._bot_task, self._keep_alive_task, self._current_scan_task]:
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass


# -------------------------------------------------------------------
# HTTP Server (for Render)
# -------------------------------------------------------------------


async def health_handler(request):
    return web.Response(text="✅ Bot is running", status=200)


async def start_http_server():
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


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------


async def main():
    load_dotenv(dotenv_path='accounts.env')
    config = Config()
    errors = config.validate()
    if errors:
        for e in errors:
            print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    setup_logging(config.log_level)
    logging.info("=== Telegram Help Requests Monitor v9 (SIMPLE) ===")
    logging.info(f"Bot token: {config.bot_token[:20]}...")
    logging.info(f"Channel ID: {config.channel_id}")

    Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

    db = DatabaseManager()
    await db.init_db()

    monitor = Monitor(config, db)
    await monitor.start()
    http_runner = await start_http_server()

    logging.info("✅ Bot started. Users can now add it to their groups!")

    shutdown = asyncio.Event()
    def sh(): shutdown.set()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, sh)
        except:
            try:
                signal.signal(sig, lambda *_: sh())
            except:
                pass
    await shutdown.wait()
    logging.info("Stopping...")
    await monitor.stop()
    await db.close()
    await http_runner.cleanup()
    logging.info("Stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Interrupted")
    except Exception as e:
        logging.critical(f"Fatal: {e}", exc_info=True)
