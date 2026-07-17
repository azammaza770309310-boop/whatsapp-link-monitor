#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram WhatsApp Link Monitor - v11 PROFESSIONAL
أقوى نسخة - مصممة لـ Render + ميزات احترافية

المميزات:
1. ✅ سحب روابط واتساب فقط (جميع الأنواع)
2. ✅ واجهة أزرار احترافية متعددة المستويات
3. ✅ استبعاد خدمات الطلاب المدفوعة
4. ✅ دعوة الأصدقاء + إدارة الأصدقاء
5. ✅ إحصائيات مفصلة
6. ✅ مسح تاريخي ذكي
7. ✅ إعادة اتصال تلقائي
8. ✅ Keep-alive مدمج
9. ✅ Filter متقدم
10. ✅ Logging مفصل
"""

import asyncio
import logging
import os
import re
import signal
import sys
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Set

import aiohttp
import aiosqlite
from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import Message
from aiohttp import web

# -------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------

SESSIONS_DIR = "sessions"
DATA_DIR = "data"
LOGS_DIR = "logs"
DB_FILE = os.path.join(DATA_DIR, "whatsapp_pro.db")
LOG_FILE = os.path.join(LOGS_DIR, "app.log")
MAX_MESSAGE_LENGTH = 1000

# Regex شامل لجميع روابط واتساب
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

# خدمات طلابية معروفة (تُستبعد تلقائياً)
STUDENT_SERVICES_BLACKLIST = [
    'services', 'service', 'serv',
    'help_student', 'studenthelp', 'student_help',
    'solve', 'solutions', 'solution',
    'homework_help', 'hw_help',
    'answers', 'answer',
    'kau_services', 'imamu_services', 'seu_services',
    'taif_services', 'majmah_services', 'stu_services',
    'talib', 'taleb', 'eshterak', 'eshtirak',
    'solution_paid', 'paid_solution',
    'mzaya', 'wathq', 'wathiq', 'naqdi', 'naqdy',
    'shahadati', 'shahada', 'hulool', 'hulul', 'hll',
    'fasl', 'fasil', 'qiyas', 'qias',
    'tahsil', 'tahsel', 'tawjihi', 'tawjih',
    'academic_service', 'edu_services',
    'education_paid', 'mktbah', 'maktaba',
    'ads_channel', 'ads_only', 'free_ads',
]

PAID_SERVICE_KEYWORDS = [
    'paid', 'مدفوع', 'بمقابل', 'بأجر',
    'services', 'خدمات',
    'solutions', 'حلول',
    'answers', 'اجابات',
]


# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------


class Config:
    def __init__(self):
        load_dotenv()
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
        self.history_max_per_chat = int(os.getenv("HISTORY_MAX_PER_CHAT", "1000"))
        self.history_batch_size = max(1, min(int(os.getenv("HISTORY_BATCH_SIZE", "10")), 20))
        self.history_skip_channel_posts = os.getenv("HISTORY_SKIP_CHANNEL_POSTS", "false").lower() == "true"
        self.startup_scan_days = None
        ssd = os.getenv("STARTUP_SCAN_DAYS", "")
        if ssd and ssd.lower() not in ("none", "null", ""):
            try:
                self.startup_scan_days = int(ssd)
            except:
                pass
        if self.startup_scan_days is None and not ssd:
            self.startup_scan_days = 30  # افتراضي: 30 يوم عند البدء

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
        await conn.execute("""CREATE TABLE IF NOT EXISTS forwarded_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT NOT NULL,
            link_key TEXT NOT NULL UNIQUE,
            link_type TEXT,
            message_text TEXT,
            message_date TIMESTAMP,
            group_id INTEGER,
            group_name TEXT,
            sender_name TEXT,
            message_link TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_link_key ON forwarded_links (link_key)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_group ON forwarded_links (group_id)")
        await conn.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            groups_added INTEGER DEFAULT 0)""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL UNIQUE,
            display_name TEXT,
            added_by_user_id INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            invited INTEGER DEFAULT 0,
            joined INTEGER DEFAULT 0)""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS excluded_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT NOT NULL,
            reason TEXT,
            excluded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await conn.commit()

    async def add_user(self, user_id: int, username: str, first_name: str):
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                """INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)""",
                (user_id, username, first_name))
            await conn.commit()

    async def add_friend(self, phone: str, display_name: str, added_by_user_id: int) -> bool:
        async with self._lock:
            conn = await self._ensure_conn()
            try:
                await conn.execute(
                    """INSERT OR REPLACE INTO friends (phone, display_name, added_by_user_id)
                    VALUES (?, ?, ?)""",
                    (phone, display_name, added_by_user_id))
                await conn.commit()
                return True
            except Exception as e:
                logging.error(f"Add friend error: {e}")
                return False

    async def get_friends(self) -> List[Dict]:
        conn = await self._ensure_conn()
        cursor = await conn.execute("SELECT phone, display_name, invited, joined FROM friends ORDER BY added_at DESC")
        rows = await cursor.fetchall()
        return [{"phone": r[0], "name": r[1], "invited": r[2], "joined": r[3]} for r in rows]

    async def add_monitored_group(self, chat_id: int, chat_title: str, chat_type: str,
                                    added_by_user_id: int, added_by_name: str) -> bool:
        async with self._lock:
            conn = await self._ensure_conn()
            try:
                # التحقق هل المجموعة موجودة مسبقاً
                cur = await conn.execute("SELECT is_active FROM monitored_groups WHERE chat_id = ?", (chat_id,))
                existing = await cur.fetchone()
                if existing and existing[0] == 1:
                    return False  # مضافة ومفعلة مسبقاً
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

    async def insert_link(self, link, link_type, message_text, message_date,
                          group_id, group_name, sender_name, message_link=None):
        async with self._lock:
            conn = await self._ensure_conn()
            link_key = self._normalize_link(link)
            try:
                await conn.execute(
                    """INSERT OR IGNORE INTO forwarded_links
                    (link, link_key, link_type, message_text, message_date, group_id, group_name, sender_name, message_link)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (link, link_key, link_type, message_text,
                     message_date.isoformat() if message_date else None,
                     group_id, group_name, sender_name, message_link))
                await conn.commit()
                cursor = await conn.execute("SELECT changes()")
                changes = await cursor.fetchone()
                return changes[0] > 0
            except Exception as e:
                logging.error(f"Insert link error: {e}")
                return False

    async def insert_excluded(self, link: str, reason: str):
        async with self._lock:
            conn = await self._ensure_conn()
            try:
                await conn.execute(
                    "INSERT OR IGNORE INTO excluded_links (link, reason) VALUES (?, ?)",
                    (link, reason))
                await conn.commit()
            except:
                pass

    async def count_links(self):
        conn = await self._ensure_conn()
        cursor = await conn.execute("SELECT COUNT(*) FROM forwarded_links")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def count_excluded(self):
        conn = await self._ensure_conn()
        cursor = await conn.execute("SELECT COUNT(*) FROM excluded_links")
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

    async def count_friends(self):
        conn = await self._ensure_conn()
        cursor = await conn.execute("SELECT COUNT(*) FROM friends")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_recent_links(self, limit=5):
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT link, group_name, sender_name, created_at FROM forwarded_links ORDER BY created_at DESC LIMIT ?",
            (limit,))
        rows = await cursor.fetchall()
        return [{"link": r[0], "group": r[1], "sender": r[2], "date": r[3]} for r in rows]

    async def get_top_groups(self, limit=5):
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT group_name, COUNT(*) as cnt FROM forwarded_links GROUP BY group_id ORDER BY cnt DESC LIMIT ?",
            (limit,))
        rows = await cursor.fetchall()
        return [{"name": r[0], "count": r[1]} for r in rows]

    @staticmethod
    def _normalize_link(link: str) -> str:
        link = link.lower().strip()
        if link.startswith("https://"): link = link[8:]
        elif link.startswith("http://"): link = link[7:]
        return link.rstrip("/")

    @staticmethod
    def _detect_link_type(link: str) -> str:
        l = link.lower()
        if "chat.whatsapp.com" in l: return "group_invite"
        if "/channel" in l: return "channel"
        if "/message" in l: return "message_link"
        if "wa.me" in l: return "direct_chat"
        if "api.whatsapp.com/send" in l: return "api_send"
        if "api.whatsapp.com/q" in l: return "qr_code"
        if "l.whatsapp.com" in l: return "short_link"
        return "other"

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None


# -------------------------------------------------------------------
# Link Extractor + Filter
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

    @staticmethod
    def is_student_service(link: str) -> bool:
        link_lower = link.lower()
        for blacklisted in STUDENT_SERVICES_BLACKLIST:
            if blacklisted in link_lower:
                return True
        for kw in PAID_SERVICE_KEYWORDS:
            if kw in link_lower:
                return True
        return False

    @staticmethod
    def filter_links(links: List[str]) -> Tuple[List[str], List[Tuple[str, str]]]:
        """يقسم الروابط إلى (صالحة، مستبعدة مع السبب)"""
        valid = []
        excluded = []
        for link in links:
            if LinkExtractor.is_student_service(link):
                excluded.append((link, "خدمة طلابية مدفوعة"))
            else:
                valid.append(link)
        return valid, excluded


# -------------------------------------------------------------------
# Message Formatter
# -------------------------------------------------------------------


class MessageFormatter:
    @staticmethod
    def format_link_message(group_name, sender_name, message_date, links, message_text, message_link=None):
        if len(message_text) > MAX_MESSAGE_LENGTH:
            message_text = message_text[:MAX_MESSAGE_LENGTH] + "..."
        date_str = message_date.strftime("%Y-%m-%d %H:%M") if message_date else "غير معروف"
        links_text = "\n".join(f"• {link}" for link in links)
        lines = [
            "📥 رابط واتساب جديد",
            "",
            f"👥 المجموعة: {group_name}",
            f"👤 المرسل: {sender_name}",
            f"🕒 التاريخ: {date_str}",
            "",
            "🔗 الرابط:",
            links_text,
        ]
        if message_link:
            lines.extend(["", f"📨 الرسالة الأصلية: {message_link}"])
        lines.extend(["", "💬 النص:", message_text])
        return "\n".join(lines)

    @staticmethod
    def format_scan_summary(total_scanned, total_links, new_count, excluded_count, chats_scanned, period_desc, duration_sec):
        return (f"📊 ملخص المسح التاريخي\n\n"
                f"📅 الفترة: {period_desc}\n"
                f"💬 المجموعات: {chats_scanned}\n"
                f"🔍 الرسائل المفحوصة: {total_scanned}\n"
                f"🔗 روابط واتساب: {total_links}\n"
                f"✅ روابط جديدة منشورة: {new_count}\n"
                f"❌ خدمات طلابية مستبعدة: {excluded_count}\n"
                f"⏱️ المدة: {duration_sec:.1f} ثانية\n")

    @staticmethod
    def format_welcome(bot_username, user_first_name=""):
        name_part = f" {user_first_name}" if user_first_name else ""
        return (
            f"🤖 أهلاً بك{name_part} في بوت سحب روابط واتساب!\n\n"
            "📚 ماذا يفعل هذا البوت؟\n"
            "• يراقب مجموعاتك الدراسية\n"
            "• يستخرج روابط واتساب منها\n"
            "• يستبعد خدمات الطلاب المدفوعة تلقائياً\n"
            "• ينشر الروابط في قناة مشتركة\n\n"
            "🚀 اختر من القائمة أدناه:"
        )

    @staticmethod
    def format_status(total_links, groups_count, users_count, friends_count, excluded_count, scan_running, scan_progress=""):
        return (f"📊 حالة البوت\n\n"
                f"📥 روابط واتساب منشورة: {total_links}\n"
                f"❌ خدمات مستبعدة: {excluded_count}\n"
                f"👥 المجموعات المراقَبة: {groups_count}\n"
                f"👤 المستخدمون: {users_count}\n"
                f"👨‍💼 الأصدقاء: {friends_count}\n"
                f"🔄 المسح التاريخي: "
                + ("قيد التنفيذ" + (f" ({scan_progress})" if scan_progress else "") if scan_running else "متوقف")
                + "\n")

    @staticmethod
    def format_detailed_stats(recent_links, top_groups):
        lines = ["📈 إحصائيات مفصلة", ""]
        if top_groups:
            lines.append("🏆 أكثر المجموعات نشاطاً:")
            for i, g in enumerate(top_groups, 1):
                lines.append(f"   {i}. {g['name']}: {g['count']} رابط")
            lines.append("")
        if recent_links:
            lines.append("🆕 أحدث الروابط:")
            for link in recent_links[:3]:
                date_str = link['date'][:16] if link['date'] else ""
                lines.append(f"   • {link['group']}")
                lines.append(f"     {link['link'][:50]}...")
                lines.append(f"     👤 {link['sender']} | 📅 {date_str}")
                lines.append("")
        return "\n".join(lines)

    @staticmethod
    def format_help():
        return (
            "🤖 دليل الاستخدام الكامل\n\n"
            "📌 كيف يعمل البوت؟\n\n"
            "1️⃣ اضغط «➕ إضافة لمجموعة»\n"
            "2️⃣ ارفع البوت كـ مشرف (Admin) في المجموعة\n"
            "3️⃣ البوت يراقب المجموعة تلقائياً\n"
            "4️⃣ كل رابط واتساب يُنشر في القناة\n\n"
            "📌 المميزات:\n"
            "✅ سحب جميع أنواع روابط واتساب\n"
            "❌ استبعاد خدمات الطلاب المدفوعة\n"
            "📚 مسح آخر 30 يوم تلقائياً\n"
            "🔄 مسح تاريخي عند الطلب\n"
            "📊 إحصائيات مفصلة\n"
            "👥 دعوة الأصدقاء\n\n"
            "📌 للأصدقاء:\n"
            "• اضغط «👥 دعوة صديق» لمشاركة البوت\n"
            "• يمكنهم الانضمام واستخدامه مجاناً\n\n"
            "📌 للمجموعات:\n"
            "• البوت يحتاج صلاحية: قراءة الرسائل\n"
            "• لا يحتاج صلاحية حذف أو تثبيت"
        )


# -------------------------------------------------------------------
# History Scanner
# -------------------------------------------------------------------


class HistoryScanner:
    def __init__(self, bot_client, db, channel_id, days_back, max_per_chat, progress_callback=None):
        self.bot_client = bot_client
        self.db = db
        self.channel_id = channel_id
        self.days_back = days_back
        self.max_per_chat = max_per_chat
        self.progress_callback = progress_callback
        self.total_scanned = 0
        self.total_links = 0
        self.new_count = 0
        self.excluded_count = 0
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
            await asyncio.sleep(0.3)

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

                links = LinkExtractor.extract_links(msg.text)
                if not links:
                    continue

                valid_links, excluded_links = LinkExtractor.filter_links(links)
                self.total_links += len(links)
                self.excluded_count += len(excluded_links)

                # حفظ المستبعدة
                for link, reason in excluded_links:
                    await self.db.insert_excluded(link, reason)

                if not valid_links:
                    continue

                try:
                    sender = await msg.get_sender()
                    sn = (sender.first_name or "User") if hasattr(sender, 'first_name') else "User"
                except:
                    sn = "User"

                msg_link = None
                try:
                    msg_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{msg.id}"
                except:
                    pass

                for link in valid_links:
                    link_type = DatabaseManager._detect_link_type(link)
                    inserted = await self.db.insert_link(
                        link, link_type, msg.text, md, chat_id, chat_title, sn, msg_link)
                    if inserted:
                        self.new_count += 1
                        try:
                            formatted = MessageFormatter.format_link_message(
                                chat_title, sn, md, [link], msg.text, msg_link)
                            await self.bot_client.send_message(self.channel_id, formatted)
                            await asyncio.sleep(0.5)
                        except FloodWaitError as e:
                            await asyncio.sleep(e.seconds + 1)
                        except Exception as e:
                            logging.error(f"[SCAN] publish error: {e}")

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
            self.total_scanned, self.total_links, self.new_count,
            self.excluded_count, self.chats_scanned, period, dur)
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
        self._add_friend_states: Dict[int, Dict] = {}

    def _create_bot_client(self):
        sp = os.path.join(SESSIONS_DIR, "bot")
        return TelegramClient(sp, self.config.api_id, self.config.api_hash,
                              connection_retries=None, retry_delay=5, request_retries=5,
                              auto_reconnect=True, sequential_updates=False)

    def _register_handlers(self):
        if self._handlers_registered:
            return

        self.bot_client.add_event_handler(
            self._on_new_message,
            events.NewMessage()
        )
        self.bot_client.add_event_handler(
            self._on_chat_action,
            events.ChatAction()
        )
        self.bot_client.add_event_handler(
            self._on_private_message,
            events.NewMessage(func=lambda e: e.is_private)
        )
        self.bot_client.add_event_handler(
            self._on_callback,
            events.CallbackQuery()
        )
        self.bot_client.add_event_handler(
            self._on_channel_command,
            events.NewMessage(chats=self.config.channel_id, pattern=r"^/[a-zA-Z_]+")
        )

        self._handlers_registered = True
        logging.info("Bot handlers registered (PRO mode)")

    async def _get_main_menu(self):
        return [
            [Button.inline("➕ إضافة لمجموعة", b"add_to_group")],
            [Button.inline("👥 دعوة صديق", b"invite_friend"),
             Button.inline("👨‍💼 أصدقائي", b"my_friends")],
            [Button.inline("📊 الحالة", b"status"),
             Button.inline("📈 إحصائيات", b"stats")],
            [Button.inline("❓ المساعدة", b"help"),
             Button.inline("⚙️ الأوامر", b"commands")]
        ]

    async def _on_chat_action(self, event):
        try:
            if event.user_added or event.user_joined:
                me = await self.bot_client.get_me()
                if event.user_id == me.id:
                    chat = await event.get_chat()
                    chat_id = chat.id
                    chat_title = chat.title or "Unknown"
                    chat_type = "channel" if hasattr(chat, 'broadcast') and chat.broadcast else "group"

                    added_by = await event.get_user()
                    added_by_id = added_by.id if added_by else None
                    added_by_name = (added_by.first_name if added_by and hasattr(added_by, 'first_name') else "Unknown")

                    added = await self.db.add_monitored_group(
                        chat_id, chat_title, chat_type, added_by_id, added_by_name)
                    if added:
                        logging.info(f"[NEW GROUP] {chat_title} ({chat_id}) by {added_by_name}")
                        try:
                            await event.reply(
                                f"✅ شكراً لإضافتي لمجموعة: {chat_title}\n\n"
                                f"📥 سأبدأ بمراقبة روابط واتساب.\n"
                                f"🔍 سأقوم بمسح آخر {self.config.startup_scan_days or 30} يوم تلقائياً.\n"
                                f"❌ سأستبعد روابط خدمات الطلاب المدفوعة.\n\n"
                                f"📢 كل رابط صالح سيُنشر في القناة."
                            )
                        except:
                            pass
                        asyncio.create_task(self._scan_new_group(chat_id, chat_title))
                    else:
                        logging.info(f"[GROUP EXISTS] {chat_title} already monitored")

            elif event.user_kicked or event.user_left:
                me = await self.bot_client.get_me()
                if event.user_id == me.id:
                    chat = await event.get_chat()
                    await self.db.deactivate_group(chat.id)
                    logging.info(f"[GROUP REMOVED] {chat.title} ({chat.id})")
        except Exception as e:
            logging.error(f"Chat action error: {e}", exc_info=True)

    async def _on_new_message(self, event):
        try:
            msg = event.message
            if not msg or not msg.text:
                return
            chat = await event.get_chat()
            if hasattr(chat, 'id') and chat.id == self.config.channel_id:
                return

            group_name = chat.title or "Unknown"
            sender = await event.get_sender()
            sender_name = (sender.first_name or "User") if hasattr(sender, 'first_name') else "User"

            links = LinkExtractor.extract_links(msg.text)
            if not links:
                return

            valid_links, excluded_links = LinkExtractor.filter_links(links)
            if excluded_links:
                for link, reason in excluded_links:
                    await self.db.insert_excluded(link, reason)
                logging.info(f"[LIVE] Excluded {len(excluded_links)} links in {group_name}")

            if not valid_links:
                return

            msg_link = None
            try:
                msg_link = f"https://t.me/c/{str(chat.id).replace('-100', '')}/{msg.id}"
            except:
                pass

            new_links = []
            for link in valid_links:
                link_type = DatabaseManager._detect_link_type(link)
                inserted = await self.db.insert_link(
                    link, link_type, msg.text, msg.date.replace(tzinfo=None) if msg.date else datetime.now(),
                    chat.id, group_name, sender_name, msg_link)
                if inserted:
                    new_links.append(link)

            if not new_links:
                return

            formatted = MessageFormatter.format_link_message(
                group_name, sender_name, msg.date, new_links, msg.text, msg_link)
            await self._send(formatted)
            logging.info(f"[LIVE] Forwarded {len(new_links)} WhatsApp links from {group_name}")

        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"New message error: {e}", exc_info=True)

    async def _on_private_message(self, event):
        try:
            text = (event.message.text or "").strip()
            sender = await event.get_sender()
            sender_id = sender.id if sender else None

            if sender:
                await self.db.add_user(
                    sender.id,
                    getattr(sender, 'username', None),
                    getattr(sender, 'first_name', None))

            if sender_id in self._add_friend_states:
                await self._handle_add_friend_input(event, sender, text)
                return

            if text.startswith("/start"):
                me = await self.bot_client.get_me()
                first_name = sender.first_name if sender and hasattr(sender, 'first_name') else ""
                await event.reply(
                    MessageFormatter.format_welcome(me.username, first_name),
                    buttons=await self._get_main_menu()
                )
                return

            if text == "/help":
                await event.reply(
                    MessageFormatter.format_help(),
                    buttons=[Button.inline("🔙 القائمة الرئيسية", b"main_menu")]
                )
                return

            if text == "/status":
                total = await self.db.count_links()
                groups = await self.db.count_groups()
                users = await self.db.count_users()
                friends = await self.db.count_friends()
                excluded = await self.db.count_excluded()
                await event.reply(
                    MessageFormatter.format_status(
                        total, groups, users, friends, excluded,
                        self.is_scan_running(), self._scan_progress),
                    buttons=[Button.inline("🔙 القائمة الرئيسية", b"main_menu")]
                )
                return

            if text and not text.startswith('/'):
                me = await self.bot_client.get_me()
                first_name = sender.first_name if sender and hasattr(sender, 'first_name') else ""
                await event.reply(
                    MessageFormatter.format_welcome(me.username, first_name),
                    buttons=await self._get_main_menu()
                )

        except Exception as e:
            logging.error(f"Private message error: {e}", exc_info=True)

    async def _on_callback(self, event):
        try:
            data = event.data.decode('utf-8')
            sender = await event.get_sender()
            sender_id = sender.id if sender else None

            logging.info(f"[CALLBACK] {sender_id}: {data}")

            if data == "add_to_group":
                me = await self.bot_client.get_me()
                await event.answer()
                await event.edit(
                    "➕ إضافة البوت لمجموعتك\n\n"
                    "📌 الخطوات:\n\n"
                    "1️⃣ افتح المجموعة الدراسية\n"
                    "2️⃣ اضغط على اسم المجموعة (أعلى الشاشة)\n"
                    "3️⃣ اختر «إضافة مشرفين» (Administrators)\n"
                    "4️⃣ ابحث عن البوت: @" + (me.username or "YourBot") + "\n"
                    "5️⃣ اضغط على البوت وفعّل الصلاحيات:\n"
                    "   ✅ Read Messages (قراءة الرسائل)\n\n"
                    "✅ بعد الإضافة، سأبدأ المراقبة تلقائياً!",
                    buttons=[Button.inline("🔙 رجوع", b"main_menu")]
                )

            elif data == "invite_friend":
                me = await self.bot_client.get_me()
                bot_username = me.username or "YourBot"

                await event.answer()
                await event.edit(
                    "👥 دعوة صديق\n\n"
                    "📌 طريقتان لدعوة أصدقائك:\n\n"
                    "1️⃣ شارك رابط البوت مباشرة:\n"
                    f"`https://t.me/{bot_username}`\n\n"
                    "2️⃣ أضف رقم صديقك يدوياً:\n"
                    "اضغط الزر أدناه وأرسل رقمه",
                    buttons=[
                        [Button.inline("📱 إضافة رقم صديق", b"add_friend_phone")],
                        [Button.inline("📤 مشاركة رابط البوت", b"share_link")],
                        [Button.inline("🔙 رجوع", b"main_menu")]
                    ],
                    link_preview=False
                )

            elif data == "add_friend_phone":
                self._add_friend_states[sender_id] = {"step": "phone"}
                await event.answer()
                await event.edit(
                    "📱 إضافة رقم صديق\n\n"
                    "📌 أرسل رقم صديقك بالصيغة الدولية.\n"
                    "مثال: +966500000000\n\n"
                    "⚠️ سيُحفظ في قائمة الأصدقاء.\n\n"
                    "للإلغاء: /cancel",
                    buttons=[Button.inline("🔙 إلغاء", b"cancel_add_friend")]
                )

            elif data == "cancel_add_friend":
                if sender_id in self._add_friend_states:
                    del self._add_friend_states[sender_id]
                await event.answer("تم الإلغاء")
                me = await self.bot_client.get_me()
                first_name = sender.first_name if sender and hasattr(sender, 'first_name') else ""
                await event.edit(
                    MessageFormatter.format_welcome(me.username, first_name),
                    buttons=await self._get_main_menu()
                )

            elif data == "share_link":
                me = await self.bot_client.get_me()
                bot_username = me.username or "YourBot"
                await event.answer()
                await event.edit(
                    f"📤 شارك رابط البوت مع أصدقائك:\n\n"
                    f"`https://t.me/{bot_username}`\n\n"
                    "👥 بعد انضمامهم، سيستطيعون:\n"
                    "• إضافة البوت لمجموعاتهم\n"
                    "• الحصول على روابط واتساب\n"
                    "• استخدام كل الميزات",
                    buttons=[Button.inline("🔙 رجوع", b"main_menu")],
                    link_preview=False
                )

            elif data == "my_friends":
                friends = await self.db.get_friends()
                if not friends:
                    await event.answer()
                    await event.edit(
                        "👨‍💼 لا يوجد أصدقاء مُضافون بعد.\n\n"
                        "اضغط «👥 دعوة صديق» لإضافة صديق.",
                        buttons=[Button.inline("🔙 رجوع", b"main_menu")]
                    )
                else:
                    lines = [f"👨‍💼 أصدقاؤك ({len(friends)}):\n"]
                    for f in friends[:15]:
                        status = "✅ انضم" if f['joined'] else ("📤 دُعي" if f['invited'] else "⏳ بانتظار")
                        lines.append(f"• {f['name'] or 'بدون اسم'} - {f['phone']}\n  {status}")
                    if len(friends) > 15:
                        lines.append(f"\n... و {len(friends)-15} صديق آخر")
                    await event.answer()
                    await event.edit(
                        "\n".join(lines),
                        buttons=[Button.inline("🔙 رجوع", b"main_menu")]
                    )

            elif data == "status":
                total = await self.db.count_links()
                groups = await self.db.count_groups()
                users = await self.db.count_users()
                friends = await self.db.count_friends()
                excluded = await self.db.count_excluded()
                await event.answer()
                await event.edit(
                    MessageFormatter.format_status(
                        total, groups, users, friends, excluded,
                        self.is_scan_running(), self._scan_progress),
                    buttons=[Button.inline("🔙 رجوع", b"main_menu")]
                )

            elif data == "stats":
                recent = await self.db.get_recent_links(5)
                top = await self.db.get_top_groups(5)
                await event.answer()
                await event.edit(
                    MessageFormatter.format_detailed_stats(recent, top),
                    buttons=[Button.inline("🔙 رجوع", b"main_menu")]
                )

            elif data == "help":
                await event.answer()
                await event.edit(
                    MessageFormatter.format_help(),
                    buttons=[Button.inline("🔙 رجوع", b"main_menu")]
                )

            elif data == "commands":
                await event.answer()
                await event.edit(
                    "⚙️ أوامر القناة (للمالك)\n\n"
                    "📌 في قناة البوت:\n"
                    "• `/help` - دليل الأوامر\n"
                    "• `/status` - حالة البوت\n"
                    "• `/scan_week` - مسح آخر 7 أيام\n"
                    "• `/scan_month` - مسح آخر 30 يوم\n"
                    "• `/scan_full` - مسح كامل\n"
                    "• `/scan_stop` - إيقاف المسح\n"
                    "• `/groups` - قائمة المجموعات\n\n"
                    "📌 في الدردشة مع البوت:\n"
                    "• `/start` - القائمة الرئيسية\n"
                    "• `/help` - المساعدة\n"
                    "• `/status` - الحالة",
                    buttons=[Button.inline("🔙 رجوع", b"main_menu")]
                )

            elif data == "main_menu":
                me = await self.bot_client.get_me()
                first_name = sender.first_name if sender and hasattr(sender, 'first_name') else ""
                await event.answer()
                await event.edit(
                    MessageFormatter.format_welcome(me.username, first_name),
                    buttons=await self._get_main_menu()
                )

            else:
                await event.answer("أمر غير معروف")

        except Exception as e:
            logging.error(f"Callback error: {e}", exc_info=True)
            try:
                await event.answer("حدث خطأ")
            except:
                pass

    async def _handle_add_friend_input(self, event, sender, text):
        sender_id = sender.id
        state = self._add_friend_states.get(sender_id)
        if not state:
            return

        if text == "/cancel":
            del self._add_friend_states[sender_id]
            me = await self.bot_client.get_me()
            first_name = sender.first_name if sender and hasattr(sender, 'first_name') else ""
            await event.reply(
                "❌ تم الإلغاء.",
                buttons=await self._get_main_menu()
            )
            return

        phone = text.strip()
        if not phone.startswith("+"):
            await event.reply("❌ الرقم يجب أن يبدأ بـ +\nمثال: +966500000000\n\nأعد الإرسال أو /cancel")
            return

        added = await self.db.add_friend(phone, "صديق", sender_id)
        if not added:
            await event.reply("❌ فشل الحفظ. حاول مرة أخرى.")
            return

        me = await self.bot_client.get_me()
        bot_username = me.username or "YourBot"

        try:
            invite_text = (
                f"👋 أهلاً {sender.first_name or 'صديقك'}!\n\n"
                f"📞 {sender.first_name or 'صديقك'} دعاك لانضمام لبوت سحب روابط واتساب.\n\n"
                f"📌 اضغط الرابط للانضمام:\n"
                f"https://t.me/{bot_username}?start=ref_{sender_id}\n\n"
                f"✅ البوت يجمع روابط واتساب من المجموعات الدراسية."
            )
            try:
                await self.bot_client.send_message(phone, invite_text)
                await event.reply(
                    f"✅ تمت دعوة {phone} بنجاح!\n\n"
                    f"📤 أُرسل له رابط البوت في تيليجرام.",
                    buttons=await self._get_main_menu()
                )
            except Exception as send_err:
                await event.reply(
                    f"⚠️ تم حفظ {phone} في قائمة الأصدقاء.\n\n"
                    f"❌ لم أستطع إرسال دعوة تلقائية (إعدادات خصوصية).\n\n"
                    f"📤 شارك هذا الرابط معه يدوياً:\n"
                    f"`https://t.me/{bot_username}?start=ref_{sender_id}`",
                    buttons=await self._get_main_menu(),
                    link_preview=False
                )
        except Exception as e:
            logging.error(f"Friend invite error: {e}")
            await event.reply(
                f"⚠️ تم حفظ {phone}.\n❌ خطأ: {e}",
                buttons=await self._get_main_menu()
            )

        del self._add_friend_states[sender_id]

    async def _on_channel_command(self, event):
        try:
            text = (event.message.text or "").strip()
            if not text:
                return
            cmd = text.split()[0].lower()

            if self.config.owner_id:
                sender = await event.get_sender()
                if getattr(sender, 'id', None) != self.config.owner_id:
                    return

            logging.info(f"[CMD-CHANNEL] {cmd}")

            if cmd == "/help":
                await self._send("⚙️ أوامر القناة:\n• /status - الحالة\n• /scan_week - مسح أسبوع\n• /scan_month - مسح شهر\n• /scan_full - مسح كامل\n• /scan_stop - إيقاف\n• /groups - المجموعات")
            elif cmd == "/status":
                total = await self.db.count_links()
                groups = await self.db.count_groups()
                users = await self.db.count_users()
                friends = await self.db.count_friends()
                excluded = await self.db.count_excluded()
                await self._send(MessageFormatter.format_status(
                    total, groups, users, friends, excluded,
                    self.is_scan_running(), self._scan_progress))
            elif cmd == "/scan_week":
                await self._start_scan(7, "/scan_week")
            elif cmd == "/scan_month":
                await self._start_scan(30, "/scan_month")
            elif cmd == "/scan_full":
                await self._start_scan(None, "/scan_full")
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
        try:
            await asyncio.sleep(3)
            logging.info(f"[NEW GROUP SCAN] Starting scan for {chat_title}")
            cutoff = datetime.now() - timedelta(days=self.config.startup_scan_days or 30)
            count = 0
            excluded = 0
            async for msg in self.bot_client.iter_messages(chat_id, reverse=False, limit=self.config.history_max_per_chat):
                try:
                    md = msg.date.replace(tzinfo=None) if msg.date else None
                except:
                    md = None
                if md and md < cutoff:
                    break
                if not msg or not msg.text:
                    continue
                links = LinkExtractor.extract_links(msg.text)
                if not links:
                    continue
                valid_links, excluded_links = LinkExtractor.filter_links(links)
                excluded += len(excluded_links)
                for link, reason in excluded_links:
                    await self.db.insert_excluded(link, reason)
                if not valid_links:
                    continue

                try:
                    sender = await msg.get_sender()
                    sn = (sender.first_name or "User") if hasattr(sender, 'first_name') else "User"
                except:
                    sn = "User"

                msg_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{msg.id}"

                for link in valid_links:
                    link_type = DatabaseManager._detect_link_type(link)
                    inserted = await self.db.insert_link(
                        link, link_type, msg.text, md, chat_id, chat_title, sn, msg_link)
                    if inserted:
                        count += 1
                        formatted = MessageFormatter.format_link_message(
                            chat_title, sn, md, [link], msg.text, msg_link)
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
            logging.info(f"[NEW GROUP SCAN] {chat_title}: {count} new links, {excluded} excluded")
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
# HTTP Server
# -------------------------------------------------------------------


async def health_handler(request):
    return web.Response(text="✅ Bot is running v11 PRO", status=200)


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
    config = Config()
    errors = config.validate()
    if errors:
        for e in errors:
            print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    setup_logging(config.log_level)
    logging.info("=== Telegram WhatsApp Link Monitor v11 PRO ===")
    logging.info(f"Bot token: {config.bot_token[:20]}...")
    logging.info(f"Channel ID: {config.channel_id}")
    if config.startup_scan_days is not None:
        logging.info(f"Startup scan: {config.startup_scan_days} days")

    Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

    db = DatabaseManager()
    await db.init_db()

    monitor = Monitor(config, db)
    await monitor.start()
    http_runner = await start_http_server()

    logging.info("✅ Bot v11 PRO started. Send /start to bot.")

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
