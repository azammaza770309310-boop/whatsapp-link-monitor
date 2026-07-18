#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram Help Requests Monitor - v7
بوت سحب طلبات المساعدة الدراسية متعدد المستخدمين

المعمارية:
- كل مستخدم يضيف حسابه الشخصي (User Account) كـ "مُراقب"
- كل المستخدمين يرسلون طلبات المساعدة لقناة مشتركة واحدة
- البوت (BOT_TOKEN) ينشر في القناة + يرد على الأوامر
- DB مشتركة لكل المستخدمين

المميزات:
1. دعم متعدد المستخدمين (Multi-User)
2. سحب طلبات المساعدة الدراسية (أكثر من 30 كلمة مفتاحية)
3. فلترة رسائل السبام والإعلانات
4. مسح تاريخي عند اشتراك مستخدم جديد (يسحب من مجموعاته)
5. أوامر إدارة لكل مستخدم
6. دعم StringSession للنشر السحابي
"""

import asyncio
import logging
import os
import re
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Set

import aiohttp
import aiosqlite
from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession
from telethon.tl.types import Message
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

# Regex لروابط واتساب وتيليجرام فقط
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

TELEGRAM_LINK_PATTERN = re.compile(
    r"""
    (?:https?://)?
    (?:
        t\.me
      | telegram\.me
    )
    /[^\s<>"'\)\]]*
    """,
    re.IGNORECASE | re.VERBOSE,
)

# كلمات إعلانية - إذا وجدت في الرسالة، يتم استبعاد الرابط
ADVERTISER_KEYWORDS = [
    # تواصل وخدمات
    "للتواصل", "عبر حسابنا", "مكتبنا", "خدمات طلابية", "بأسعار مناسبة",
    "تواصل خاص", "تواصل واتساب", "عرض احتياجك", "سجل طلبك",
    "اعذار ولقيت", "اعذار طبية جاهزة", "في صحتي",
    "يكلمني ويبشر", "سكليف اجازه مرضيه معتمدة بصحتي",
    "رقم للتواصل", "ارسال رسالة", "عرض خدمات", "طلب خدمة",
    "حساب شخصي", "رقم جوال", "مراسلة", "سجل طلبك هنا",
    "خدمة مدرسية", "حل واجبات", "طلب تدريبي", "تواصل معانا",
    "خدمات تعليمية", "project service", "study help",
    "دعم دراسي", "توصيل مشروع", "تسليم واجب",
    "خدمة اونلاين", "حل واجب فوري", "حل بحث سريع",
    "طلب مشروع", "تسليم مشروع", "خدمات اكاديمية",
    "مراسلة عبر واتساب", "رقم واتساب", "تواصل شخصي",
    # أرقام هواتف
    "+966", "056", "053", "050", "054", "055", "058", "059",
    # كلمات تسويقية
    "promotion", "announcement", "اعلان", "اعلانات",
    "خصم", "عروض", "تخفيض", "خصومات", "عروض خاصة",
    "عرض محدود", "عرض لفترة محدودة", "استفد الآن",
    "احجز الآن", "اطلب الآن", "سارع", "بسرعة",
    "فرصة", "فرصه", "محدودة", "العدد محدود",
    "أماكن محدودة", "مقاعد محدودة", "حجز", "احجز",
    "حجوزات", "حجز مسبق", "حجز الآن",
    # دفع
    "دفع", "الدفع", "دفع اونلاين", "الدفع اونلاين",
    "سداد", "السداد", "الدفع المسبق", "دفع مسبق",
    "الدفع عند الاستلام", "دفع عند الاستلام",
    # ضمانات تسويقية
    "ضمان", "ضمان استرجاع", "ضمان الجودة",
    "جودة عالية", "عالية الجودة", "مضمون",
    "نتيجة مضمونة", "نتائج مضمونة", "ضمان النتيجة",
    "خبرة طويلة", "سنوات من الخبرة",
    "كفاءة عالية", "سرعة في التنفيذ", "تنفيذ سريع",
    "انجاز سريع", "انجاز في وقت قياسي",
    "سرية تامة", "خصوصية تامة",
    # مؤسسات تعليمية (إعلانات)
    "مكتب خدمات", "مركز تعليمي", "مركز تدريب",
    "معهد تعليمي", "معهد تدريب", "أكاديمية تعليمية",
    "أكاديمية تدريب", "مؤسسة تعليمية", "مؤسسة تدريب",
    "شركة تعليمية", "شركة تدريب", "مجموعة تعليمية",
    "مجموعة تدريب", "فريق تعليمي", "فريق تدريب",
]

# كلمات للتجاهل التام (رسائل قصيرة/ترحيبية)
IGNORE_KEYWORDS = [
    "صباح الخير", "مساء الخير", "اهلا", "مرحبا", "شكرا",
    "الله يسعدك", "ماقصرت", "يعطيك العافية", "تمام",
    "حلو", "جيد", "ممتاز", "تسلم", "بالتوفيق",
]


def is_advertiser_message(text: str) -> bool:
    """يتحقق إن كانت الرسالة إعلانية (يتم استبعادها)"""
    if not text:
        return False

    # رسائل طويلة جداً = غالباً إعلانات
    if len(text.splitlines()) >= 6:
        return True

    text_lower = text.lower()

    # فحص الكلمات الإعلانية
    for kw in ADVERTISER_KEYWORDS:
        if kw.lower() in text_lower:
            return True

    # فحص أرقام الهواتف السعودية
    if re.search(r"\+966\d{9}", text):
        return True
    if re.search(r"\b05\d{8}\b", text):
        return True

    return False


def extract_whatsapp_telegram_links(text: str) -> list:
    """يستخرج روابط واتساب وتيليجرام فقط من النص"""
    if not text:
        return []

    links = []

    # روابط واتساب
    for match in WHATSAPP_LINK_PATTERN.findall(text):
        link = match.rstrip(".,;:!?)]}>\"'")
        if link and link not in links:
            links.append(link)

    # روابط تيليجرام
    for match in TELEGRAM_LINK_PATTERN.findall(text):
        link = match.rstrip(".,;:!?)]}>\"'")
        # استبعاد روابط الانضمام للمجموعات (t.me/+xxx) - هذه دعوات، ليست محتوى
        if "/+" in link or "joinchat" in link.lower():
            continue
        if link and link not in links:
            links.append(link)

    return links


# أنماط استخراج بيانات التواصل من نص الرسالة
PHONE_PATTERN = re.compile(r'(\+966\d{8,9}|\+967\d{8,9}|\+968\d{8,9}|\+971\d{8,9}|\+20\d{8,9}|05\d{8})')
USERNAME_PATTERN = re.compile(r'(@[a-zA-Z0-9_]{4,})')


def extract_sender_contact(text: str) -> str:
    """يستخرج رقم الهاتف أو اليوزر من نص الرسالة"""
    if not text:
        return ""
    
    # البحث عن رقم هاتف أولاً
    phone_match = PHONE_PATTERN.search(text)
    if phone_match:
        return f"📱 {phone_match.group(1)}"
    
    # البحث عن يوزر تيليجرام
    username_match = USERNAME_PATTERN.search(text)
    if username_match:
        return f"✈️ {username_match.group(1)}"
    
    return ""

SCAN_COMMANDS = {"/scan_week": 7, "/scan_month": 30, "/scan_60": 60, "/scan_90": 90, "/scan_full": None}


# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------


class Config:
    def __init__(self):
        load_dotenv(dotenv_path='accounts.env')
        self.api_id = int(os.getenv("API_ID", "0"))
        self.api_hash = os.getenv("API_HASH", "")
        self.bot_token = os.getenv("BOT_TOKEN", "")
        self.channel_id = int(os.getenv("CHANNEL_ID", "0"))
        self.owner_id = None
        oid = os.getenv("OWNER_ID", "")
        if oid:
            try: self.owner_id = int(oid)
            except: pass
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.history_max_per_chat = int(os.getenv("HISTORY_MAX_PER_CHAT", "500"))
        self.history_batch_size = max(1, min(int(os.getenv("HISTORY_BATCH_SIZE", "5")), 20))
        self.history_skip_channel_posts = os.getenv("HISTORY_SKIP_CHANNEL_POSTS", "false").lower() == "true"
        self.startup_scan_days = None
        ssd = os.getenv("STARTUP_SCAN_DAYS", "")
        if ssd and ssd.lower() not in ("none", "null", ""):
            try:
                self.startup_scan_days = int(ssd)
            except:
                pass
        if self.startup_scan_days is None and not ssd:
            self.startup_scan_days = 30
            logging.info("Default startup scan: 30 days")
        # متغيرات إضافية
        self.min_message_length = int(os.getenv("MIN_MESSAGE_LENGTH", "20"))
        self.max_message_length = int(os.getenv("MAX_MESSAGE_LENGTH_FILTER", "2000"))

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
# Database Manager (Multi-User)
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
        # جدول المستخدمين المراقبين
        await conn.execute("""CREATE TABLE IF NOT EXISTS watchers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL UNIQUE,
            display_name TEXT,
            session_string TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1)""")
        # جدول طلبات المساعدة المحوّلة
        await conn.execute("""CREATE TABLE IF NOT EXISTS forwarded_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_text TEXT NOT NULL,
            message_date TIMESTAMP,
            group_name TEXT,
            sender_name TEXT,
            source_phone TEXT,
            message_link TEXT,
            content_hash TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_content_hash ON forwarded_requests (content_hash)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_source_phone ON forwarded_requests (source_phone)")
        # جدول سجل المسح لكل مستخدم
        await conn.execute("""CREATE TABLE IF NOT EXISTS scan_state (
            source_phone TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            chat_name TEXT,
            last_scanned_at TIMESTAMP NOT NULL,
            last_scanned_message_date TIMESTAMP NOT NULL,
            PRIMARY KEY (source_phone, chat_id))""")
        await conn.commit()

    async def add_watcher(self, phone: str, display_name: str, session_string: str) -> bool:
        """إضافة مستخدم مراقب جديد - يدعم التحديث إذا كان موجوداً"""
        async with self._lock:
            conn = await self._ensure_conn()
            try:
                # التحقق إن كان موجوداً أولاً
                cursor = await conn.execute(
                    "SELECT phone FROM watchers WHERE phone = ?", (phone,))
                existing = await cursor.fetchone()
                if existing:
                    # تحديث الموجود
                    await conn.execute(
                        """UPDATE watchers SET display_name = ?, session_string = ?, is_active = 1
                        WHERE phone = ?""",
                        (display_name, session_string, phone))
                else:
                    # إدراج جديد
                    await conn.execute(
                        """INSERT INTO watchers (phone, display_name, session_string, is_active)
                        VALUES (?, ?, ?, 1)""",
                        (phone, display_name, session_string))
                await conn.commit()
                return True
            except Exception as e:
                logging.error(f"Add watcher error: {e}")
                return False

    async def get_active_watchers(self) -> List[Dict]:
        """جلب كل المستخدمين المراقبين النشطين"""
        conn = await self._ensure_conn()
        cursor = await conn.execute("SELECT phone, display_name, session_string FROM watchers WHERE is_active = 1")
        rows = await cursor.fetchall()
        return [{"phone": r[0], "display_name": r[1], "session_string": r[2]} for r in rows]

    async def remove_watcher(self, phone: str) -> bool:
        async with self._lock:
            conn = await self._ensure_conn()
            cursor = await conn.execute("UPDATE watchers SET is_active = 0 WHERE phone = ?", (phone,))
            await conn.commit()
            return cursor.rowcount > 0

    async def insert_request(self, message_text: str, message_date: datetime,
                              group_name: str, sender_name: str, source_phone: str,
                              message_link: str = None) -> bool:
        """إدراج طلب مساعدة جديد (مع منع التكرار)"""
        async with self._lock:
            conn = await self._ensure_conn()
            import hashlib
            content_hash = hashlib.md5(message_text[:500].encode()).hexdigest()
            try:
                await conn.execute(
                    """INSERT OR IGNORE INTO forwarded_requests
                    (message_text, message_date, group_name, sender_name, source_phone, message_link, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (message_text, message_date.isoformat() if message_date else None,
                     group_name, sender_name, source_phone, message_link, content_hash))
                await conn.commit()
                cursor = await conn.execute("SELECT changes()")
                changes = await cursor.fetchone()
                return changes[0] > 0
            except Exception as e:
                logging.error(f"Insert request error: {e}")
                return False

    async def count_requests(self, source_phone: str = None) -> int:
        conn = await self._ensure_conn()
        if source_phone:
            cursor = await conn.execute("SELECT COUNT(*) FROM forwarded_requests WHERE source_phone = ?", (source_phone,))
        else:
            cursor = await conn.execute("SELECT COUNT(*) FROM forwarded_requests")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_last_scan_date(self, source_phone: str, chat_id: int):
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT last_scanned_message_date FROM scan_state WHERE source_phone = ? AND chat_id = ?",
            (source_phone, chat_id))
        row = await cursor.fetchone()
        if row and row[0]:
            try: return datetime.fromisoformat(row[0])
            except: return None
        return None

    async def update_scan_state(self, source_phone: str, chat_id: int, chat_name: str, last_msg_date: datetime):
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                """INSERT INTO scan_state (source_phone, chat_id, chat_name, last_scanned_at, last_scanned_message_date)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(source_phone, chat_id) DO UPDATE SET
                chat_name=excluded.chat_name, last_scanned_at=excluded.last_scanned_at,
                last_scanned_message_date=excluded.last_scanned_message_date""",
                (source_phone, chat_id, chat_name, datetime.now().isoformat(), last_msg_date.isoformat()))
            await conn.commit()

    async def reset_scan_state(self, source_phone: str = None):
        async with self._lock:
            conn = await self._ensure_conn()
            if source_phone:
                cursor = await conn.execute("DELETE FROM scan_state WHERE source_phone = ?", (source_phone,))
            else:
                cursor = await conn.execute("DELETE FROM scan_state")
            await conn.commit()
            return cursor.rowcount

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None


# -------------------------------------------------------------------
# Help Request Detector
# -------------------------------------------------------------------


class HelpRequestDetector:
    """يكشف ما إذا كانت الرسالة تحتوي على طلب مساعدة دراسية"""

    @staticmethod
    def is_help_request(text: str, min_length: int = 20, max_length: int = 2000) -> Tuple[bool, List[str]]:
        """
        يفحص النص ويعيد:
        - True + كلمات مطابقة إن كان طلب مساعدة
        - False + [] إن لم يكن
        """
        if not text:
            return False, []

        text_str = text.strip()
        if len(text_str) < min_length or len(text_str) > max_length:
            return False, []

        # فحص السبام أولاً
        text_lower = text_str.lower()
        for spam in SPAM_KEYWORDS:
            if spam.lower() in text_lower:
                return False, []

        # فحص الكلمات المفتاحية
        found_keywords = []
        text_lower = text_str.lower()

        # فحص الكلمات المركبة أولاً
        for kw in HELP_KEYWORDS:
            if ' ' in kw:
                if kw.lower() in text_lower:
                    found_keywords.append(kw)

        # فحص الكلمات المنفردة عبر regex
        single_keywords = [kw for kw in HELP_KEYWORDS if ' ' not in kw]
        if single_keywords:
            pattern = re.compile(r'\b(' + '|'.join(re.escape(k) for k in single_keywords) + r')\b', re.IGNORECASE)
            matches = pattern.findall(text_str)
            found_keywords.extend(matches)

        # إزالة التكرار
        found_keywords = list(dict.fromkeys(found_keywords))

        # يجب أن يحتوي على كلمة واحدة على الأقل
        if len(found_keywords) >= 1:
            return True, found_keywords
        return False, []


# -------------------------------------------------------------------
# Message Formatter
# -------------------------------------------------------------------


class MessageFormatter:
    @staticmethod
    def format_link_message(group_name, sender_name, sender_contact, message_date, link,
                            message_text, source_phone, message_link=None):
        """تنسيق رابط واتساب/تيليجرام للنشر في القناة مع دوائر ملونة"""
        # اقتطاع النص الطويل
        if len(message_text) > MAX_MESSAGE_LENGTH:
            message_text = message_text[:MAX_MESSAGE_LENGTH] + "..."

        date_str = message_date.strftime("%Y-%m-%d %H:%M") if message_date else "غير معروف"

        # تحديد نوع الرابط مع الدائرة الملونة
        link_lower = link.lower()
        if "chat.whatsapp.com" in link_lower or "wa.me" in link_lower or "whatsapp.com" in link_lower:
            link_type = "🟢 واتساب"
        elif "t.me" in link_lower or "telegram.me" in link_lower:
            link_type = "🔵 تيليجرام"
        else:
            link_type = "🔗 رابط"

        lines = [
            f"📥 رابط جديد ({link_type})",
            "",
            f"👥 المجموعة: {group_name}",
            f"👤 المرسل: {sender_name}",
        ]
        if sender_contact:
            lines.append(f"📞 تواصل المرسل: {sender_contact}")
        lines.extend([
            f"🕒 التاريخ: {date_str}",
            f"📡 المصدر: {source_phone}",
            "",
            f"🔗 الرابط: {link}",
        ])
        if message_link:
            lines.append(f"📨 الرسالة الأصلية: {message_link}")
        lines.extend(["", "💬 النص:", message_text])
        return "\n".join(lines)

    @staticmethod
    def format_help_request(group_name, sender_name, message_date, message_text,
                            keywords_found, source_phone, message_link=None):
        """تنسيق طلب مساعدة للنشر في القناة (محفوظ للتوافق)"""
        # اقتطاع النص الطويل
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
            f"📡 المصدر: {source_phone}",
        ]
        if message_link:
            lines.append(f"🔗 الرابط: {message_link}")
        lines.extend(["", "💬 الرسالة:", message_text])
        return "\n".join(lines)

    @staticmethod
    def format_history_batch(batch):
        """تنسيق دفعة طلبات تاريخية"""
        lines = ["📚 طلبات مساعدة تاريخية", ""]
        for i, item in enumerate(batch, 1):
            date_str = item['date'].strftime("%Y-%m-%d") if item.get('date') else "غير معروف"
            short_group = item['group'][:30] + "…" if len(item.get('group', '')) > 30 else item.get('group', '')
            preview = item['text'][:100] + "..." if len(item.get('text', '')) > 100 else item.get('text', '')
            lines.append(f"{i}. 📚 {short_group}")
            lines.append(f"   📅 {date_str} | 👤 {item.get('sender', 'Unknown')}")
            lines.append(f"   💬 {preview}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def format_scan_summary(total_scanned, total_found, new_count, chats_scanned, period_desc, duration_sec, source_phone):
        return (f"📊 ملخص المسح التاريخي\n\n"
                f"📡 المصدر: {source_phone}\n"
                f"📅 الفترة: {period_desc}\n"
                f"💬 المحادثات: {chats_scanned}\n"
                f"🔍 الرسائل المفحوصة: {total_scanned}\n"
                f"📚 طلبات مساعدة موجودة: {total_found}\n"
                f"✅ طلبات جديدة منشورة: {new_count}\n"
                f"⏱️ المدة: {duration_sec:.1f} ثانية\n")

    @staticmethod
    def format_welcome(user_first_name=""):
        name_part = f" {user_first_name}" if user_first_name else ""
        return (
            f"🤖 أهلاً بك{name_part} في بوت سحب روابط واتساب!\n\n"
            "📚 ماذا يفعل هذا البوت؟\n"
            "• يراقب مجموعاتك الدراسية تلقائياً\n"
            "• يسحب كل روابط واتساب منها\n"
            "• ينشرها في قناة مشتركة\n\n"
            "🚀 للبدء، اضغط زر «🔐 تسجيل الدخول» أدناه\n"
            "ثم أرسل رقم هاتفك + كود تيليجرام."
        )

    @staticmethod
    def format_help():
        return (
            "🤖 دليل الاستخدام\n\n"
            "📌 كيف يعمل البوت؟\n\n"
            "1️⃣ اضغط «🔐 تسجيل الدخول»\n"
            "2️⃣ أرسل رقم هاتفك (+967...)\n"
            "3️⃣ أرسل كود تيليجرام الذي تصله\n"
            "4️⃣ ✅ البوت يراقب مجموعاتك تلقائياً!\n\n"
            "📌 المميزات:\n"
            "• ✅ سحب جميع أنواع روابط واتساب\n"
            "• 📚 مسح آخر 30 يوم تلقائياً\n"
            "• 🔄 مسح تاريخي عند الطلب\n"
            "• 📊 إحصائيات مفصلة\n"
            "• 👥 دعوة الأصدقاء\n\n"
            "📌 للأصدقاء:\n"
            "• شارك رابط البوت معهم\n"
            "• سيسجلون دخولهم بنفس الطريقة\n"
            "• سيتم سحب روابط مجموعاتهم أيضاً"
        )

    @staticmethod
    def format_status(total_links, watchers_count, scan_running, scan_progress="", total_groups=0):
        return (f"📊 حالة البوت\n\n"
                f"📥 روابط واتساب منشورة: {total_links}\n"
                f"👥 المستخدمون المراقبون: {watchers_count}\n"
                f"💬 المجموعات المراقَبة: {total_groups}\n"
                f"🔄 المسح التاريخي: "
                + ("قيد التنفيذ" + (f" ({scan_progress})" if scan_progress else "") if scan_running else "متوقف")
                + "\n")


# -------------------------------------------------------------------
# History Scanner
# -------------------------------------------------------------------


class HistoryScanner:
    def __init__(self, user_client, bot_client, db, channel_id,
                 days_back, max_per_chat, batch_size, skip_channel_posts,
                 source_phone, source_name, progress_callback=None):
        self.user_client = user_client
        self.bot_client = bot_client
        self.db = db
        self.channel_id = channel_id
        self.days_back = days_back
        self.max_per_chat = max_per_chat
        self.batch_size = batch_size
        self.skip_channel_posts = skip_channel_posts
        self.source_phone = source_phone
        self.source_name = source_name
        self.progress_callback = progress_callback

        self.total_scanned = 0
        self.total_found = 0
        self.new_count = 0
        self.chats_scanned = 0
        self._cancelled = False

    def cancel(self): self._cancelled = True
    def _is_cancelled(self): return self._cancelled

    async def scan(self):
        start = datetime.now()
        if self.days_back is not None:
            hard = datetime.now() - timedelta(days=self.days_back)
        else: hard = None
        soft = None
        try:
            conn = await self.db._ensure_conn()
            cursor = await conn.execute(
                "SELECT MAX(last_scanned_message_date) FROM scan_state WHERE source_phone = ?",
                (self.source_phone,))
            row = await cursor.fetchone()
            if row and row[0]:
                soft = datetime.fromisoformat(row[0])
        except: pass
        eff = max(hard, soft) if (hard and soft) else (hard or soft)
        period = f"آخر {(datetime.now()-eff).days} يوم (متزايد)" if eff else "كامل"
        logging.info(f"[SCAN {self.source_phone}] Period: {period}")

        try:
            dialogs = await self.user_client.get_dialogs()
        except Exception as e:
            logging.error(f"[SCAN {self.source_phone}] get_dialogs: {e}")
            return period

        for idx, d in enumerate(dialogs, 1):
            if self._is_cancelled(): break
            if d.id == self.channel_id: continue
            if self.skip_channel_posts:
                try:
                    if d.is_channel: continue
                except: pass
            name = d.name or "Unknown"
            if self.progress_callback:
                try: self.progress_callback(idx, len(dialogs), name)
                except: pass
            try: await self._scan_chat(d, eff, name)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                logging.error(f"[SCAN {self.source_phone}] Error {name}: {e}")
            await asyncio.sleep(0.3)
        dur = (datetime.now()-start).total_seconds()
        await self._send_summary(period, dur)
        return period

    async def _scan_chat(self, dialog, cutoff, name):
        batch = []
        last_date = None
        chat_cut = cutoff
        if chat_cut is None:
            try:
                ls = await self.db.get_last_scan_date(self.source_phone, dialog.id)
                if ls: chat_cut = ls
            except: pass
        try:
            async for msg in self.user_client.iter_messages(dialog, reverse=False, limit=self.max_per_chat):
                if self._is_cancelled(): break
                try: md = msg.date.replace(tzinfo=None) if msg.date else None
                except: md = None
                if md and chat_cut and md < chat_cut: break
                self.total_scanned += 1
                if md and (last_date is None or md > last_date): last_date = md
                if not msg or not msg.text: continue

                # استخراج روابط واتساب وتيليجرام
                links = extract_whatsapp_telegram_links(msg.text)
                if not links: continue

                # استبعاد الرسائل الإعلانية
                if is_advertiser_message(msg.text):
                    continue

                self.total_found += len(links)
                try:
                    sender = await msg.get_sender()
                    sn = Monitor._get_sender_name(sender)
                except: sn = "Unknown"

                # استخراج بيانات تواصل المرسل
                contact = extract_sender_contact(msg.text)
                if not contact and sender and hasattr(sender, 'username') and sender.username:
                    contact = f"✈️ @{sender.username}"

                # رابط الرسالة
                msg_link = None
                try:
                    msg_link = f"https://t.me/c/{str(dialog.id).replace('-100', '')}/{msg.id}"
                except: pass

                # إدراج كل رابط
                for link in links:
                    inserted = await self.db.insert_request(
                        link, md, name, sn, self.source_phone, msg_link)
                    if inserted:
                        self.new_count += 1
                        batch.append({
                            'link': link, 'text': msg.text, 'date': md,
                            'group': name, 'sender': sn, 'msg_link': msg_link,
                            'contact': contact
                        })
                        if len(batch) >= self.batch_size:
                            await self._send_batch(batch)
                            batch = []
        except FloodWaitError: raise
        except Exception as e:
            logging.error(f"[SCAN {self.source_phone}] iter error: {e}")
            if last_date:
                try: await self.db.update_scan_state(self.source_phone, dialog.id, name, last_date)
                except: pass
            return
        if batch: await self._send_batch(batch)
        if last_date:
            try: await self.db.update_scan_state(self.source_phone, dialog.id, name, last_date)
            except: pass
        self.chats_scanned += 1

    async def _send_batch(self, batch):
        # نشر كل رابط على حدة
        for item in batch:
            try:
                formatted = MessageFormatter.format_link_message(
                    item['group'], item['sender'], item.get('contact', ''), item['date'],
                    item['link'], item['text'], self.source_phone, item.get('msg_link'))
                await self.bot_client.send_message(self.channel_id, formatted)
                await asyncio.sleep(0.5)  # تجنب الفلو
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                logging.error(f"[SCAN] send error: {e}")

    async def _send_summary(self, period, dur):
        if self.new_count == 0 and self.total_scanned == 0: return
        f = MessageFormatter.format_scan_summary(
            self.total_scanned, self.total_found, self.new_count,
            self.chats_scanned, period, dur, self.source_phone)
        try: await self.bot_client.send_message(self.channel_id, f)
        except Exception as e: logging.error(f"[SCAN] summary: {e}")


# -------------------------------------------------------------------
# Monitor (Multi-User)
# -------------------------------------------------------------------


class Monitor:
    def __init__(self, config, db):
        self.config = config
        self.db = db
        self.bot_client = None
        # كل مستخدم مراقب له user_client خاص
        self.user_clients: Dict[str, TelegramClient] = {}
        self._running = False
        self._handlers_registered = False
        self._send_lock = asyncio.Lock()
        self._current_scanners: Dict[str, HistoryScanner] = {}
        self._current_scan_tasks: List[asyncio.Task] = []
        self._scan_progress: str = ""
        self._bot_task = None
        self._keep_alive_task = None
        # نظام تسجيل الدخول التفاعلي
        # كل مستخدم يبدأ التسجيل → نحفظ حالته مؤقتاً
        # states: {user_id: {"step": "phone"|"code"|"password"|"phone_code_hash", ...}}
        self._login_sessions: Dict[int, Dict] = {}
        self._user_tasks: Dict[str, asyncio.Task] = {}
        self._startup_scan_done: Set[str] = set()

    @staticmethod
    def _get_chat_name(chat):
        if hasattr(chat, "title") and chat.title: return chat.title
        if hasattr(chat, "first_name"):
            n = chat.first_name or ""
            if hasattr(chat, "last_name") and chat.last_name: n += f" {chat.last_name}"
            return n.strip() or "Private"
        return "Unknown Group"

    @staticmethod
    def _get_sender_name(sender):
        if not sender: return "Unknown"
        if hasattr(sender, "first_name"):
            n = sender.first_name or ""
            if hasattr(sender, "last_name") and sender.last_name: n += f" {sender.last_name}"
            return n.strip() or getattr(sender, "username", "") or "Unknown"
        return getattr(sender, "username", "Unknown") or "Unknown"

    def _create_bot_client(self):
        sp = os.path.join(SESSIONS_DIR, "bot")
        return TelegramClient(sp, self.config.api_id, self.config.api_hash,
                              connection_retries=None, retry_delay=5, request_retries=5,
                              auto_reconnect=True, sequential_updates=False)

    def _create_user_client(self, session_string, phone):
        """إنشاء user_client من StringSession"""
        return TelegramClient(
            StringSession(session_string),
            self.config.api_id, self.config.api_hash,
            connection_retries=None, retry_delay=5, request_retries=5,
            auto_reconnect=True, sequential_updates=False)

    def _register_handlers(self):
        if self._handlers_registered: return
        # معالج أوامر القناة
        self.bot_client.add_event_handler(
            self._on_command,
            events.NewMessage(chats=self.config.channel_id, pattern=r"^/[a-zA-Z_]+"))
        # معالج الدردشة الخاصة مع البوت (لـ /start و /login)
        self.bot_client.add_event_handler(
            self._on_private_message,
            events.NewMessage(func=lambda e: e.is_private))
        # معالج ضغطات الأزرار (Callback Queries)
        self.bot_client.add_event_handler(
            self._on_callback,
            events.CallbackQuery()
        )
        self._handlers_registered = True
        logging.info("Bot handlers registered (channel + private + buttons)")

    def _get_main_menu(self, is_logged_in=False):
        """القائمة الرئيسية - أزرار تفاعلية (بدون قائمة الأصدقاء)"""
        if is_logged_in:
            return [
                [Button.inline("📊 الحالة", b"status"),
                 Button.inline("📈 إحصائياتي", b"my_stats")],
                [Button.inline("🔄 مسح آخر أسبوع", b"scan_week"),
                 Button.inline("📅 مسح آخر شهر", b"scan_month")],
                [Button.inline("❓ المساعدة", b"help")],
            ]
        else:
            return [
                [Button.inline("🔐 تسجيل الدخول", b"login_start")],
                [Button.inline("❓ المساعدة", b"help"),
                 Button.inline("📊 الحالة", b"status")],
            ]

    async def _on_callback(self, event):
        """معالج ضغطات الأزرار"""
        try:
            data = event.data.decode('utf-8')
            sender = await event.get_sender()
            sender_id = sender.id if sender else None

            logging.info(f"[CALLBACK] {sender_id}: {data}")

            if data == "login_start":
                await self._handle_login_start(event, sender)
                return

            if data == "main_menu":
                # التحقق إن كان المستخدم مسجل دخول
                watchers = await self.db.get_active_watchers()
                user_phone = None
                for w in watchers:
                    if w.get('session_string'):
                        # التحقق عبر StringSession
                        user_phone = w['phone']
                        break
                is_logged_in = user_phone is not None
                first_name = sender.first_name if sender and hasattr(sender, 'first_name') else ""
                await event.edit(
                    MessageFormatter.format_welcome(first_name),
                    buttons=self._get_main_menu(is_logged_in)
                )
                return

            if data == "help":
                await event.answer()
                await event.edit(
                    MessageFormatter.format_help(),
                    buttons=[Button.inline("🔙 القائمة الرئيسية", b"main_menu")]
                )
                return

            if data == "status":
                watchers = await self.db.get_active_watchers()
                total_links = await self.db.count_requests()
                is_running = self.is_scan_running()
                await event.answer()
                await event.edit(
                    MessageFormatter.format_status(
                        total_links, len(watchers), is_running, self._scan_progress
                    ),
                    buttons=[Button.inline("🔙 القائمة الرئيسية", b"main_menu")]
                )
                return

            if data == "my_stats":
                watchers = await self.db.get_active_watchers()
                await event.answer()
                if not watchers:
                    await event.edit(
                        "ℹ️ أنت لم تسجل دخولك بعد.\nاضغط «🔐 تسجيل الدخول» للبدء.",
                        buttons=[Button.inline("🔙 القائمة الرئيسية", b"main_menu")]
                    )
                    return
                await event.edit(
                    f"📈 إحصائياتك\n\n👥 المستخدمون المراقبون: {len(watchers)}\n🔄 المسح: {'قيد التنفيذ' if self.is_scan_running() else 'متوقف'}",
                    buttons=[Button.inline("🔙 القائمة الرئيسية", b"main_menu")]
                )
                return

            if data == "scan_week":
                await event.answer("جاري بدء المسح...")
                await self._start_scan_all(7, "/scan_week")
                return

            if data == "scan_month":
                await event.answer("جاري بدء المسح...")
                await self._start_scan_all(30, "/scan_month")
                return

            await event.answer()

        except Exception as e:
            logging.error(f"Callback error: {e}", exc_info=True)
            try:
                await event.answer("حدث خطأ")
            except:
                pass

    def _register_user_handlers(self, phone: str):
        """تسجيل معالجات الرسائل لكل user_client"""
        client = self.user_clients.get(phone)
        if not client: return
        client.add_event_handler(
            lambda e: self._on_user_message(e, phone),
            events.NewMessage(incoming=True)
        )
        logging.info(f"User handlers registered for {phone}")

    async def _on_user_message(self, event, source_phone: str):
        """معالج رسائل المستخدم - يستخرج روابط واتساب وتيليجرام تلقائياً"""
        try:
            msg = event.message
            if not msg or not msg.text: return
            chat = await event.get_chat()
            if hasattr(chat, 'id') and chat.id == self.config.channel_id: return
            group_name = self._get_chat_name(chat)
            sender = await event.get_sender()
            sender_name = self._get_sender_name(sender)

            # استخراج روابط واتساب وتيليجرام
            links = extract_whatsapp_telegram_links(msg.text)
            if not links: return  # لا توجد روابط

            # استبعاد الرسائل الإعلانية
            if is_advertiser_message(msg.text):
                logging.info(f"[LIVE {source_phone}] Skipped advertiser message in {group_name}")
                return

            # استخراج بيانات تواصل المرسل من نص الرسالة
            sender_contact = extract_sender_contact(msg.text)
            # إذا لم يجد في النص، يستخدم يوزر مرسل الرسالة
            if not sender_contact and sender and hasattr(sender, 'username') and sender.username:
                sender_contact = f"✈️ @{sender.username}"

            # محاولة الحصول على رابط الرسالة
            msg_link = None
            try:
                msg_link = f"https://t.me/c/{str(chat.id).replace('-100', '')}/{msg.id}"
            except: pass

            # نشر كل رابط جديد
            for link in links:
                # إدراج في DB (يمنع التكرار)
                inserted = await self.db.insert_request(
                    link, msg.date.replace(tzinfo=None) if msg.date else datetime.now(),
                    group_name, sender_name, source_phone, msg_link)
                if not inserted: continue  # مكرر

                # تنسيق ونشر مع بيانات المرسل
                formatted = MessageFormatter.format_link_message(
                    group_name, sender_name, sender_contact, msg.date,
                    link, msg.text, source_phone, msg_link)
                await self._send(formatted)
                logging.info(f"[LIVE {source_phone}] Forwarded link from {group_name}: {link[:50]}")

        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"User message error: {e}", exc_info=True)

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

    async def _on_private_message(self, event):
        """معالج رسائل الدردشة الخاصة مع البوت - يدعم /start و /login"""
        try:
            text = (event.message.text or "").strip()
            if not text:
                return

            sender = await event.get_sender()
            sender_id = sender.id if sender else None
            if not sender_id:
                return

            # الأوامر
            if text.startswith("/start"):
                await self._handle_start(event, sender)
                return

            if text.startswith("/login"):
                await self._handle_login_start(event, sender)
                return

            if text == "/cancel":
                if sender_id in self._login_sessions:
                    del self._login_sessions[sender_id]
                await event.reply("❌ تم إلغاء عملية التسجيل.")
                return

            if text == "/status":
                watchers = await self.db.get_active_watchers()
                user_phone = sender.phone if hasattr(sender, 'phone') and sender.phone else None
                is_watcher = any(w['phone'] == user_phone for w in watchers) if user_phone else False
                await event.reply(
                    f"📊 حالتك:\n"
                    f"👤 معرّفك: {sender_id}\n"
                    f"📡 مراقب نشط: {'✅ نعم' if is_watcher else '❌ لا'}\n"
                    f"👥 إجمالي المراقبين: {len(watchers)}\n\n"
                    f"للتسجيل: /login\n"
                    f"للمساعدة: /start"
                )
                return

            # إذا كان في عملية تسجيل
            if sender_id in self._login_sessions:
                await self._handle_login_step(event, sender, text)
                return

            # رسالة غير معروفة
            await event.reply(
                "🤖 أهلاً!\n\n"
                "📌 الأوامر المتاحة:\n"
                "• /start - البدء\n"
                "• /login - تسجيل الدخول بحسابك\n"
                "• /status - حالتك\n"
                "• /cancel - إلغاء العملية"
            )

        except Exception as e:
            logging.error(f"Private message error: {e}", exc_info=True)

    async def _handle_start(self, event, sender):
        """معالج أمر /start - يعرض القائمة بالأزرار"""
        sender_id = sender.id
        first_name = sender.first_name if sender and hasattr(sender, 'first_name') else ""

        # التحقق إن كان المستخدم مسجل دخول (لديه جلسة نشطة)
        watchers = await self.db.get_active_watchers()
        is_logged_in = len(watchers) > 0  # مبسط: أي مراقب نشط = مسجل

        # ملاحظة: لا نقوم بحفظ المستخدمين في قاعدة البيانات (متطلب المالك)
        # المستخدمون يبقون مخفيين تماماً، لا تظهر أسماؤهم في أي قائمة

        await event.reply(
            MessageFormatter.format_welcome(first_name),
            buttons=self._get_main_menu(is_logged_in)
        )

    async def _handle_login_start(self, event, sender):
        """بدء عملية تسجيل الدخول التفاعلية"""
        sender_id = sender.id

        # التحقق من عدم وجود تسجيل سابق
        if sender_id in self._login_sessions:
            await event.reply("⚠️ لديك عملية تسجيل قائمة بالفعل. أرسل /cancel للإلغاء.")
            return

        # بدء جلسة تسجيل جديدة
        self._login_sessions[sender_id] = {
            "step": "phone",
            "temp_client": None,
            "phone": None,
            "phone_code_hash": None,
        }

        await event.reply(
            "🔐 تسجيل الدخول\n\n"
            "📌 أرسل رقم هاتفك بالصيغة الدولية.\n"
            "مثال: +967770309310\n\n"
            "⚠️ الرقم يجب أن يكون مرتبطاً بحساب تيليجرام.\n\n"
            "للإلغاء: /cancel"
        )

    async def _handle_login_step(self, event, sender, text):
        """معالجة خطوات تسجيل الدخول التفاعلية"""
        sender_id = sender.id
        session = self._login_sessions.get(sender_id)
        if not session:
            return

        step = session.get("step")

        if step == "phone":
            # استلام رقم الهاتف
            phone = text.strip()
            if not phone.startswith("+"):
                await event.reply("❌ الرقم يجب أن يبدأ بـ +\nمثال: +967770309310\n\nأعد الإرسال أو /cancel")
                return

            # إنشاء عميل مؤقت
            try:
                # تنظيف أي عميل سابق
                if session.get("temp_client"):
                    await session["temp_client"].disconnect()

                temp_client = TelegramClient(
                    StringSession(),
                    self.config.api_id, self.config.api_hash,
                    connection_retries=3, retry_delay=2, request_retries=3,
                )
                await temp_client.connect()

                # إرسال كود تيليجرام
                result = await temp_client.send_code_request(phone)
                session["temp_client"] = temp_client
                session["phone"] = phone
                session["phone_code_hash"] = result.phone_code_hash
                session["step"] = "code"

                await event.reply(
                    f"✅ تم إرسال كود التحقق إلى حسابك في تيليجرام.\n\n"
                    f"📲 تحقق من رسائل تيليجرام (من حساب Telegram الرسمي).\n\n"
                    f"📌 أرسل الكود الآن (مثال: 12345):\n\n"
                    f"للإلغاء: /cancel"
                )
            except Exception as e:
                logging.error(f"Login phone error: {e}")
                await event.reply(f"❌ خطأ: {e}\n\nأعد المحاولة بـ /login أو /cancel")
                if session.get("temp_client"):
                    try: await session["temp_client"].disconnect()
                    except: pass
                del self._login_sessions[sender_id]

        elif step == "code":
            # استلام كود التحقق
            code = text.strip().replace(" ", "").replace("-", "")
            try:
                temp_client = session["temp_client"]
                phone = session["phone"]
                phone_code_hash = session["phone_code_hash"]

                # محاولة تسجيل الدخول
                try:
                    await temp_client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
                except Exception as e:
                    err_str = str(e)
                    if "SessionPasswordNeeded" in err_str or "PASSWORD" in err_str.upper():
                        # الحساب محمي بكلمة سر (2FA)
                        session["step"] = "password"
                        await event.reply(
                            "🔐 حسابك محمي بالتحقق بخطوتين (2FA).\n\n"
                            "📌 أرسل كلمة سر تيليجرام الآن:\n\n"
                            "للإلغاء: /cancel"
                        )
                        return
                    elif "PhoneCodeInvalid" in err_str:
                        await event.reply("❌ الكود غير صحيح. أعد الإرسال أو /cancel")
                        return
                    elif "PhoneCodeExpired" in err_str:
                        await event.reply("❌ انتهت صلاحية الكود. ابدأ من جديد بـ /login")
                        del self._login_sessions[sender_id]
                        return
                    else:
                        raise

                # نجح تسجيل الدخول! توليد StringSession
                string_session = StringSession.save(temp_client.session)
                me = await temp_client.get_me()
                display_name = me.first_name or "User"

                # حفظ في DB
                added = await self.db.add_watcher(phone, display_name, string_session)
                if not added:
                    await event.reply("❌ فشل الحفظ في قاعدة البيانات. حاول لاحقاً.")
                    await temp_client.disconnect()
                    del self._login_sessions[sender_id]
                    return

                # بدء user_client للمستخدم الجديد فوراً
                watcher = {"phone": phone, "display_name": display_name, "session_string": string_session}
                if phone not in self.user_clients:
                    self._user_tasks[phone] = asyncio.create_task(self._run_user_client(watcher))

                # تنظيف
                await temp_client.disconnect()
                del self._login_sessions[sender_id]

                await event.reply(
                    f"🎉 تم تسجيلك بنجاح!\n\n"
                    f"👤 الاسم: {display_name}\n"
                    f"📞 الرقم: {phone}\n\n"
                    f"📚 سيبدأ البوت بمسح آخر 30 يوم من مجموعاتك.\n"
                    f"📡 ستظهر طلبات المساعدة في قناة المشتركة.\n\n"
                    f"✅ شكراً لانضمامك!"
                )

                logging.info(f"[LOGIN] New watcher registered: {phone} ({display_name})")

            except Exception as e:
                logging.error(f"Login code error: {e}")
                await event.reply(f"❌ خطأ: {e}\n\nأعد المحاولة بـ /login")

        elif step == "password":
            # استلام كلمة سر 2FA
            password = text.strip()
            try:
                temp_client = session["temp_client"]
                await temp_client.sign_in(password=password)

                # نجح! نفس خطوات النجاح السابقة
                string_session = StringSession.save(temp_client.session)
                me = await temp_client.get_me()
                display_name = me.first_name or "User"
                phone = session["phone"]

                added = await self.db.add_watcher(phone, display_name, string_session)
                if not added:
                    await event.reply("❌ فشل الحفظ. حاول لاحقاً.")
                    await temp_client.disconnect()
                    del self._login_sessions[sender_id]
                    return

                watcher = {"phone": phone, "display_name": display_name, "session_string": string_session}
                if phone not in self.user_clients:
                    self._user_tasks[phone] = asyncio.create_task(self._run_user_client(watcher))

                await temp_client.disconnect()
                del self._login_sessions[sender_id]

                await event.reply(
                    f"🎉 تم تسجيلك بنجاح!\n\n"
                    f"👤 {display_name} ({phone})\n\n"
                    f"📚 سيبدأ المسح التاريخي من مجموعاتك...\n"
                    f"✅ شكراً لانضمامك!"
                )
                logging.info(f"[LOGIN] New watcher (2FA): {phone}")

            except Exception as e:
                logging.error(f"Login password error: {e}")
                err = str(e)
                if "PasswordHashInvalid" in err:
                    await event.reply("❌ كلمة السر غير صحيحة. أعد الإرسال أو /cancel")
                else:
                    await event.reply(f"❌ خطأ: {e}\n\nأعد بـ /login")

    async def _on_command(self, event):
        try:
            text = (event.message.text or "").strip()
            parts = text.split()
            if not parts: return
            cmd = parts[0].lower()

            if self.config.owner_id:
                s = await event.get_sender()
                if getattr(s, 'id', None) != self.config.owner_id: return

            logging.info(f"[CMD] {cmd}")

            async def reply(t):
                try: await self.bot_client.send_message(self.config.channel_id, t)
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
                    try: await self.bot_client.send_message(self.config.channel_id, t)
                    except: pass
                except: pass

            if cmd == "/help": await reply(MessageFormatter.format_help())

            elif cmd == "/status":
                total = await self.db.count_requests()
                watchers = await self.db.get_active_watchers()
                await reply(MessageFormatter.format_status(total, len(watchers), self.is_scan_running(), self._scan_progress))

            elif cmd == "/watchers":
                watchers = await self.db.get_active_watchers()
                if not watchers:
                    await reply("ℹ️ لا يوجد مستخدمون مراقبون")
                else:
                    lines = ["👥 المستخدمون المراقبون:", ""]
                    for w in watchers:
                        lines.append(f"• {w['phone']} ({w['display_name'] or 'بدون اسم'})")
                    await reply("\n".join(lines))

            elif cmd == "/add_watcher":
                await reply("ℹ️ لإضافة مستخدم مراقب:\n1. شغّل سكريبت add_watcher.py على هاتفه\n2. سيرسل لك StringSession\n3. أضفه يدوياً لـ DB أو استخدم /add_watcher PHONE SESSION_STRING")

            elif cmd in SCAN_COMMANDS:
                days = SCAN_COMMANDS[cmd]
                await self._start_scan_all(days, cmd)

            elif cmd == "/scan_stop":
                if self.is_scan_running():
                    for scanner in self._current_scanners.values():
                        scanner.cancel()
                    await reply("⏹️ تم إيقاف المسح")
                else:
                    await reply("ℹ️ لا يوجد مسح")

            elif cmd == "/reset_scan":
                d = await self.db.reset_scan_state()
                await reply(f"✅ تم حذف {d} سجل مسح")

            else: await reply(f"❓ أمر غير معروف: {cmd}\nاكتب /help")

        except Exception as e:
            logging.error(f"CMD error: {e}", exc_info=True)

    def is_scan_running(self):
        return any(not t.done() for t in self._current_scan_tasks)

    def stop_scan(self):
        for s in self._current_scanners.values(): s.cancel()

    async def _start_scan_all(self, days, cmd_name):
        """بدء مسح لكل المستخدمين المراقبين"""
        if self.is_scan_running():
            await self._send("⚠️ يوجد مسح قيد التنفيذ\nأرسل /scan_stop لإيقافه")
            return
        watchers = await self.db.get_active_watchers()
        if not watchers:
            await self._send("❌ لا يوجد مستخدمون مراقبون")
            return
        d = f"{days} يوم" if days else "كامل"
        await self._send(f"🚀 بدء المسح ({cmd_name}) لـ {len(watchers)} مستخدم\n📅 الفترة: {d}\n⏳ جاري...")
        self._current_scan_tasks = []
        for w in watchers:
            task = asyncio.create_task(self._run_scan_for_watcher(w, days))
            self._current_scan_tasks.append(task)

    async def _run_scan_for_watcher(self, watcher, days):
        try:
            phone = watcher['phone']
            client = self.user_clients.get(phone)
            if not client or not client.is_connected():
                logging.warning(f"[SCAN] {phone} not connected, skipping")
                return
            def p(i, t, n): self._scan_progress = f"{phone}: {i}/{t}"
            scanner = HistoryScanner(
                client, self.bot_client, self.db, self.config.channel_id,
                days, self.config.history_max_per_chat, self.config.history_batch_size,
                self.config.history_skip_channel_posts, phone, watcher.get('display_name', ''), p)
            self._current_scanners[phone] = scanner
            await scanner.scan()
        except asyncio.CancelledError: pass
        except Exception as e: logging.error(f"Scan error {watcher['phone']}: {e}", exc_info=True)
        finally: self._current_scanners.pop(watcher['phone'], None)

    async def _run_user_client(self, watcher):
        """تشغيل user_client لمستخدم مراقب"""
        phone = watcher['phone']
        session_string = watcher['session_string']
        backoff = 5
        while self._running:
            try:
                client = self.user_clients.get(phone)
                if client is None:
                    client = self._create_user_client(session_string, phone)
                    self.user_clients[phone] = client
                    self._register_user_handlers(phone)
                if not client.is_connected():
                    logging.info(f"Connecting user {phone}...")
                    await client.connect()
                    if not await client.is_user_authorized():
                        logging.error(f"User {phone} session not authorized!")
                        return
                    logging.info(f"User {phone} connected")
                    backoff = 5
                    # مسح البدء لهذا المستخدم
                    if phone not in self._startup_scan_done:
                        self._startup_scan_done.add(phone)
                        if self.config.startup_scan_days is not None:
                            asyncio.create_task(self._run_startup_scan(watcher))
                await client.run_until_disconnected()
            except FloodWaitError as e: await asyncio.sleep(e.seconds + 1)
            except (RPCError, ConnectionError, OSError) as e: logging.error(f"User {phone} error: {e}")
            except asyncio.CancelledError: raise
            except Exception as e: logging.error(f"User {phone} unexpected: {e}", exc_info=True)
            finally:
                client = self.user_clients.get(phone)
                if client and client.is_connected():
                    try: await client.disconnect()
                    except: pass
            if not self._running: break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 600)

    async def _run_startup_scan(self, watcher):
        try:
            await asyncio.sleep(5)
            scanner = HistoryScanner(
                self.user_clients[watcher['phone']], self.bot_client, self.db,
                self.config.channel_id, self.config.startup_scan_days,
                self.config.history_max_per_chat, self.config.history_batch_size,
                self.config.history_skip_channel_posts, watcher['phone'],
                watcher.get('display_name', ''))
            self._current_scanners[watcher['phone']] = scanner
            await scanner.scan()
        except asyncio.CancelledError: pass
        except Exception as e: logging.error(f"Startup scan: {e}", exc_info=True)
        finally: self._current_scanners.pop(watcher['phone'], None)

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
            except FloodWaitError as e: await asyncio.sleep(e.seconds + 1)
            except (RPCError, ConnectionError, OSError) as e: logging.error(f"Bot error: {e}")
            except asyncio.CancelledError: raise
            except Exception as e: logging.error(f"Bot unexpected: {e}", exc_info=True)
            finally:
                if self.bot_client and self.bot_client.is_connected():
                    try: await self.bot_client.disconnect()
                    except: pass
            if not self._running: break
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
                except asyncio.CancelledError: break
                except: pass

    async def start(self):
        self._running = True
        self.bot_client = self._create_bot_client()
        self._register_handlers()
        # بدء البوت أولاً
        self._bot_task = asyncio.create_task(self._run_bot())
        await asyncio.sleep(3)
        # بدء كل المستخدمين المراقبين
        watchers = await self.db.get_active_watchers()
        logging.info(f"Starting {len(watchers)} watchers")
        for w in watchers:
            self._user_tasks[w['phone']] = asyncio.create_task(self._run_user_client(w))
        self._keep_alive_task = asyncio.create_task(self._keep_alive())

    async def stop(self):
        self._running = False
        self.stop_scan()
        if self.bot_client and self.bot_client.is_connected():
            try: await self.bot_client.disconnect()
            except: pass
        for c in self.user_clients.values():
            if c.is_connected():
                try: await c.disconnect()
                except: pass
        for t in [self._bot_task, self._keep_alive_task] + list(self._user_tasks.values()) + self._current_scan_tasks:
            if t and not t.done():
                t.cancel()
                try: await t
                except asyncio.CancelledError: pass


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
        for e in errors: print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    setup_logging(config.log_level)
    logging.info("=== Telegram Help Requests Monitor v7 ===")
    logging.info(f"Bot token: {config.bot_token[:20]}...")
    logging.info(f"Channel ID: {config.channel_id}")
    if config.startup_scan_days is not None:
        logging.info(f"Startup scan: {config.startup_scan_days} days for each watcher")

    Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

    db = DatabaseManager()
    await db.init_db()

    # ملاحظة: لا نضيف المالك تلقائياً - سيستخدم /login للتسجيل
    # هذا يحل مشكلة "فشل الحفظ" عند إضافة رقم المالك

    monitor = Monitor(config, db)
    await monitor.start()
    http_runner = await start_http_server()

    logging.info("✅ Monitor started. Send /help to channel.")

    shutdown = asyncio.Event()
    def sh(): shutdown.set()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, sh)
        except:
            try: signal.signal(sig, lambda *_: sh())
            except: pass
    await shutdown.wait()
    logging.info("Stopping...")
    await monitor.stop()
    await db.close()
    await http_runner.cleanup()
    logging.info("Stopped.")


if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: logging.info("Interrupted")
    except Exception as e: logging.critical(f"Fatal: {e}", exc_info=True)
