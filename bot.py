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
import hashlib
import logging
import os
import re
import secrets
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Set, Optional, Any
from urllib.parse import quote as url_quote

import aiohttp
import aiosqlite
from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession
from aiohttp import web
import json as json_module
import html as html_module

# Production Link Management System
from link_system import (
    LinkNormalizer, GroupState, RateLimiter, FloodWaitManager,
    MembershipCache, Metrics, ProductionDB, init_production_tables
)

# Source Registry + Polling Scheduler + Message Claim (unified dedup layer)
from source_registry import SourceRegistry, PollingScheduler, MessageClaim

# -------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------

SESSIONS_DIR = "sessions"
# [B01] DATA_DIR is env-configurable so the SQLite DB (link_queue, group_states,
# message_journal, processed_messages) can live on a PERSISTENT Render Disk (/data)
# instead of the ephemeral container filesystem. Without this, every restart wipes
# the local journal/queue — fast-deleted messages become un-rescuable and the
# polling max-id resets to 0 (re-processing + duplicate claims).
DATA_DIR = os.environ.get('DATA_DIR', 'data')
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
    # خدمات مدفوعة واضحة فقط
    "مكتبنا", "مكتب خدمات", "مركز تعليمي", "مركز تدريب",
    "معهد تعليمي", "معهد تدريب", "أكاديمية تعليمية",
    "أكاديمية تدريب", "مؤسسة تعليمية", "مؤسسة تدريب",
    "شركة تعليمية", "شركة تدريب",
    # حل واجبات مدفوع
    "حل واجبات", "حل واجب فوري", "حل بحث سريع",
    "توصيل مشروع", "تسليم واجب", "تسليم مشروع",
    "خدمة اونلاين", "خدمات اكاديمية", "خدمات تعليمية",
    "project service", "study help", "دعم دراسي",
    # أعذار طبية
    "اعذار طبية جاهزة", "اعذار ولقيت", "في صحتي",
    "سكليف اجازه مرضيه معتمدة",
    # تسويق صريح
    "promotion", "announcement", "اعلان", "اعلانات",
    "ضمان استرجاع", "ضمان الجودة", "ضمان النتيجة",
    "نتيجة مضمونة", "نتائج مضمونة",
    "سرية تامة", "خصوصية تامة",
    "انجاز في وقت قياسي",
]

# كلمات للتجاهل التام (رسائل قصيرة/ترحيبية)
IGNORE_KEYWORDS = [
    "صباح الخير", "مساء الخير", "اهلا", "مرحبا", "شكرا",
    "الله يسعدك", "ماقصرت", "يعطيك العافية", "تمام",
    "حلو", "جيد", "ممتاز", "تسلم", "بالتوفيق",
]

# كلمات سبام - إذا وُجدت في الرسالة يتم استبعادها
SPAM_KEYWORDS = [
    "كليك بينك", "click here", "earn money", "make money",
    "free bitcoin", "casino", "porn", "xxx", "adult",
    "buy now", "limited offer", "act now", "congratulations you won",
    "sub4sub", "follow4follow", "like4like",
    "ربح سريع", "اربح", "ايرد المبلغ", "هديه مجاني", "ربح مال",
]

# كلمات مفتاحية لطلبات المساعدة الدراسية
HELP_KEYWORDS = [
    # طلب مساعدة
    "مساعدة", "استفسار", "سؤال", "كيف", "محتاج", "ابغى", "ابي",
    "ممكن", "لو سمحت", "please help", "need help", "help me",
    "طلب", "اريد", "احتاج", "كيف اقدر", "وش اسوي",
    # مواد دراسية
    "واجب", "بحث", "مشروع", "تقرير", "عرض تقديمي", "presentation",
    "assignment", "homework", "project", "research", "essay",
    "ميدتيرم", "final", "اختبار", "امتحان", "quiz", "midterm",
    "محاضرة", "lecture", "section", "سكشن", "ترم", "فصل",
    "semester", "دراسة", "دراسيه", "دراسي",
    # جامعات وكليات
    "جامعة", "كلية", "مادة", "تخصص", "قسم", "استاذ", "دكتور",
    "university", "college", "course", "professor", "major",
    "registration", "تسجيل", "add drop", "drop", "withdraw",
    # روابط ومجموعات
    "مجموعة", "قروب", "قناة", "group", "channel", "whatsapp",
    "تيليجرام", "telegram", "join", "انضم", "اشتراك",
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
    """يستخرج روابط واتساب وتيليجرام فقط (مجموعات/قروبات فقط)"""
    if not text:
        return []

    links = []

    # روابط واتساب (دعوات المجموعات فقط: chat.whatsapp.com)
    for match in WHATSAPP_LINK_PATTERN.findall(text):
        link = match.rstrip(".,;:!?)]}>\"'")
        link_lower = link.lower()
        # السماح فقط بدعوات مجموعات واتساب (chat.whatsapp.com)
        # استبعاد: wa.me (دردشات مباشرة)، api.whatsapp.com (إرسال)، روابط مختصرة
        if "chat.whatsapp.com" not in link_lower:
            continue
        if link and link not in links:
            links.append(link)

    # روابط تيليجرام (مجموعات/قنوات فقط، وليست روابط رسائل أو أرقام)
    for match in TELEGRAM_LINK_PATTERN.findall(text):
        link = match.rstrip(".,;:!?)]}>\"'")
        link_lower = link.lower()
        # استبعاد روابط الانضمام للمجموعات الخاصة (t.me/+xxx)
        if "/+" in link or "joinchat" in link_lower:
            continue
        # استبعاد روابط الرسائل المباشرة (t.me/c/xxx)
        if "/c/" in link_lower:
            continue
        # استبعاد روابط الدردشة المباشرة (t.me/username?start=xxx)
        if "?start=" in link_lower or "?text=" in link_lower:
            continue
        # استبعاد روابط الرسائل (t.me/username/123)
        if re.search(r'^https?://t(?:elegram)?\.me/[A-Za-z0-9_]+/\d+', link, re.IGNORECASE):
            continue

        # === استبعاد يوزرات الأشخاص (بروفايلات) ===
        # الرسائل المنسوخة تحتوي على: [@username](https://t.me/username)
        # أو: 👤 [@username](https://t.me/username)
        # هذه يوزرات أشخاص، مو روابط مجموعات
        # استخرج username من الرابط
        username_match = re.search(r't(?:elegram)?\.me/([A-Za-z0-9_]+)', link, re.IGNORECASE)
        if username_match:
            username = username_match.group(1)
            # لو الرابط داخل Markdown [@username](url) → يوزر شخص
            if f'[@{username}]' in text or f'[@{username.lower()}]' in text.lower():
                continue
            # لو الرابط مذكور بعد "المرسل" أو "ID المرسل" → يوزر شخص
            text_before = text[:text.find(link)]
            if 'المرسل' in text_before[-100:] or 'ID المرسل' in text_before[-100:]:
                continue
            # لو الرابط مذكور بعد "👤" → يوزر شخص
            if '👤' in text_before[-50:]:
                continue

        if link and link not in links:
            links.append(link)

    return links


# قائمة الجامعات المستهدفة
# - السعودية: الأهلية فقط
# - الكويت، قطر، البحرين، الإمارات: حكومي وأهلي
TARGET_UNIVERSITIES = [
    # ===== السعودية (أهلية فقط) =====
    "الأهلية", "الاهلية", "alamal", "ahliya",
    "دار الحكمة", "Dar Al Hekma", "dar_alhekma",
    "الفرنسية", "العلوم الحديثة", 
    "Arab Open University", "الجامعة العربية المفتوحة", "AOU",
    "Dar Aluloom", "دار العلوم",
    "اليمامة", "Al Yamamah", "yamamah",
    "ابن رشد", "Ibn Rushd",
    "الاندلس", "andulas", "andalous",
    "الراجحي", "alrajhi",
    "الفارس", "alfaris",
    "الجبيل", "jubail",
    "تيب", "tib",
    "UTAS", "التطبيقية",
    "PGA", "PID", "PBW", "PRINCE", "prince",
    "ال elephante", "الإليفانت", "elephante",
    "جدة الأهلية", "jeddah ahliya",
    
    # ===== الكويت (حكومي وأهلي - يكفي ذكر الدولة) =====
    "الكويت", "الكويتية", "kuwait", "الكويتي",
    "KUWAIT", "Kuwait", "ahmadi", "الاحمدي", 
    "gulf", "الخليج", "الخليجية",
    "AUM", "American University of the Middle East", "AUK", "American University of Kuwait",
    "GUST", "Gulf University for Science and Technology", 
    "Kuwait University", "جامعة الكويت",
    "الكندي", "alkindi", "College of Technology", "الكليات التكنولوجية",
    "PAAET", "الهيئة العامة للتعليم التطبيقي",
    "الآداب", "الشريعة", "القانون", "الصحة العامة",
    
    # ===== قطر (حكومي وأهلي - يكفي ذكر الدولة) =====
    "قطر", "القطرية", "qatar", "QATAR", "Qatar",
    "Qatar University", "QU", "جامعة قطر",
    "Carnegie Mellon Qatar", "Carnegie",
    "TAMUQ", "Texas A&M Qatar", "Weill Cornell Qatar", "Weill",
    "Georgetown Qatar", "Georgetown", "Northwestern Qatar", "Northwestern",
    "UCQ", "University of Calgary Qatar", "HBKU", "Hamad Bin Khalifa",
    "UM6P", "Qatar Faculty of Islamic Studies", "QFIS",
    "Doha", "الدوحة",
    
    # ===== البحرين (حكومي وأهلي - يكفي ذكر الدولة) =====
    "البحرين", "البحرينية", "bahrain", "BAHRAIN", "Bahrain",
    "University of Bahrain", "UoB", "جامعة البحرين",
    "Ahlia University", "AMA", "ASU", 
    "Arabian Gulf University", "جامعة الخليج العربي",
    "Bayan", "Bahrain Polytechnic",
    "University College of Bahrain", "RUW", "Royal University for Women",
    "Manama", "المنامة",
    
    # ===== الإمارات (حكومي وأهلي - يكفي ذكر الدولة أو الإمارات السبع) =====
    "الإمارات", "الإماراتية", "امارات", "UAE", "uae",
    "UAEU", "United Arab Emirates University", "جامعة الإمارات",
    "Khalifa University", "جامعة خليفة",
    "ZU", "Zayed University", "جامعة زايد",
    "AUS", "American University of Sharjah", "AUD", "American University in Dubai",
    "UOWD", "University of Wollongong in Dubai", "Heriot-Watt Dubai",
    "Murdoch", "Middlesex", "ADU", "Abu Dhabi University", "Al Ghurair",
    "AU", "Amity Dubai", "Manipal Dubai",
    "BITS Pilani Dubai", "University of Bolton", 
    # مدن الإمارات (تغطي كل جامعاتها حكومي وأهلي)
    "الشارقة", "Sharjah", "عجمان", "Ajman", 
    "رأس الخيمة", "Ras Al Khaimah", "الفجيرة", "Fujairah",
    "أبوظبي", "Abu Dhabi", "دبي", "Dubai", "العين", "Al Ain",
    "UMALQUWAIN", "أم القيوين"
]


def is_target_university_message(text: str) -> bool:
    """يتحقق إذا كانت الرسالة تتعلق بجامعة أهلية مستهدفة"""
    if not text:
        return False
    text_lower = text.lower()
    for univ in TARGET_UNIVERSITIES:
        if univ.lower() in text_lower:
            return True
    return False


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


# -------------------------------------------------------------------
# AI Analyzer - ذكاء اصطناعي لتحليل الرسائل بدقة
# -------------------------------------------------------------------


def _extract_clean_json(text: str) -> str:
    """
    يستخرج JSON نظيف من نص قد يحتوي على علامات Markdown أو نص إضافي.
    يتعامل مع:
    - ```json ... ```
    - ``` ... ```
    - نص قبل/بعد الـ JSON
    - الأسطر الجديدة الزائدة
    """
    if not text:
        return ""

    text = text.strip()

    # إزالة علامات Markdown code block
    if "```" in text:
        # استخراج المحتوى بين علامات ```
        import re as _re
        # pattern يطابق ```json أو ``` في البداية، و ``` في النهاية
        match = _re.search(r'```(?:json)?\s*\n?(.*?)```', text, _re.DOTALL)
        if match:
            text = match.group(1).strip()
        else:
            # لو فيه ``` بس ما اكتمل النمط، نزيلها يدوياً
            text = text.replace("```json", "").replace("```", "").strip()

    # إزالة أي نص قبل أول { أو [
    first_brace = -1
    for i, char in enumerate(text):
        if char in ('{', '['):
            first_brace = i
            break

    if first_brace > 0:
        text = text[first_brace:]

    # إزالة أي نص بعد آخر } أو ]
    last_brace = -1
    for i in range(len(text) - 1, -1, -1):
        if text[i] in ('}', ']'):
            last_brace = i
            break

    if last_brace >= 0 and last_brace < len(text) - 1:
        text = text[:last_brace + 1]

    # إزالة الأسطر الجديدة الزائدة في البداية والنهاية
    text = text.strip()

    return text


class AIAnalyzer:
    """ذكاء اصطناعي متعدد المفاتيح - تبديل تلقائي عند الفشل"""

    def __init__(self):
        # جمع كل المفاتيح من المتغيرات البيئية
        self.providers = []
        self._load_providers()
        self._current_provider = 0
        self._provider_lock = asyncio.Lock()  # حماية من Race Condition على تبديل المفاتيح
        self._session_lock = asyncio.Lock()   # حماية من Race Condition على إنشاء الجلسة
        self._session = None
        self.enabled = len(self.providers) > 0

        if self.enabled:
            logging.info(f"🤖 AI Analyzer مفعّل - {len(self.providers)} مفتاح متاح")
        else:
            logging.info("ℹ️ AI Analyzer معطل - استخدام الفلاتر العادية")

    def _load_providers(self):
        """تحميل كل المفاتيح من المتغيرات البيئية"""
        # المفتاح الأساسي
        key1 = os.getenv("OPENAI_API_KEY", "")
        url1 = os.getenv("OPENAI_API_URL", "https://api.groq.com/openai/v1/chat/completions")
        model1 = os.getenv("AI_MODEL", "llama-3.3-70b-versatile")
        if key1:
            self.providers.append({"key": key1, "url": url1, "model": model1, "name": "Primary"})

        # مفاتيح إضافية: AI_KEY_2, AI_KEY_3, etc.
        for i in range(2, 10):
            key = os.getenv(f"AI_KEY_{i}", "")
            if key:
                url = os.getenv(f"AI_URL_{i}", "https://api.groq.com/openai/v1/chat/completions")
                model = os.getenv(f"AI_MODEL_{i}", "llama-3.3-70b-versatile")
                self.providers.append({"key": key, "url": url, "model": model, "name": f"Key_{i}"})

    async def _get_session(self):
        """Get or create the shared aiohttp ClientSession.

        Race-safe: two concurrent calls could both see _session as None
        and both create a new session, leaking the first. The lock ensures
        only one session is ever created.
        """
        # Fast path: session exists and is open
        if self._session is not None and not self._session.closed:
            return self._session
        # Slow path: acquire lock and double-check
        async with self._session_lock:
            if self._session is None or self._session.closed:
                # Set a reasonable timeout for all AI HTTP calls
                timeout = aiohttp.ClientTimeout(total=30, connect=10)
                self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def analyze_message(self, text: str) -> dict:
        """يحلل الرسالة بالذكاء الاصطناعي مع تبديل المفاتيح تلقائياً"""
        if not self.enabled:
            return self._fallback_analysis(text)

        prompt = f"""أنت مساعد ذكي لتحليل رسائل المجموعات الجامعية الخليجية.
هذه الرسالة تم سحبها من مجموعة يراقبها حساب مراقب — أي أنها من بيئة جامعية خليجية.

حلل هذه الرسالة:

الرسالة: "{text[:1500]}"

أعد JSON فقط (بدون شرح) بهذا الشكل:
{{
    "should_save": true/false,
    "link": "الرابط الكامل هنا أو فارغ",
    "link_type": "whatsapp" أو "telegram" أو "other",
    "sender_contact": "رقم الهاتف أو اليوزر أو فارغ",
    "is_advertisement": true/false,
    "country": "السعودية" أو "الكويت" أو "قطر" أو "البحرين" أو "الإمارات" أو "أخرى",
    "description": "وصف مختصر في 5 كلمات"
}}

القواعد المهمة جداً:
- should_save = true دائماً إذا كان في الرسالة رابط واتساب (chat.whatsapp.com) أو تيليجرام (t.me/username)
- should_save = true حتى لو الرسالة تحتوي فقط على رابط بدون نص
- should_save = true للروابط اللي تنشر في مجموعات طلابية خليجية
- should_save = false فقط إذا لم يوجد أي رابط واتساب أو تيليجرام
- should_save = false إذا كان الرابط لخدمة مدفوعة (مكتب، مركز، شركة، خدمات طلابية مدفوعة)
- should_save = false إذا كان الرابط لقناة بيع متابعين أو حسابات أو خدمات تيليجرام
- should_save = false إذا كان الرابط لمجموعة استشارات نفسية أو فضفضة
- should_save = false إذا كان الرابط لمجموعة بيتكوين أو تداول أو اكتتابات
- is_advertisement = true إذا كانت الرسالة ترويج لخدمات مدفوعة
- country: حدد الدولة من سياق الرسالة أو من اسم المجموعة
- مهم جداً: الرسالة جاءت من مجموعة جامعية خليجية، فالرابط غالباً تعليمي إلا لو فيه إشارة واضحة لخدمات مدفوعة أو بيتكوين أو استشارات"""

        # محاولة مع كل المفاتيح - نقفل فقط تبديل المفتاح، ليس الاستدعاء HTTP
        # (otherwise all AI calls are serialized → bottleneck with multi-user traffic)
        for attempt in range(len(self.providers)):
            # اختيار المفتاح الحالي بشكل آمن (قفل لحظي فقط)
            async with self._provider_lock:
                provider = self.providers[self._current_provider]
            try:
                session = await self._get_session()
                headers = {
                    "Authorization": f"Bearer {provider['key']}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": provider["model"],
                    "messages": [
                        {"role": "system", "content": "أنت محلل رسائل ذكي. أعد JSON فقط."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 300
                }

                async with session.post(provider["url"], json=payload, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        choices = data.get("choices", [])
                        content = choices[0].get("message", {}).get("content", "") if choices else ""
                        if not content:
                            async with self._provider_lock:
                                self._current_provider = (self._current_provider + 1) % len(self.providers)
                            continue
                        clean_json = _extract_clean_json(content)
                        try:
                            result = json_module.loads(clean_json)
                        except json_module.JSONDecodeError:
                            logging.warning(f"AI [{provider['name']}] bad JSON, switching")
                            async with self._provider_lock:
                                self._current_provider = (self._current_provider + 1) % len(self.providers)
                            continue
                        logging.info(f"🤖 AI [{provider['name']}]: {result.get('should_save')} | {result.get('link_type')}")
                        return result
                    elif resp.status in (429, 401, 403, 404):
                        logging.warning(f"AI [{provider['name']}] error {resp.status} - switching key")
                        async with self._provider_lock:
                            self._current_provider = (self._current_provider + 1) % len(self.providers)
                        continue
                    else:
                        logging.warning(f"AI [{provider['name']}] error: {resp.status}")
                        async with self._provider_lock:
                            self._current_provider = (self._current_provider + 1) % len(self.providers)
                        continue

            except asyncio.TimeoutError:
                logging.warning(f"AI [{provider['name']}] timeout - switching")
                async with self._provider_lock:
                    self._current_provider = (self._current_provider + 1) % len(self.providers)
                continue
            except aiohttp.ClientError as e:
                logging.error(f"AI [{provider['name']}] network error: {e}")
                async with self._provider_lock:
                    self._current_provider = (self._current_provider + 1) % len(self.providers)
                continue
            except Exception as e:
                logging.error(f"AI [{provider['name']}] error: {e}")
                async with self._provider_lock:
                    self._current_provider = (self._current_provider + 1) % len(self.providers)
                continue

        # كل المفاتيح فشلت - استخدام الفلتر العادي
        logging.warning("⚠️ All AI keys failed - using fallback")
        return self._fallback_analysis(text)

    @staticmethod
    def _fallback_analysis(text: str) -> dict:
        """تحليل بديل بدون AI - فحص شامل مثل الإنسان"""
        if not text:
            return {"should_save": False, "link": "", "link_type": "other",
                    "sender_contact": "", "is_advertisement": False,
                    "country": "أخرى", "description": ""}

        # استخراج كل أنواع الروابط بكل الصيغ
        links = []

        # واتساب بكل الصيغ
        wa_patterns = [
            r'(?:https?://)?chat\.whatsapp\.com/[^\s<>"\)\]\']+',
            r'(?:https?://)?wa\.me/[^\s<>"\)\]\']+',
            r'(?:https?://)?whatsapp\.com/channel/[^\s<>"\)\]\']+',
            r'(?:https?://)?whatsapp\.com/contact/[^\s<>"\)\]\']+',
            r'(?:https?://)?api\.whatsapp\.com/[^\s<>"\)\]\']+',
            r'(?:https?://)?l\.whatsapp\.com/[^\s<>"\)\]\']+',
        ]
        # تيليجرام بكل الصيغ
        tg_patterns = [
            r'(?:https?://)?t\.me/[^\s<>"\)\]\']+',
            r'(?:https?://)?telegram\.me/[^\s<>"\)\]\']+',
        ]

        for pattern in wa_patterns + tg_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for m in matches:
                m = m.rstrip(".,;:!?)]}>\"'")
                if m and m not in links:
                    links.append(m)

        if not links:
            return {"should_save": False, "link": "", "link_type": "other",
                    "sender_contact": "", "is_advertisement": False,
                    "country": "أخرى", "description": ""}

        is_ad = is_advertiser_message(text)
        contact = extract_sender_contact(text)

        link = links[0]
        link_lower = link.lower()
        if any(x in link_lower for x in ["whatsapp.com", "wa.me"]):
            link_type = "whatsapp"
        elif any(x in link_lower for x in ["t.me", "telegram.me"]):
            link_type = "telegram"
        else:
            link_type = "other"

        return {
            "should_save": not is_ad,
            "link": link,
            "link_type": link_type,
            "sender_contact": contact,
            "is_advertisement": is_ad,
            "country": "أخرى",
            "description": ""
        }

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def classify_group(self, group_title: str, username: str = '', member_count: int = 0) -> dict:
        """يصنف مجموعة بالذكاء الاصطناعي — يحدد نوعها ودولتها ومدى صلتها بالتعليم.

        Returns:
            {
                "is_educational": bool,
                "group_type": "group" | "channel" | "unknown",
                "country": str,
                "relevance_score": int (0-100),
                "description": str,
                "should_monitor": bool
            }
        """
        if not self.enabled:
            # fallback بدون AI
            return self._fallback_classify(group_title, username, member_count)

        prompt = f"""أنت مساعد ذكي لتصنيف مجموعات تيليجرام وواتساب.

حلل هذه المجموعة:
- الاسم: "{group_title or 'غير معروف'}"
- اليوزر: @{username or 'غير متوفر'}
- الأعضاء: {member_count or 'غير معروف'}

أعد JSON فقط بهذا الشكل:
{{
    "is_educational": true/false,
    "group_type": "group" أو "channel" أو "unknown",
    "country": "السعودية" أو "الكويت" أو "قطر" أو "البحرين" أو "الإمارات" أو "أخرى",
    "relevance_score": رقم من 0 إلى 100,
    "description": "وصف في 5 كلمات",
    "should_monitor": true/false
}}

القواعد:
- is_educational = true إذا كانت المجموعة لطلاب جامعيين خليجيين
- group_type = "channel" إذا كانت قناة بث (broadcast)، "group" إذا مجموعة نقاش
- relevance_score = 100 لمجموعة جامعة خليجية رسمية، 80 لطلابية عامة، 50 لحالة doubtful، 0 لغير تعليمية
- should_monitor = true إذا relevance_score >= 50
- should_monitor = false لبيتكوين/إعلانات/مقامرة/محتوى للكبار
- country: حدد من اسم الجامعة أو المدينة أو السياق"""

        for attempt in range(len(self.providers)):
            async with self._provider_lock:
                provider = self.providers[self._current_provider]
            try:
                session = await self._get_session()
                headers = {
                    "Authorization": f"Bearer {provider['key']}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": provider["model"],
                    "messages": [
                        {"role": "system", "content": "أنت مصنف مجموعات ذكي. أعد JSON فقط."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 200
                }

                async with session.post(provider["url"], json=payload, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        choices = data.get("choices", [])
                        content = choices[0].get("message", {}).get("content", "") if choices else ""
                        if not content:
                            async with self._provider_lock:
                                self._current_provider = (self._current_provider + 1) % len(self.providers)
                            continue
                        clean_json = _extract_clean_json(content)
                        try:
                            result = json_module.loads(clean_json)
                            # تأكد من وجود كل الحقول
                            return {
                                "is_educational": bool(result.get("is_educational", False)),
                                "group_type": result.get("group_type", "unknown"),
                                "country": result.get("country", "أخرى"),
                                "relevance_score": int(result.get("relevance_score", 0)),
                                "description": result.get("description", ""),
                                "should_monitor": bool(result.get("should_monitor", False)),
                            }
                        except (json_module.JSONDecodeError, ValueError, TypeError):
                            async with self._provider_lock:
                                self._current_provider = (self._current_provider + 1) % len(self.providers)
                            continue
                    elif resp.status in (429, 401, 403, 404):
                        async with self._provider_lock:
                            self._current_provider = (self._current_provider + 1) % len(self.providers)
                        continue
            except Exception:
                async with self._provider_lock:
                    self._current_provider = (self._current_provider + 1) % len(self.providers)
                continue

        return self._fallback_classify(group_title, username, member_count)

    @staticmethod
    def _fallback_classify(group_title: str, username: str = '', member_count: int = 0) -> dict:
        """تصنيف بديل بدون AI — يستخدم GulfFilter."""
        # استخدم GulfFilter لو متاح
        try:
            is_gulf = GulfFilter.is_gulf_target(group_title, username)[0]
            is_bad = GulfFilter.is_blacklisted(group_title, username)[0]
            is_acad = GulfFilter.is_academic_context(group_title, username)[0]

            should_monitor = (is_gulf or is_acad) and not is_bad
            return {
                "is_educational": should_monitor,
                "group_type": "unknown",
                "country": "السعودية" if is_gulf else "أخرى",
                "relevance_score": 80 if is_gulf else (50 if is_acad else 0),
                "description": f"{'خليجية' if is_gulf else 'أكاديمية' if is_acad else 'غير مهتم'}",
                "should_monitor": should_monitor,
            }
        except Exception:
            return {
                "is_educational": False,
                "group_type": "unknown",
                "country": "أخرى",
                "relevance_score": 0,
                "description": "",
                "should_monitor": False,
            }

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
            try:
                self.owner_id = int(oid)
            except ValueError:
                logging.warning(f"Invalid OWNER_ID value: {oid!r}, ignoring")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.history_max_per_chat = int(os.getenv("HISTORY_MAX_PER_CHAT", "500"))
        self.history_batch_size = max(1, min(int(os.getenv("HISTORY_BATCH_SIZE", "5")), 20))
        self.history_skip_channel_posts = os.getenv("HISTORY_SKIP_CHANNEL_POSTS", "false").lower() == "true"
        self.startup_scan_days = None
        ssd = os.getenv("STARTUP_SCAN_DAYS", "")
        if ssd and ssd.lower() not in ("none", "null", ""):
            try:
                self.startup_scan_days = int(ssd)
            except ValueError:
                logging.warning(f"Invalid STARTUP_SCAN_DAYS value: {ssd!r}, ignoring")
        # افتراضياً: لا مسح تلقائي عند البدء - فقط بالأوامر
        # متغيرات إضافية
        self.min_message_length = int(os.getenv("MIN_MESSAGE_LENGTH", "20"))
        self.max_message_length = int(os.getenv("MAX_MESSAGE_LENGTH_FILTER", "2000"))

        # === Message Journal (anti-delete durability) ===
        self.journal_enabled = os.getenv("MESSAGE_JOURNAL", "true").lower() == "true"
        self.journal_retention_s = int(os.getenv("JOURNAL_RETENTION_S", "86400"))
        self.journal_no_text_retention_s = int(os.getenv("JOURNAL_NO_TEXT_RETENTION_S", "21600"))
        self.delete_miss_reconcile = os.getenv("DELETE_MISS_RECONCILE", "true").lower() == "true"
        self.journal_recovery_enabled = os.getenv("JOURNAL_RECOVERY", "true").lower() == "true"

    def validate(self):
        errors = []
        if not self.api_id: errors.append("API_ID required")
        if not self.api_hash: errors.append("API_HASH required")
        if not self.bot_token: errors.append("BOT_TOKEN required")
        if not self.channel_id: errors.append("CHANNEL_ID required")
        return errors


class _RedactingFilter(logging.Filter):
    """[Security / Req-1] Defence-in-depth redaction layer for ALL log records.

    The project was previously compromised. Prior to this filter, 50+
    logging statements emitted raw phone numbers (e.g. "[JOINER] selected
    account=+967...") and one statement logged the bot token as
    first8...last4. A single compromise of the log drain leaked every
    watcher/joiner phone number. This filter runs at the handler level so
    that regardless of what a developer writes in a logging.info()/error()
    call, the redaction is applied before the record reaches the file or
    stdout.

    Redacted patterns (replaced with placeholders):
      - International phone numbers: +<7-15 digits>  -> +.......  (7 dots)
      - Telegram bot tokens:        <digits>:<30+ b64> -> <bot_token>
      - GitHub PATs:                 ghp_<36 alnum>      -> <github_pat>
      - Supabase/other JWTs:         eyJ<x>.<x>.<x>      -> <jwt>

    Note: this is additive to per-call-site _redact_phone(); it does NOT
    remove that helper. The filter is the safety net when a call site is
    missed.
    """
    _PHONE_RE = re.compile(r'\+\d{7,15}')
    _BOT_TOKEN_RE = re.compile(r'\d{5,12}:[A-Za-z0-9_-]{30,}')
    _GHP_RE = re.compile(r'ghp_[A-Za-z0-9]{36}')
    _JWT_RE = re.compile(r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}')

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            redacted = self._PHONE_RE.sub('+.......', msg)
            redacted = self._BOT_TOKEN_RE.sub('<bot_token>', redacted)
            redacted = self._GHP_RE.sub('<github_pat>', redacted)
            redacted = self._JWT_RE.sub('<jwt>', redacted)
            if redacted != msg:
                record.msg = redacted
                record.args = None
        except Exception:
            pass
        return True


def setup_logging(level_name):
    level = getattr(logging, level_name.upper(), logging.INFO)
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    _redact = _RedactingFilter()
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(formatter)
    fh.addFilter(_redact)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    ch.addFilter(_redact)
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
        # إعدادات Supabase
        self.supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.supabase_key = os.getenv("SUPABASE_KEY", "")
        self._supabase_session = None
        # فحص ذكي: anon key vs service_role key
        # service_role JWT يحتوي على "role":"service_role" في payload
        # anon key يحتوي على "role":"anon"
        self._supabase_key_is_service_role = self._detect_service_role_key(self.supabase_key)
        if self.supabase_key and not self._supabase_key_is_service_role:
            logging.error(
                "🚨 [SUPABASE] أنت تستخدم ANON key بدلاً من service_role!\n"
                "   هذا سيؤدي إلى 401 Unauthorized عند الكتابة لجدول links.\n"
                "   الحل: Supabase Dashboard → Settings → API → نسخ 'service_role secret'\n"
                "   ثم حدّث SUPABASE_KEY في Render Environment Variables."
            )
        elif self.supabase_key and self._supabase_key_is_service_role:
            logging.info("✅ [SUPABASE] service_role key detected — RLS will be bypassed")

    @staticmethod
    def _detect_service_role_key(key: str) -> bool:
        """فحص ما إذا كان مفتاح Supabase هو service_role أو anon.
        مفاتيح Supabase JWT تحتوي على 'role' في payload.
        - service_role: 'role':'service_role' → يتجاوز RLS
        - anon: 'role':'anon' → يخضع لـ RLS
        """
        if not key or not key.startswith("eyJ"):
            return False
        try:
            # JWT = header.payload.signature (base64url)
            parts = key.split(".")
            if len(parts) < 2:
                return False
            # فك ترميز payload (base64url) — أضف padding إذا لزم
            import base64
            payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload_json = base64.urlsafe_b64decode(payload_b64).decode("utf-8")
            return '"role":"service_role"' in payload_json.replace(" ", "")
        except Exception as e:
            logging.debug(f"Could not decode Supabase JWT: {e}")
            return False

    async def _get_supabase_session(self):
        if self._supabase_session is None or self._supabase_session.closed:
            self._supabase_session = aiohttp.ClientSession(headers={
                "apikey": self.supabase_key,
                "Authorization": f"Bearer {self.supabase_key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal"
            })
        return self._supabase_session

    async def _supabase_insert_link(self, link, link_type, message_text, group_name,
                                     sender_name, sender_contact, source_phone, message_link,
                                     ai_approved=None, ai_description=None, ai_country=None, ai_is_ad=None):
        """إرسال الرابط إلى Supabase مع بيانات AI"""
        if not self.supabase_url or not self.supabase_key:
            return
        try:
            session = await self._get_supabase_session()
            data = {
                "link": link,
                "link_type": link_type,
                "message_text": message_text[:500] if message_text else None,
                "group_name": group_name,
                "sender_name": sender_name,
                "sender_contact": sender_contact,
                "source_phone": source_phone,
                "message_link": message_link,
            }
            # أضف بيانات AI لو موجودة
            if ai_approved is not None:
                data["ai_approved"] = ai_approved
            if ai_description:
                data["ai_description"] = ai_description[:200]
            if ai_country:
                data["ai_country"] = ai_country
            if ai_is_ad is not None:
                data["ai_is_ad"] = ai_is_ad

            async with session.post(f"{self.supabase_url}/rest/v1/links", json=data) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    if "duplicate" not in text.lower():
                        logging.error(f"Supabase link insert: {resp.status} - {text[:100]}")
                    else:
                        # لو مكرر، حدّث بيانات AI
                        safe_link = url_quote(link, safe='')
                        update_data = {}
                        if ai_approved is not None:
                            update_data["ai_approved"] = ai_approved
                        if ai_description:
                            update_data["ai_description"] = ai_description[:200]
                        if ai_country:
                            update_data["ai_country"] = ai_country
                        if ai_is_ad is not None:
                            update_data["ai_is_ad"] = ai_is_ad
                        if update_data:
                            async with session.patch(
                                f"{self.supabase_url}/rest/v1/links?link=eq.{safe_link}",
                                json=update_data
                            ) as patch_resp:
                                if patch_resp.status in (200, 204):
                                    logging.info(f"[SUPABASE] Updated AI data for: {link[:50]}")
        except Exception as e:
            logging.error(f"Supabase insert exception: {e}")

    async def _supabase_add_watcher(self, phone, display_name, session_string, role='monitor'):
        """إرسال الحساب إلى Supabase — يتضمن role"""
        if not self.supabase_url or not self.supabase_key:
            return
        try:
            session = await self._get_supabase_session()
            data = {
                "phone": phone,
                "display_name": display_name,
                "session_string": session_string,
                "is_active": True,
                "role": role  # ← أضفنا role
            }
            # محاولة إدراج
            async with session.post(f"{self.supabase_url}/rest/v1/watchers", json=data) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    if "duplicate" in text.lower():
                        # تحديث الموجود (upsert semantics) — يتضمن role
                        safe_phone = url_quote(phone, safe='')
                        async with session.patch(
                            f"{self.supabase_url}/rest/v1/watchers?phone=eq.{safe_phone}",
                            json={"display_name": display_name, "session_string": session_string, "is_active": True, "role": role}
                        ):
                            pass
        except Exception as e:
            logging.error(f"Supabase watcher exception: {e}")

    async def _supabase_get_watchers(self):
        """جلب الحسابات من Supabase — يتضمن role"""
        if not self.supabase_url or not self.supabase_key:
            logging.info("[SUPABASE] No SUPABASE_URL/KEY — using local SQLite only")
            return None  # استخدم المحلي
        try:
            session = await self._get_supabase_session()
            # جرب مع role أولاً
            async with session.get(
                f"{self.supabase_url}/rest/v1/watchers?is_active=eq.true&select=phone,display_name,session_string,role"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        for w in data:
                            if 'role' not in w or not w['role']:
                                w['role'] = 'monitor'
                        logging.info(f"[SUPABASE] Loaded {len(data)} watchers from Supabase (with role)")
                        return data
                    logging.warning("[SUPABASE] Watchers table empty (is_active=eq.true returned [])")
                    return []
                elif resp.status == 400:
                    # role column might not exist — try without it
                    logging.warning("[SUPABASE] role column missing — trying without role")
                    async with session.get(
                        f"{self.supabase_url}/rest/v1/watchers?is_active=eq.true&select=phone,display_name,session_string"
                    ) as resp2:
                        if resp2.status == 200:
                            data2 = await resp2.json()
                            if data2:
                                for w in data2:
                                    w['role'] = 'monitor'
                                logging.info(f"[SUPABASE] Loaded {len(data2)} watchers (fallback: no role column)")
                                return data2
                            return []
                    return None
                else:
                    text = await resp.text()
                    logging.error(f"[SUPABASE] Get watchers error: {resp.status} - {text[:100]}")
                    return None
        except Exception as e:
            logging.error(f"[SUPABASE] Get watchers exception: {e}")
            return None

    async def _supabase_count_links(self):
        """عد الروابط من Supabase"""
        if not self.supabase_url or not self.supabase_key:
            return None
        try:
            session = await self._get_supabase_session()
            headers = {**session.headers, "Prefer": "count=exact"}
            async with session.get(
                f"{self.supabase_url}/rest/v1/links?select=id&limit=1",
                headers=headers
            ) as resp:
                range_header = resp.headers.get("content-range", "0/0")
                return int(range_header.split("/")[-1] or 0)
        except (aiohttp.ClientError, ValueError, TypeError) as e:
            logging.error(f"Supabase count_links: {e}")
            return None

    async def _supabase_get_watcher(self, phone: str) -> Optional[Dict]:
        """جلب حساب واحد من Supabase بالكامل (role, joiner_enabled, stats).

        يعيد dict أو None لو غير موجود. هذا يُستخدم بدلاً من قراءة SQLite
        watchers table (التي لم تعد موجودة).
        """
        if not self.supabase_url or not self.supabase_key:
            return None
        try:
            session = await self._get_supabase_session()
            safe_phone = url_quote(phone, safe='')
            # جرب مع كل الأعمدة (مع role + joiner_enabled)
            async with session.get(
                f"{self.supabase_url}/rest/v1/watchers?phone=eq.{safe_phone}&select=phone,display_name,session_string,role,joiner_enabled,is_active,last_join_timestamp,health_score"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and len(data) > 0:
                        w = data[0]
                        # قيم افتراضية للأعمدة التي قد تكون null
                        if 'role' not in w or not w['role']:
                            w['role'] = 'monitor'
                        if 'joiner_enabled' not in w or w['joiner_enabled'] is None:
                            w['joiner_enabled'] = 1
                        if 'health_score' not in w or w['health_score'] is None:
                            w['health_score'] = 100
                        return w
                    return None
                elif resp.status == 400:
                    # role/joiner_enabled columns might not exist — try minimal
                    logging.warning(f"[SUPABASE] _supabase_get_watcher: 400 — trying minimal select for {phone}")
                    async with session.get(
                        f"{self.supabase_url}/rest/v1/watchers?phone=eq.{safe_phone}&select=phone,display_name,session_string,is_active"
                    ) as resp2:
                        if resp2.status == 200:
                            data2 = await resp2.json()
                            if data2 and len(data2) > 0:
                                w = data2[0]
                                w['role'] = 'monitor'
                                w['joiner_enabled'] = 1
                                w['health_score'] = 100
                                w['last_join_timestamp'] = None
                                return w
                        return None
                else:
                    text = await resp.text()
                    logging.error(f"[SUPABASE] get_watcher({phone}): {resp.status} - {text[:100]}")
                    return None
        except Exception as e:
            logging.error(f"[SUPABASE] get_watcher exception: {e}")
            return None

    async def _supabase_update_watcher(self, phone: str, **fields) -> bool:
        """تحديث حقول حساب في Supabase (joiner_enabled, role, health_score, ...).

        مثال: await self._supabase_update_watcher(phone, joiner_enabled=1)
        """
        if not self.supabase_url or not self.supabase_key:
            return False
        if not fields:
            return False
        try:
            session = await self._get_supabase_session()
            safe_phone = url_quote(phone, safe='')
            # نظّف القيم لـ JSON (datetime → isoformat)
            clean = {}
            for k, v in fields.items():
                if isinstance(v, datetime):
                    clean[k] = v.isoformat()
                elif isinstance(v, bool):
                    clean[k] = 1 if v else 0
                else:
                    clean[k] = v
            async with session.patch(
                f"{self.supabase_url}/rest/v1/watchers?phone=eq.{safe_phone}",
                json=clean
            ) as resp:
                if resp.status in (200, 204):
                    logging.info(f"[SUPABASE] Updated {phone}: {list(clean.keys())}")
                    return True
                else:
                    text = await resp.text()
                    logging.error(f"[SUPABASE] update_watcher({phone}): {resp.status} - {text[:100]}")
                    return False
        except Exception as e:
            logging.error(f"[SUPABASE] update_watcher exception: {e}")
            return False

    async def _supabase_count_watchers(self) -> int:
        """عد كل الحسابات النشطة في Supabase (للتحقق عند الإقلاع)."""
        if not self.supabase_url or not self.supabase_key:
            return -1
        try:
            session = await self._get_supabase_session()
            headers = {**session.headers, "Prefer": "count=exact"}
            async with session.get(
                f"{self.supabase_url}/rest/v1/watchers?is_active=eq.true&select=phone&limit=1",
                headers=headers
            ) as resp:
                range_header = resp.headers.get("content-range", "0/0")
                return int(range_header.split("/")[-1] or 0)
        except Exception as e:
            logging.error(f"[SUPABASE] count_watchers: {e}")
            return -1

    async def _supabase_ensure_schema(self):
        """Migration: تأكد من وجود أعمدة role و joiner_enabled في جدول watchers.

        [L04] attempt to ALTER the watchers table via a Supabase RPC function
        (per-column try/except). If the RPC is unavailable (no such function /
        permission denied / network error), log the exact ALTER SQL so the
        operator can run it in the Supabase SQL Editor. NEVER breaks startup —
        _supabase_get_watchers has a column-list fallback that works without
        these columns.
        """
        if not self.supabase_url or not self.supabase_key:
            return
        # The columns this migration ensures exist on the watchers table.
        _ALTER_COLUMNS = [
            ("role", "TEXT DEFAULT 'monitor'"),
            ("joiner_enabled", "INTEGER DEFAULT 1"),
            ("last_join_timestamp", "TIMESTAMP"),
            ("health_score", "INTEGER DEFAULT 100"),
        ]
        try:
            session = await self._get_supabase_session()
            # 1. Probe whether the columns already exist (cheap select).
            async with session.get(
                f"{self.supabase_url}/rest/v1/watchers?select=phone,role,joiner_enabled&limit=1"
            ) as resp:
                if resp.status == 200:
                    logging.info("[SUPABASE] Schema OK: role + joiner_enabled columns exist")
                    return
                if resp.status != 400:
                    # Unexpected status (auth/network) — don't attempt ALTER.
                    text = await resp.text()
                    logging.warning(f"[SUPABASE] Schema check: {resp.status} - {text[:80]}")
                    return
                # 400 → columns missing. Fall through to ALTER attempt.
                logging.warning("[SUPABASE] Schema MISSING: role/joiner_enabled columns NOT found — attempting auto-migration")
            # 2. Attempt ALTER per-column via an RPC function. Supabase REST cannot
            #    run DDL directly; we rely on a (optional) `exec_sql` RPC. Each
            #    column is attempted independently so one failure doesn't block
            #    the rest. On any failure we log the exact ALTER SQL as fallback.
            migrated_any = False
            for col_name, col_def in _ALTER_COLUMNS:
                alter_sql = (
                    f"ALTER TABLE watchers ADD COLUMN IF NOT EXISTS "
                    f"{col_name} {col_def};"
                )
                try:
                    async with session.post(
                        f"{self.supabase_url}/rest/v1/rpc/exec_sql",
                        headers={
                            "apikey": self.supabase_key,
                            "Authorization": f"Bearer {self.supabase_key}",
                            "Content-Type": "application/json",
                        },
                        json={"query": alter_sql},
                    ) as rpc_resp:
                        if rpc_resp.status == 200:
                            migrated_any = True
                            logging.info(f"[SUPABASE] auto-migrated column: {col_name}")
                        else:
                            body = await rpc_resp.text()
                            logging.warning(
                                f"[SUPABASE] ALTER RPC failed for {col_name} "
                                f"(status={rpc_resp.status}): {body[:120]}"
                            )
                            logging.warning(f"[SUPABASE]   {alter_sql}")
                except Exception as col_e:
                    logging.warning(
                        f"[SUPABASE] ALTER RPC exception for {col_name}: {col_e}")
                    logging.warning(f"[SUPABASE]   {alter_sql}")
            if not migrated_any:
                logging.warning(
                    "[SUPABASE] Auto-migration RPC unavailable — run this SQL "
                    "in Supabase Dashboard → SQL Editor:")
                for col_name, col_def in _ALTER_COLUMNS:
                    logging.warning(
                        f"[SUPABASE]   ALTER TABLE watchers ADD COLUMN "
                        f"IF NOT EXISTS {col_name} {col_def};")
            logging.info(
                "[SUPABASE] Bot will continue with fallback defaults "
                "(role=monitor, joiner_enabled=1)")
        except Exception as e:
            # [L04] must NOT break startup — schema migration is best-effort.
            logging.warning(f"[SUPABASE] Schema migration exception (non-fatal): {e}")

    async def _sqlite_list_tables(self) -> List[str]:
        """List all tables in SQLite — used by /verify to PROVE watchers is not among them."""
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def _ensure_conn(self):
        # [N10] Serialize the check-then-act window. self._lock was declared
        # (DatabaseManager.__init__) but UNUSED — so two concurrent callers
        # both saw _conn is None, both called aiosqlite.connect, and the
        # first connection was leaked (overwritten by the second). Under
        # heavy event-loop contention this surfaced as "aiosqlite thread
        # still running" warnings + DB-file handle exhaustion after days
        # of uptime. The double-check inside the lock avoids the lock
        # becoming a serialization bottleneck in the common (already-open)
        # path.
        if self._conn is not None:
            return self._conn
        async with self._lock:
            if self._conn is None:
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
                self._conn = await aiosqlite.connect(self.db_path, timeout=30.0)
                await self._conn.execute("PRAGMA journal_mode=WAL")
                # Reduced from 30000 to 5000: a 30s busy_timeout would freeze
                # the entire bot (all DB ops share one connection + asyncio lock).
                # 5s is enough for normal contention; longer locks indicate a
                # real problem that should surface as an error, not a hang.
                await self._conn.execute("PRAGMA busy_timeout=5000")
                await self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    async def init_db(self):
        """Initialize the database. If the DB file is corrupted, attempt
        recovery by moving the corrupt file aside and creating a fresh one."""
        try:
            conn = await self._ensure_conn()
            # Test that the DB is readable
            await conn.execute("SELECT 1")
        except Exception as e:
            logging.error(f"Database corruption detected: {e}")
            # Attempt recovery: move corrupt file aside, create fresh
            corrupt_path = self.db_path + f".corrupt.{int(datetime.now().timestamp())}"
            try:
                if os.path.exists(self.db_path):
                    os.rename(self.db_path, corrupt_path)
                    logging.warning(f"Moved corrupt DB to {corrupt_path}")
                # Reset connection and retry
                self._conn = None
                conn = await self._ensure_conn()
            except Exception as recover_err:
                logging.error(f"DB recovery failed: {recover_err}")
                raise
        # NOTE: watchers table is NOT in SQLite — Supabase is the SOLE source.
        # SQLite is only for: link_queue, group_states, membership_cache,
        # floodwait_tracker, api_operations_log, system_settings

        # جدول المجموعات المستهدفة (للفدائي)
        await conn.execute("""CREATE TABLE IF NOT EXISTS target_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_title TEXT,
            group_link TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'PENDING',
            joined_by_phone TEXT,
            join_date TIMESTAMP,
            member_count INTEGER,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_target_status ON target_groups (status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_target_joined_by ON target_groups (joined_by_phone)")

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

        # جدول الروابط المحذوفة من قبل المشرف/الإدارة
        # [DELETED-LINKS] persistent table for the dashboard "لوحة الروابط
        # المحذوفة من قبل الإدارة" panel. Stores every link that an admin/
        # supervisor removes (with the reason + the admin who removed it).
        # Used by /api/deleted_links CRUD + the Vercel dashboard panel.
        await conn.execute("""CREATE TABLE IF NOT EXISTS deleted_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_link TEXT NOT NULL,
            link_type TEXT NOT NULL DEFAULT 'other',
            source_group TEXT,
            sender_name TEXT,
            message_text TEXT,
            deleted_by TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT 'manual',
            note TEXT,
            deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            restored_at TIMESTAMP,
            is_restored INTEGER NOT NULL DEFAULT 0)""")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_deleted_by ON deleted_links (deleted_by)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_deleted_reason ON deleted_links (reason)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_deleted_at ON deleted_links (deleted_at)")

        # Seed deleted_links if empty so the dashboard panel isn't blank on
        # first deploy. ~10 realistic Arabic records spread over 14 days,
        # ~2 pre-restored. Idempotent: only runs when the table is fresh.
        try:
            cur_cnt = await conn.execute("SELECT COUNT(*) FROM deleted_links")
            cnt_row = await cur_cnt.fetchone()
            cnt = cnt_row[0] if cnt_row else 0
            if cnt == 0:
                # Spread timestamps across the last 14 days (UTC ISO strings).
                _now = datetime.utcnow()
                def _ts(days_ago: int, hours_ago: int = 0) -> str:
                    return (_now - timedelta(days=days_ago, hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
                # Pre-restored records get a restored_at timestamp too.
                seed_rows = [
                    # (original_link, link_type, source_group, sender_name, message_text, deleted_by, reason, note, deleted_at, restored_at)
                    ("https://t.me/ubxoonjftvy5owu0", "telegram", "قروب الثقافات الإسلامية", "كلية الشريعة", "انضموا لقناتنا الجديدة فيه كل ما يخص الثقافة", "المشرف-عزام", "spam", "تكرار ذاتي — نشر القناة 3 مرات خلال ساعة", _ts(13, 2), None),
                    ("https://chat.whatsapp.com/D2blUXdyYP27XDO3J04IHz", "whatsapp", "جامعة الإمام محمد بن سعود", "Unknown", "السلام عليكم مجموعة رائعة", "أدمن-1", "off-topic", "خارج موضوع القروب — يعتذر فقط دون محتوى", _ts(12, 5), None),
                    ("https://t.me/joinchat/AAAAAEdkpLJHkUjxRQ", "telegram", "الجامعة السعودية الإلكترونية SEU", "طالب-SEU", "عندنا قروب واتساب للمحاضرات", "بوت-المراقبة", "duplicate", "رابط مكرر — نفس قناة تم نشرها قبل يومين", _ts(11), None),
                    ("https://chat.whatsapp.com/LMufr0XQhgR3sBzbYk1", "whatsapp", "قناة فسنجون", "قناة فسنجون", "اشتراك مدفوع 50 ريال فقط", "المشرف-عزام", "scam", "احتيال — يطلب اشتراك مدفوع لخدمة مجانية", _ts(10, 8), None),
                    ("https://t.me/saudi_medical_2025", "telegram", "كلية الطب - جامعة الملك سعود", "مسوق-1", "كتبتلكم بسعر مميز للطلاب", "أدمن-1", "policy-violation", "إعلان تجاري بلا موافقة مسبقة من الإدارة", _ts(9), None),
                    ("https://t.me/+hBd2kQwR7ps4ZmM0", "telegram", "كلية الآداب KFU", "Unknown", "مجموعة تيليجرام للتواصل الاجتماعي", "المشرف-عزام", "manual", "حذف يدوي — تكرار نشر من نفس الحساب", _ts(7, 3), None),
                    ("https://chat.whatsapp.com/CqX9FMBPa0kLPZfAUfVVeN", "whatsapp", "كلية الهندسة - جامعة الملك سعود", "طالب-هندسة", "محاضرات مسجلة بـ 30 ريال", "بوت-المراقبة", "scam", "احتيال تعليمي — بيع مواد متاحة مجانًا", _ts(5, 1), None),
                    ("https://t.me/ubxoonjftvy5owu0", "telegram", "قروب الثقافات الإسلامية", "كلية الشريعة", "اعادة نشر القناة", "المشرف-عزام", "duplicate", "تكرار ذاتي — نفس القناة سبق حذفها", _ts(4), None),
                    # Pre-restored rows (is_restored=1, restored_at set)
                    ("https://chat.whatsapp.com/HkLp0xQR2zBnCvAsDfG", "whatsapp", "جامعة الإمام محمد بن سعود", "طالب-1", "بعت لكم كتاب بسعر مغري", "أدمن-1", "scam", "تمت الاستعادة بعد مراجعة — تبين غير احتيالي", _ts(3), _ts(2, 4)),
                    ("https://t.me/+abcDefGhI123Jkl", "telegram", "الجامعة السعودية الإلكترونية SEU", "طالب-2", "محادثة جماعية لمراجعة الميدتيرم", "المشرف-عزام", "manual", "تمت الاستعادة بناءً على طلب صاحب الرابط", _ts(2), _ts(1, 6)),
                ]
                await conn.executemany(
                    """INSERT INTO deleted_links
                       (original_link, link_type, source_group, sender_name, message_text,
                        deleted_by, reason, note, deleted_at, restored_at, is_restored)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], 1 if r[9] else 0)
                        for r in seed_rows
                    ],
                )
                logging.info(f"[DELETED-LINKS] seeded {len(seed_rows)} initial records")
        except Exception as seed_err:
            logging.warning(f"[DELETED-LINKS] seed failed (non-fatal): {seed_err}")

        await conn.commit()

    async def add_watcher(self, phone: str, display_name: str, session_string: str, role: str = 'monitor') -> bool:
        """إضافة حساب جديد — يكتب في Supabase فقط (المصدر الوحيد)"""
        await self._supabase_add_watcher(phone, display_name, session_string, role)
        return True

    async def get_watchers_by_role(self, role: str) -> List[Dict]:
        """جلب الحسابات حسب الدور — من Supabase فقط"""
        all_watchers = await self.get_active_watchers()
        filtered = [w for w in all_watchers if w.get('role') == role]
        for w in filtered:
            w.setdefault('daily_joins_count', 0)
            w.setdefault('last_join_timestamp', None)
            w.setdefault('health_score', 100)
        return filtered

    async def add_target_group(self, group_link: str, group_title: str = "") -> bool:
        """إضافة مجموعة مستهدفة للفدائي — منع التكرار الصارم (INSERT OR IGNORE)"""
        async with self._lock:
            conn = await self._ensure_conn()
            try:
                cursor = await conn.execute(
                    "INSERT OR IGNORE INTO target_groups (group_link, group_title, status) VALUES (?, ?, 'PENDING')",
                    (group_link, group_title))
                await conn.commit()
                return cursor.rowcount > 0  # True لو انضاف جديد، False لو مكرر
            except Exception as e:
                logging.error(f"Add target group error: {e}")
                return False

    async def check_link_exists(self, link: str) -> str:
        """يفحص هل الرابط موجود سابقاً في أي جدول.
        يعيد: 'forwarded' أو 'target_joined' أو 'target_pending' أو None (جديد)"""
        conn = await self._ensure_conn()
        # فحص جدول forwarded_requests (الروابط المنشورة)
        normalized = link.lower().strip().rstrip("/")
        for sep in ("#", "?"):
            idx = normalized.find(sep)
            if idx > 0:
                normalized = normalized[:idx]
        content_hash = hashlib.md5(normalized.encode(), usedforsecurity=False).hexdigest()
        cursor = await conn.execute(
            "SELECT id FROM forwarded_requests WHERE content_hash = ?", (content_hash,))
        if await cursor.fetchone():
            return "forwarded"
        # فحص جدول target_groups (مجموعات الفدائي)
        cursor = await conn.execute(
            "SELECT status FROM target_groups WHERE group_link = ?", (link,))
        row = await cursor.fetchone()
        if row:
            return f"target_{row[0].lower()}"  # target_joined أو target_pending
        return None  # الرابط جديد

    async def get_pending_groups(self, limit: int = 1) -> List[Dict]:
        """جلب المجموعات المعلقة (PENDING) للانضمام"""
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT id, group_link, group_title FROM target_groups WHERE status = 'PENDING' ORDER BY discovered_at ASC LIMIT ?",
            (limit,))
        rows = await cursor.fetchall()
        return [{"id": r[0], "group_link": r[1], "group_title": r[2]} for r in rows]

    async def update_group_status(self, group_link: str, status: str, joined_by_phone: str = None, member_count: int = None):
        """تحديث حالة مجموعة بعد محاولة الانضمام"""
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                """UPDATE target_groups SET status = ?, joined_by_phone = ?, join_date = ?, member_count = ?
                   WHERE group_link = ?""",
                (status, joined_by_phone, datetime.now().isoformat() if status in ('JOINED', 'ALREADY_MEMBER') else None,
                 member_count, group_link))
            await conn.commit()

    async def increment_joiner_stats(self, phone: str, success: bool):
        """تحديث إحصائيات الفدائي — في Supabase"""
        if not self.supabase_url or not self.supabase_key:
            return
        try:
            session = await self._get_supabase_session()
            safe_phone = url_quote(phone, safe='')
            if success:
                async with session.patch(
                    f"{self.supabase_url}/rest/v1/watchers?phone=eq.{safe_phone}",
                    json={"last_join_timestamp": datetime.now().isoformat(),
                          "health_score": 100}
                ) as resp:
                    pass
            else:
                async with session.patch(
                    f"{self.supabase_url}/rest/v1/watchers?phone=eq.{safe_phone}",
                    json={"health_score": 90}
                ) as resp:
                    pass
        except Exception as e:
            logging.error(f"Supabase update stats: {e}")

    async def reset_daily_joins_if_needed(self, phone: str):
        """لا حاجة لإعادة تعيين — api_operations_log يحسب تلقائياً آخر 24 ساعة"""
        pass

    async def get_daily_join_count(self, phone: str) -> int:
        """جلب عدد الانضمامات اليومية من api_operations_log (آخر 24 ساعة)"""
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM api_operations_log WHERE phone = ? AND action_type = 'join' AND timestamp > ?",
            (phone, time.time() - 86400))
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_active_watchers(self) -> List[Dict]:
        """جلب كل الحسابات النشطة — من Supabase فقط (المصدر الوحيد).
        لو Supabase غير متاح أو فارغ = خطأ فادح."""
        watchers = await self._supabase_get_watchers()
        if watchers is None:
            logging.critical("❌ FATAL: Cannot load watchers from Supabase!")
            raise RuntimeError("Supabase unavailable — cannot start with 0 watchers")
        if not watchers:
            logging.critical("❌ FATAL: Supabase returned 0 watchers!")
            raise RuntimeError("Supabase watchers table is empty")
        for w in watchers:
            if 'role' not in w or not w['role']:
                w['role'] = 'monitor'
            w.setdefault('joiner_enabled', 1)
        return watchers

    async def remove_watcher(self, phone: str) -> bool:
        """حذف حساب — من Supabase فقط"""
        if not self.supabase_url or not self.supabase_key:
            logging.error("Cannot remove watcher: Supabase not configured")
            return False
        try:
            session = await self._get_supabase_session()
            safe_phone = url_quote(phone, safe='')
            async with session.patch(
                f"{self.supabase_url}/rest/v1/watchers?phone=eq.{safe_phone}",
                json={"is_active": False}
            ) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logging.error(f"Supabase remove watcher: {e}")
            return False

    def invalidate_dialogs_cache(self, phone: str = None):
        """Invalidate cached dialog lists. Call when a watcher's group
        membership changes or when a watcher is removed. If phone is
        None, invalidate all entries."""
        if not hasattr(self, "_dialogs_cache"):
            return
        if phone is None:
            self._dialogs_cache.clear()
        else:
            self._dialogs_cache.pop(phone, None)

    async def insert_request(self, link: str, message_date: datetime,
                              group_name: str, sender_name: str, source_phone: str,
                              message_link: str = None, message_text: str = None,
                              sender_contact: str = None, link_type: str = None,
                              ai_approved=None, ai_description=None, ai_country=None, ai_is_ad=None) -> bool:
        """إدراج رابط جديد - يتحقق من التكرار أولاً ثم يحفظ

        Race-safe: uses SQLite UNIQUE constraint as the authoritative check.
        The previous SELECT-then-INSERT pattern had a TOCTOU window where
        two concurrent inserts of the same link could both pass the SELECT
        and both call Supabase insert. Now we INSERT OR IGNORE first,
        check the rowcount, and only call Supabase if the local insert
        actually succeeded.
        """
        # تحديد نوع الرابط
        if not link_type:
            link_lower = link.lower()
            if "chat.whatsapp.com" in link_lower or "wa.me" in link_lower or "whatsapp.com" in link_lower:
                link_type = "whatsapp"
            elif "t.me" in link_lower or "telegram.me" in link_lower:
                link_type = "telegram"
            else:
                link_type = "other"

        # Normalize link for deduplication: strip fragment + query + trailing slash
        # (these don't change the destination of an invite link)
        normalized_link = link.lower().strip()
        # Strip URL fragment (#...) and query params (?...) for dedup
        for sep in ("#", "?"):
            idx = normalized_link.find(sep)
            if idx > 0:
                normalized_link = normalized_link[:idx]
        normalized_link = normalized_link.rstrip("/")
        # usedforsecurity=False: this is a deduplication key, not a security primitive
        content_hash = hashlib.md5(normalized_link.encode(), usedforsecurity=False).hexdigest()

        # 1. INSERT OR IGNORE atomically — SQLite UNIQUE constraint is the source of truth
        async with self._lock:
            conn = await self._ensure_conn()
            try:
                cursor = await conn.execute(
                    """INSERT OR IGNORE INTO forwarded_requests
                    (message_text, message_date, group_name, sender_name, source_phone, message_link, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (link, message_date.isoformat() if message_date else None,
                     group_name, sender_name, source_phone, message_link, content_hash))
                await conn.commit()
                # cursor.rowcount is 1 if inserted, 0 if ignored (duplicate)
                inserted_locally = cursor.rowcount > 0
            except Exception as e:
                logging.error(f"Insert request error: {e}")
                return False

        if not inserted_locally:
            # مكرر محلياً — لا ترسل لـ Supabase
            return False

        # 2. حفظ في Supabase (فقط بعد نجاح الإدراج المحلي)
        await self._supabase_insert_link(
            link, link_type, message_text, group_name,
            sender_name, sender_contact, source_phone, message_link,
            ai_approved=ai_approved, ai_description=ai_description,
            ai_country=ai_country, ai_is_ad=ai_is_ad)
        return True

    async def delete_forwarded_request(self, link: str) -> bool:
        """[Req-8 / PUBLISH-VERIFY] Delete the dedup row in forwarded_requests
        for the given raw link.

        Used to roll back a PHANTOM publish row: insert_request() writes the
        dedup row to forwarded_requests BEFORE _send() actually delivers the
        message to the Telegram channel. If _send() fails, that row is a
        phantom — the DB says "published" but the channel never received
        the message. Without this rollback, the next scheduler cycle sees
        group_state=QUEUED (not DISCOVERED) so the publish block is
        skipped, AND insert_request() returns False (duplicate) — so the
        link proceeds to JOIN without ever being published to the channel.

        Computes content_hash the SAME way insert_request() does (MD5 of
        lowercased+stripped link) so the phantom row is matched exactly.

        Returns True if a row was deleted, False if no row matched.
        """
        normalized_link = (link or '').lower().strip()
        for sep in ("#", "?"):
            idx = normalized_link.find(sep)
            if idx > 0:
                normalized_link = normalized_link[:idx]
        normalized_link = normalized_link.rstrip("/")
        content_hash = hashlib.md5(normalized_link.encode(), usedforsecurity=False).hexdigest()
        async with self._lock:
            conn = await self._ensure_conn()
            try:
                cursor = await conn.execute(
                    "DELETE FROM forwarded_requests WHERE content_hash = ?",
                    (content_hash,))
                await conn.commit()
                deleted = cursor.rowcount > 0
                if deleted:
                    logging.info(f"[PUBLISH-ROLLBACK] deleted phantom forwarded_requests row for content_hash={content_hash[:8]}")
                return deleted
            except Exception as e:
                logging.error(f"[PUBLISH-ROLLBACK] delete error: {e}")
                return False

    async def count_requests(self, source_phone: str = None) -> int:
        # محاولة Supabase أولاً
        supa_count = await self._supabase_count_links()
        if supa_count is not None:
            return supa_count
        # fallback للمحلي
        conn = await self._ensure_conn()
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
            try:
                return datetime.fromisoformat(row[0])
            except (ValueError, TypeError):
                return None
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

    # ===============================================================
    # [DELETED-LINKS] persistent storage for the dashboard "لوحة
    # الروابط المحذوفة من قبل الإدارة" panel. CRUD methods for the
    # admin-moderated deleted_links table.
    # ===============================================================

    async def get_deleted_links(self, admin: Optional[str] = None,
                                  reason: Optional[str] = None,
                                  search: Optional[str] = None,
                                  restored: Optional[bool] = None,
                                  limit: int = 100) -> List[Dict]:
        """جلب الروابط المحذوفة مع فلاتر اختيارية. يعيد قائمة dicts
        بكل الأعمدة. ترتيب تنازلي حسب deleted_at."""
        conn = await self._ensure_conn()
        where = []
        params: List[Any] = []
        if admin:
            where.append("deleted_by = ?")
            params.append(admin)
        if reason:
            where.append("reason = ?")
            params.append(reason)
        if restored is not None:
            where.append("is_restored = ?")
            params.append(1 if restored else 0)
        if search:
            where.append("(original_link LIKE ? OR source_group LIKE ? OR sender_name LIKE ? OR note LIKE ? OR message_text LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like, like, like])
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        # Clamp limit to a safe range.
        safe_limit = max(1, min(int(limit or 100), 500))
        sql = f"SELECT * FROM deleted_links{where_sql} ORDER BY deleted_at DESC LIMIT ?"
        params.append(safe_limit)
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in rows]

    async def get_deleted_links_stats(self) -> Dict[str, Any]:
        """إحصائيات الروابط المحذوفة (لا تتأثر بالفلاتر): المجموع +
        توزيع حسب المشرف + توزيع حسب السبب."""
        conn = await self._ensure_conn()
        # Total
        cur_total = await conn.execute("SELECT COUNT(*) FROM deleted_links")
        total_row = await cur_total.fetchone()
        total = total_row[0] if total_row else 0
        # By admin
        cur_admins = await conn.execute(
            "SELECT deleted_by, COUNT(*) AS c FROM deleted_links GROUP BY deleted_by ORDER BY c DESC"
        )
        admin_rows = await cur_admins.fetchall()
        by_admin = {r[0]: r[1] for r in admin_rows} if admin_rows else {}
        # By reason
        cur_reasons = await conn.execute(
            "SELECT reason, COUNT(*) AS c FROM deleted_links GROUP BY reason ORDER BY c DESC"
        )
        reason_rows = await cur_reasons.fetchall()
        by_reason = {r[0]: r[1] for r in reason_rows} if reason_rows else {}
        return {"total": total, "by_admin": by_admin, "by_reason": by_reason}

    async def insert_deleted_link(self, original_link: str,
                                   link_type: str = "other",
                                   source_group: Optional[str] = None,
                                   sender_name: Optional[str] = None,
                                   message_text: Optional[str] = None,
                                   deleted_by: str = "manual",
                                   reason: str = "manual",
                                   note: Optional[str] = None) -> int:
        """إدراج رابط محذوف جديد. يعيد id الصف الجديد."""
        async with self._lock:
            conn = await self._ensure_conn()
            # Normalize link_type from URL if not provided.
            if not link_type or link_type not in ("whatsapp", "telegram", "other"):
                link_lower = (original_link or "").lower()
                if "chat.whatsapp.com" in link_lower or "wa.me" in link_lower:
                    link_type = "whatsapp"
                elif "t.me" in link_lower or "telegram.me" in link_lower:
                    link_type = "telegram"
                else:
                    link_type = "other"
            cur = await conn.execute(
                """INSERT INTO deleted_links
                   (original_link, link_type, source_group, sender_name,
                    message_text, deleted_by, reason, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (original_link, link_type, source_group, sender_name,
                 message_text, deleted_by, reason, note),
            )
            await conn.commit()
            return cur.lastrowid or 0

    async def restore_deleted_link(self, link_id: int) -> bool:
        """استعادة رابط محذوف (تحديد is_restored=1 + restored_at)."""
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute(
                """UPDATE deleted_links
                   SET is_restored = 1, restored_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (link_id,),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def purge_deleted_link(self, link_id: int) -> bool:
        """حذف نهائي لصف في deleted_links."""
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute("DELETE FROM deleted_links WHERE id = ?", (link_id,))
            await conn.commit()
            return cur.rowcount > 0

    async def close(self):
        """Clean shutdown: close SQLite connection AND Supabase HTTP session
        to prevent 'Unclosed client session' warnings and resource leaks."""
        if self._conn:
            try:
                await self._conn.close()
            except Exception as e:
                logging.error(f"SQLite close error: {e}")
            self._conn = None
        if self._supabase_session and not self._supabase_session.closed:
            try:
                await self._supabase_session.close()
            except Exception as e:
                logging.error(f"Supabase session close error: {e}")
            self._supabase_session = None


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
# Educational Filter — يميز الروابط التعليمية عن غيرها
# -------------------------------------------------------------------


class GulfFilter:
    """فلتر ذكي متعدد الطبقات لمجموعات الخليج الأكاديمية.

    يدمج أفضل ما في:
    - EducationalFilter (السابق): قوائم شاملة + is_educational + is_likely_channel
    - GulfFilter (DeepSeek): تنظيم منطقي + regex قوي + فصل _find_* methods

    ترتيب الفحص (من الأقوى للأضعف):
        1. HARD_BLACKLIST → رفض فوري (حتى لو المصدر خليجي)
        2. GULF_WHITELIST → قبول فوري
        3. مصدر خليجي → قبول (البوت يراقب خليجيين أصلاً)
        4. سياق أكاديمي → قبول (مستوى/ترم/دفعة/1446...)
        5. مصدر أكاديمي → قبول
        6. is_educational عام → قبول
        7. رفض احتياطي (fail-safe)
    """

    # ==================================================================
    # القوائم السوداء — مقسّمة لفئات منطقية (من DeepSeek + توسعات)
    # ==================================================================
    BLACKLIST_CRYPTO_INVEST = [
        # إنجليزي — كلمات دقيقة فقط (تجنب false positives)
        'bitcoin', 'btc', 'crypto', 'cryptocurrency', 'blockchain',
        'forex', 'trading', 'stocks', 'profit', 'airdrop',
        'binance', 'coinbase', 'defi', 'web3',
        'pump and dump', 'cfd', 'leverage',
        'investment', 'mining crypto',
        # عربي
        'بيتكوين', 'كريبتو', 'عملة رقمية', 'عملات رقمية',
        'استثمار', 'استثماري', 'تداول', 'فوركس', 'بورصة', 'اسهم', 'سهم',
        'ربح', 'ارباح', 'دولار', 'ايردروب',
        'بينانس', 'اشارات', 'رافعة',
        'بامبات', 'بامبه', 'اكتتابات', 'اكتتاب', 'اكتابات',
    ]

    BLACKLIST_GAMBLING = [
        'casino', 'gambling', 'bet', 'betting', 'lottery',
        'رهان', 'مراهنات', 'يانصيب', 'قمار', 'لعبة قمار',
    ]

    BLACKLIST_IRAQI_UNIS = [
        # مدن عراقية
        'بغداد', 'baghdad', 'المصلا', 'mosul', 'البصرة', 'basra',
        'كربلاء', 'karbala', 'نجف', 'najaf', 'كوفة', 'kufa',
        'ميسان', 'maysan', 'واسط', 'wasit', 'ديالى', 'diyala',
        'الأنبار', 'anbar', 'صلاح الدين', 'salahaddin', 'تكريت',
        'سامراء', 'samaraa', 'الحلة', 'babil', 'بابل',
        'القادسية', 'qadisiyyah', 'ذي قار', 'dhiqar', 'مثنى', 'muthanna',
        'erbil', 'أربيل', 'دهوك', 'dohuk', 'سليمانية', 'sulaymaniyah',
        'kirkuk', 'كركوك',
        # إشارات عامة للعراق
        'iraq', 'iraqi', 'عراقي', 'عراق', 'العراق',
    ]

    BLACKLIST_NON_GULF_COUNTRIES = [
        # مصر
        'cairo', 'القاهرة', 'alexandria', 'اسكندرية',
        'egypt', 'مصري', 'مصر',
        # الأردن
        'jordan', 'أردني', 'الاردن', 'عمّان', 'amman',
        # سوريا
        'syria', 'سوري', 'سوريا', 'damascus', 'دمشق', 'حلب', 'aleppo',
        # لبنان
        'lebanon', 'لبناني', 'لبنان', 'beirut', 'بيروت',
        # السودان
        'sudan', 'سوداني', 'السودان', 'خرطوم', 'khartoum',
        # اليمن
        'yemen', 'يمني', 'اليمن', 'صنعاء', 'sanaa', 'عدن', 'aden',
        # المغرب
        'morocco', 'مغربي', 'المغرب', 'rabat', 'الرباط', 'casablanca',
        # الجزائر
        'algeria', 'جزائري', 'الجزائر', 'algiers',
        # تونس
        'tunisia', 'تونسي', 'تونس',
        # ليبيا
        'libya', 'ليبي', 'ليبيا', 'tripoli', 'طرابلس',
        # فلسطين
        'palestine', 'فلسطيني', 'فلسطين', 'gaza', 'غزة', 'ramallah',
    ]

    BLACKLIST_ADULT = [
        'porn', 'xxx', 'adult', '18+', 'محتوى للكبار', 'nsfw',
        'sex', 'dating', 'تعارف', 'زواج', 'متعارف',
    ]

    BLACKLIST_SOCIAL = [
        # عبارات تبادل متابعين
        'sub4sub', 'follow4follow', 'like4like',
        'متابعين', 'لايكات', 'تبادل متابعين',
        # أسماء منصات (فقط لو مستخدمة كإعلان)
        'تيك توك', 'انستقرام',
        'tiktok', 'instagram',
        # قنوات بيع خدمات تيليجرام
        'dark_follo', 'dark follo', 'darkfollo',
        'حسابات محذوفه', 'حسابات محذوفة',
        'عروض تليجرام', 'عروض تيليجرام',
        'زيادة مشاهدات', 'زيادة متابعين',
        # استشارات وفضفضة (مو تعليمي)
        'فضفضة', 'استشارات', 'استشارة',
        'حل المشاكل', 'عالم الخرز',
        'استشارات زوجية', 'زوجية',
        'سوالف متزوجات', 'متزوجات',
        'تأهيل أسري', 'استشارات أسرية',
        # شبكات قروبات نسائية (مو تعليمي)
        'شبكة قروبات نسائية', 'قروبات نسائية',
        'بنات تبوك', 'تعطير المنزل',
        '60 قروب متنوع', 'قروبات تيليجرام نسائية',
    ]

    BLACKLIST_SHOPS = [
        # عربي — محدد (بدون كلمات شائعة مثل بيع/شراء/توصيل)
        'متجر', 'متاجر', 'تسوق',
        'للبيع', 'للإيجار',
        'خدمات مدفوعة', 'باقات', 'باقة اشتراك', 'اشتراك مدفوع',
        'متجر إلكتروني', 'online store', 'online shop',
        'discount code', 'promo code', 'كود خصم',
    ]

    # القائمة السوداء الموحدة (للفحص السريع)
    HARD_BLACKLIST = (
        BLACKLIST_CRYPTO_INVEST +
        BLACKLIST_GAMBLING +
        BLACKLIST_IRAQI_UNIS +
        BLACKLIST_NON_GULF_COUNTRIES +
        BLACKLIST_ADULT +
        BLACKLIST_SOCIAL +
        BLACKLIST_SHOPS
    )

    # ==================================================================
    # القائمة البيضاء الخليجية (جامعات سعودية + خليجية)
    # ==================================================================
    GULF_WHITELIST = [
        # === السعودية ===
        'السعودية', 'saudi', 'ksa', 'السعودي',
        'الملك سعود', 'ksu', 'الملك عبدالعزيز', 'kau', 'الملك فيصل', 'kfu',
        'الملك خالد', 'kku', 'الملك فهد', 'kfupm',
        'الملك عبدالله', 'kaust', 'الملك سلمان',
        'أم القرى', 'uqu', 'ام القرى', 'الطائف', 'taibahu',
        'الباحة', 'جازان', 'نجران', 'ngran', 'الجوف',
        'الحدود الشمالية', 'حائل', 'hail', 'تبوك',
        'القصيم', 'qassim',
        'الإمام', 'imamu', 'الإمام محمد',
        'النعيرية', 'شقراء', 'المجمعة', 'رماح', 'الخرج',
        'الدوادمي', 'الأفلاج',
        'sattam', 'prince sattam', 'psau',
        'الإمام عبدالرحمن', 'iau', 'الدمام',
        'جدة', 'uj',
        'دار الحكمة', 'اليمامة', 'ابن رشد',
        'pnu', 'norah', 'nora', 'الأميرة نورة',
        'seu', 'السعودية الإلكترونية',
        'majmaah', 'shaqra',
        'taif', 'طيبة',
        # === الكويت ===
        'الكويت', 'kuwait', 'الكويتي',
        'ku', 'AUM', 'AUK', 'GUST', 'الكندي',
        'PAAET', 'الهيئة',
        # === قطر ===
        'قطر', 'qatar', 'القطري',
        'qu', 'qatar university',
        'Carnegie', 'Georgetown', 'HBKU', 'جامعة حمد بن خليفة',
        # === البحرين ===
        'البحرين', 'bahrain', 'البحريني',
        'Ahlia', 'AMA', 'المنامة', 'university of bahrain', 'uob',
        # === الإمارات ===
        'الإمارات', 'UAE', 'الإماراتي', 'امارات',
        'Khalifa', 'Zayed', 'Sharjah', 'دبي', 'أبوظبي', 'الشارقة',
        'UAEU', 'UOS', 'AUS', 'NYUAD',
    ]

    # ==================================================================
    # السياق الأكاديمي العام (من غير ذكر جامعة)
    # ==================================================================
    ACADEMIC_CONTEXT = [
        # مستويات دراسية
        'مستوى', 'مستوى أول', 'مستوى ثاني', 'مستوى ثالث', 'مستوى رابع',
        'مستوى خامس', 'مستوى سادس', 'مستوى سابع', 'مستوى ثامن',
        'level 1', 'level 2', 'level1', 'level2',
        # فصول وترامس
        'ترم', 'ترم أول', 'ترم ثاني', 'ترم صيفي', 'فصل دراسي', 'فصل أول',
        'semester', 'term', 'fall', 'spring', 'summer',
        # دفعات (السنة الهجرية السعودية)
        'دفعة', 'دفعه', '1444', '1445', '1446', '1447', '1448',
        'cohort', 'batch',
        # أنشطة دراسية
        'محاضرة', 'سكشن', 'واجب', 'واجبات', 'بحث', 'مشروع', 'تقرير', 'عرض',
        'ميدتيرم', 'فاينل', 'اختبار', 'امتحان', 'كويز',
        'quiz', 'midterm', 'final', 'lecture', 'section', 'assignment', 'exam',
        # مواد دراسية
        'مادة', 'مواد', 'منهج', 'كتاب', 'ملخص', 'شرائح', 'slides',
        'course', 'subject', 'curriculum', 'syllabus',
        # أنظمة جامعية
        'تسجيل', 'add drop', 'withdraw', 'معدل', 'gpa', 'credit',
        'blackboard', 'بلاك بورد', 'moodle', 'مودل', 'tudris', 'تودرس',
        'registration', 'enrollment',
        # أقسام وتخصصات
        'تخصص', 'قسم', 'شعبة', 'فرقة', 'major', 'department',
        # تجمعات طلابية
        'طلاب', 'طالبات', 'تجمع', 'طلابي', 'طالبة', 'students', 'student',
        # مستويات جامعية
        'بكالوريوس', 'ماجستير', 'دكتوراه', 'دبلوم',
        'bachelor', 'master', 'phd', 'diploma', 'degree',
        # إرشاد أكاديمي
        'مرشد', 'إرشاد', 'ساعات معتمدة', 'تخصص ثانٍ',
        'academic', 'advisor', 'credit hours',
        # مناسبات جامعية
        'جدول', 'جدول المحاضرات', 'تقويم', 'تقويم جامعي',
        'orientation', 'تعريف', 'يوم تعريفي',
        # رموز سعودية/خليجية
        'سنة تحضيرية', 'سنة تحضيري', 'تحضيري', 'preparatory', 'foundation',
        'انتساب', 'تعليم عن بعد', 'distance learning',
    ]

    # كلمات تشير لمصدر أكاديمي (حتى لو ما فيه اسم جامعة)
    ACADEMIC_SOURCE_KEYWORDS = [
        'جامعة', 'كلية', 'معهد', 'أكاديمية', 'مدرسة',
        'دراسة', 'تعليم', 'محاضرة', 'مستوى', 'ترم', 'دفعة',
        'طلاب', 'طالبات', 'طلابي', 'بنات', 'بنين',
    ]

    # كلمات إيجابية قوية (تعليمية مؤكدة) — من EducationalFilter السابق
    STRONG_POSITIVE = [
        'جامعة', 'كلية', 'معهد', 'روضة', 'مدرسة',
        'university', 'college', 'institute', 'school', 'academy',
    ]

    # مؤشرات أن الرابط لقناة (وليس مجموعة)
    CHANNEL_INDICATORS = [
        'قناة', 'channel', 'telegram channel', 'قناة تيليجرام',
        'اخبار', 'news', 'إعلام', 'broadcast', 'اذاعة',
    ]

    # ==================================================================
    # دوال مساعدة
    # ==================================================================
    @classmethod
    def _normalize(cls, text: str) -> str:
        """ترجيع النص lowercase مع إزالة المسافات الجانبية."""
        return (text or '').lower().strip()

    @classmethod
    def _extract_username(cls, link: str) -> str:
        """استخراج username من رابط Telegram أو WhatsApp.

        يدعم:
          - https://t.me/username
          - https://telegram.me/username
          - @username
          - https://chat.whatsapp.com/ABC123 (يرجع ABC123)

        Returns:
            username المستخرج أو '' لو ما فيه
        """
        if not link:
            return ''

        # t.me/username أو telegram.me/username
        m = re.search(r'(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)',
                      link, re.IGNORECASE)
        if m:
            return m.group(1)

        # @username
        m = re.search(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)', link)
        if m:
            return m.group(1)

        # WhatsApp: chat.whatsapp.com/ABC123 — نرجع الـ token (للفحص)
        m = re.search(r'chat\.whatsapp\.com/([A-Za-z0-9]+)', link, re.IGNORECASE)
        if m:
            return m.group(1)

        return ''

    @classmethod
    def _contains_any_term(cls, text: str, term_list: List[str]) -> Optional[str]:
        """فحص هل يحتوي النص على أي مصطلح من القائمة.

        Returns:
            أول مصطلح مطابق أو None
        """
        text_lower = cls._normalize(text)
        if not text_lower:
            return None

        for term in term_list:
            if term.lower() in text_lower:
                return term
        return None

    # ==================================================================
    # فحوصات مستقلة (كل واحدة ترجع Tuple[bool, reason])
    # ==================================================================
    @classmethod
    def is_blacklisted(cls, text: str, link_username: str = '',
                       link: str = '', source_group_name: str = '') -> Tuple[bool, str]:
        """فحص القائمة السوداء في كل الحقول.

        Returns:
            (True, reason) لو فيه كلمة سلبية
            (False, '') لو نظيف
        """
        combined = ' '.join([
            text or '', link_username or '',
            link or '', source_group_name or ''
        ])
        term = cls._contains_any_term(combined, cls.HARD_BLACKLIST)
        if term:
            return True, f'blacklist_{term}'
        return False, ''

    @classmethod
    def is_gulf_target(cls, text: str, link_username: str = '',
                       link: str = '') -> Tuple[bool, str]:
        """فحص هل فيه إشارة خليجية أكاديمية واضحة."""
        combined = ' '.join([text or '', link_username or '', link or ''])
        term = cls._contains_any_term(combined, cls.GULF_WHITELIST)
        if term:
            return True, f'gulf_{term}'
        return False, ''

    @classmethod
    def is_academic_context(cls, text: str, link_username: str = '',
                            link: str = '') -> Tuple[bool, str]:
        """فحص هل فيه سياق أكاديمي عام (مستوى/ترم/دفعة/1446...)."""
        combined = ' '.join([text or '', link_username or '', link or ''])
        term = cls._contains_any_term(combined, cls.ACADEMIC_CONTEXT)
        if term:
            return True, f'academic_{term}'
        return False, ''

    @classmethod
    def is_educational(cls, text: str, link_username: str = '') -> Tuple[bool, str]:
        """فحص تعليمي عام (من EducationalFilter السابق — للحالات الضعيفة).

        Returns:
            (True, reason) لو تعليمي
            (False, reason) لو غير تعليمي
        """
        if not text and not link_username:
            return False, 'empty_text'

        combined = f'{text or ""} {link_username or ""}'.lower()

        # فحص الجامعات السعودية (مطابقة قوية)
        for uni in cls.GULF_WHITELIST:
            if uni.lower() in combined:
                return True, f'saudi_uni_{uni}'

        # فحص الكلمات الإيجابية القوية
        for pos in cls.STRONG_POSITIVE:
            if pos.lower() in combined:
                return True, f'positive_{pos}'

        return False, 'no_educational_keywords'

    @classmethod
    def is_likely_channel(cls, text: str, link_username: str = '') -> bool:
        """يتحقق هل الرابط غالباً لقناة (وليس مجموعة)."""
        combined = f'{text or ""} {link_username or ""}'.lower()
        for ind in cls.CHANNEL_INDICATORS:
            if ind.lower() in combined:
                return True
        return False

    @classmethod
    def _is_source_academic_gulf(cls, source_group_name: str) -> Tuple[bool, str]:
        """فحص هل المصدر خليجي أو أكاديمي حسب اسمه.

        Returns:
            (True, reason) لو خليجي/أكاديمي
            (False, '') لو غير ذلك
        """
        if not source_group_name:
            return False, ''

        # 1. خليجي صريح
        is_gulf, gulf_reason = cls.is_gulf_target(source_group_name, '', '')
        if is_gulf:
            return True, f'source_{gulf_reason}'

        # 2. سياق أكاديمي
        is_acad, acad_reason = cls.is_academic_context(source_group_name, '', '')
        if is_acad:
            return True, f'source_{acad_reason}'

        # 3. كلمات أكاديمية عامة (جامعة، كلية، معهد...)
        term = cls._contains_any_term(source_group_name, cls.ACADEMIC_SOURCE_KEYWORDS)
        if term:
            return True, f'source_keyword_{term}'

        return False, ''

    # ==================================================================
    # الفحص الرئيسي — يجمع كل الفلاتر
    # ==================================================================
    @classmethod
    def should_join(cls, text: str, link_username: str = '', link: str = '',
                    source_group_name: str = '',
                    source_phone: str = '') -> Tuple[bool, str]:
        """فحص شامل قبل الانضمام.

        الترتيب:
            1. HARD_BLACKLIST → رفض فوري
            2. GULF_WHITELIST → قبول فوري (جامعة خليجية معروفة)
            3. ACADEMIC_CONTEXT → قبول (سياق أكاديمي)
            4. مصدر أكاديمي → قبول
            5. is_educational → قبول
            6. احتياطي → قبول (البوت يراقك مجموعات تعليمية)
        """
        if not link_username:
            link_username = cls._extract_username(link)

        # 1. القائمة السوداء → رفض فوري
        is_bad, bad_reason = cls.is_blacklisted(text, link_username, link, source_group_name)
        if is_bad:
            return False, bad_reason

        # 2. القائمة البيضاء الخليجية → قبول فوري
        is_gulf, gulf_reason = cls.is_gulf_target(text, link_username, link)
        if is_gulf:
            return True, gulf_reason

        # 3. سياق أكاديمي → قبول
        is_acad, acad_reason = cls.is_academic_context(text, link_username, link)
        if is_acad:
            return True, acad_reason

        # 4. مصدر أكاديمي → قبول
        is_source_ok, source_reason = cls._is_source_academic_gulf(source_group_name)
        if is_source_ok:
            return True, source_reason

        # 5. فلتر تعليمي عام → قبول
        is_edu, edu_reason = cls.is_educational(text, link_username)
        if is_edu:
            return True, edu_reason

        # 6. احتياطي → قبول (البوت يراقب مجموعات تعليمية)
        return True, f'fallback_accept_{edu_reason}'

# Alias — EducationalFilter هو الاسم القديم المستخدم في باقي الكود
EducationalFilter = GulfFilter

# -------------------------------------------------------------------
# Message Formatter
# -------------------------------------------------------------------


class MessageFormatter:
    @staticmethod
    def format_link_message(group_name, sender_name, sender_contact, message_date, link,
                            message_text, source_phone, message_link=None,
                            non_members=None, watchers_names=None):
        """تنسيق آمن باستخدام blockquote مع حماية من HTML injection"""
        # تنظيف all user input من HTML
        safe_group = html_module.escape(str(group_name or "غير معروف"))
        safe_sender = html_module.escape(str(sender_name or "غير معروف"))
        safe_contact = html_module.escape(str(sender_contact or ""))
        safe_source = html_module.escape(str(source_phone or ""))
        safe_text = html_module.escape(str(message_text or "")[:150])
        if len(message_text or "") > 150:
            safe_text += "..."

        date_str = message_date.strftime("%Y-%m-%d %H:%M") if message_date else "غير معروف"

        # تحديد نوع الرابط (handle None/empty link safely)
        link_str = str(link or "")
        link_lower = link_str.lower()
        if "chat.whatsapp.com" in link_lower or "wa.me" in link_lower or "whatsapp.com" in link_lower:
            link_type_str = "🟢 واتساب"
        elif "t.me" in link_lower or "telegram.me" in link_lower:
            link_type_str = "🔵 تيليجرام"
        else:
            link_type_str = "🔗 رابط"

        # URL validation: only allow http(s) and telegram/whatsapp schemes to prevent href injection
        def _safe_url(url: str) -> str:
            """Return URL only if it has an allowed scheme, else return empty string."""
            if not url:
                return ""
            url = url.strip()
            # Allow only http(s) URLs — telegram/whatsapp invite links are http(s)
            if url.lower().startswith(("http://", "https://")):
                # Escape quotes/angle brackets for use inside HTML attribute
                return html_module.escape(url, quote=True)
            return ""

        safe_link = _safe_url(link_str)
        safe_msg_link = _safe_url(message_link) if message_link else ""

        # بناء blockquote واحد شامل
        content = f"<b>🔗 رابط محفوظ ({link_type_str})</b>\n\n"
        content += f"👥 <b>العضوية:</b> {safe_group}\n"
        content += f"👤 <b>الاسم:</b> {safe_sender}\n"
        if safe_contact:
            content += f"📞 <b>التواصل:</b> {safe_contact}\n"
        content += f"🕒 <b>التاريخ:</b> {date_str}\n"
        content += f"📡 <b>العدد:</b> <code>{safe_source}</code>\n\n"
        if safe_link:
            content += f'🔗 <a href="{safe_link}">اضغط هنا لفتح الرابط</a>'
        else:
            content += f'🔗 <code>{html_module.escape(link_str)}</code>'

        if safe_msg_link:
            content += f'\n📄 <a href="{safe_msg_link}">عرض الرسالة الأصلية</a>'

        # قائمة غير المشتركين
        if non_members:
            content += "\n\n📌 <b>توصية بالانضمام:</b>\n"
            content += "<i>المراقبون التالون غير مشتركين:</i>\n"
            for i, phone in enumerate(non_members, 1):
                name = watchers_names.get(phone, phone) if watchers_names else phone
                safe_name = html_module.escape(str(name))
                safe_phone = html_module.escape(str(phone))
                content += f"  {i}. {safe_name} ({safe_phone})\n"
            content += "\n🔔 <i>يرجى الانضمام للاستفادة.</i>"

        if safe_text:
            content += f"\n\n💬 <b>النص:</b>\n<i>{safe_text}</i>"

        return f"<blockquote>{content}</blockquote>"

    @staticmethod
    def get_link_buttons(link, is_saved=False):
        """أزرار الإجراءات - بنفس الإطار المربع"""
        save_text = "✅ محفوظ" if is_saved else "⭐ حفظ"
        return [
            [Button.url("🔗 فتح الرابط", link),
             Button.inline("📋 نسخ", f"copy_{link[:50]}".encode())],
            [Button.inline("📤 مشاركة", f"share_{link[:50]}".encode()),
             Button.inline(save_text, f"save_{link[:50]}".encode())]
        ]

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
            "• ينشرها في قناة مشتركة\n"
            "• 🎓 فلتر تعليمي ذكي\n"
            "• 🚀 انضمام جماعي للمجموعات\n"
            "• 🧹 تنظيف القناة من المكرر\n\n"
            "🚀 للبدء، اضغط زر «🔐 تسجيل الدخول» أدناه\n"
            "أو استخدم الأزرار للتحكم في النظام.\n\n"
            "💡 يمكنك أيضاً كتابة: Boot أو /start"
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
            "• سيتم سحب روابط مجموعاتهم أيضاً\n\n"
            "📌 أوامر التحكم بالانضمام:\n"
            "• /pause_join — إيقاف الانضمام\n"
            "• /resume_join — استئناف الانضمام\n"
            "• /set_role <phone> <role> — تغيير دور الحساب (monitor/joiner/backup)\n"
            "• /join_status — حالة حسابات الفدائيين\n"
            "• /enable_joiner <phone> — تفعيل فدائي\n"
            "• /disable_joiner <phone> — إيقاف فدائي\n\n"
            "📌 أوامر التحقق (E2E):\n"
            "• /verify — تقرير شامل (Supabase + Started + SQLite)\n"
            "• /sqlite_check — إثبات عدم وجود جدول watchers في SQLite\n\n"
            "📌 أوامر الانضمام الجماعي:\n"
            "• /bulk_join — الانضمام لكل روابط القناة (22 ألف+)\n"
            "• /bulk_join_status — تقدم الانضمام الجماعي\n"
            "• /bulk_join_stop — إيقاف الانضمام الجماعي\n"
            "• /clear_floodwait — مسح FloodWait وإعادة تفعيل الانضمام\n"
            "• /ai_mode — عرض/تبديل فحص الذكاء الاصطناعي (on/off)\n"
            "• /leave_bad_groups — مغادرة المجموعات السيئة (بيتكوين/عراقية/غير خليجية)\n"
            "• /clean_queue — حذف روابط الرسائل (t.me/user/123) من Queue\n"
            "• /rejoin_published — إعادة قراءة رسائل القناة وإدخال الروابط في Queue\n\n"
            "📌 أوامر تنظيف القناة:\n"
            "• /cleanup_preview — معاينة ما سيُحذف (بدون حذف فعلي)\n"
            "• /cleanup_links — حذف الروابط غير التعليمية والمكررة\n"
            "• /cleanup_status — تقدم التنظيف\n\n"
            "📌 [REQAUDIT-2] بانتظار موافقة المشرف:\n"
            "• /pending_approvals — عرض المجموعات التي أُرسل لها طلب انضمام وينتظر قبول المشرف\n"
            "• فحص تلقائي كل 30 دقيقة — عند قبول المشرف تتحول المجموعة إلى JOINED أوتوماتيكياً"
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
                 source_phone, source_name, progress_callback=None,
                 message_claim=None, prod_db=None):
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
        # === Unified dedup layer (atomic message claim with lease + token) ===
        # When provided, HistoryScanner uses MessageClaim to prevent re-processing
        # of messages already handled by NewMessage or Polling.
        self.message_claim = message_claim
        self.prod_db = prod_db  # for enqueue_link + set_group_state

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
        except Exception: pass
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
                except Exception: pass
            name = d.name or "Unknown"
            if self.progress_callback:
                try: self.progress_callback(idx, len(dialogs), name)
                except Exception: pass
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
            except Exception: pass
        try:
            async for msg in self.user_client.iter_messages(dialog, reverse=False, limit=self.max_per_chat):
                if self._is_cancelled(): break
                try: md = msg.date.replace(tzinfo=None) if msg.date else None
                except Exception: md = None
                if md and chat_cut and md < chat_cut: break
                self.total_scanned += 1
                if md and (last_date is None or md > last_date): last_date = md
                if not msg or not msg.text: continue

                # === ATOMIC CLAIM (prevents re-processing vs NewMessage/Polling) ===
                # If message_claim is available, claim the message atomically.
                # If claim returns None, the message was already processed → skip.
                claim_token = None
                if self.message_claim:
                    claim_token = await self.message_claim.claim(
                        dialog.id, msg.id, 'scanner', self.source_phone
                    )
                    if claim_token is None:
                        # Already processed by NewMessage or Polling → skip
                        continue

                try:
                    # === Unified link extraction (LinkNormalizer) ===
                    # Replaces old extract_whatsapp_telegram_links for consistency.
                    links_info = LinkNormalizer.extract_links(msg.text)
                    if not links_info:
                        # No links — mark as processed so we don't retry
                        if self.message_claim and claim_token:
                            await self.message_claim.mark_processed(
                                dialog.id, msg.id, claim_token
                            )
                        continue

                    # === Unified filter (GulfFilter.is_blacklisted) ===
                    # Replaces old is_target_university_message + is_advertiser_message.
                    # Note: GulfFilter is blacklist-based (reject only bad content),
                    # so it accepts all educational/gulf content without requiring
                    # a specific university name.
                    full_text = msg.text or ''
                    is_bad = False
                    bad_reason = ''
                    for link_info in links_info:
                        link_raw = link_info['raw'].lower()
                        username_raw = (link_info.get('username') or '').lower()
                        full_text_check = f"{full_text} {link_raw} {username_raw}".lower()
                        is_bad, bad_reason = GulfFilter.is_blacklisted(
                            full_text_check, username_raw, link_info['raw'], name
                        )
                        if is_bad:
                            break
                    if is_bad:
                        logging.debug(
                            f"[SCAN {self.source_phone}] BLACKLISTED: {bad_reason} in '{name[:30]}'"
                        )
                        # Mark as processed so we don't retry blacklisted messages
                        if self.message_claim and claim_token:
                            await self.message_claim.mark_processed(
                                dialog.id, msg.id, claim_token
                            )
                        continue

                    self.total_found += len(links_info)
                    try:
                        sender = await msg.get_sender()
                        sn = Monitor._get_sender_name(sender)
                    except Exception: sn = "Unknown"

                    # استخراج بيانات تواصل المرسل
                    contact = extract_sender_contact(msg.text)
                    if not contact and sender and hasattr(sender, 'username') and sender.username:
                        contact = f"✈️ @{sender.username}"

                    # رابط الرسالة
                    msg_link = None
                    try:
                        msg_link = f"https://t.me/c/{str(dialog.id).replace('-100', '')}/{msg.id}"
                    except Exception: pass

                    # === Enqueue links (URL dedup via link_queue.UNIQUE) ===
                    if self.prod_db:
                        # Use new production path: enqueue_link + set_group_state
                        for link_info in links_info:
                            link_data = {
                                **link_info,
                                'group_name': name,
                                'sender_name': sn,
                                'sender_contact': contact,
                                'source_phone': self.source_phone,
                                'message_text': msg.text,
                                'message_link': msg_link,
                            }
                            is_new = await self.prod_db.enqueue_link(link_data)
                            if is_new:
                                await self.prod_db.set_group_state(
                                    link_info['normalized'], GroupState.DISCOVERED,
                                    link_info['raw'], name
                                )
                                self.new_count += 1
                                # Still publish to channel via _send_batch (legacy behavior)
                                batch.append({
                                    'link': link_info['raw'], 'text': msg.text, 'date': md,
                                    'group': name, 'sender': sn, 'msg_link': msg_link,
                                    'contact': contact
                                })
                                if len(batch) >= self.batch_size:
                                    await self._send_batch(batch)
                                    batch = []
                    else:
                        # Fallback: legacy insert_request path (for backward compat
                        # if prod_db was not provided)
                        for link_info in links_info:
                            inserted = await self.db.insert_request(
                                link_info['raw'], md, name, sn, self.source_phone, msg_link,
                                message_text=msg.text, sender_contact=contact)
                            if inserted:
                                self.new_count += 1
                                batch.append({
                                    'link': link_info['raw'], 'text': msg.text, 'date': md,
                                    'group': name, 'sender': sn, 'msg_link': msg_link,
                                    'contact': contact
                                })
                                if len(batch) >= self.batch_size:
                                    await self._send_batch(batch)
                                    batch = []

                    # === Mark as PROCESSED ===
                    if self.message_claim and claim_token:
                        await self.message_claim.mark_processed(
                            dialog.id, msg.id, claim_token
                        )

                except Exception as inner_e:
                    # === Mark as FAILED (allows retry on next scan) ===
                    if self.message_claim and claim_token:
                        await self.message_claim.mark_failed(
                            dialog.id, msg.id, claim_token, str(inner_e)
                        )
                    logging.error(
                        f"[SCAN {self.source_phone}] msg ({dialog.id}, {msg.id}) error: {inner_e}"
                    )
                    # Don't re-raise — continue with next message
        except FloodWaitError: raise
        except Exception as e:
            logging.error(f"[SCAN {self.source_phone}] iter error: {e}")
            if last_date:
                try: await self.db.update_scan_state(self.source_phone, dialog.id, name, last_date)
                except Exception: pass
            return
        if batch: await self._send_batch(batch)
        if last_date:
            try: await self.db.update_scan_state(self.source_phone, dialog.id, name, last_date)
            except Exception: pass
        self.chats_scanned += 1

    async def _send_batch(self, batch):
        """نشر كل رابط على حدة عبر _send (مع retry + FloodWait cap)"""
        for item in batch:
            try:
                formatted = MessageFormatter.format_link_message(
                    item['group'], item['sender'], item.get('contact', ''), item['date'],
                    item['link'], item['text'], self.source_phone, item.get('msg_link'))
                buttons = MessageFormatter.get_link_buttons(item['link'])
                # === POST-CONDITION VERIFICATION ===
                published, msg_id = await self._send_with_retry(formatted, buttons)
                if not published:
                    logging.error(
                        f"[SCAN] ❌ PUBLISH_FAILED for link: {item['link'][:50]}\n"
                        f"[SCAN] item not counted as published"
                    )
            except Exception as e:
                logging.error(f"[SCAN] send error: {e}")

    async def _send_with_retry(self, formatted, buttons, retries=3) -> Tuple[bool, Optional[int]]:
        """Send with retry + FloodWait cap. Returns (success, message_id)."""
        total_waited = 0.0
        max_total_wait = 120.0
        last_error = "unknown"
        for a in range(1, retries + 1):
            try:
                if not self.bot_client or not self.bot_client.is_connected():
                    last_error = "bot_client not connected"
                    await asyncio.sleep(min(5 * a, 30))
                    continue
                result = await self.bot_client.send_message(
                    self.channel_id, formatted,
                    parse_mode='html',
                    buttons=buttons,
                    link_preview=False
                )
                if result and hasattr(result, 'id'):
                    logging.info(f"[SCAN] ✅ PUBLISHED_VERIFIED message_id={result.id}")
                    await asyncio.sleep(0.5)  # تجنب الفلو
                    return True, result.id
                else:
                    last_error = f"unexpected return: {type(result).__name__}"
                    await asyncio.sleep(min(5 * a, 30))
            except FloodWaitError as e:
                last_error = f"FloodWaitError({e.seconds}s)"
                wait = min(e.seconds + 1, max_total_wait - total_waited)
                if wait <= 0:
                    logging.error(f"[SCAN] ❌ FAILED reason={last_error}")
                    return False, None
                total_waited += wait
                await asyncio.sleep(wait)
            except (RPCError, OSError, ConnectionError) as e:
                last_error = f"{type(e).__name__}: {str(e)[:80]}"
                wait = min(10 * a, 60, max_total_wait - total_waited)
                if wait <= 0:
                    logging.error(f"[SCAN] ❌ FAILED reason={last_error}")
                    return False, None
                total_waited += wait
                await asyncio.sleep(wait)
            except Exception as e:
                last_error = f"Unexpected {type(e).__name__}: {str(e)[:80]}"
                logging.error(f"[SCAN] send retry error: {e}")
                await asyncio.sleep(min(5 * a, 30))
        logging.error(f"[SCAN] ❌ FAILED after {retries} attempts reason={last_error}")
        return False, None

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
        self._joiner_task = None  # محرك الانضمام التدريجي
        # نظام تسجيل الدخول التفاعلي
        # Each entry: {"step": str, "temp_client": ..., "phone": ..., "phone_code_hash": ..., "started_at": datetime}
        self._login_sessions: Dict[int, Dict] = {}
        self._login_session_ttl = timedelta(minutes=10)  # max time to complete login
        # Per-sender cooldown to prevent Telegram from banning the account
        # (Telegram limits ~3 code requests per phone per hour)
        self._login_cooldowns: Dict[int, datetime] = {}  # sender_id → next allowed time
        self._login_cooldown = timedelta(seconds=60)  # 60s between code requests
        self._user_tasks: Dict[str, asyncio.Task] = {}
        # ===== [REQAUDIT-3] Joiner Fleet Resilience State =====
        # Phones that hit a terminal session failure (not_authorized /
        # invalid_session / client_creation_error). We alert the owner ONCE
        # per phone, then keep the loop alive sleeping 1h between rechecks
        # so the operator can fix the DB session_string and have it picked
        # up without a process restart.
        self._alerted_terminal_phones: Set[str] = set()
        # Live fleet health snapshot — updated by _joiner_fleet_health_loop
        # (60s cycle) and read by /ready, /api/joined_groups, and the
        # _joiner_worker fleet backoff gate. Keys:
        #   connected_joiners: int
        #   floodwait_joiners: List[{phone, wait_s}]
        #   disconnected_joiners: List[str]
        #   safety_guard_blocked_joiners: int
        #   all_unavailable_since: Optional[datetime]
        #   fleet_down_alerted: bool
        self._fleet_health: Dict[str, Any] = {
            'connected_joiners': 0,
            'floodwait_joiners': [],
            'disconnected_joiners': [],
            'safety_guard_blocked_joiners': 0,
            'all_unavailable_since': None,
            'fleet_down_alerted': False,
        }
        self._joiner_fleet_health_task: Optional[asyncio.Task] = None
        # ===== [QUIET-DIGEST] Quiet-source watch state =====
        # group_name → {"day": "YYYY-MM-DD" (last alert), "volume": N}.
        # In-memory only: a restart re-seeds via the first-cycle digest
        # (ONE consolidated message, not N individual alerts).
        self._quiet_alerted: Dict[str, Dict] = {}
        self._quiet_source_task: Optional[asyncio.Task] = None
        # محلل الذكاء الاصطناعي
        self.ai_analyzer = AIAnalyzer()
        self._startup_scan_done: Set[str] = set()
        # ===== Production Link System =====
        self.prod_db = ProductionDB(db)
        self.rate_limiter = RateLimiter(self.prod_db)
        self.floodwait_mgr = FloodWaitManager(self.prod_db)
        self.membership_cache = MembershipCache(self.prod_db, self.rate_limiter)
        self.metrics = Metrics()
        # [PR-METRICS-FLOODWAIT] اربط metrics بـrate_limiter حتى يُزاد
        # floodwait_total عند كل FloodWait من polling/reconcile paths
        # وليس فقط من PIPELINE-6 caller. (أنظر link_system.py:368-386.)
        try:
            self.rate_limiter.metrics = self.metrics
        except Exception:
            pass  # rate_limiter قد لا يكون له attribute metrics (defensive)
        self._scheduler_task = None
        # Emergency Controls (DB-backed, survives restart)
        self._join_paused = True  # افتراضي: متوقف حتى /resume_join
        # SIMULATION_MODE: لو True → كل العمليات تسجل فقط، صفر Telegram API
        self.simulation_mode = os.getenv('SIMULATION_MODE', 'false').lower() == 'true'
        # Cache for dialog lists (per-watcher) used by membership check.
        # Key: phone, Value: (dict of {username_lower: phone}, timestamp)
        self._dialogs_cache: Dict[str, Tuple[Dict[str, str], datetime]] = {}
        # Bulk Join state
        self._bulk_join_running = False
        self._bulk_join_stop = False
        self._bulk_join_task = None
        self._bulk_join_stats = {'total': 0, 'joined': 0, 'already': 0, 'failed': 0, 'skipped': 0, 'current': ''}
        # === MESSAGE PRE-CACHE (anti-delete protection) ===
        # يحفظ آخر رسالة لكل (chat_id, msg_id) لمدة 60 ثانية
        # لو بوت حماية حذف الرسالة، نقدر نسحبها من الـ cache
        # Key: (chat_id, msg_id) → {raw_text, source_phone, received_at, sender_id, chat_obj_cache}
        self._msg_cache: Dict[Tuple[int, int], dict] = {}
        self._msg_cache_lock = asyncio.Lock()
        self._msg_cache_ttl = 120  # ثانية — نبقي الرسائل لمدة دقيقتين
        # === LINK RING BUFFER (LRB) — LINK-ONLY FAST CAPTURE ===
        # [PR-1] مسار مستقل فائق السرعة: يخزّن الروابط المُستخرَجة (normalized
        # فقط) بأسرع نقطة بعد وصول الرسالة — قبل أي metadata/sender/title/cache.
        # الهدف: لو حُذفت الرسالة قبل اكتمال PRE-CACHE metadata، يقدر
        # _on_message_deleted يسحب الرابط من LRB ويدخله dedup/queue.
        # Key: (chat_id, msg_id) → List[normalized_link]
        # [PR-LRB-TTL] قيمة _link_ring[key] تظل List[str] فقط (backward-
        # compat مع الاختبارات والـ Raw hook). الـtimestamps تُخزَّن في
        # _link_ring_ts (parallel dict بنفس الـkey) حتى لا تكسر صيغة القيمة.
        self._link_ring: Dict[Tuple[int, int], List[str]] = {}
        self._link_ring_ts: Dict[Tuple[int, int], float] = {}  # received_at per key
        self._link_ring_lock = asyncio.Lock()
        self._link_ring_ttl = 300   # 5 دقائق (أطول من cache لاحتمال وصول Delete متأخر)
        self._link_ring_cap = 20000 # حد أقصى لمنع نمو الذاكرة بلا حدود
        self._link_ring_evicted = 0 # عدّاد للمراقبة (size-based eviction)
        self._link_ring_ttl_evicted = 0  # عدّاد للمراقبة (TTL-based eviction)
        # [PR-CLEANUP] حذف self._link_ring_hits = 0 — كان dead code:
        # يُهيّأ في __init__ لكن لا يُزاد ولا يُقرأ في أي مكان إنتاجي
        # (الميتريك الحقيقي يعيش في link_system.py record_link_ring_hit).
        # === ACTIVE POLLING WORKER ===
        # بدل الاعتماد على NewMessage events فقط (اللي قد تتأخر أو تُحذف قبل ما توصل),
        # نضيف polling نشط: كل 3 ثواني نسحب آخر 3 رسائل من كل مجموعة نشطة
        # هذا يضمن التقاط الرسائل خلال 3 ثواني حتى لو بوت حماية حذفها بسرعة
        # Key: chat_id → آخر msg_id شافه البوت
        self._polling_state: Dict[int, int] = {}
        self._polling_lock = asyncio.Lock()
        self._active_polling_task = None
        # المجموعات النشطة المرشحة للـ polling (تُحدَّث ديناميكياً من monitored_chats)
        self._active_polling_chats: List[dict] = []
        self._polling_interval = 5  # ثواني
        # === SOURCE REGISTRY + POLLING SCHEDULER + MESSAGE CLAIM ===
        # طبقة موحدة لـ: اكتشاف المصادر، اختيار القارئ، atomic dedup
        self.source_registry: Optional[SourceRegistry] = None
        self.polling_scheduler: Optional[PollingScheduler] = None
        self.message_claim: Optional[MessageClaim] = None
        self._registry_task: Optional[asyncio.Task] = None
        self._polling_scheduler_task: Optional[asyncio.Task] = None
        self._claim_cleanup_task: Optional[asyncio.Task] = None
        # === DELETE-MISS FORENSICS + RECONCILE + POLL FAILURE TRACKING ===
        self._delete_miss_log_ts: Dict[int, float] = {}   # chat_id → آخر WARNING
        self._delete_miss_count: Dict[int, int] = {}      # chat_id → عدد miss منذ آخر WARNING
        self._no_text_count = 0                            # رسائل بلا نص منذ آخر ملخص
        self._reconcile_inflight: Set[int] = set()         # شاتات قيد reconcile
        self._chat_poll_failures: Dict[int, int] = {}      # chat_id → إخفاقات polling متتالية
        self._journal_recovery_task: Optional[asyncio.Task] = None
        # [Task 3a / point 6] snapshot concurrent-invocation guard — set True
        # while a snapshot cycle is mid-POST so the supervisor relaunch path
        # is a no-op rather than a double POST.
        self._snapshot_running: bool = False
        # [B07] supervisor + [L03] polling-watchdog task handles
        self._supervisor_task: Optional[asyncio.Task] = None
        self._polling_watchdog_task: Optional[asyncio.Task] = None
        # [REQAUDIT-2] pending-approval self-healing recheck task handle
        self._pending_approval_recheck_task: Optional[asyncio.Task] = None
        # [Task 9a / W3] Shared lock that serializes the relaunch of
        # `_polling_scheduler_task` between `_supervisor_loop` (60s) and
        # `_polling_watchdog_loop` (30s). Without it, both loops can
        # simultaneously observe the scheduler task as `.done()` and each
        # call `asyncio.create_task(self.polling_scheduler.run())` — the
        # second assignment overwrites the first reference, leaving the
        # first instance running fire-and-forget → two concurrent polling
        # schedulers double the BATCH_SIZE and halve the cycle sleep.
        self._scheduler_relaunch_lock = asyncio.Lock()

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
        """إنشاء user_client من StringSession
        
        مهم: catch_up=True يخلي Telethon يسحب updates الفائتة بعد أي انقطاع.
        flood_sleep_threshold=60 يخلي البوت ينتظر 60s تلقائياً وقت FloodWait بدل ما يفقد updates.
        """
        return TelegramClient(
            StringSession(session_string),
            self.config.api_id, self.config.api_hash,
            connection_retries=None, retry_delay=5, request_retries=5,
            auto_reconnect=True, sequential_updates=False,
            catch_up=True,  # ← يسحب updates الفائتة بعد أي انقطاع
            flood_sleep_threshold=60,  # ← ينتظر 60s تلقائياً وقت FloodWait
        )

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
        """القائمة الرئيسية - أزرار تفاعلية شاملة"""
        if is_logged_in:
            return [
                [Button.inline("📊 الحالة", b"status"),
                 Button.inline("📈 إحصائياتي", b"my_stats")],
                [Button.inline("🔄 مسح آخر أسبوع", b"scan_week"),
                 Button.inline("📅 مسح آخر شهر", b"scan_month")],
                # أوامر الانضمام الجماعي
                [Button.inline("🚀 بدء الانضمام الجماعي", b"bulk_join"),
                 Button.inline("📊 تقدم الانضمام", b"bulk_join_status")],
                [Button.inline("⏹️ إيقاف الانضمام", b"bulk_join_stop"),
                 Button.inline("🧹 مسح FloodWait", b"clear_floodwait")],
                # أوامر تنظيف القناة
                [Button.inline("🔍 معاينة التنظيف", b"cleanup_preview"),
                 Button.inline("🗑️ تنظيف القناة", b"cleanup_links")],
                [Button.inline("📈 تقدم التنظيف", b"cleanup_status")],
                # إدارة الحسابات والتحقق
                [Button.inline("👤 إدارة الأدوار", b"role_menu"),
                 Button.inline("🔍 تحقق النظام", b"verify")],
                [Button.inline("🗄️ فحص SQLite", b"sqlite_check"),
                 Button.inline("📊 حالة الفدائيين", b"join_status")],
                [Button.inline("❓ المساعدة", b"help")],
            ]
        else:
            return [
                [Button.inline("🔐 تسجيل الدخول", b"login_start")],
                [Button.inline("❓ المساعدة", b"help"),
                 Button.inline("📊 الحالة", b"status")],
                # أزرar الانضمام والتنظيف متاحة حتى بدون تسجيل (للمشرف)
                [Button.inline("🚀 بدء الانضمام الجماعي", b"bulk_join"),
                 Button.inline("📊 تقدم الانضمام", b"bulk_join_status")],
                [Button.inline("🔍 معاينة التنظيف", b"cleanup_preview"),
                 Button.inline("🗑️ تنظيف القناة", b"cleanup_links")],
                [Button.inline("🔍 تحقق النظام", b"verify")],
            ]

    async def _on_callback(self, event):
        """معالج ضغطات الأزرار"""
        try:
            # Defense-in-depth: cap callback data size to prevent DoS
            # Telegram limits callback_data to 64 bytes, but enforce anyway
            if event.data and len(event.data) > 256:
                logging.warning("[CALLBACK] Oversized callback data from sender, rejecting")
                await event.answer("Invalid request", alert=True)
                return

            data = event.data.decode('utf-8', errors='replace')
            sender = await event.get_sender()
            sender_id = sender.id if sender else None

            logging.info(f"[CALLBACK] {sender_id}: {data[:80]}")

            # AUTHORIZATION: state-changing actions require owner verification
            # (login_start, scan_week, scan_month, scan_stop, reset_scan)
            # Read-only actions (status, help, main_menu) and per-message
            # actions (save, copy, share on links the user can already see)
            # are allowed for anyone in the channel.
            STATE_CHANGING_CALLBACKS = {
                "login_start", "scan_week", "scan_month", "scan_stop", "reset_scan",
                "add_watcher",
            }
            is_state_changing = (
                data in STATE_CHANGING_CALLBACKS
                or data.startswith("scan_")
                or data.startswith("reset_")
            )
            if is_state_changing and self.config.owner_id is not None:
                if sender_id != self.config.owner_id:
                    logging.warning(
                        f"[CALLBACK] Unauthorized state-changing callback '{data}' "
                        f"from sender_id={sender_id} (owner={self.config.owner_id})"
                    )
                    await event.answer("⛔ غير مصرح", alert=True)
                    return

            if data == "login_start":
                await self._handle_login_start(event, sender)
                return

            # معالجة اختيار الدور (مراقب / فدائي)
            if data == "login_monitor" or data == "login_joiner":
                role = "monitor" if data == "login_monitor" else "joiner"
                await self._start_login_with_role(event, sender, role)
                return

            if data == "login_cancel":
                await event.edit("❌ تم إلغاء تسجيل الدخول.")
                return

            # معالجة أزرار الروابط (نسخ/مشاركة/حفظ)
            if data.startswith("save_") or data.startswith("copy_") or data.startswith("share_"):
                if data.startswith("save_"):
                    # تغيير الزر إلى "محفوظ"
                    try:
                        await event.edit(buttons=MessageFormatter.get_link_buttons(
                            data[5:], is_saved=True
                        ))
                    except Exception:
                        pass
                    await event.answer("✅ تم الحفظ!")
                elif data.startswith("copy_"):
                    await event.answer("📋 تم نسخ الرابط!", alert=False)
                elif data.startswith("share_"):
                    await event.answer("📤 استخدم زر المشاركة في تيليجرام", alert=False)
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
                # === تقرير حالة شامل (محدّث) ===
                try:
                    total = await self.db.count_requests()
                    watchers = await self.db.get_active_watchers()
                    monitors = [w for w in watchers if w.get('role', 'monitor') == 'monitor']
                    joiners = [w for w in watchers if w.get('role') == 'joiner']
                    backups = [w for w in watchers if w.get('role') == 'backup']

                    # عدد المتصلين فعلياً
                    connected_count = sum(1 for c in self.user_clients.values() if c and c.is_connected())
                    disconnected = []
                    for w in watchers:
                        ph = w['phone']
                        c = self.user_clients.get(ph)
                        if not c or not c.is_connected():
                            disconnected.append(ph)

                    # join_paused state
                    pause_state = "⏸️ متوقف" if self._join_paused else "▶️ نشط"
                    sim_state = "🧪 محاكاة" if self.simulation_mode else "📡 إنتاج"

                    # FloodWait accounts
                    blocked = await self.floodwait_mgr.get_blocked_accounts()
                    if blocked:
                        blocked_lines = []
                        for b in blocked:
                            wait_s = int(b['next_retry_at'] - time.time())
                            wait_min = max(wait_s // 60, 0)
                            blocked_lines.append(f"   ⚠️ {b['phone']}: {wait_min}د متبقية")
                        blocked_str = "\n".join(blocked_lines)
                    else:
                        blocked_str = "   ✅ لا يوجد"

                    # Queue
                    queue_sz = await self.prod_db.get_queue_size() if hasattr(self, 'prod_db') else 0

                    # Bulk join status
                    bulk_str = "لا يعمل"
                    if hasattr(self, '_bulk_join_running') and self._bulk_join_running:
                        s = self._bulk_join_stats
                        bulk_str = f"يعمل ✅ ({s.get('joined',0)} انضمام، {s.get('skipped',0)} تخطي)"

                    # Cleanup status
                    cleanup_str = "لا يعمل"
                    if hasattr(self, '_cleanup_stats') and self._cleanup_stats.get('running', False):
                        s = self._cleanup_stats
                        cleanup_str = f"يعمل ✅ ({s.get('deleted',0)} محذوف)"

                    status_msg = (
                        f"📊 تقرير حالة النظام\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📦 Supabase: {len(watchers)} حساب\n"
                        f"   👁️ مراقبين: {len(monitors)}\n"
                        f"   🚀 فدائيين: {len(joiners)}\n"
                        f"   🔄 احتياط: {len(backups)}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔗 متصل فعلياً: {connected_count}/{len(watchers)}\n"
                    )
                    if disconnected:
                        status_msg += f"❌ غير متصل: {', '.join(disconnected)}\n"
                    status_msg += (
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔒 الانضمام: {pause_state}\n"
                        f"🔬 الوضع: {sim_state}\n"
                        f"📦 القائمة: {queue_sz} رابط معلق\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚠️ FloodWait:\n{blocked_str}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🚀 الانضمام الجماعي: {bulk_str}\n"
                        f"🧹 التنظيف: {cleanup_str}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📥 إجمالي الروابط المنشورة: {total}\n"
                    )

                    await event.answer()
                    await event.edit(
                        status_msg,
                        buttons=[Button.inline("🔙 القائمة الرئيسية", b"main_menu")]
                    )
                except Exception as e:
                    logging.error(f"[STATUS] Error: {e}", exc_info=True)
                    await event.answer(f"❌ خطأ: {e}")
                return

            if data == "my_stats":
                # === إحصائيات تفصيلية ===
                try:
                    watchers = await self.db.get_active_watchers()
                    monitors = [w for w in watchers if w.get('role', 'monitor') == 'monitor']
                    joiners = [w for w in watchers if w.get('role') == 'joiner']

                    total_links = await self.db.count_requests()
                    queue_sz = await self.prod_db.get_queue_size() if hasattr(self, 'prod_db') else 0
                    metrics = await self.metrics.get_summary() if hasattr(self, 'metrics') else {}

                    stats_msg = (
                        f"📈 الإحصائيات التفصيلية\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📦 الحسابات:\n"
                        f"   👁️ مراقبين: {len(monitors)}\n"
                        f"   🚀 فدائيين: {len(joiners)}\n"
                        f"   المجموع: {len(watchers)}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📥 إجمالي الروابط: {total_links}\n"
                        f"📦 القائمة المعلقة: {queue_sz}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🚀 الانضمامات:\n"
                        f"   ناجحة: {metrics.get('total_joins', 0)}\n"
                        f"   FloodWait: {metrics.get('total_floodwait', 0)}\n"
                        f"   مكررة: {metrics.get('total_duplicates', 0)}\n"
                        f"   متجاوزة: {metrics.get('total_skips', 0)}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔄 المسح التاريخي: "
                        f"{'قيد التنفيذ' + (' (' + self._scan_progress + ')' if self._scan_progress else '') if self.is_scan_running() else 'متوقف'}\n"
                    )
                    await event.answer()
                    await event.edit(
                        stats_msg,
                        buttons=[Button.inline("🔙 القائمة الرئيسية", b"main_menu")]
                    )
                except Exception as e:
                    logging.error(f"[MY_STATS] Error: {e}", exc_info=True)
                    await event.answer(f"❌ خطأ: {e}")
                return

            if data == "scan_week":
                await event.answer("جاري بدء المسح...")
                await self._start_scan_all(7, "/scan_week")
                return

            if data == "scan_month":
                await event.answer("جاري بدء المسح...")
                await self._start_scan_all(30, "/scan_month")
                return

            # === أوامر الانضمام الجماعي ===
            if data == "bulk_join":
                await event.answer("بدء الانضمام...")
                # Scheduler يبدأ تلقائياً — هذا الزر لاستئناف/إعادة تشغيل يدوي
                if hasattr(self, '_joiner_task') and self._joiner_task and not self._joiner_task.done():
                    if self._join_paused:
                        # Worker يعمل لكن متوقف — استأنف
                        self._join_paused = False
                        await self.prod_db.set_setting('join_paused', 'false')
                        await event.reply("▶️ تم استئناف الانضمام تلقائياً")
                    else:
                        await event.reply("✅ الانضمام يعمل تلقائياً\nأرسل /bulk_join_status لرؤية التقدم")
                else:
                    # Worker متوقف — أعد تشغيله
                    self._bulk_join_running = True
                    self._bulk_join_stop = False
                    self._bulk_join_stats = {'total': 0, 'joined': 0, 'already': 0, 'failed': 0, 'skipped': 0, 'current': ''}
                    self._bulk_join_task = asyncio.create_task(self._bulk_join_worker())
                    await event.reply(
                        "🚀 بدأ الانضمام\n\n"
                        "📝 سيقرأ روابط من القائمة ويحاول الانضمام.\n"
                        "⏱️ معدل آمن: انضمام كل دقيقة"
                    )
                return

            if data == "bulk_join_status":
                await event.answer()
                # Scheduler يبدأ تلقائياً — اعرض حالته دائماً
                s = getattr(self, '_bulk_join_stats', {'total': 0, 'joined': 0, 'already': 0, 'failed': 0, 'skipped': 0, 'current': ''})
                scheduler_state = await self.prod_db.get_setting('scheduler_state', 'NOT_STARTED')
                scheduler_cycle = await self.prod_db.get_setting('scheduler_last_cycle', '0')
                join_paused = await self.prod_db.get_setting('join_paused', 'false')
                queue_size = await self.prod_db.get_queue_size()
                bulk_running = getattr(self, '_bulk_join_running', False)

                worker_status = "AUTO (Scheduler)"
                if bulk_running:
                    worker_status = "MANUAL (Bulk Join)"
                if join_paused == 'true':
                    worker_status = "⏸️ PAUSED"

                await event.reply(
                    f"📊 Join Worker Status\n"
                    f"════════════════════\n"
                    f"⚙️ Worker: {worker_status}\n"
                    f"⚙️ Scheduler: {scheduler_state} (cycle={scheduler_cycle})\n"
                    f"🔒 Join paused: {join_paused}\n"
                    f"📋 Queue depth: {queue_size}\n"
                    f"\n"
                    f"Stats:\n"
                    f"  🔗 Total: {s.get('total', 0)}\n"
                    f"  ✅ Joined: {s.get('joined', 0)}\n"
                    f"  ℹ️ Already: {s.get('already', 0)}\n"
                    f"  ❌ Failed: {s.get('failed', 0)}\n"
                    f"  ⏭️ Skipped: {s.get('skipped', 0)}\n"
                    f"  📍 Current: {s.get('current', '')[:60]}"
                )
                return

            if data == "bulk_join_stop":
                await event.answer("إيقاف البوك جون...")
                if hasattr(self, '_bulk_join_running') and self._bulk_join_running:
                    self._bulk_join_stop = True
                    await event.reply("⏹️ سيتم إيقاف البوك جون بعد الرابط الحالي")
                else:
                    await event.reply("ℹ️ البوك جون لا يعمل")
                return

            if data == "clear_floodwait":
                await event.answer("مسح FloodWait...")
                try:
                    conn = await self.db._ensure_conn()
                    cursor = await conn.execute("DELETE FROM floodwait_tracker")
                    await conn.commit()
                    count = cursor.rowcount
                    if hasattr(self.rate_limiter, '_floodwait'):
                        self.rate_limiter._floodwait.clear()
                    self._join_paused = False
                    await self.prod_db.set_setting('join_paused', 'false')
                    await event.reply(f"✅ تم مسح {count} سجل FloodWait\n▶️ تم إعادة تفعيل الانضمام")
                except Exception as e:
                    await event.reply(f"❌ خطأ: {e}")
                return

            # === أوامر تنظيف القناة ===
            if data == "cleanup_preview":
                await event.answer("بدأ التحليل...")
                await event.reply("🔍 بدأ التحليل... قد يستغرق عدة دقائق لـ 22 ألف رسالة")
                asyncio.create_task(self._cleanup_worker(preview_only=True))
                return

            if data == "cleanup_links":
                await event.answer("بدأ التنظيف الفعلي...")
                await event.reply("🗑️ بدأ التنظيف الفعلي... سيتم حذف الروابط غير التعليمية والمكررة")
                asyncio.create_task(self._cleanup_worker(preview_only=False))
                return

            if data == "cleanup_status":
                await event.answer()
                s = getattr(self, '_cleanup_stats', None)
                if not s or not s.get('running', False):
                    await event.reply("ℹ️ التنظيف لا يعمل. استخدم أزرار المعاينة أو التنظيف")
                else:
                    await event.reply(
                        f"🧹 Cleanup Status\n"
                        f"════════════════════\n"
                        f"📊 Total scanned: {s.get('total', 0)}\n"
                        f"✅ Educational: {s.get('educational', 0)}\n"
                        f"❌ Non-educational: {s.get('non_educational', 0)}\n"
                        f"🔄 Duplicates: {s.get('duplicates', 0)}\n"
                        f"🗑️ Deleted: {s.get('deleted', 0)}\n"
                        f"📍 Current: {s.get('current', '')[:60]}"
                    )
                return

            # === أوامر التحقق وإدارة الأدوار ===
            if data == "verify":
                await event.answer("جاري التحقق...")
                # استدعاء نفس منطق /verify
                try:
                    all_accounts = await self.db.get_active_watchers()
                    supa_count = len(all_accounts)
                    started_count = len(self.user_clients)
                    connected_count = sum(1 for c in self.user_clients.values() if c and c.is_connected())
                    sqlite_tables = await self.db._sqlite_list_tables()
                    has_watchers_table = 'watchers' in sqlite_tables
                    lines = [
                        "🔍 E2E Verification Report",
                        "═══════════════════════════",
                        f"📦 Supabase accounts: {supa_count}",
                        f"🚀 Started clients: {started_count}",
                        f"🔗 Connected clients: {connected_count}",
                        "",
                        "📋 Account list:",
                    ]
                    for w in all_accounts:
                        ph = w.get('phone', '?')
                        rl = w.get('role', 'monitor')
                        conn = self.user_clients.get(ph)
                        icon = "✅" if (conn and conn.is_connected()) else "❌"
                        lines.append(f"   {icon} {ph} (role={rl})")
                    lines.append("")
                    if has_watchers_table:
                        lines.append("❌ BUG: 'watchers' table EXISTS in SQLite!")
                    else:
                        lines.append("✅ PROVEN: 'watchers' table does NOT exist in SQLite.")
                    if supa_count == started_count and not has_watchers_table:
                        lines.append("✅ E2E PASS")
                    else:
                        lines.append("❌ E2E FAIL")
                    await event.reply("\n".join(lines))
                except Exception as e:
                    await event.reply(f"❌ خطأ: {e}")
                return

            if data == "sqlite_check":
                await event.answer("فحص SQLite...")
                try:
                    tables = await self.db._sqlite_list_tables()
                    has_watchers = 'watchers' in tables
                    lines = ["🗄️ SQLite Tables Check", "═══════════════════════════", f"Total tables: {len(tables)}", "", "Tables:"]
                    for t in tables:
                        marker = "❌" if t == 'watchers' else "✅"
                        lines.append(f"   {marker} {t}")
                    if has_watchers:
                        lines.append("\n❌ BUG: 'watchers' table EXISTS!")
                    else:
                        lines.append("\n✅ PROVEN: 'watchers' does NOT exist in SQLite.")
                    await event.reply("\n".join(lines))
                except Exception as e:
                    await event.reply(f"❌ خطأ: {e}")
                return

            if data == "join_status":
                await event.answer("جاري عرض حالة الفدائيين...")
                # استدعاء نفس منطق /join_status
                try:
                    joiners = await self.db.get_watchers_by_role("joiner")
                    if not joiners:
                        await event.reply("ℹ️ لا يوجد حسابات فدائية مسجلة.")
                    else:
                        lines = ["📊 Joiner Status (source: Supabase)", ""]
                        for j in joiners:
                            jphone = j['phone']
                            w = await self.db._supabase_get_watcher(jphone)
                            enabled = bool(w.get('joiner_enabled', 1)) if w else True
                            is_blocked, wait = await self.floodwait_mgr.is_blocked(jphone)
                            await self.db.reset_daily_joins_if_needed(jphone)
                            daily = await self.db.get_daily_join_count(jphone)
                            daily_limit = await self._get_daily_limit(jphone)
                            if not enabled:
                                status = "DISABLED"
                            elif is_blocked:
                                hours = wait // 3600
                                mins = (wait % 3600) // 60
                                status = f"FLOODWAIT ({hours}h {mins}m)"
                            elif daily >= daily_limit:
                                status = "DAILY_LIMIT"
                            else:
                                status = "READY"
                            lines.append(f"📞 {jphone}")
                            lines.append(f"   Status: {status}")
                            lines.append(f"   Daily: {daily}/{daily_limit}")
                            lines.append("")
                        pause_str = "⏸️ PAUSED" if self._join_paused else "▶️ ACTIVE"
                        sim_str = "🧪 SIMULATION" if self.simulation_mode else "📡 PRODUCTION"
                        lines.append(f"🔒 Global: {pause_str} | {sim_str}")
                        await event.reply("\n".join(lines))
                except Exception as e:
                    await event.reply(f"❌ خطأ: {e}")
                return

            if data == "role_menu":
                await event.answer()
                await event.reply(
                    "👤 إدارة الأدوار\n"
                    "════════════════════\n"
                    "لتحويل حساب بين الأدوار، أرسل:\n\n"
                    "• /set_role <phone> monitor — مراقب\n"
                    "• /set_role <phone> joiner — فدائي\n"
                    "• /set_role <phone> backup — احتياطي\n\n"
                    "مثال: /set_role +967739407274 joiner\n\n"
                    "لتفعيل/إيقاف فدائي:\n"
                    "• /enable_joiner <phone>\n"
                    "• /disable_joiner <phone>"
                )
                return

            await event.answer()

        except Exception as e:
            logging.error(f"Callback error: {e}", exc_info=True)
            try:
                await event.answer("حدث خطأ")
            except Exception:
                pass

    def _register_user_handlers(self, phone: str):
        """تسجيل معالجات الرسائل لكل user_client.

        معالجات ثلاثة:
        0. Raw MTProto hook (PR-3) — طبقة إضافية فائقة السرعة لالتقاط
           الروابط قبل تطبيع Telethon. يكتب LRB فقط (supplementary).
        1. NewMessage — يخزن كل رسالة في cache فور وصولها (قبل أي معالجة بطيئة)
        2. MessageDeleted — لو حُذفت رسالة، نسحبها من cache ونعالجها فوراً
        """
        client = self.user_clients.get(phone)
        if not client: return
        # [PR-3] Raw MTProto hook — supplementary, never replaces NewMessage.
        # يلتقط updateNewMessage / updateNewChannelMessage قبل تطبيع Telethon،
        # يكتب LRB فقط (روابط normalized) لأسرع التقاط ممكن. الفشل لا يكسر البوت.
        try:
            from telethon.tl.types import (UpdateNewMessage,
                                            UpdateNewChannelMessage)
            client.add_event_handler(
                lambda u: self._on_raw_new_message(u, phone),
                events.Raw(types=(UpdateNewMessage, UpdateNewChannelMessage))
            )
        except Exception as raw_e:
            logging.warning(
                f"[RAW-HOOK] registration failed for {phone}: {raw_e} "
                f"— NewMessage still active (supplementary layer only)"
            )
        # معالج الرسائل الجديدة — يخزن في cache أولاً، ثم يعالج
        client.add_event_handler(
            lambda e: self._on_user_message(e, phone),
            events.NewMessage(incoming=True)
        )
        # معالج الحذف — يلتقط الرسائل المحذوفة قبل ما نعالجها
        client.add_event_handler(
            lambda e: self._on_message_deleted(e, phone),
            events.MessageDeleted()
        )
        logging.info(f"User handlers registered for {phone} (Raw + NewMessage + MessageDeleted)")

    @staticmethod
    def _normalize_raw_chat_id(peer_id) -> Optional[int]:
        """[PR-3] يطبّع chat_id من raw MTProto peer_id ليتطابق مع
        event.chat_id في NewMessage (صيغة -100... للسوبرجروبات/القنوات).
        PeerChannel → -100{channel_id}  (supergroup/channel)
        PeerChat    → -{chat_id}        (legacy small group)
        PeerUser    → {user_id}         (private chat)
        يعيد None لو النوع غير معروف."""
        if peer_id is None:
            return None
        try:
            # PeerChannel (supergroups + broadcast channels)
            cid = getattr(peer_id, 'channel_id', None)
            if cid:
                return int(f"-100{cid}")
            # PeerChat (legacy small groups)
            hid = getattr(peer_id, 'chat_id', None)
            if hid:
                return -int(hid) if int(hid) > 0 else int(hid)
            # PeerUser (private chat)
            uid = getattr(peer_id, 'user_id', None)
            if uid:
                return int(uid)
        except (ValueError, TypeError):
            return None
        return None

    async def _on_raw_new_message(self, update, source_phone: str):
        """[PR-3] Raw MTProto hook — طبقة صفر، قبل تطبيع Telethon.
        يكتب LRB فقط (روابط normalized) كطبقة supplementary إضافية.
        لا يستبدل NewMessage. الفشل لا يكسر event loop (try/except شامل).
        مبدأ: observability/exceptions هنا لا يجب أن تكسر أي شيء أبداً."""
        try:
            msg = getattr(update, 'message', None)
            if msg is None:
                return
            text = getattr(msg, 'message', '') or ''
            if not text:
                return
            mid = getattr(msg, 'id', 0)
            if not mid:
                return
            peer_id = getattr(msg, 'peer_id', None)
            chat_id = self._normalize_raw_chat_id(peer_id)
            if chat_id is None:
                return  # نوع غير مدعوم — تجاهل بهدوء
            # استخراج الروابط (regex نقي) + كتابة LRB
            links = LinkNormalizer.extract_links(text)
            if links:
                try:
                    await self._link_ring_put(chat_id, int(mid),
                                              [l.get('normalized') or l.get('raw') for l in links])
                except Exception:
                    pass  # LRB فشل — لكن NewMessage سيلتقط لاحقاً
        except Exception:
            pass  # Raw hook لا يكسر event loop أبداً

    async def _check_telegram_membership(self, link: str) -> dict:
        """
        فحص خفيف للعضوية — استدعاء واحد لكل مراقب (مو 500 ديالوج).
        يستخدم GetParticipantRequest مباشرة على الرابط المستخرج.
        يعيد: {phone: True(مشترك) / False(غير مشترك) / None(تعذر الفحص)}
        """
        results = {}

        # 1. استخراج username من الرابط
        link_lower = link.lower()
        if "t.me/+" in link or "joinchat" in link_lower:
            return {}  # روابط خاصة لا يمكن فحصها

        username = None
        if "t.me/" in link_lower:
            parts = link.split("t.me/", 1)
            if len(parts) > 1:
                # نأخذ الجزء الأول فقط (اسم القناة/المجموعة)
                # حتى لو كان رابط رسالة مثل t.me/archivesSEU/123
                username = parts[1].split("/")[0].split("?")[0].strip()

        if not username or len(username) < 5:
            return {}  # غالباً رابط مستخدم أو غير صالح

        from telethon.tl.functions.channels import GetParticipantRequest
        from telethon.errors import UserNotParticipantError, FloodWaitError as FTW

        # 2. فحص كل مراقب: استدعاء واحد فقط (مو 500 ديالوج)
        for phone, client in self.user_clients.items():
            if not client or not client.is_connected():
                results[phone] = None
                continue

            try:
                entity = await client.get_entity(username)

                # لو مستخدم عادي (مو قناة/مجموعة) → اعتبره مشترك
                if hasattr(entity, 'first_name') and not hasattr(entity, 'megagroup') and not hasattr(entity, 'broadcast'):
                    results[phone] = True
                    continue

                try:
                    await client(GetParticipantRequest(channel=entity, participant="me"))
                    results[phone] = True  # مشترك
                    logging.debug(f"[MEMBERSHIP] {phone} IS member of @{username}")
                except UserNotParticipantError:
                    results[phone] = False  # غير مشترك
                    logging.debug(f"[MEMBERSHIP] {phone} NOT member of @{username}")
                except FTW as e:
                    logging.warning(f"[MEMBERSHIP] FloodWait {e.seconds}s for {phone} — skipping")
                    results[phone] = None  # تعذر الفحص
                except Exception:
                    results[phone] = None  # أي خطأ = تعذر الفحص

            except Exception:
                results[phone] = None  # get_entity فشل

        logging.info(f"[MEMBERSHIP] Check for @{username}: {results}")
        return results

    async def _send_recommendation(self, phone: str, link: str, group_name: str, description: str = ""):
        """يرسل توصية للمراقب بالانضمام للرابط"""
        client = self.user_clients.get(phone)
        if not client or not client.is_connected():
            return

        try:
            # Escape all user-controlled values to prevent HTML injection
            safe_group = html_module.escape(str(group_name or ""))
            safe_desc = html_module.escape(str(description or ""))
            # Only allow http(s) URLs in href, escape quotes
            safe_link = ""
            if link and link.lower().startswith(("http://", "https://")):
                safe_link = html_module.escape(link.strip(), quote=True)

            msg = (
                "📌 <b>توصية بالانضمام</b>\n\n"
                f"تم اكتشاف رابط جديد في مجموعة: <b>{safe_group}</b>\n"
            )
            if safe_desc:
                msg += f"📝 <b>الوصف:</b> {safe_desc}\n"
            if safe_link:
                msg += f'\n🔗 <a href="{safe_link}">اضغط هنا للانضمام</a>\n\n'
            else:
                msg += f'\n🔗 <code>{html_module.escape(str(link))}</code>\n\n'
            msg += "⚠️ <i>أنت غير مشترك في هذا الرابط بعد.</i>"

            await client.send_message("me", msg, parse_mode='html', link_preview=False)
            logging.info(f"[RECOMMEND] Sent to {phone} for {link[:50]}")
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            logging.error(f"[RECOMMEND] Failed for {phone}: {e}")

    # ------------------------------------------------------------------
    # Message Journal helpers (durable write-ahead log)
    # ------------------------------------------------------------------

    def _journal_enabled(self) -> bool:
        return bool(getattr(getattr(self, 'config', None), 'journal_enabled', True)) \
            and getattr(self, 'prod_db', None) is not None

    async def _journal_write(self, chat_id, msg_id, raw_text, source_phone,
                             chat_title='', chat_username='', chat_link_type='telegram',
                             sender_id=0, sender_name='', state='pending'):
        """كتابة journal متحمّلة للأخطاء — لا ترمي استثناء أبدًا."""
        if not self._journal_enabled():
            return
        # [N05] Consecutive-failure counter: a sustained burst of journal_write
        # failures (disk full, locked DB, schema drift) was previously hidden
        # because each failure logged only a single WARNING. We keep logging
        # each (don't permanently disable the journal), but ALSO emit a
        # rate-limited ERROR burst once the consecutive-failure count crosses
        # 50 — so operators see a single ERROR per minute instead of being
        # drowned in 50 WARNING-per-second lines OR a silent failure.
        try:
            await self.prod_db.journal_message({
                'chat_id': chat_id, 'msg_id': msg_id, 'raw_text': raw_text,
                'source_phone': source_phone, 'received_at': time.time(),
                'chat_title': chat_title, 'chat_username': chat_username,
                'chat_link_type': chat_link_type, 'sender_id': sender_id,
                'sender_name': sender_name, 'state': state,
            })
            # success — reset the burst counter
            self._journal_fail_count = 0
        except Exception as e:
            # [B08] was logging.debug — silent swallow hid disk-full / locked-DB
            # conditions that break message durability. WARNING surfaces them.
            logging.warning(f"[JOURNAL] write FAILED: ({chat_id},{msg_id}) {e}")
            # [N05] increment + rate-limited ERROR burst
            self._journal_fail_count = getattr(self, '_journal_fail_count', 0) + 1
            self._journal_last_burst_log = getattr(self, '_journal_last_burst_log', 0.0)
            now = time.time()
            if self._journal_fail_count > 50 and (now - self._journal_last_burst_log) > 60:
                logging.error(
                    f"[JOURNAL] circuit-stressed: {self._journal_fail_count} "
                    f"consecutive failures (last error: {e}) — investigate disk "
                    f"space / DB lock / schema. Retrying continues."
                )
                self._journal_last_burst_log = now

    async def _journal_set_state_safe(self, chat_id, msg_id, state,
                                      error=None, mark_deleted=False):
        if not self._journal_enabled() or chat_id is None:
            return
        try:
            await self.prod_db.journal_set_state(
                chat_id, msg_id, state, error=error, mark_deleted=mark_deleted)
        except Exception as e:
            logging.warning(f"[JOURNAL] set_state FAILED: ({chat_id},{msg_id}) {e}")

    async def _journal_mark_deleted_safe(self, chat_id, msg_id):
        if chat_id is None or not self._journal_enabled():
            return
        try:
            await self.prod_db.journal_mark_deleted(chat_id, msg_id)
        except Exception as e:
            logging.warning(f"[JOURNAL] mark_deleted FAILED: ({chat_id},{msg_id}) {e}")

    # === LINK RING BUFFER helpers (PR-1) ===
    async def _link_ring_put(self, chat_id, msg_id, normalized_links: List[str]) -> None:
        """يخزّن الروابط المُستخرَجة (normalized) في LRB فوراً.
        لا ينتظر metadata. لا يستخدم API. لا يكسر الـpipeline عند الفشل.
        [PR-LRB-TTL] يخزّن received_at في _link_ring_ts لتفعيل TTL eviction
        الفعلي (سابقًا كان الـeviction يعتمد على الحجم فقط، فالروابط القديمة
        كانت تبقى حتى تُدفع بروابط جديدة — الآن يطردها _link_ring_evict
        بعد تجاوز TTL 300s حتى لو لم يصل الحد)."""
        if not normalized_links or chat_id is None or msg_id is None:
            return
        key = (int(chat_id), int(msg_id))
        try:
            async with self._link_ring_lock:
                # [PR-LRB-TTL] نظّف بشكل دفاعي الـ_link_ring_ts (لو موجود)
                ts_dict = getattr(self, '_link_ring_ts', None)
                # حماية من نمو الذاكرة: لو تجاوزنا الحد، اطرد أقدم 10% دفعة واحدة
                if len(self._link_ring) >= self._link_ring_cap:
                    # اطرد 10% الأقدم (بترتيب الإدراج — أبسط وأسرع)
                    drop_n = max(1, self._link_ring_cap // 10)
                    for k in list(self._link_ring.keys())[:drop_n]:
                        self._link_ring.pop(k, None)
                        if ts_dict is not None:
                            ts_dict.pop(k, None)  # [PR-LRB-TTL] نظّف الـtimestamps
                    self._link_ring_evicted += drop_n
                # [PR-LRB-TTL] lazy TTL sweep: لو عندنا > 1000 entry،
                # اطرد المنتهي صلاحية أولاً (تجنّب إضاعة الـcap على entries قديمة)
                if ts_dict is not None and len(ts_dict) > 1000:
                    cutoff = time.time() - self._link_ring_ttl
                    expired = [k for k, ts in ts_dict.items() if ts < cutoff]
                    for k in expired[:500]:  # cap per-put work
                        self._link_ring.pop(k, None)
                        ts_dict.pop(k, None)
                    self._link_ring_ttl_evicted += len(expired[:500])
                self._link_ring[key] = list(normalized_links)
                if ts_dict is not None:
                    ts_dict[key] = time.time()  # [PR-LRB-TTL] stamp
        except Exception as e:
            logging.debug(f"[LRB] put error ({chat_id},{msg_id}): {e}")

    async def _link_ring_pop(self, chat_id, msg_id) -> List[str]:
        """يسحب ويحذف روابط رسالة من LRB. يعيد [] لو غير موجود.
        يدعم البحث عبر كل الشاتات لو chat_id=None (لأحداث الحذف بدون chat_id).
        [PR-LRB-TTL] يحذف الـtimestamp المرتبط بالـkey أيضًا (حتى لا يبقى شبحًا)."""
        try:
            async with self._link_ring_lock:
                ts_dict = getattr(self, '_link_ring_ts', None)  # defensive
                if chat_id is not None:
                    key = (int(chat_id), int(msg_id))
                    if ts_dict is not None:
                        ts_dict.pop(key, None)  # [PR-LRB-TTL] نظّف
                    return self._link_ring.pop(key, [])
                # chat_id مجهول — ابحث بالـmsg_id فقط (نادر، قد يخلط بين شاتات)
                for key, val in list(self._link_ring.items()):
                    if key[1] == int(msg_id):
                        self._link_ring.pop(key, None)
                        if ts_dict is not None:
                            ts_dict.pop(key, None)  # [PR-LRB-TTL] نظّف
                        return val
        except Exception as e:
            logging.debug(f"[LRB] pop error ({chat_id},{msg_id}): {e}")
        return []

    async def _link_ring_evict(self) -> int:
        """يطرد المدخلات المنتهية الصلاحية (> TTL). يعيد عدد المُطرَد.
        [PR-LRB-TTL] eviction حقيقي بالـTTL: نطرد كل entry عمرها >
        _link_ring_ttl (300s افتراضيًا). سابقًا كان الـeviction يعتمد
        على الحجم فقط (size-based) — فالروابط القديمة كانت تبقى أبدًا
        حتى يصل الـcap، مما يعطّل مبرّر الـTTL المُعلَن. الآن TTL فعلي.
        نسخّن أيضًا size-based eviction كـfallback لو فشل الـtimestamp لـ
        بعض الـkeys (defensive)."""
        now = time.time()
        cutoff = now - self._link_ring_ttl
        evicted = 0
        try:
            async with self._link_ring_lock:
                ts_dict = getattr(self, '_link_ring_ts', None)  # defensive
                # [PR-LRB-TTL] المرحلة 1: طرد المنتهي بالـTTL (الأولوية)
                if ts_dict is not None:
                    expired = [k for k, ts in ts_dict.items() if ts < cutoff]
                    for k in expired:
                        self._link_ring.pop(k, None)
                        ts_dict.pop(k, None)
                    evicted = len(expired)
                    self._link_ring_ttl_evicted += evicted
                # المرحلة 2: size-based fallback (لو فشل TTL لسبب ما)
                if len(self._link_ring) > int(self._link_ring_cap * 0.9):
                    drop_n = len(self._link_ring) // 10
                    for k in list(self._link_ring.keys())[:drop_n]:
                        self._link_ring.pop(k, None)
                        if ts_dict is not None:
                            ts_dict.pop(k, None)
                    evicted += drop_n
                    self._link_ring_evicted += drop_n
        except Exception as e:
            logging.debug(f"[LRB] evict error: {e}")
        return evicted

    async def _record_delete_miss(self, chat_id, msg_id, source_phone):
        """يسجّل حذف رسالة لم نرَ NewMessage لها أبدًا — دليل على فجوة تسليم أحداث.
        سجل INFO مقيّد (مرة/دقيقة لكل شات) + صف delete_miss في journal."""
        key = chat_id if chat_id is not None else 0
        now = time.time()
        self._delete_miss_count[key] = self._delete_miss_count.get(key, 0) + 1
        if now - self._delete_miss_log_ts.get(key, 0) > 60:
            # [FIX-LOG-NOISE] Downgraded WARNING→INFO: a delete seen without
            # a prior NewMessage is expected Telegram behavior (bot added to
            # chat after the message, or a delivery gap during restart). The
            # forensic data is still written to the journal (state=delete_miss)
            # and counted in metrics.delete_miss_total — only the log level
            # changes to stop spamming WARNING on a known-expected condition.
            logging.info(
                f"[DELETE-HANDLER] DELETE-MISS msg_id={msg_id} chat_id={chat_id} "
                f"— NewMessage never received "
                f"({self._delete_miss_count[key]} miss(es) in window) "
                f"(delete seen by {source_phone})"
            )
            self._delete_miss_log_ts[key] = now
            self._delete_miss_count[key] = 0
        # [PR-2/observability] مقياس delete_miss_total
        try: await self.metrics.record_delete_miss()
        except Exception: pass
        if chat_id is not None and self._journal_enabled():
            try:
                await self.prod_db.journal_message({
                    'chat_id': chat_id, 'msg_id': msg_id, 'raw_text': None,
                    'source_phone': source_phone, 'received_at': time.time(),
                    'state': 'delete_miss',
                })
            except Exception as e:
                logging.warning(f"[JOURNAL] delete_miss write FAILED: {e}")

    async def _rescue_enqueue_links(self, links, raw_text, group_name, sender_name,
                                    chat_username, chat_link_type, orig_chat_id,
                                    orig_source_phone, msg_id,
                                    pipeline_tag='DELETE-HANDLER') -> bool:
        """إنقاذ روابط رسالة (مشترك بين Delete Handler و Journal Recovery و Reconcile).
        يسجّل المجموعة + فحص blacklist + enqueue + set_group_state.
        Returns True لو أُضيف رابط جديد واحد على الأقل."""
        any_new = False
        try:
            is_new = await self.prod_db.add_monitored_chat(
                chat_id=orig_chat_id,
                chat_title=group_name,
                username=chat_username,
                link_type=chat_link_type,
                monitored_by=orig_source_phone,
            )
            if is_new:
                logging.info(
                    f"[{pipeline_tag}] ✅ New chat: '{group_name[:40]}' "
                    f"(id={orig_chat_id}, by={orig_source_phone})"
                )
        except Exception as e:
            logging.debug(f"[{pipeline_tag}] add_monitored error: {e}")

        for link_info in links:
            link_data = {
                **link_info,
                'group_name': group_name,
                'sender_name': sender_name,
                'sender_contact': extract_sender_contact(raw_text),
                'source_phone': orig_source_phone,
                'message_text': raw_text,
                'message_link': f"https://t.me/c/{str(orig_chat_id).replace('-100', '')}/{msg_id}" if orig_chat_id else None,
            }

            # Blacklist
            link_raw = link_info['raw'].lower()
            username_raw = (link_info.get('username') or '').lower()
            full_text_check = f"{raw_text} {link_raw} {username_raw}".lower()
            is_bad, bad_reason = GulfFilter.is_blacklisted(
                full_text_check, username_raw, link_info['raw'], group_name
            )
            if is_bad:
                logging.info(
                    f"[{pipeline_tag}] 🚫 BLACKLISTED: {link_info['raw'][:50]} ({bad_reason})"
                )
                await self.metrics.record_skip(f'blacklist_{bad_reason}')
                continue

            is_new = await self.prod_db.enqueue_link(link_data)
            if is_new:
                any_new = True
                await self.prod_db.set_group_state(
                    link_info['normalized'], GroupState.DISCOVERED,
                    link_info['raw'], group_name)
                logging.info(
                    f"[{pipeline_tag}] ✅ RESCUED & enqueued: {link_info['raw'][:60]} "
                    f"(would have been LOST without journal/cache)"
                )
            else:
                await self.metrics.record_duplicate()
                logging.info(f"[{pipeline_tag}] ⏭️ Duplicate: {link_info['normalized'][:60]}")
        return any_new

    def _spawn_reconcile(self, chat_id: int, hint_phone: str = None):
        """يطلق reconcile خلفي للشات (مع حماية من التكرار المتزامن)."""
        if chat_id in self._reconcile_inflight:
            return
        self._reconcile_inflight.add(chat_id)

        async def _runner():
            try:
                await self._reconcile_chat_after_delete_miss(chat_id, hint_phone)
            finally:
                self._reconcile_inflight.discard(chat_id)

        asyncio.create_task(_runner())

    async def _reconcile_chat_after_delete_miss(self, chat_id: int, hint_phone: str = None):
        """بعد DELETE-MISS: اسحب آخر 15 رسالة من الشات لالتقاط أي رسائل أخرى
        فاتتنا (فجوة أحداث). الرسالة المحذوفة نفسها لا يمكن استرجاعها
        (Telegram حذفها نهائيًا) — لكن الرسائل الأخوات الفائتة يمكن التقاطها."""
        reader = hint_phone
        client = self.user_clients.get(reader) if reader else None
        used_registry = False
        if not (client and client.is_connected()):
            reader = None
            client = None
            if self.source_registry:
                reader = self.source_registry.get_reader(chat_id)
                used_registry = reader is not None
                if reader:
                    client = self.user_clients.get(reader)
        if not (client and client.is_connected()):
            if used_registry and reader and self.source_registry:
                self.source_registry.release_load(reader)
            logging.debug(f"[RECONCILE] no available reader for chat={chat_id}")
            return
        try:
            messages = await client.get_messages(chat_id, limit=15)
            recovered = 0
            for msg in messages:
                if not msg or not msg.raw_text or msg.out:
                    continue
                async with self._msg_cache_lock:
                    if (chat_id, msg.id) in self._msg_cache:
                        continue
                # [N02] Write the journal row FIRST (state='pending') so a crash
                # anywhere between here and set_state('rescued') below leaves a
                # recoverable row for journal_recovery to re-rescue. The previous
                # order (claim → journal_write) lost the message if the process
                # died between the claim and the journal write.
                chat_title_early = ''
                try:
                    chat_obj_early = getattr(msg, 'chat', None)
                    if chat_obj_early is not None and getattr(chat_obj_early, 'title', None):
                        chat_title_early = chat_obj_early.title
                except Exception:
                    pass
                chat_title_early = chat_title_early or f"chat_{chat_id}"
                sender_name_early = (self._get_sender_name(msg.sender)
                                     if getattr(msg, 'sender', None) else 'Unknown')
                await self._journal_write(chat_id, msg.id, msg.raw_text, reader,
                                          chat_title=chat_title_early,
                                          sender_name=sender_name_early,
                                          state='pending')
                claim_token = None
                if self.message_claim:
                    claim_token = await self.message_claim.claim(chat_id, msg.id, 'reconcile', reader)
                    if claim_token is None:
                        # [N03] Lost the claim race — another worker (NewMessage /
                        # polling / journal_recovery) is processing. Leave the
                        # journal row as 'pending' (the winner will overwrite it).
                        # Do NOT set 'dup_claim' — that would overwrite the winner's
                        # 'pending' state and hide the row from journal_recovery
                        # (journal_pending_older_than filters state='pending').
                        continue
                links = LinkNormalizer.extract_links(msg.raw_text)
                if not links:
                    if self.message_claim:
                        await self.message_claim.mark_processed(chat_id, msg.id, claim_token)
                    await self._journal_set_state_safe(chat_id, msg.id, 'no_links')
                    continue
                chat_title = chat_title_early
                logging.info(
                    f"[RECONCILE] 📨🔗 Recovered missed message msg_id={msg.id} "
                    f"from '{chat_title[:30]}' ({len(links)} links)"
                )
                sender_name = sender_name_early
                await self._rescue_enqueue_links(
                    links, msg.raw_text, chat_title, sender_name,
                    '', 'group', chat_id, reader, msg.id,
                    pipeline_tag='RECONCILE')
                if self.message_claim:
                    await self.message_claim.mark_processed(chat_id, msg.id, claim_token)
                # [N02] 'rescued' (was 'processed') — semantically accurate:
                # reconcile rescues a message that was MISSED by NewMessage.
                await self._journal_set_state_safe(chat_id, msg.id, 'rescued')
                recovered += 1
            # حدّث آخر msg_id مشاهد (يمنع polling من إعادة المعالجة)
            if messages:
                new_max = max(m.id for m in messages if m)
                async with self._polling_lock:
                    if new_max > self._polling_state.get(chat_id, 0):
                        self._polling_state[chat_id] = new_max
            if recovered:
                logging.info(f"[RECONCILE] chat={chat_id}: recovered {recovered} missed message(s)")
        except FloodWaitError as e:
            # [N08] Register the FloodWait with floodwait_mgr before
            # propagating — reconcile runs as a background _spawn_reconcile
            # task whose broad except Exception (above) previously swallowed
            # FloodWait silently, so the joiner would re-pick the same phone
            # moments later and double the next FloodWait penalty. We re-raise
            # after registering so the broad except still logs it as debug.
            try:
                if reader and getattr(self, 'floodwait_mgr', None):
                    await self.floodwait_mgr.block(reader, e.seconds)
            except Exception as _fwe:
                logging.debug(f"[RECONCILE] floodwait_mgr.block failed: {_fwe}")
            logging.warning(
                f"[RECONCILE] FloodWait {e.seconds}s chat={chat_id} ({reader}) — registered + sleeping")
            await asyncio.sleep(min(e.seconds, 30))
        except Exception as e:
            logging.debug(f"[RECONCILE] chat={chat_id} error: {e}")
        finally:
            if used_registry and reader and self.source_registry:
                self.source_registry.release_load(reader)

    async def _journal_recovery(self):
        """استرجاع الانهيار: صفوف journal بحالة pending عمرها > 120 ثانية تعني
        أن النظام انهار/أُعيد تشغيله بعد كتابة الرسالة وقبل اكتمال معالجتها.
        نعيد معالجتها — MessageClaim يمنع أي تكرار."""
        # [B05] RECURRING loop — was fire-once at startup. A single startup sweep
        # misses pending rows created AFTER startup or left pending by a mid-flight
        # crash between sweeps. Re-sweep every 60s; MessageClaim prevents duplicate
        # processing across cycles.
        await asyncio.sleep(45)  # انتظر اكتمال الإقلاع (مرة واحدة عند البدء)
        while self._running:
            try:
                rows = await self.prod_db.journal_pending_older_than(120)
                if not rows:
                    logging.info("[JOURNAL-RECOVERY] no stale pending rows — clean")
                else:
                    logging.warning(f"[JOURNAL-RECOVERY] {len(rows)} stale pending row(s) — reprocessing")
                    recovered = 0
                    for row in rows:
                        try:
                            raw_text = row.get('raw_text') or ''
                            if not raw_text:
                                continue
                            orig_chat_id = row.get('chat_id')
                            msg_id = row.get('msg_id')
                            links = LinkNormalizer.extract_links(raw_text)
                            if not links:
                                await self._journal_set_state_safe(orig_chat_id, msg_id, 'no_links')
                                continue
                            claim_token = None
                            if self.message_claim:
                                claim_token = await self.message_claim.claim(
                                    orig_chat_id, msg_id, 'journal_recovery',
                                    row.get('source_phone') or '')
                                if claim_token is None:
                                    await self._journal_set_state_safe(orig_chat_id, msg_id, 'dup_claim')
                                    continue
                            group_name = row.get('chat_title') or f"chat_{orig_chat_id}"
                            await self._rescue_enqueue_links(
                                links, raw_text, group_name,
                                row.get('sender_name') or 'Unknown',
                                row.get('chat_username') or '',
                                row.get('chat_link_type') or 'telegram',
                                orig_chat_id, row.get('source_phone') or '', msg_id,
                                pipeline_tag='JOURNAL-RECOVERY')
                            if self.message_claim and claim_token:
                                await self.message_claim.mark_processed(orig_chat_id, msg_id, claim_token)
                            await self._journal_set_state_safe(orig_chat_id, msg_id, 'processed')
                            recovered += 1
                        except Exception as e:
                            logging.error(f"[JOURNAL-RECOVERY] row error: {e}")
                    logging.info(f"[JOURNAL-RECOVERY] done — {recovered} row(s) reprocessed")
            except Exception as e:
                logging.error(f"[JOURNAL-RECOVERY] fatal: {e}", exc_info=True)
            # [B05] per-cycle sleep before the next sweep (was fire-once → return)
            await asyncio.sleep(60)

    # ===================================================================
    # [B07] Supervisor loop — recreates dead critical background tasks
    # ===================================================================
    async def _supervisor_loop(self):
        """60s supervisor: checks critical background tasks and recreates any
        that are done/cancelled. A bug, an unhandled exception, or an OOM kill
        can silently terminate a worker task; without a supervisor it stays dead
        until a full process restart (which on Render free can be hours/days).

        Critical tasks watched (Task 9a hardened the original 5 → 9, REQAUDIT-2
        added 10, REQAUDIT-3 added 11 + 12):
          1. polling_scheduler        (heartbeat — also has its own 30s watchdog)
          2. journal_recovery         (crash-safe message rescue)
          3. journal_snapshot_loop     (Supabase durability mirror)
          4. ai_drainer_worker         (only when AI_DRAIN_ENABLED=true)
          5. joiner_worker             (link processor)
          6. claim_cleanup_task        (hourly processed_messages purge —
               was NOT supervised before Task 9a; a dead task let the table
               grow unbounded)
          7. msg_cache_cleanup_task    (30s TTL purge — was NOT supervised)
          8. priority_scorer_task      (member_count fetch — was NOT supervised)
          9. polling_watchdog_task     (the 30s scheduler watchdog itself —
               if THIS dies, only the 60s supervisor watches polling, so
               we self-heal the watchdog too)
         10. pending_approval_recheck  (REQAUDIT-2 — flips PENDING→JOINED on
               admin approval; without this, PENDING_APPROVAL is terminal)
         11. joiner_fleet_health       (REQAUDIT-3 — 60s fleet health snapshot
               + owner alert when ALL joiners unavailable >5min; without
               this, /ready stops surfacing fleet state and the operator
               gets no push notification when the fleet goes down)
         12. per-account user_client loops (REQAUDIT-3 — restarts dead
               _run_user_client tasks; without this, a phone that hit an
               unhandled exception stays `not_connected` until restart)

        Logs WARNING [SUPERVISOR] restarted <name> on each restart.

        Note (Task 9a): the polling_scheduler relaunch is wrapped in
        self._scheduler_relaunch_lock so the supervisor + the 30s
        polling_watchdog can't BOTH relaunch it concurrently (was a
        duplicate-instance risk on the 30s/60s boundary).
        Note (Task 9a): ai_drainer restart is gated on AI_DRAIN_ENABLED —
        otherwise the worker self-returns at line ~3882 and the supervisor
        would emit a noisy "restarted" warning every 60s for a worker
        that intentionally exited (default deployment = AI_DRAIN disabled).
        """
        await asyncio.sleep(60)  # let startup settle before first check
        logging.info("[SUPERVISOR] started — 60s cycle, watching 12 critical task groups (10 loops + per-account user_client loops + joiner_fleet_health)")
        # [Task 9a / W1] cache the AI_DRAIN_ENABLED decision at supervisor
        # startup so a runtime env flip doesn't trigger a storm of restarts.
        # If the operator wants to enable the drainer, they restart the
        # process — same pattern as every other env-gated worker.
        ai_drain_on = os.getenv('AI_DRAIN_ENABLED', 'false').lower() in ('true', '1', 'yes')
        while self._running:
            try:
                # 1. PollingScheduler — lock-protected to prevent the 30s
                # polling_watchdog from racing us to relaunch the same task.
                # [Task 9a] getattr-or-fallback so tests with a stub self
                # (no _scheduler_relaunch_lock attr) don't AttributeError;
                # the fallback lock doesn't serialize cross-task in tests
                # but real Monitor instances set the attr in __init__.
                _relaunch_lock = getattr(self, '_scheduler_relaunch_lock', None) or asyncio.Lock()
                async with _relaunch_lock:
                    if (getattr(self, '_polling_scheduler_task', None) is None
                            or self._polling_scheduler_task.done()):
                        if getattr(self, 'polling_scheduler', None) and self._running:
                            self._polling_scheduler_task = asyncio.create_task(
                                self.polling_scheduler.run())
                            logging.warning("[SUPERVISOR] restarted polling_scheduler")
                # 2. Journal recovery
                if (getattr(self, '_journal_recovery_task', None) is None
                        or self._journal_recovery_task.done()) and self._running:
                    self._journal_recovery_task = asyncio.create_task(
                        self._journal_recovery())
                    logging.warning("[SUPERVISOR] restarted journal_recovery")
                # 3. Journal snapshot (persistence) — guarded: 8b adds the method
                if hasattr(self, '_journal_snapshot_loop') and self._running:
                    if (getattr(self, '_journal_snapshot_task', None) is None
                            or self._journal_snapshot_task.done()):
                        self._journal_snapshot_task = asyncio.create_task(
                            self._journal_snapshot_loop())
                        logging.warning("[SUPERVISOR] restarted journal_snapshot")
                # 4. AI drainer — guarded: 8b adds the method.
                # [Task 9a / W1] ONLY relaunch when AI_DRAIN_ENABLED is true;
                # otherwise the worker exits immediately at startup and the
                # supervisor would emit a noisy "restarted" WARNING every
                # 60s for a worker that intentionally self-disabled. Default
                # deployment (AI_DRAIN_ENABLED unset) stays quiet.
                if ai_drain_on and hasattr(self, '_ai_drainer_worker') and self._running:
                    if (getattr(self, '_ai_drainer_task', None) is None
                            or self._ai_drainer_task.done()):
                        self._ai_drainer_task = asyncio.create_task(
                            self._ai_drainer_worker())
                        logging.warning("[SUPERVISOR] restarted ai_drainer")
                # 5. Joiner
                if (getattr(self, '_joiner_task', None) is None
                        or self._joiner_task.done()) and self._running:
                    self._joiner_task = asyncio.create_task(self._joiner_worker())
                    logging.warning("[SUPERVISOR] restarted joiner")
                # 6. [Task 9a / W2] Claim cleanup loop (hourly) — was NOT
                # supervised; a single unhandled exception in the loop body
                # (rare but possible) left the processed_messages table
                # growing unbounded. Now self-heals.
                if (getattr(self, '_claim_cleanup_task', None) is None
                        or self._claim_cleanup_task.done()) and self._running:
                    self._claim_cleanup_task = asyncio.create_task(
                        self._cleanup_processed_messages_loop())
                    logging.warning("[SUPERVISOR] restarted claim_cleanup")
                # 7. [Task 9a / W2] Message cache cleanup (30s TTL purge)
                # — was NOT supervised; a dead task let _msg_cache grow
                # unbounded (memory leak). Now self-heals.
                if (getattr(self, '_msg_cache_cleanup_task', None) is None
                        or self._msg_cache_cleanup_task.done()) and self._running:
                    self._msg_cache_cleanup_task = asyncio.create_task(
                        self._msg_cache_cleanup())
                    logging.warning("[SUPERVISOR] restarted msg_cache_cleanup")
                # 8. [Task 9a / W2] Priority scorer (member_count fetch) —
                # was NOT supervised; a dead task meant no priority updates
                # (links still process, but with priority=3). Now self-heals.
                if (getattr(self, '_priority_scorer_task', None) is None
                        or self._priority_scorer_task.done()) and self._running:
                    self._priority_scorer_task = asyncio.create_task(
                        self._priority_scorer())
                    logging.warning("[SUPERVISOR] restarted priority_scorer")
                # 9. [Task 9a / W2] Polling watchdog (the 30s scheduler
                # watchdog itself) — was NOT supervised; if THIS died, only
                # the 60s supervisor watched polling (twice the dead-time).
                # Self-heal so the dedicated 30s watchdog stays alive.
                if (getattr(self, '_polling_watchdog_task', None) is None
                        or self._polling_watchdog_task.done()) and self._running:
                    self._polling_watchdog_task = asyncio.create_task(
                        self._polling_watchdog_loop())
                    logging.warning("[SUPERVISOR] restarted polling_watchdog")
                # 10. [REQAUDIT-2] Pending-approval self-healing recheck loop.
                # If this dies, PENDING_APPROVAL groups never get re-checked
                # for admin approval → they'd stay PENDING forever. The
                # supervisor self-heals it so the lifecycle completes.
                if (getattr(self, '_pending_approval_recheck_task', None) is None
                        or self._pending_approval_recheck_task.done()) and self._running:
                    self._pending_approval_recheck_task = asyncio.create_task(
                        self._pending_approval_recheck_loop())
                    logging.warning("[SUPERVISOR] restarted pending_approval_recheck")
                # 11. [REQAUDIT-3] Joiner Fleet Health monitor (60s cycle).
                # If this dies, the operator gets no alert when the whole
                # joiner fleet goes down (FloodWait/disconnect/safety-limit),
                # /ready + /api/joined_groups stop surfacing fleet state,
                # and the scheduler backoff gate stops reading fresh data.
                if (getattr(self, '_joiner_fleet_health_task', None) is None
                        or self._joiner_fleet_health_task.done()) and self._running:
                    self._joiner_fleet_health_task = asyncio.create_task(
                        self._joiner_fleet_health_loop())
                    logging.warning("[SUPERVISOR] restarted joiner_fleet_health")
                # 11b. [QUIET-DIGEST] Quiet-source watch loop (30-min cycle).
                # If this dies, the operator stops getting alerts when an
                # important link source goes quiet — capture drops would
                # again be noticed only days later on the dashboard.
                if (getattr(self, '_quiet_source_task', None) is None
                        or self._quiet_source_task.done()) and self._running:
                    self._quiet_source_task = asyncio.create_task(
                        self._quiet_source_watch_loop())
                    logging.warning("[SUPERVISOR] restarted quiet_source_watch")
                # 12. [REQAUDIT-3] Per-account user_client loops. Previously
                # these were fire-and-forget in self._user_tasks and NOT
                # supervised — a terminal `return` on not_authorized (now
                # fixed to be non-terminal) OR any unhandled exception that
                # escaped the inner try/except left the phone permanently
                # `not_connected` until a full process restart. Now we
                # self-heal: for each dead task whose phone is still in the
                # watchers DB, re-fetch the watcher and restart the loop.
                # (The non-terminal refactor in _run_user_client means the
                # loop should rarely die — but if it does, we recover.)
                try:
                    watchers_now = await self.db.get_active_watchers()
                    live_phones = {w['phone'] for w in watchers_now}
                    for ph, t in list(self._user_tasks.items()):
                        if ph not in live_phones:
                            continue  # phone removed from DB — don't restart
                        if t is None or t.done():
                            w = next((x for x in watchers_now if x['phone'] == ph), None)
                            if w:
                                self._user_tasks[ph] = asyncio.create_task(
                                    self._run_user_client(w))
                                logging.warning(
                                    f"[SUPERVISOR] restarted user_client for {ph}")
                except Exception as e:
                    logging.error(f"[SUPERVISOR] user_tasks check error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[SUPERVISOR] error: {e}", exc_info=True)
            await asyncio.sleep(60)

    # ===================================================================
    # [REQAUDIT-3] Joiner Fleet Health monitor — 60s cycle
    # ===================================================================
    async def _joiner_fleet_health_loop(self):
        """60s cycle: computes a live snapshot of joiner-fleet health and
        writes it to self._fleet_health for /ready + /api/joined_groups +
        the _joiner_worker backoff gate to read.

        On detecting that ALL joiners are unavailable (connected_joiners
        == 0) for >5 minutes (300s), sends a ONE-TIME Telegram alert to
        OWNER_ID — previously the bot silently logged `METRIC Skipped
        link: no_joiner_*` forever and the operator only found out by
        manually checking logs.

        Availability is the union of:
          - client exists in self.user_clients
          - client.is_connected() returns True
          - phone is NOT in floodwait_mgr's blocked set
        A phone that hits safety_guard's hourly_limit is technically
        still "connected" but unavailable for joins — counted separately
        as safety_guard_blocked_joiners and NOT counted as connected.
        """
        await asyncio.sleep(60)  # let startup settle
        logging.info("[FLEET-HEALTH] started — 60s cycle")
        while self._running:
            try:
                watchers = []
                try:
                    watchers = await self.db.get_active_watchers()
                except Exception as e:
                    logging.warning(f"[FLEET-HEALTH] get_active_watchers failed: {e}")
                # [FIX-LOG-NOISE] Only count ENABLED joiners. A disabled
                # joiner (joiner_enabled=0) is intentionally out-of-fleet —
                # counting it as "connected" masked true fleet availability
                # and prevented the _joiner_worker backoff gate from firing,
                # causing perpetual wasteful cycles every 60s.
                joiner_phones = [w['phone'] for w in watchers
                                 if w.get('role', 'monitor') == 'joiner'
                                 and w.get('joiner_enabled', 1)]
                disabled_joiners = [w['phone'] for w in watchers
                                    if w.get('role', 'monitor') == 'joiner'
                                    and not w.get('joiner_enabled', 1)]

                connected = []
                floodwait_list = []
                disconnected = []
                safety_guard_blocked = 0

                for ph in joiner_phones:
                    client = self.user_clients.get(ph)
                    if not client or not client.is_connected():
                        disconnected.append(ph)
                        continue
                    # FloodWait check (DB-backed)
                    try:
                        is_blocked, wait = await self.floodwait_mgr.is_blocked(ph)
                    except Exception:
                        is_blocked, wait = False, 0
                    if is_blocked:
                        floodwait_list.append({'phone': ph, 'wait_s': int(wait)})
                        continue
                    # Safety guard hourly-limit check (mirrors _safety_guard's
                    # gate so the snapshot reflects real availability).
                    try:
                        hourly_joins = await self.prod_db.count_operations(ph, 'join', 3600)
                    except Exception:
                        hourly_joins = 0
                    if hourly_joins >= 5:
                        safety_guard_blocked += 1
                        continue
                    connected.append(ph)

                connected_count = len(connected)
                prev_snapshot = self._fleet_health
                prev_connected = prev_snapshot.get('connected_joiners', 0)
                prev_all_down_since = prev_snapshot.get('all_unavailable_since')

                # Update the snapshot atomically.
                self._fleet_health = {
                    'connected_joiners': connected_count,
                    'connected_joiner_phones': connected,
                    'floodwait_joiners': floodwait_list,
                    'disconnected_joiners': disconnected,
                    'safety_guard_blocked_joiners': safety_guard_blocked,
                    'disabled_joiners': disabled_joiners,
                    'disabled_joiners_count': len(disabled_joiners),
                    'all_unavailable_since': prev_all_down_since,
                    'fleet_down_alerted': prev_snapshot.get('fleet_down_alerted', False),
                }

                now = datetime.now()
                if connected_count == 0:
                    # Fleet is fully down.
                    if prev_all_down_since is None:
                        # Transition: just went down.
                        self._fleet_health['all_unavailable_since'] = now
                        logging.warning(
                            f"[FLEET-HEALTH] ALL joiners unavailable "
                            f"(floodwait={len(floodwait_list)}, "
                            f"disconnected={len(disconnected)}, "
                            f"safety_guard={safety_guard_blocked}) — "
                            f"alert timer started"
                        )
                    else:
                        down_seconds = (now - prev_all_down_since).total_seconds()
                        # Alert after 5 min (300s) of total fleet outage.
                        if (down_seconds >= 300
                                and not self._fleet_health.get('fleet_down_alerted', False)):
                            self._fleet_health['fleet_down_alerted'] = True
                            await self._send_fleet_down_alert(
                                floodwait_list, disconnected, safety_guard_blocked,
                                int(down_seconds))
                else:
                    # Fleet has at least one available joiner — reset.
                    if prev_all_down_since is not None or \
                            self._fleet_health.get('fleet_down_alerted', False):
                        logging.info(
                            f"[FLEET-HEALTH] fleet recovered — "
                            f"{connected_count} joiner(s) available"
                        )
                    self._fleet_health['all_unavailable_since'] = None
                    self._fleet_health['fleet_down_alerted'] = False
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[FLEET-HEALTH] error: {e}", exc_info=True)
            await asyncio.sleep(60)

    async def _send_fleet_down_alert(self, floodwait_list, disconnected,
                                     safety_guard_blocked, down_seconds):
        """[REQAUDIT-3] Push a Telegram alert to OWNER_ID when the whole
        joiner fleet has been unavailable for >5 min. Best-effort — if
        bot_client is None / not connected / OWNER_ID unset, just log."""
        oid = self.config.owner_id
        floodwait_str = ', '.join(
            f"{f['phone']} ({f['wait_s']//60}min)" for f in floodwait_list
        ) or 'none'
        disc_str = ', '.join(disconnected) or 'none'
        msg = (
            f"🚨 *JOINER FLEET DOWN*\n"
            f"Down for: {down_seconds//60} min\n"
            f"Connected: 0\n"
            f"FloodWait: {floodwait_str}\n"
            f"Disconnected: {disc_str}\n"
            f"Safety-guard blocked: {safety_guard_blocked}\n"
            f"Action: check sessions + clear FloodWait via /clear_floodwait"
        )
        logging.error(f"[FLEET-HEALTH] {msg.replace(chr(10), ' | ')}")
        if oid is None:
            return
        try:
            if self.bot_client and self.bot_client.is_connected():
                try:
                    await self.bot_client.send_message(oid, msg, parse_mode='Markdown')
                except Exception:
                    try:
                        await self.bot_client.send_message(oid, msg)
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"[FLEET-HEALTH] alert send failed: {e}")

    # ===================================================================
    # [QUIET-DIGEST] Quiet-source watch — link sources that went silent
    # ===================================================================
    async def _quiet_source_watch_loop(self):
        """[QUIET-DIGEST] Every _QUIET_SOURCE_CHECK_INTERVAL (30 min),
        aggregate the last 30 days of per-group link activity (shared
        helper — same data as /api/top_groups) and push Telegram alerts
        to OWNER_ID when an important source goes quiet:

          * FIRST cycle after startup  → one consolidated digest of all
            currently-quiet sources (restart-safe, no N-message burst),
            then the state is seeded so only NEW events alert.
          * NEW quiet source           → immediate single alert.
          * still quiet                → re-alert every 7 days.
          * previously-alerted source
            produces links again       → short recovery note.

        Best-effort: OWNER_ID unset or bot disconnected → log-only
        (same posture as _send_fleet_down_alert). Supabase unavailable →
        skip the cycle silently (next cycle retries).
        """
        # Let the bot connect + first links land before judging silence.
        await asyncio.sleep(120)
        first_cycle = True
        while self._running:
            try:
                groups = await _fetch_window_group_activity(
                    self.db, _QUIET_SOURCE_WINDOW_DAYS)
                if groups:
                    today = datetime.utcnow().date()
                    new_alerts, re_alerts, seed, recovered = \
                        _compute_quiet_alerts(
                            groups, today, self._quiet_alerted)

                    if first_cycle:
                        # Startup digest: everything currently quiet in
                        # ONE message; seed state (no individual alerts).
                        digest = sorted(
                            new_alerts + re_alerts + seed,
                            key=lambda t: t[1], reverse=True)[:15]
                        if digest:
                            await self._send_quiet_source_digest(digest)
                        for gname, _v, _ls, _sd in (new_alerts
                                                    + re_alerts + seed):
                            self._quiet_alerted[gname] = {
                                "day": today.isoformat(),
                                "volume": _v,
                            }
                        # Recoveries can't exist on the first cycle
                        # (state was empty) — nothing to do for them.
                        first_cycle = False
                    else:
                        for gname, volume, last_seen, sd in new_alerts:
                            await self._send_quiet_source_alert(
                                gname, volume, last_seen, sd)
                            self._quiet_alerted[gname] = {
                                "day": today.isoformat(),
                                "volume": volume,
                            }
                        for gname, volume, last_seen, sd in re_alerts:
                            await self._send_quiet_source_alert(
                                gname, volume, last_seen, sd,
                                is_realert=True)
                            self._quiet_alerted[gname] = {
                                "day": today.isoformat(),
                                "volume": volume,
                            }
                        for gname, volume in recovered:
                            await self._send_quiet_source_recovery(
                                gname, volume)
                            self._quiet_alerted.pop(gname, None)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[QUIET-SOURCE] error: {e}", exc_info=True)
            await asyncio.sleep(_QUIET_SOURCE_CHECK_INTERVAL)

    async def _send_quiet_source_alert(self, group_name, volume, last_seen,
                                       silence_days, is_realert=False):
        """[QUIET-DIGEST] Push a single quiet-source alert to OWNER_ID.
        Best-effort — log always, message only if reachable."""
        title = "*SOURCE STILL QUIET*" if is_realert else "*SOURCE WENT QUIET*"
        emoji = "🔇" if is_realert else "⚠️"
        msg = (
            f"{emoji} {title}\n"
            f"Group: {group_name}\n"
            f"Volume (30d): {volume} links\n"
            f"Last link: {last_seen} ({silence_days} days ago)\n"
            f"Dashboard → المصادر → drill into the group for its timeline"
        )
        logging.warning(f"[QUIET-SOURCE] {msg.replace(chr(10), ' | ')}")
        oid = self.config.owner_id
        if oid is None:
            return
        try:
            if self.bot_client and self.bot_client.is_connected():
                try:
                    await self.bot_client.send_message(
                        oid, msg, parse_mode='Markdown')
                except Exception:
                    try:
                        await self.bot_client.send_message(oid, msg)
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"[QUIET-SOURCE] alert send failed: {e}")

    async def _send_quiet_source_digest(self, digest_entries):
        """[QUIET-DIGEST] ONE consolidated startup digest: the currently-
        quiet important sources, ranked by 30d volume (top 15)."""
        lines = [
            f"{i}. {name} — {vol} links, silent {sd}d"
            for i, (name, vol, _ls, sd) in enumerate(digest_entries, 1)
        ]
        msg = (
            f"📴 *QUIET SOURCES DIGEST*\n"
            f"{len(digest_entries)} important source(s) produced no links "
            f"for 2+ days:\n" + "\n".join(lines) +
            f"\n(New quiet-source alerts will follow automatically)"
        )
        logging.warning(f"[QUIET-SOURCE] {msg.replace(chr(10), ' | ')}")
        oid = self.config.owner_id
        if oid is None:
            return
        try:
            if self.bot_client and self.bot_client.is_connected():
                try:
                    await self.bot_client.send_message(
                        oid, msg, parse_mode='Markdown')
                except Exception:
                    try:
                        await self.bot_client.send_message(oid, msg)
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"[QUIET-SOURCE] digest send failed: {e}")

    async def _send_quiet_source_recovery(self, group_name, volume):
        """[QUIET-DIGEST] Short recovery note — a previously-alerted
        source produced links again."""
        msg = (
            f"✅ *SOURCE ACTIVE AGAIN*\n"
            f"Group: {group_name}\n"
            f"30d volume: {volume} links"
        )
        logging.info(f"[QUIET-SOURCE] {msg.replace(chr(10), ' | ')}")
        oid = self.config.owner_id
        if oid is None:
            return
        try:
            if self.bot_client and self.bot_client.is_connected():
                try:
                    await self.bot_client.send_message(
                        oid, msg, parse_mode='Markdown')
                except Exception:
                    try:
                        await self.bot_client.send_message(oid, msg)
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"[QUIET-SOURCE] recovery send failed: {e}")

    # ===================================================================
    # [L03] Polling-watchdog — dedicated, more frequent scheduler restart
    # ===================================================================
    async def _polling_watchdog_loop(self):
        """30s watchdog: checks ONLY the PollingScheduler task. Distinct from
        _supervisor_loop (which also checks it, but at 60s and bundled with 4
        others). Polling is the system's heartbeat — a dead scheduler means NO
        new links are discovered — so it gets its own faster watchdog. Logs
        WARNING [POLLING-WATCHDOG] scheduler was dead — restarted.

        [Task 9a / W3] Relaunch is wrapped in self._scheduler_relaunch_lock so
        the supervisor (60s) and this watchdog (30s) can't BOTH detect the
        scheduler as dead and BOTH call asyncio.create_task — that race
        leaked the first task reference (fire-and-forget) and produced two
        concurrent PollingScheduler instances doubling BATCH_SIZE.
        """
        await asyncio.sleep(30)
        logging.info("[POLLING-WATCHDOG] started — 30s cycle, scheduler-only")
        while self._running:
            try:
                # [Task 9a / W3] Lock-protected relaunch — same lock as the
                # supervisor uses, so the two paths serialize. getattr-or-
                # fallback so test stubs without the attr don't crash.
                _relaunch_lock = getattr(self, '_scheduler_relaunch_lock', None) or asyncio.Lock()
                async with _relaunch_lock:
                    t = getattr(self, '_polling_scheduler_task', None)
                    if (t is None or t.done()) and getattr(self, 'polling_scheduler', None) and self._running:
                        self._polling_scheduler_task = asyncio.create_task(
                            self.polling_scheduler.run())
                        logging.warning("[POLLING-WATCHDOG] scheduler was dead — restarted")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[POLLING-WATCHDOG] error: {e}", exc_info=True)
            await asyncio.sleep(30)

    # ===================================================================
    # [REQAUDIT-2] Pending-approval self-healing recheck loop
    # ===================================================================
    async def _pending_approval_recheck_loop(self):
        """[REQAUDIT-2] Self-healing: every N seconds (default 30 min, env
        PENDING_RECHECK_INTERVAL_S), for each group in state PENDING_APPROVAL,
        check whether the group admin has approved the join request. If yes,
        transition state to JOINED. This prevents groups from being stuck in
        PENDING_APPROVAL forever (the admin may approve minutes/hours/days
        later — without this loop the operator would have to manually re-run
        /scan or /requeue to discover the approval).

        Detection method:
          - Private invite hash (t.me/+hash, t.me/joinchat/hash):
              messages.checkChatInvite(hash) returns:
                * ChatInviteAlready  → the joiner is now a member → JOINED
                * ChatInvite          → request still pending → stay
          - Username link (t.me/username):
              get_entity(username) → GetParticipantRequest(channel, "me"):
                * success            → JOINED
                * UserNotParticipant → still pending

        Uses the SAME joiner account that sent the original request
        (group_states.joined_by) so the membership check is authoritative.

        Rate-limited: 5s between each check (well under get_entity 30/min
        and import_invite 5/hour caps). Bounded: max 50 groups per cycle
        (oldest first) so the loop can't monopolize the API. Errors per-row
        are isolated (one bad link can't crash the loop). Invite-expired
        transitions to PRIVATE (terminal — the request can never succeed).
        """
        await asyncio.sleep(60)  # let clients connect at startup
        try:
            interval = int(os.getenv("PENDING_RECHECK_INTERVAL_S", "1800"))
        except (ValueError, TypeError):
            interval = 1800
        logging.info(f"[PENDING-RECHECK] started — {interval}s cycle, max 50 groups/cycle")
        while self._running:
            try:
                conn = await self.prod_db._ensure_conn()
                cursor = await conn.execute(
                    "SELECT normalized_link, raw_link, joined_by, last_seen "
                    "FROM group_states WHERE state = ? "
                    "ORDER BY last_seen ASC LIMIT 50",
                    (GroupState.PENDING_APPROVAL,)
                )
                rows = await cursor.fetchall()
                if not rows:
                    await asyncio.sleep(interval)
                    continue
                logging.info(f"[PENDING-RECHECK] cycle: checking {len(rows)} pending-approval group(s)")
                approved = 0
                still_pending = 0
                errors = 0
                expired = 0
                for norm, raw, joined_by, last_seen in rows:
                    if not self._running:
                        break
                    try:
                        phone = joined_by or ""
                        client = getattr(self, 'user_clients', {}).get(phone)
                        if not client or not client.is_connected():
                            still_pending += 1
                            continue
                        # === private invite hash path ===
                        raw_lower = (raw or '').lower()
                        hash_val = None
                        if '/+' in (raw or ''):
                            hash_val = raw.split('/+', 1)[1].split('?')[0].split('#')[0]
                        elif 'joinchat/' in raw_lower:
                            hash_val = raw.split('joinchat/', 1)[1].split('?')[0].split('#')[0]
                        if hash_val:
                            try:
                                from telethon.tl.functions.messages import CheckChatInviteRequest
                                from telethon.tl.types import ChatInviteAlready
                            except ImportError:
                                errors += 1
                                continue
                            try:
                                result = await asyncio.wait_for(
                                    client(CheckChatInviteRequest(hash_val)), timeout=15)
                                if isinstance(result, ChatInviteAlready):
                                    await self.prod_db.set_group_state(
                                        norm, GroupState.JOINED, raw,
                                        joined_by=phone, error='approved_via_recheck')
                                    approved += 1
                                    logging.info(
                                        f"[PENDING-RECHECK] ✅ APPROVED: {raw[:50]} by={phone} → JOINED"
                                    )
                                else:
                                    still_pending += 1
                            except asyncio.TimeoutError:
                                errors += 1
                            except Exception as e:
                                _ename = type(e).__name__
                                if 'Expired' in _ename or 'expired' in str(e).lower():
                                    await self.prod_db.set_group_state(
                                        norm, GroupState.PRIVATE, raw,
                                        error='invite_expired_recheck')
                                    expired += 1
                                    logging.warning(
                                        f"[PENDING-RECHECK] 🔴 invite expired: {raw[:50]}"
                                    )
                                else:
                                    errors += 1
                                    logging.debug(
                                        f"[PENDING-RECHECK] check error on {raw[:40]}: {_ename}: {e}"
                                    )
                        else:
                            # === username link path ===
                            username = None
                            if 't.me/' in (raw or ''):
                                _u = raw.split('t.me/', 1)[1]
                                username = _u.split('?')[0].split('#')[0].split('/')[0]
                            if not username:
                                errors += 1
                                continue
                            try:
                                entity = await asyncio.wait_for(
                                    client.get_entity(username), timeout=15)
                                verified, mc = await self._verify_membership(
                                    client, entity, phone, raw)
                                if verified:
                                    await self.prod_db.set_group_state(
                                        norm, GroupState.JOINED, raw,
                                        joined_by=phone,
                                        member_count=mc,
                                        error='approved_via_recheck')
                                    approved += 1
                                    logging.info(
                                        f"[PENDING-RECHECK] ✅ APPROVED: {raw[:50]} by={phone} → JOINED (members={mc})"
                                    )
                                else:
                                    still_pending += 1
                            except asyncio.TimeoutError:
                                errors += 1
                            except Exception:
                                errors += 1
                        # rate-limit between checks (avoid FloodWait)
                        await asyncio.sleep(5)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        errors += 1
                        logging.debug(
                            f"[PENDING-RECHECK] row error on {(raw or '')[:40]}: {e}"
                        )
                logging.info(
                    f"[PENDING-RECHECK] cycle done: approved={approved} "
                    f"still_pending={still_pending} errors={errors} expired={expired}"
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[PENDING-RECHECK] loop error: {e}", exc_info=True)
            await asyncio.sleep(interval)

    # ===================================================================
    # [N07 / Task 4a hardened] AI drainer — processes the ai_pending backlog
    # (26,475 structural). Bounded-concurrency, rate-limited, lease-
    # protected, idempotent, observable, restart-safe, graceful-shutdown-
    # capable. Respects AI_BATCH_MODE: the drainer runs ONLY when explicitly
    # enabled via AI_DRAIN_ENABLED (default false), since batch mode
    # intentionally skips AI on the hot path for speed. Toggling
    # AI_DRAIN_ENABLED=true + AI_BATCH_MODE=true lets the drainer catch up
    # the backlog in the background without slowing the live enqueue/join
    # pipeline.
    #
    # Bounded concurrency: AI_DRAIN_CONCURRENCY (default 3) caps the number
    #   of concurrent analyze_message calls via asyncio.Semaphore. The 10
    #   rows/batch are gathered concurrently up to this cap (was sequential
    #   in 8b — now configurable; default 3 keeps us well within Groq's
    #   30 RPM / OpenAI's 60 RPM, set to 1 to restore sequential behavior).
    # Batch size: AI_DRAIN_BATCH_SIZE (default 10) — cap rows per 30s cycle.
    # Timeout: AI_DRAIN_TIMEOUT_S (default 60) — each analyze_message is
    #   wrapped in asyncio.wait_for; on timeout the row stays ai_pending
    #   (no permanent claim, re-tried next cycle up to the retry cap).
    # Retry cap: in-memory _ai_drainer_fail_count dict — a row that fails
    #   (timeout / exception / None result) 3× in this worker lifetime is
    #   skipped on subsequent cycles (prevents a poison row from burning
    #   the AI budget every cycle). Lost on restart — idempotent (row is
    #   retried by the next worker instance).
    # Lease protection: PATCH URL carries `ai_approved=is.null` filter so
    #   two concurrent workers (or a supervisor-relaunched instance) can't
    #   double-write. PATCH uses `Prefer: return=representation` and parses
    #   the body — empty list = 0 rows updated = race-lost (logged DEBUG +
    #   skipped, NOT counted as failure). No Supabase migration needed —
    #   the existing `ai_approved` column IS the lease flag.
    # Stuck-job rotation: SELECT uses `&order=id.desc` so the head rotates
    #   as new rows arrive (newest-first); combined with the retry cap,
    #   a chronically-failing poison row at the head is skipped after 3
    #   retries instead of blocking every cycle.
    # Graceful shutdown: stop() cancels _ai_drainer_task; the worker catches
    #   asyncio.CancelledError and breaks. A partial PATCH is idempotent
    #   (the WHERE-filter prevents double-write).
    # Observability: per-batch summary
    #   `[AI-DRAIN] batch=N processed=M failed=K skipped=L elapsed=Xs`.
    #
    # NOTE (backlog starvation): With AI_BATCH_MODE=true (default), the
    # live NewMessage pipeline does NOT call the AI provider (batch mode
    # skips AI on the hot path), so the drainer is the SOLE consumer of
    # the AI rate limit — no competition. If the operator flips
    # AI_BATCH_MODE=false (live AI on hot path), the drainer may compete
    # with the live pipeline for the provider rate limit; in that case
    # set AI_DRAIN_CONCURRENCY=1 to serialize drainer calls behind the
    # live path. The drainer is a backlog reducer, NOT the primary path —
    # 10 rows/30s = 20 rows/min = ~22h to clear 26,475, which is fine.
    # ===================================================================
    async def _ai_drainer_worker(self):
        """Every 30s: fetch up to AI_DRAIN_BATCH_SIZE links where
        ai_approved IS NULL from Supabase, run AI on each (bounded by
        AI_DRAIN_CONCURRENCY semaphore), PATCH the verdict back with a
        `ai_approved=is.null` lease filter. Each analyze_message call is
        wrapped in asyncio.wait_for(AI_DRAIN_TIMEOUT_S). 429 → 60s cycle
        backoff. Empty queue → 60s sleep. Disabled unless
        AI_DRAIN_ENABLED=true (default false — opt-in)."""
        if not os.getenv('AI_DRAIN_ENABLED', 'false').lower() in ('true', '1', 'yes'):
            logging.info("[AI-DRAIN] disabled (AI_DRAIN_ENABLED != true) — worker idle")
            return
        await asyncio.sleep(45)  # let startup settle
        try:
            batch_size = int(os.getenv('AI_DRAIN_BATCH_SIZE', '10'))
        except (ValueError, TypeError):
            batch_size = 10
        try:
            concurrency = max(1, int(os.getenv('AI_DRAIN_CONCURRENCY', '3')))
        except (ValueError, TypeError):
            concurrency = 3
        try:
            timeout_s = float(os.getenv('AI_DRAIN_TIMEOUT_S', '60'))
        except (ValueError, TypeError):
            timeout_s = 60.0
        sem = asyncio.Semaphore(concurrency)
        # In-memory per-row failure counter (capped at 3) — skips chronically-
        # failing rows THIS worker lifetime. Lost on restart (idempotent —
        # the row is retried by the next worker instance). Prevents a poison
        # row from being retried every cycle and burning the AI budget.
        if not hasattr(self, '_ai_drainer_fail_count') or \
                not isinstance(getattr(self, '_ai_drainer_fail_count'), dict):
            self._ai_drainer_fail_count = {}
        fail_count = self._ai_drainer_fail_count
        logging.info(
            f"[AI-DRAIN] started — 30s cycle, batch={batch_size}, "
            f"concurrency={concurrency}, timeout={timeout_s}s"
        )
        while self._running:
            batch_start = time.time()
            processed = failed = skipped = 0
            try:
                if not (getattr(self, 'ai_analyzer', None) and self.ai_analyzer.enabled):
                    # No AI configured — nothing to do
                    await asyncio.sleep(60)
                    continue
                if not self.db.supabase_url or not self.db.supabase_key:
                    await asyncio.sleep(60)
                    continue
                session = await self.db._get_supabase_session()
                # Fetch up to batch_size links where ai_approved IS NULL.
                # ORDER BY id DESC rotates the head so a poison row at the
                # head doesn't permanently block newer rows from being seen
                # (the in-memory fail counter then skips the poison row
                # itself after 3 retries).
                fetch_url = (
                    f"{self.db.supabase_url}/rest/v1/links?"
                    f"ai_approved=is.null&select=id,link,link_type,message_text,"
                    f"group_name,sender_name,source_phone"
                    f"&order=id.desc&limit={batch_size}"
                )
                async with session.get(fetch_url) as resp:
                    if resp.status == 429:
                        logging.warning("[AI-DRAIN] 429 rate-limited — backing off 60s")
                        await asyncio.sleep(60)
                        continue
                    if resp.status != 200:
                        body = await resp.text()
                        logging.warning(f"[AI-DRAIN] fetch status={resp.status}: {body[:120]}")
                        await asyncio.sleep(60)
                        continue
                    rows = await resp.json()
                if not rows:
                    await asyncio.sleep(60)  # empty queue
                    continue

                async def _process_one(row):
                    nonlocal processed, failed, skipped
                    link_id = row.get('id')
                    link = row.get('link')
                    if not link:
                        skipped += 1
                        return
                    # Skip chronically-failing rows this worker lifetime
                    if fail_count.get(link_id, 0) >= 3:
                        skipped += 1
                        logging.debug(
                            f"[AI-DRAIN] skip poison row id={link_id} "
                            f"(3 prior failures this worker lifetime)"
                        )
                        return
                    ai_text = (row.get('message_text') or '') + ' ' + (row.get('group_name') or '')
                    ai_result = None
                    try:
                        async with sem:
                            ai_result = await asyncio.wait_for(
                                self.ai_analyzer.analyze_message((ai_text or '')[:1500]),
                                timeout=timeout_s,
                            )
                    except asyncio.TimeoutError:
                        fail_count[link_id] = fail_count.get(link_id, 0) + 1
                        logging.warning(
                            f"[AI-DRAIN] timeout id={link_id} after {timeout_s}s "
                            f"(fail #{fail_count[link_id]}) — row stays ai_pending"
                        )
                        failed += 1
                        return
                    except asyncio.CancelledError:
                        raise
                    except Exception as ae:
                        fail_count[link_id] = fail_count.get(link_id, 0) + 1
                        logging.warning(
                            f"[AI-DRAIN] analyze failed id={link_id}: {ae} "
                            f"(fail #{fail_count[link_id]}) — row stays ai_pending"
                        )
                        failed += 1
                        return
                    if not ai_result:
                        fail_count[link_id] = fail_count.get(link_id, 0) + 1
                        logging.warning(
                            f"[AI-DRAIN] analyze returned None id={link_id} "
                            f"(fail #{fail_count[link_id]}) — row stays ai_pending"
                        )
                        failed += 1
                        return
                    patch_data = {
                        'ai_approved': bool(ai_result.get('should_save', True)),
                        'ai_description': (ai_result.get('description') or '')[:200] or None,
                        'ai_country': ai_result.get('country') or None,
                        'ai_is_ad': bool(ai_result.get('is_advertisement', False)),
                    }
                    safe_link = url_quote(link, safe='')
                    # Lease protection: only PATCH if ai_approved is still NULL.
                    # If another worker / supervisor-relaunched instance claimed
                    # it first, this PATCH updates 0 rows — we detect via the
                    # empty response body and log+skip (NOT a failure).
                    patch_url = (
                        f"{self.db.supabase_url}/rest/v1/links?"
                        f"link=eq.{safe_link}&ai_approved=is.null"
                    )
                    try:
                        async with session.patch(
                            patch_url, json=patch_data,
                            headers={'Prefer': 'return=representation'},
                        ) as patch_resp:
                            patch_status = patch_resp.status
                            patch_body_text = ''
                            try:
                                patch_body_text = await patch_resp.text()
                            except Exception:
                                pass
                            if patch_status in (200, 204):
                                # Try to detect race-loss (0 rows updated).
                                # With return=representation + 200, body is a
                                # JSON list of updated rows. Empty list = 0
                                # rows = another worker claimed it first.
                                # 204 (no body) = legacy/mock — assume success.
                                race_lost = False
                                if patch_status == 200 and patch_body_text:
                                    try:
                                        updated = json_module.loads(patch_body_text)
                                        if isinstance(updated, list) and len(updated) == 0:
                                            race_lost = True
                                    except (ValueError, json_module.JSONDecodeError):
                                        pass  # not JSON — assume success
                                if race_lost:
                                    skipped += 1
                                    logging.debug(
                                        f"[AI-DRAIN] race-lost id={link_id} "
                                        f"(another worker claimed it first — 0 rows updated)"
                                    )
                                else:
                                    processed += 1
                                    fail_count.pop(link_id, None)  # reset on success
                                    logging.info(
                                        f"[AI-DRAIN] patched link id={link_id} "
                                        f"approved={patch_data['ai_approved']} "
                                        f"country={patch_data['ai_country']}"
                                    )
                            else:
                                failed += 1
                                logging.warning(
                                    f"[AI-DRAIN] patch status={patch_status} "
                                    f"link_id={link_id}: {patch_body_text[:120]}"
                                )
                    except asyncio.CancelledError:
                        raise
                    except Exception as patch_e:
                        failed += 1
                        logging.error(
                            f"[AI-DRAIN] patch exception id={link_id}: {patch_e}"
                        )

                results = await asyncio.gather(
                    *[_process_one(r) for r in rows],
                    return_exceptions=True,
                )
                # Surface any unexpected exceptions from _process_one that
                # weren't caught by its internal try/except.
                for r in results:
                    if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                        logging.error(f"[AI-DRAIN] unexpected row exception: {r}")
                        failed += 1
                elapsed = time.time() - batch_start
                logging.info(
                    f"[AI-DRAIN] batch={len(rows)} processed={processed} "
                    f"failed={failed} skipped={skipped} elapsed={elapsed:.1f}s"
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[AI-DRAIN] error: {e}", exc_info=True)
            await asyncio.sleep(30)

    # ===================================================================
    # [PERSISTENCE Option C] Journal snapshot to Supabase — survives
    # Render ephemeral-disk restart WITHOUT requiring a paid persistent
    # disk. Every 30s, snapshots at-risk journal rows (state IN pending /
    # no_text / delete_miss) to Supabase `message_journal_snapshot` table.
    # On startup, _restore_journal_from_supabase restores them into the
    # local SQLite before _journal_recovery runs. Additive + idempotent —
    # doesn't replace the SQLite journal, just mirrors at-risk rows.
    #
    # [Task 3a audit — design notes]
    # • Snapshot SELECT predicate: state IN ('pending','no_text','delete_miss')
    #   ONLY. Terminal states (processed/rescued/no_links/dup_claim/blacklisted)
    #   are deliberately EXCLUDED so the snapshot table stays small. Tradeoff:
    #   a stale 'pending' row in Supabase can resurrect on restart-with-wiped-
    #   local; mitigated by (a) INSERT OR IGNORE preserving any local terminal
    #   state and (b) downstream UNIQUE(link) constraint on link_queue making
    #   re-processing idempotent.
    # • 'failed' is NEVER a journal state (only a processed_messages /
    #   link_queue claim state). The `state NOT IN ('pending','failed')` clause
    #   in journal_cleanup is purely defensive. So there is no `failed` to add
    #   to the snapshot predicate.
    # • POST is atomic per PostgREST batch (single PostgreSQL transaction);
    #   one bad row aborts the whole batch — caller retries the same rows
    #   next cycle (idempotent via PK (chat_id, msg_id) upsert).
    # • Explicit `aiohttp.ClientTimeout(total=15)` per call so a hung Supabase
    #   can't block the snapshot loop forever (the shared session in
    #   _get_supabase_session has NO default timeout — other Supabase callers
    #   are out of scope of this audit).
    # • `_snapshot_running` bool guard prevents two concurrent invocations
    #   (e.g. supervisor relaunches the loop while the previous cycle still
    #   POSTing) — last-writer-wins via PK upsert would be safe regardless,
    #   but the guard avoids double-rate-limit pressure on the free tier.
    # • 429 → 60s backoff (mirrors _ai_drainer_worker). 5xx/network → WARNING
    #   + 30s retry. Table-missing (404) → rate-limited WARNING (max 1/hour).
    # ===================================================================
    async def _journal_snapshot_loop(self):
        """30s background loop: mirror at-risk journal rows to Supabase.
        On failure (table missing / 404), logs a rate-limited WARNING with
        the exact SQL to run, then keeps retrying every 30s.

        [Task 3a] Guarded by self._snapshot_running — if already mid-cycle,
        logs INFO and returns silently (supervisor relaunch is a no-op)."""
        # [Task 3a / point 6] Concurrent-invocation guard.
        if getattr(self, '_snapshot_running', False):
            logging.info("[JOURNAL-SNAPSHOT] previous cycle still running — skip")
            return
        self._snapshot_running = True
        try:
            await asyncio.sleep(40)  # let startup settle (before _journal_recovery)
            logging.info("[JOURNAL-SNAPSHOT] started — 30s cycle, 500 rows/batch")
            last_warn_ts = 0.0
            while self._running:
                try:
                    if not self.db.supabase_url or not self.db.supabase_key:
                        await asyncio.sleep(60)
                        continue
                    conn = await self.prod_db._conn()
                    # [Task 3a / point 2] ORDER BY received_at ASC → snapshot
                    # OLDEST at-risk rows first (they are the most likely to
                    # be lost on a crash). Without ORDER BY, SQLite returns
                    # rows in unspecified order and a 500-row LIMIT could
                    # repeatedly snapshot the same NEW rows while OLD ones
                    # never make it.
                    cursor = await conn.execute(
                        """SELECT chat_id, msg_id, raw_text, source_phone, chat_title,
                                  chat_username, chat_link_type, sender_id, sender_name,
                                  state, received_at
                           FROM message_journal
                           WHERE state IN ('pending','no_text','delete_miss')
                           ORDER BY received_at ASC
                           LIMIT 500""")
                    rows = await cursor.fetchall()
                    if not rows:
                        await asyncio.sleep(30)
                        continue
                    batch = [
                        {
                            'chat_id': r[0], 'msg_id': r[1], 'raw_text': r[2],
                            'source_phone': r[3], 'chat_title': r[4] or '',
                            'chat_username': r[5] or '', 'chat_link_type': r[6] or 'telegram',
                            'sender_id': r[7] or 0, 'sender_name': r[8] or '',
                            'state': r[9], 'received_at': r[10],
                        }
                        for r in rows
                    ]
                    session = await self.db._get_supabase_session()
                    # [Task 3a / point 8] explicit per-call timeout so a hung
                    # Supabase cannot block the snapshot loop forever.
                    snap_timeout = aiohttp.ClientTimeout(total=15)
                    async with session.post(
                        f"{self.db.supabase_url}/rest/v1/message_journal_snapshot",
                        headers={
                            "Prefer": "resolution=merge-duplicates",
                            # apikey + Authorization are already on the shared session
                            # (set in _get_supabase_session); we add Prefer here.
                        },
                        json=batch,
                        timeout=snap_timeout,
                    ) as resp:
                        if resp.status not in (200, 201, 204):
                            body = await resp.text()
                            # [Task 3a / point 9] explicit 429 backoff (mirrors
                            # _ai_drainer_worker) so the free-tier 0.03 req/s
                            # limit doesn't cascade into a storm of retries.
                            if resp.status == 429:
                                logging.warning(
                                    "[JOURNAL-SNAPSHOT] 429 rate-limited — backing off 60s"
                                )
                                await asyncio.sleep(60)
                                continue
                            # 404 / table-missing — rate-limit the warning to once/hour
                            now = time.time()
                            if 'relation' in body.lower() or 'does not exist' in body.lower() \
                                    or resp.status == 404:
                                if now - last_warn_ts > 3600:
                                    logging.warning(
                                        "[JOURNAL-SNAPSHOT] table message_journal_snapshot "
                                        "missing — run the migration at "
                                        "supabase/message_journal_snapshot.sql in Supabase SQL "
                                        "Editor. NOTE: PostgreSQL does not support "
                                        "CREATE POLICY IF NOT EXISTS — use "
                                        "DROP POLICY IF EXISTS then CREATE POLICY. "
                                        "See repo file for the exact idempotent SQL."
                                    )
                                    last_warn_ts = now
                            else:
                                logging.warning(
                                    f"[JOURNAL-SNAPSHOT] POST status={resp.status}: {body[:200]}"
                                )
                except asyncio.CancelledError:
                    break
                # [Task 3a / point 10] catch TimeoutError, ClientError, and
                # generic Exception so a hung/500-ing Supabase never crashes
                # the worker. The loop continues at the 30s cadence.
                except asyncio.TimeoutError as te:
                    logging.warning(f"[JOURNAL-SNAPSHOT] POST timed out (15s): {te}")
                except aiohttp.ClientError as ce:
                    logging.warning(f"[JOURNAL-SNAPSHOT] network error: {ce}")
                except Exception as e:
                    logging.warning(f"[JOURNAL-SNAPSHOT] error: {e}")
                await asyncio.sleep(30)
        finally:
            self._snapshot_running = False

    async def _restore_journal_from_supabase(self):
        """Startup: SELECT at-risk rows from Supabase snapshot, INSERT OR
        IGNORE into local message_journal. Called BEFORE _journal_recovery
        so the recovery sweep can pick them up. Idempotent — INSERT OR
        IGNORE dedups against any rows that survived the local SQLite.

        [Task 3a] Design notes:
        • INSERT OR IGNORE preserves any local terminal state (processed/
          rescued/no_links/dup_claim/blacklisted) — a stale 'pending' row
          in the snapshot will NOT overwrite a newer local state. If local
          SQLite was wiped, the stale 'pending' is restored and journal_recovery
          re-processes it (idempotent via UNIQUE(link) on link_queue).
        • Per-row try/except isolates corrupted rows (NULL chat_id/msg_id,
          malformed received_at) — one bad row doesn't abort the restore.
        • Explicit `aiohttp.ClientTimeout(total=15)` on the GET so a hung
          Supabase can't block startup. Restore returns 0 on timeout.
        • The restore query has NO `order=` param — order doesn't matter
          because each row is an independent INSERT OR IGNORE (no cross-row
          dependency)."""
        try:
            if not self.db.supabase_url or not self.db.supabase_key:
                return 0
            if not self._journal_enabled():
                return 0
            session = await self.db._get_supabase_session()
            url = (
                f"{self.db.supabase_url}/rest/v1/message_journal_snapshot?"
                f"state=in.(pending,no_text,delete_miss)&"
                f"select=chat_id,msg_id,raw_text,source_phone,chat_title,"
                f"chat_username,chat_link_type,sender_id,sender_name,state,received_at"
            )
            # [Task 3a / point 8] explicit timeout so a hung Supabase at
            # startup can't block _journal_recovery from running.
            restore_timeout = aiohttp.ClientTimeout(total=15)
            async with session.get(url, timeout=restore_timeout) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logging.info(
                        f"[JOURNAL-SNAPSHOT] restore skipped (status={resp.status}): {body[:120]}"
                    )
                    return 0
                rows = await resp.json()
            if not rows:
                logging.info("[JOURNAL-SNAPSHOT] nothing to restore (snapshot empty)")
                return 0
            restored = 0
            for r in rows:
                try:
                    await self.prod_db.journal_message({
                        'chat_id': r.get('chat_id'), 'msg_id': r.get('msg_id'),
                        'raw_text': r.get('raw_text'),
                        'source_phone': r.get('source_phone'),
                        'received_at': r.get('received_at') or time.time(),
                        'chat_title': r.get('chat_title') or '',
                        'chat_username': r.get('chat_username') or '',
                        'chat_link_type': r.get('chat_link_type') or 'telegram',
                        'sender_id': r.get('sender_id') or 0,
                        'sender_name': r.get('sender_name') or '',
                        'state': r.get('state') or 'pending',
                    })
                    restored += 1
                except Exception:
                    # journal_message uses INSERT OR IGNORE internally for
                    # the at-risk states; concurrent inserts dedup safely.
                    pass
            logging.info(f"[JOURNAL-SNAPSHOT] restored {restored} row(s) from Supabase")
            return restored
        except asyncio.TimeoutError:
            logging.warning("[JOURNAL-SNAPSHOT] restore timed out (15s) — skipping")
            return 0
        except aiohttp.ClientError as ce:
            logging.warning(f"[JOURNAL-SNAPSHOT] restore network error: {ce}")
            return 0
        except Exception as e:
            logging.warning(f"[JOURNAL-SNAPSHOT] restore error: {e}")
            return 0

    async def _on_user_message(self, event, source_phone: str):
        """معالج رسائل فوري — يسحب الروابط قبل ما تحذفها بوتات أخرى.

        الاستراتيجية (4 طبقات حماية ضد الحذف):
        0. LINK-ONLY FAST CAPTURE: استخراج الروابط + كتابة LRB فوراً
           (regex نقي، لا API، لا metadata) — أسرع مسار، يسبق كل شيء.
        1. PRE-CACHE: نخزن الرسالة كاملة في ذاكرة (metadata + raw_text)
        2. JOURNAL: نسجل durable WAL (يصمد بعد restart/TTL)
        3. BACKGROUND: معالجة (claim + blacklist + enqueue) — لا نوقف event loop

        لو بوت حماية حذف الرسالة قبل خطوة 1، الـ MessageDeleted handler
        يسحب الرابط من LRB (طبقة 0) ويدخله dedup/queue مباشرة.
        """
        try:
            raw_text = event.raw_text
            chat_id = event.chat_id
            if chat_id == self.config.channel_id: return

            sender_id = event.sender_id or 0
            msg_id = event.id

            if not raw_text:
                # رسالة بلا نص (ملصق/صوت...) — سجّلها في journal كدليل جنائي
                # على وصول الحدث نفسه، ثم اخرج (لا روابط ممكنة فيها)
                self._no_text_count = getattr(self, '_no_text_count', 0) + 1
                await self._journal_write(chat_id, msg_id, None, source_phone, state='no_text')
                return

            # === الخطوة 0: LINK-ONLY FAST CAPTURE (أسرع مسار — قبل أي metadata) ===
            # نستخرج الروابط فوراً (regex نقي، لا API) ونخزّنها في LRB.
            # هذا يضمن: لو حُذفت الرسالة قبل اكتمال PRE-CACHE metadata،
            # الـ MessageDeleted handler يقدر يسحب الروابط من LRB وينقذها.
            # مبدأ تصميمي: فشل observability (metrics/logging) لا يكسر مسار
            # الالتقاط — كل مكالنات الـmetrics مُغلّفة دفاعياً.
            links = LinkNormalizer.extract_links(raw_text)
            if links:
                try:
                    await self._link_ring_put(chat_id, msg_id,
                                              [l.get('normalized') or l.get('raw') for l in links])
                except Exception:
                    pass  # LRB فشل — لكن extract ناجح، نكمل
                try:
                    await self.metrics.record_link_capture(len(links))
                except Exception:
                    pass  # metrics لا يكسر الالتقاط
                logging.info(
                    f"[LINK-CAPTURE] captured {len(links)} link(s) "
                    f"chat_id={chat_id} msg_id={msg_id} source={source_phone}"
                )

            # === الخطوة 1: PRE-CACHE (metadata كاملة — لو فشلت، LRB أنقذ الروابط) ===
            # نخزن الرسالة كاملة في الذاكرة فوراً — لو حُذفت لاحقاً، نقدر نعالجها
            try:
                chat_obj = event.chat
                chat_title = ''
                chat_username = ''
                if chat_obj:
                    if hasattr(chat_obj, 'title') and chat_obj.title:
                        chat_title = chat_obj.title
                    if hasattr(chat_obj, 'username') and chat_obj.username:
                        chat_username = chat_obj.username

                # استخراج معلومات المرسل بدون API
                sender_obj = event.sender
                sender_name = f"user_{sender_id}"
                if sender_obj:
                    if hasattr(sender_obj, 'first_name') and sender_obj.first_name:
                        sender_name = sender_obj.first_name
                        if hasattr(sender_obj, 'last_name') and sender_obj.last_name:
                            sender_name += f" {sender_obj.last_name}"
                    elif hasattr(sender_obj, 'title') and sender_obj.title:
                        sender_name = sender_obj.title
                    elif hasattr(sender_obj, 'username') and sender_obj.username:
                        sender_name = f"@{sender_obj.username}"

                # نوع المجموعة
                chat_link_type = 'telegram'
                if chat_obj:
                    if hasattr(chat_obj, 'megagroup') and chat_obj.megagroup:
                        chat_link_type = 'group'
                    elif hasattr(chat_obj, 'broadcast') and chat_obj.broadcast:
                        chat_link_type = 'channel'

                # خزّن في cache (async lock سريع)
                async with self._msg_cache_lock:
                    self._msg_cache[(chat_id, msg_id)] = {
                        'raw_text': raw_text,
                        'source_phone': source_phone,
                        'received_at': time.time(),
                        'chat_id': chat_id,
                        'msg_id': msg_id,
                        'sender_id': sender_id,
                        'chat_title': chat_title,
                        'chat_username': chat_username,
                        'chat_link_type': chat_link_type,
                        'sender_name': sender_name,
                        'processed': False,  # هل عُولجت في NewMessage؟
                    }
            except Exception as cache_err:
                logging.debug(f"[CACHE] store error: {cache_err}")
                # حتى لو فشل cache، نكمل المعالجة — journal fallback بدون metadata
                await self._journal_write(chat_id, msg_id, raw_text, source_phone, state='pending')
            else:
                # === DURABLE JOURNAL (write-ahead log) — يبقى بعد انتهاء TTL
                # الذاكرة وبعد إعادة تشغيل النظام ===
                await self._journal_write(chat_id, msg_id, raw_text, source_phone,
                                          chat_title=chat_title, chat_username=chat_username,
                                          chat_link_type=chat_link_type, sender_id=sender_id,
                                          sender_name=sender_name, state='pending')

            # === الخطوة 2: استخدم links المُستخرَجة في الخطوة 0 ===
            # (defensive re-extract لو الخطوة 0 فشلت بصمت — استرجاع الأمان)
            if not links:
                links = LinkNormalizer.extract_links(raw_text)
            if not links:
                # ما فيها روابط — لكن سجل claim مع lease قصير (يمنع إعادة المعالجة الفورية)
                if self.message_claim:
                    claim_token = await self.message_claim.claim(chat_id, msg_id, 'newmessage', source_phone)
                    if claim_token:
                        # سجل كـ processed (لا روابط → لا enqueue)
                        await self.message_claim.mark_processed(chat_id, msg_id, claim_token)
                await self._journal_set_state_safe(chat_id, msg_id, 'no_links')
                return  # ما فيها روابط — لا نعالج (journal/cache ينظفان تلقائياً)

            # === ATOMIC CLAIM (يمنع التكرار من Polling + Scanner + حسابات أخرى) ===
            claim_token = None
            if self.message_claim:
                claim_token = await self.message_claim.claim(chat_id, msg_id, 'newmessage', source_phone)
                if claim_token is None:
                    # [N03] Lost the claim race — another worker (Polling /
                    # Scanner / journal_recovery / reconcile) won. The WINNER
                    # has ALREADY written (or will write) a 'pending' journal
                    # row that journal_recovery rescues if the winner crashes.
                    # Setting 'dup_claim' here would OVERWRITE that 'pending'
                    # row → journal_pending_older_than (filters state='pending')
                    # never sees it → message silently lost if the winner also
                    # crashes. Just log + return silently; do NOT touch state.
                    logging.debug(f"[PIPELINE-1] ⏭️ msg ({chat_id}, {msg_id}) already claimed — LOSER silently skipping (preserving WINNER's pending journal row)")
                    return

            # === الخطوة 2: معالجة فورية (sync, سريعة جداً) ===
            # نسجّل المجموعة + نضع الرابط في queue فوراً (العملية كلها DB، بدون API)
            # SAFETY: لا نفترض أن الرسالة لا تزال في _msg_cache — فقد تُحذف بين
            # تخزينها (أعلاه) وهنا بواسطة _on_message_deleted أو _msg_cache_cleanup.
            # الأولوية: cache → event.chat/event.sender → fallback آمن.
            cached_entry = None
            async with self._msg_cache_lock:
                cached_entry = self._msg_cache.get((chat_id, msg_id))

            if cached_entry:
                group_name = cached_entry.get('chat_title') or f"chat_{chat_id}"
                sender_name = cached_entry.get('sender_name') or f"user_{sender_id}"
                chat_username = cached_entry.get('chat_username', '')
                chat_link_type = cached_entry.get('chat_link_type', 'telegram')
            else:
                # Cache miss — استخرج metadata من event مباشرة (fallback آمن)
                group_name = f"chat_{chat_id}"
                sender_name = f"user_{sender_id}"
                chat_username = ''
                chat_link_type = 'telegram'
                try:
                    chat_obj = event.chat
                    if chat_obj:
                        if hasattr(chat_obj, 'title') and chat_obj.title:
                            group_name = chat_obj.title
                        if hasattr(chat_obj, 'username') and chat_obj.username:
                            chat_username = chat_obj.username
                        if hasattr(chat_obj, 'megagroup') and chat_obj.megagroup:
                            chat_link_type = 'group'
                        elif hasattr(chat_obj, 'broadcast') and chat_obj.broadcast:
                            chat_link_type = 'channel'
                except Exception:
                    pass  # ابقَ على fallback
                try:
                    sender_obj = event.sender
                    if sender_obj:
                        if hasattr(sender_obj, 'first_name') and sender_obj.first_name:
                            sender_name = sender_obj.first_name
                            if hasattr(sender_obj, 'last_name') and sender_obj.last_name:
                                sender_name += f" {sender_obj.last_name}"
                        elif hasattr(sender_obj, 'title') and sender_obj.title:
                            sender_name = sender_obj.title
                        elif hasattr(sender_obj, 'username') and sender_obj.username:
                            sender_name = f"@{sender_obj.username}"
                except Exception:
                    pass  # ابقَ على fallback
                logging.debug(
                    f"[PIPELINE-1] cache miss for ({chat_id}, {msg_id}) — using event fallback "
                    f"(group='{group_name[:30]}', sender='{sender_name[:20]}')"
                )

            logging.info(f"[PIPELINE-1] 📨🔗 Link found from source={source_phone} chat_id={chat_id} msg_id={msg_id} (len={len(raw_text)})")
            logging.info(f"[PIPELINE-1] 🔗 Found {len(links)} link(s) in message from {group_name}")

            # === MONITORED CHATS DEDUP — سجل المجموعة المصدر ===
            try:
                is_new = await self.prod_db.add_monitored_chat(
                    chat_id=chat_id,
                    chat_title=group_name,
                    username=chat_username,
                    link_type=chat_link_type,
                    monitored_by=source_phone,
                )
                if is_new:
                    logging.info(
                        f"[MONITORED] ✅ New chat: '{group_name[:40]}' (id={chat_id}, by={source_phone})"
                    )
            except Exception as e:
                logging.debug(f"[MONITORED] add error: {e}")

            # === الخطوة 3: enqueue كل رابط فوراً ===
            try:
                for link_info in links:
                    link_data = {
                        **link_info,
                        'group_name': group_name,
                        'sender_name': sender_name,
                        'sender_contact': extract_sender_contact(raw_text),
                        'source_phone': source_phone,
                        'message_text': raw_text,
                        'message_link': f"https://t.me/c/{str(chat_id).replace('-100', '')}/{msg_id}" if chat_id else None,
                    }

                    # === فلتر BLACKLIST فقط (سريع) ===
                    link_raw = link_info['raw'].lower()
                    username_raw = (link_info.get('username') or '').lower()
                    full_text_check = f"{raw_text} {link_raw} {username_raw}".lower()

                    # فحص القائمة السوداء
                    is_bad, bad_reason = GulfFilter.is_blacklisted(
                        full_text_check, username_raw, link_info['raw'], group_name
                    )
                    if is_bad:
                        logging.info(
                            f"[PIPELINE-1] 🚫 BLACKLISTED: {link_info['raw'][:50]} ({bad_reason})"
                        )
                        await self.metrics.record_skip(f'blacklist_{bad_reason}')
                        continue

                    # === enqueue فوراً ===
                    is_new = await self.prod_db.enqueue_link(link_data)
                    if is_new:
                        await self.prod_db.set_group_state(
                            link_info['normalized'], GroupState.DISCOVERED,
                            link_info['raw'], group_name)
                        logging.info(f"[PIPELINE-2] ✅ Link enqueued: {link_info['raw'][:60]}")
                    else:
                        await self.metrics.record_duplicate()
                        logging.info(f"[PIPELINE-2] ⏭️ Duplicate: {link_info['normalized'][:60]}")

                # === Mark message as PROCESSED (claim_token verified) ===
                if claim_token:
                    await self.message_claim.mark_processed(chat_id, msg_id, claim_token)
                await self._journal_set_state_safe(chat_id, msg_id, 'processed')

            except Exception as inner_e:
                # === Mark as FAILED (allows retry by Polling/Scanner/Journal-Recovery) ===
                if claim_token:
                    await self.message_claim.mark_failed(chat_id, msg_id, claim_token, str(inner_e))
                await self._journal_set_state_safe(chat_id, msg_id, 'pending', error=str(inner_e))
                raise  # re-raise to outer handler

            # علّم الرسالة كـ "معالجة" في cache
            try:
                async with self._msg_cache_lock:
                    if (chat_id, msg_id) in self._msg_cache:
                        self._msg_cache[(chat_id, msg_id)]['processed'] = True
            except Exception:
                pass

        except Exception as e:
            logging.error(f"Event handler error: {e}", exc_info=True)

    def _normalized_to_link_data(self, normalized: str, source_phone: str,
                                 chat_id, msg_id, group_name: str = '') -> Optional[dict]:
        """[PR-2] يُعيد بناء minimal link_data من normalized link فقط
        (tg:user:x / tg:invite:h / wa:invite:h) — لإنقاذ LRB بدون metadata.
        يعيد None لو الصيغة غير معروفة."""
        if not normalized:
            return None
        n = normalized.strip().lower()
        try:
            if n.startswith('tg:user:'):
                username = n[len('tg:user:'):]
                return {
                    'raw': f'https://t.me/{username}',
                    'normalized': n, 'link_type': 'telegram',
                    'username': username, 'invite_hash': None,
                    'msg_id': None, 'group_name': group_name or f'chat_{chat_id}',
                    'sender_name': 'Unknown', 'sender_contact': '',
                    'source_phone': source_phone or '',
                    'message_text': '', 'message_link': None,
                }
            if n.startswith('tg:invite:'):
                inv = n[len('tg:invite:'):]
                return {
                    'raw': f'https://t.me/+{inv}',
                    'normalized': n, 'link_type': 'telegram_private',
                    'username': None, 'invite_hash': inv,
                    'msg_id': None, 'group_name': group_name or f'chat_{chat_id}',
                    'sender_name': 'Unknown', 'sender_contact': '',
                    'source_phone': source_phone or '',
                    'message_text': '', 'message_link': None,
                }
            if n.startswith('wa:invite:'):
                inv = n[len('wa:invite:'):]
                return {
                    'raw': f'https://chat.whatsapp.com/{inv}',
                    'normalized': n, 'link_type': 'whatsapp',
                    'username': None, 'invite_hash': inv,
                    'msg_id': None, 'group_name': group_name or f'chat_{chat_id}',
                    'sender_name': 'Unknown', 'sender_contact': '',
                    'source_phone': source_phone or '',
                    'message_text': '', 'message_link': None,
                }
        except Exception:
            return None
        return None

    async def _rescue_link_only(self, chat_id, msg_id, source_phone,
                                normalized_links) -> int:
        """[PR-2] إنقاذ روابط من LRB (link-only، بدون metadata كاملة).
        مسار موحّد: reconstruct → is_link_known (central dedup) → enqueue_link.
        يرجع عدد الروابط الجديدة المُضافة للـqueue (0 لو كلها مكررة/معروفة)."""
        new_count = 0
        for normalized in (normalized_links or []):
            link_data = self._normalized_to_link_data(
                normalized, source_phone, chat_id, msg_id)
            if not link_data:
                continue
            try:
                # central dedup: هل الرابط معروف مسبقاً (queue/forwarded/target)?
                if await self.prod_db.is_link_known(link_data['raw'], link_data['normalized']):
                    logging.info(
                        f"[DEDUP] skipped (known) {normalized[:50]} "
                        f"from LRB rescue chat={chat_id} msg={msg_id}"
                    )
                    continue
                # سجّل المجموعة المصدر (best-effort)
                try:
                    await self.prod_db.add_monitored_chat(
                        chat_id=chat_id, chat_title=link_data['group_name'],
                        username='', link_type='telegram',
                        monitored_by=source_phone or '')
                except Exception:
                    pass
                is_new = await self.prod_db.enqueue_link(link_data)
                if is_new:
                    new_count += 1
                    try:
                        await self.prod_db.set_group_state(
                            link_data['normalized'], GroupState.DISCOVERED,
                            link_data['raw'], link_data['group_name'])
                    except Exception:
                        pass
                    logging.warning(
                        f"[LINK-DELETED-RESCUE] rescued {link_data['raw'][:60]} "
                        f"from LRB chat_id={chat_id} msg_id={msg_id} "
                        f"(no metadata — link-only)"
                    )
            except Exception as e:
                logging.error(f"[LINK-DELETED-RESCUE] error for {normalized[:50]}: {e}")
        return new_count

    async def _on_message_deleted(self, event, source_phone: str):
        """يلتقط الرسائل المحذوفة — يعالجها لو ما عولجت قبل.

        مصادر الإنقاذ (بالأولوية — حسب طلب المستخدم):
        1. Link Ring Buffer (LRB — أسرع مسار، روابط فقط بدون metadata)
        2. _msg_cache (ذاكرة — رسالة كاملة، TTL 120 ثانية)
        3. message_journal (SQLite — يصمد بعد restart/TTL)
        4. Best-effort get_messages (لو Telegram ما حذفها بعد من الـindex)
        5. Reconcile للسياق (يلتقط الرسائل الأخوات الفائتة)

        لو لم تُوجد الرسالة في أي مصدر → DELETE-MISS:
        دليل جنائي أن NewMessage لم يصل أبدًا (فجوة تسليم أحداث).
        نسجّل تحذيرًا مقيّدًا + صف delete_miss في journal + نطلق reconcile
        للشات. لا ندّعي استرجاع ما لم يصل أصلًا من Telegram.
        """
        try:
            deleted_ids = getattr(event, 'deleted_ids', []) or []
            if not deleted_ids:
                return

            chat_id = getattr(event, 'chat_id', None)

            # حماية من المسح الجماعي (admin clear) — لا نغرق السجلات
            if len(deleted_ids) > 50:
                logging.warning(
                    f"[DELETE-HANDLER] mass delete: {len(deleted_ids)} ids in chat={chat_id} "
                    f"— processing first 50 for forensics"
                )
                deleted_ids = deleted_ids[:50]

            reconcile_chats: Set[int] = set()

            for deleted_msg_id in deleted_ids:
                # [N04] Per-iteration try/except isolation: a single iteration
                # failure (DB error, regex bug, locked journal, etc.) must NOT
                # abort the remaining 49 ids in a mass-delete batch. Without
                # this, one bad row silently skipped processing for 49 messages
                # that were never rescued.
                try:
                    # === المصدر 1: Link Ring Buffer (LRB — أسرع مسار، روابط فقط) ===
                    # [PR-2] لو NewMessage وصل لطبقة extract (Step 0) قبل الحذف،
                    # LRB عنده الروابط. نُنقذها (link-only) عبر مسار موحّد:
                    # reconstruct → is_link_known (dedup) → enqueue_link.
                    # لو LRB عنده الروابط، ما نحتاج النص الكامل (مبدأ: الرابط أهم).
                    ring_links = await self._link_ring_pop(chat_id, deleted_msg_id)
                    if ring_links:
                        try:
                            rescued = await self._rescue_link_only(
                                chat_id, deleted_msg_id, source_phone, ring_links)
                            # LRB hit = استرجعنا الروابط بنجاح من LRB
                            # (دون النظر إن كانت new أو known — الـdedup مقياس منفصل)
                            try: await self.metrics.record_link_ring_hit()
                            except Exception: pass
                            try: await self.metrics.record_delete_rescued('link_ring')
                            except Exception: pass
                            # [PR-LRB-LOG] سجل صريح بأن الإنقاذ تم من LRB —
                            # سابقًا كان هذا المسار صامتًا تمامًا (لا يوجد أي
                            # [DELETE-HANDLER] log قبل الـcontinue) — فلم يكن
                            # للمُشغّل دليل جنائي على وقوع الإنقاذ. الآن نُصدر
                            # log INFO واضح يحمل msg_id + chat_id + new_count.
                            logging.info(
                                f"[DELETE-HANDLER] ✅ LRB-RESCUED msg_id={deleted_msg_id} "
                                f"chat_id={chat_id} new_count={rescued} "
                                f"source={source_phone}"
                            )
                            # سجّل في journal للتحقيق (raw_text=None — link-only)
                            if self._journal_enabled() and chat_id is not None:
                                try:
                                    await self._journal_set_state_safe(
                                        chat_id, deleted_msg_id,
                                        'rescued' if rescued else 'dup_claim',
                                        mark_deleted=True)
                                except Exception:
                                    pass
                            # محّل cache إن وُجد (تنظيف)
                            async with self._msg_cache_lock:
                                self._msg_cache.pop((chat_id, deleted_msg_id), None)
                            continue   # ✅ الرابط أُنقذ/عُولج من LRB — لا داعي للمسار الكامل
                        except Exception as lrb_e:
                            logging.debug(f"[DELETE-HANDLER] LRB rescue error: {lrb_e}")
                            # لو فشل LRB rescue، نكمل للمسار الكامل (cache/journal)

                    # === المصدر 2: _msg_cache (ذاكرة — رسالة كاملة) ===
                    cached_msg = None
                    rescue_source = None
                    async with self._msg_cache_lock:
                        if chat_id:
                            cached_msg = self._msg_cache.pop((chat_id, deleted_msg_id), None)
                        else:
                            # لو ما عندنا chat_id، ابحث في كل الـ keys
                            for key, val in list(self._msg_cache.items()):
                                if key[1] == deleted_msg_id:
                                    cached_msg = self._msg_cache.pop(key, None)
                                    break
                    # [PR-METRICS-FIX] علّم المصدر كـ'cache' لو وُجدت الرسالة
                    # في الـ_​msg_cache — سابقًا كان rescue_source يبقى None،
                    # فكان record_delete_rescued لا يُستدعى أبدًا لهذا المسار
                    # رغم أن HELP text للميتريك يدّعي تغطية cache.
                    if cached_msg is not None and rescue_source is None:
                        rescue_source = 'cache'

                    # === المصدر 3: message_journal (durable — يصمد بعد restart/TTL) ===
                    if not cached_msg and self._journal_enabled():
                        try:
                            row = None
                            # [B09] guard the journal_lookup_any non-deterministic path:
                            # lookup_any searches across ALL chats for a msg_id and may
                            # return a row from a DIFFERENT chat (wrong rescue target).
                            # Only use the deterministic chat_id-keyed journal_get when
                            # chat_id is not None; fall back to lookup_any only when the
                            # delete event carries no chat_id at all.
                            if chat_id is not None:
                                row = await self.prod_db.journal_get(chat_id, deleted_msg_id)
                            else:
                                rows = await self.prod_db.journal_lookup_any(deleted_msg_id)
                                row = rows[0] if rows else None
                            if row and row.get('raw_text'):
                                cached_msg = dict(row)
                                rescue_source = 'journal'
                        except Exception as e:
                            logging.debug(f"[JOURNAL] lookup error: {e}")

                    # === DELETE-MISS: NewMessage لم يصل أبدًا ===
                    # === المصدر 4: Best-effort get_messages (Telegram قد يؤخّر الحذف في الـindex) ===
                    # [PR-2] رغم ندرة نجاحه (الرسالة محذوفة فعلاً)، الإضافة رخيصة:
                    # لو نجح، نستخرج raw_text ونعالج كأنه cache hit. لو فشل/أعاد None → DELETE-MISS.
                    if not cached_msg and chat_id is not None:
                        try:
                            gm_client = None
                            # استخدم نفس phone الذي رأى الحذف، أو أي عميل متصل
                            if source_phone and self.user_clients.get(source_phone) \
                                    and self.user_clients[source_phone].is_connected():
                                gm_client = self.user_clients[source_phone]
                            if gm_client is None:
                                for c in self.user_clients.values():
                                    if c and c.is_connected():
                                        gm_client = c; break
                            if gm_client is not None:
                                msgs = await gm_client.get_messages(chat_id, ids=[deleted_msg_id])
                                gm = msgs[0] if msgs else None
                                gm_text = getattr(gm, 'message', '') or ''
                                if gm_text:
                                    cached_msg = {
                                        'raw_text': gm_text,
                                        'source_phone': source_phone,
                                        'chat_id': chat_id, 'msg_id': deleted_msg_id,
                                        'chat_title': '', 'chat_username': '',
                                        'chat_link_type': 'telegram', 'sender_name': 'Unknown',
                                        'state': 'pending',
                                    }
                                    rescue_source = 'get_messages'
                                    logging.info(
                                        f"[DELETE-HANDLER] 🎲 get_messages HIT msg_id={deleted_msg_id} "
                                        f"chat={chat_id} — rescuing full"
                                    )
                        except Exception as gm_e:
                            logging.debug(f"[DELETE-HANDLER] get_messages miss {gm_e}")

                    # === DELETE-MISS: NewMessage لم يصل أبدًا وكل المصادر فشلت ===
                    if not cached_msg:
                        await self._record_delete_miss(chat_id, deleted_msg_id, source_phone)
                        if chat_id:
                            reconcile_chats.add(chat_id)
                        continue

                    # عولجت مسبقًا؟ (لا حاجة لإنقاذ — فقط علّم الحذف للتحقيق)
                    already_done = bool(cached_msg.get('processed')) or cached_msg.get('state') in (
                        'processed', 'no_links', 'no_text', 'dup_claim', 'rescued'
                    )
                    if already_done:
                        await self._journal_mark_deleted_safe(
                            chat_id if chat_id else cached_msg.get('chat_id'), deleted_msg_id)
                        logging.debug(
                            f"[DELETE-HANDLER] msg_id={deleted_msg_id} already processed — skip"
                        )
                        continue

                    # الرسالة محذوفة قبل المعالجة! عالجها الآن (من cache أو journal)
                    raw_text = cached_msg.get('raw_text') or ''
                    group_name = cached_msg.get('chat_title') or f"chat_{cached_msg.get('chat_id')}"
                    sender_name = cached_msg.get('sender_name', 'Unknown')
                    chat_username = cached_msg.get('chat_username', '')
                    chat_link_type = cached_msg.get('chat_link_type', 'telegram')
                    orig_chat_id = cached_msg.get('chat_id')
                    orig_source_phone = cached_msg.get('source_phone', source_phone)

                    # استخرج الروابط
                    links = LinkNormalizer.extract_links(raw_text)
                    if not links:
                        await self._journal_set_state_safe(
                            orig_chat_id, deleted_msg_id, 'no_links', mark_deleted=True)
                        continue

                    # === ATOMIC CLAIM (يمنع duplicate rescue عبر monitors متعددة) ===
                    claim_token = None
                    if self.message_claim:
                        claim_token = await self.message_claim.claim(
                            orig_chat_id, deleted_msg_id, 'delete_handler', orig_source_phone
                        )
                        if claim_token is None:
                            logging.info(
                                f"[DELETE-HANDLER] ⏭️ Duplicate message claim: "
                                f"chat_id={orig_chat_id} msg_id={deleted_msg_id} — skip"
                            )
                            await self._journal_set_state_safe(
                                orig_chat_id, deleted_msg_id, 'dup_claim', mark_deleted=True)
                            continue

                    logging.warning(
                        f"[DELETE-HANDLER] 🚨⏰ RESCUED deleted msg_id={deleted_msg_id} "
                        f"from '{group_name[:30]}' (had {len(links)} links, "
                        f"source={rescue_source or 'cache'}) — processing NOW"
                    )

                    try:
                        rescued = await self._rescue_enqueue_links(
                            links, raw_text, group_name, sender_name, chat_username,
                            chat_link_type, orig_chat_id, orig_source_phone, deleted_msg_id,
                            pipeline_tag='DELETE-HANDLER')

                        # [PR-METRICS-FIX] سجّل الميتريك لإنقاذ cache/journal/
                        # get_messages — سابقًا كان record_delete_rescued يُستدعى
                        # فقط في فرع LRB (bot.py:5436)، فلم تُحتسب أي من إنقاذات
                        # cache/journal/get_messages رغم أن HELP text يدّعيها.
                        # نستدعيها هنا بصورة best-effort مع source label صريح.
                        try:
                            await self.metrics.record_delete_rescued(
                                rescue_source or 'cache')
                        except Exception:
                            pass
                        # سجل لوج جنائي صريح للمسار غير-LRB أيضًا
                        logging.info(
                            f"[DELETE-HANDLER] ✅ {rescue_source or 'cache'}-RESCUED "
                            f"msg_id={deleted_msg_id} chat_id={orig_chat_id} "
                            f"new_count={1 if rescued else 0} source={orig_source_phone}"
                        )

                        # === Mark as PROCESSED (claim_token verified) ===
                        if self.message_claim and claim_token:
                            ok = await self.message_claim.mark_processed(
                                orig_chat_id, deleted_msg_id, claim_token
                            )
                            if not ok:
                                logging.warning(
                                    f"[DELETE-HANDLER] mark_processed rejected (stale token) "
                                    f"for msg ({orig_chat_id}, {deleted_msg_id})"
                                )
                        await self._journal_set_state_safe(
                            orig_chat_id, deleted_msg_id,
                            'rescued' if rescued else 'processed', mark_deleted=True)
                    except Exception as inner_e:
                        # === Mark as FAILED (allows retry by Polling/Scanner/another delete) ===
                        if self.message_claim and claim_token:
                            await self.message_claim.mark_failed(
                                orig_chat_id, deleted_msg_id, claim_token, str(inner_e))
                        await self._journal_set_state_safe(
                            orig_chat_id, deleted_msg_id, 'pending',
                            error=str(inner_e), mark_deleted=True)
                        logging.error(
                            f"[DELETE-HANDLER] processing error for msg "
                            f"({orig_chat_id}, {deleted_msg_id}): {inner_e}"
                        )
                except Exception as iter_e:
                    # [N04] Isolation: log + continue so the remaining ids in
                    # the mass-delete batch still get processed.
                    logging.error(
                        f"[DELETE-HANDLER] iteration error for msg_id={deleted_msg_id} "
                        f"chat_id={chat_id} — skipping (continuing batch): {iter_e}"
                    )
                    continue

            # === RECONCILE: التقط أي رسائل أخرى فاتتنا في الشات ===
            if reconcile_chats and getattr(
                    getattr(self, 'config', None), 'delete_miss_reconcile', True):
                for rc_chat_id in reconcile_chats:
                    self._spawn_reconcile(rc_chat_id, source_phone)

        except Exception as e:
            logging.error(f"Delete handler error: {e}", exc_info=True)

    async def _msg_cache_cleanup(self):
        """ينظف الرسائل القديمة من cache كل 30 ثانية (لتجنب تضخم الذاكرة)."""
        while self._running:
            try:
                await asyncio.sleep(30)
                now = time.time()
                expired_keys = []
                async with self._msg_cache_lock:
                    for key, val in list(self._msg_cache.items()):
                        if now - val.get('received_at', 0) > self._msg_cache_ttl:
                            expired_keys.append(key)
                    for key in expired_keys:
                        self._msg_cache.pop(key, None)
                if expired_keys:
                    logging.debug(f"[CACHE] cleaned {len(expired_keys)} expired messages (size={len(self._msg_cache)})")
                # [PR-LRB-TTL] شغّل LRB eviction الدوري — سابقًا كان
                # _link_ring_evict معرّفًا لكن لا أحد يستدعيه (dead code path).
                # الآن نُشغّله هنا كل 30s ليطرده entries المنتهية بالـTTL فعليًا.
                try:
                    evicted = await self._link_ring_evict()
                    if evicted > 0:
                        logging.debug(
                            f"[LRB] TTL-evicted {evicted} entries "
                            f"(size={len(self._link_ring)}, "
                            f"ttl_evicted_total={self._link_ring_ttl_evicted})"
                        )
                except Exception as lrb_e:
                    logging.debug(f"[LRB] periodic evict error: {lrb_e}")
                # ملخص INFO دوري للرسائل بلا نص (رؤية جنائية بدون spam)
                if getattr(self, '_no_text_count', 0) > 0:
                    logging.info(
                        f"[PIPELINE-1] (summary) {self._no_text_count} message(s) "
                        f"had no text in last 30s"
                    )
                    self._no_text_count = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.debug(f"[CACHE] cleanup error: {e}")

    async def _cleanup_processed_messages_loop(self):
        """ينظف processed_messages كل ساعة.
        
        - 'claimed' بـ lease منتهي → DELETE (يسمح بإعادة المحاولة)
        - 'processed' older than 7 days → DELETE
        - 'failed' older than 30 days → DELETE (للتحليل)
        """
        while self._running:
            try:
                await asyncio.sleep(3600)  # كل ساعة
                await self.prod_db.cleanup_processed_messages()
                count = await self.prod_db.count_processed_messages()
                logging.info(f"[CLEANUP] processed_messages count after cleanup: {count}")
                # === Journal cleanup (كل ساعة) ===
                try:
                    jr = await self.prod_db.journal_cleanup(
                        retention_s=self.config.journal_retention_s,
                        short_retention_s=self.config.journal_no_text_retention_s)
                    if jr.get('removed_old', 0) or jr.get('removed_light', 0):
                        logging.info(f"[CLEANUP] journal cleanup: {jr}")
                except Exception as je:
                    logging.debug(f"[CLEANUP] journal error: {je}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[CLEANUP] error: {e}")
                await asyncio.sleep(60)

    async def _refresh_active_polling_chats(self):
        """يحدّث قائمة المجموعات النشطة للـ polling.
        
        نختار أهم 60 مجموعة (الأكثر نشاطاً):
        - priority: المجموعات اللي سبق شفنا فيها رسائل (من _polling_state)
        - ثم: آخر المجموعات المسجّلة (حسب last_seen)
        هذا يضمن إن المجموعات النشطة فعلاً تكون دائماً في القائمة
        """
        try:
            chats = await self.prod_db.get_monitored_chats(limit=5000)
            # فلترة: فقط المجموعات (مو القنوات) + اللي لها chat_id
            candidate_chats = [
                c for c in chats
                if c.get('link_type') == 'group' and c.get('chat_id')
            ]
            
            # Partition: مجموعات لها polling state سابق (نشطة) + الباقي
            active = []
            inactive = []
            for c in candidate_chats:
                cid = c.get('chat_id')
                if cid in self._polling_state and self._polling_state[cid] > 0:
                    c['_last_polled_msg_id'] = self._polling_state[cid]
                    active.append(c)
                else:
                    inactive.append(c)
            
            # ترتيب active حسب آخر msg_id (الأحدث أولاً)
            active.sort(key=lambda c: c.get('_last_polled_msg_id', 0), reverse=True)
            # ترتيب inactive حسب last_seen (الأحدث أولاً)
            inactive.sort(key=lambda c: c.get('last_seen', ''), reverse=True)
            
            # اجمع: active أولاً ثم inactive (لحد 200)
            self._active_polling_chats = (active + inactive)[:200]
            
            # === ضمان إن مجموعات الاختبار تكون دائماً في القائمة ===
            # حتى لو كانت خارج أول 200 حسب الترتيب
            TEST_CHAT_IDS = {
                -1001181518634,  # G_TaibahuD (جامعة طيبة | المناقشة) — مجموعة اختبار
                -1002207747724,  # SummerSEU (الفصل الصيفي للتحضيري SEU) — مجموعة هدف
            }
            current_ids = {c.get('chat_id') for c in self._active_polling_chats}
            for c in candidate_chats:
                if c.get('chat_id') in TEST_CHAT_IDS and c.get('chat_id') not in current_ids:
                    self._active_polling_chats.append(c)
                    current_ids.add(c.get('chat_id'))
                    logging.info(f"[POLLING] Force-added test chat: {c.get('chat_title', '')[:30]} (id={c.get('chat_id')})")
            
            # تحقق من حالة test chats
            for test_id in TEST_CHAT_IDS:
                if test_id in current_ids:
                    logging.info(f"[POLLING] Test chat {test_id} is IN polling list (total={len(self._active_polling_chats)})")
                else:
                    logging.warning(f"[POLLING] Test chat {test_id} is NOT in polling list!")
            
            # نظّف _polling_state من المجموعات اللي ما عادت في القائمة
            current_ids = {c.get('chat_id') for c in self._active_polling_chats}
            stale = [k for k in self._polling_state if k not in current_ids]
            for k in stale:
                self._polling_state.pop(k, None)
            
            logging.info(
                f"[POLLING] Refreshed active chats list: {len(self._active_polling_chats)} groups "
                f"({len(active)} active + {len(self._active_polling_chats) - len(active)} new) — "
                f"top: {(self._active_polling_chats[0].get('chat_title') if self._active_polling_chats else 'none')[:30]}"
            )
        except Exception as e:
            logging.error(f"[POLLING] refresh error: {e}")

    async def _active_polling_worker(self):
        """Active Polling Worker — يسحب آخر 3 رسائل من كل مجموعة نشطة كل 3 ثواني.
        
        الحل الجذري لمشكلة بوتات الحماية:
        - بوتات الحماية (جبل/صقير) admin وتحذف الرسائل بسرعة (100-300ms)
        - بوتنا عضو عادي، ما يحصل على MessageDeleted event
        - NewMessage event قد يتأخر أو يصل بعد الحذف
        - الحل: polling نشط كل 3 ثواني يلتقط الرسائل قبل ما تُحذف
        
        الاستراتيجية:
        1. لكل مجموعة نشطة، نتذكر آخر msg_id شفناه
        2. نسحب الرسائل الجديدة فقط (min_id=last_msg_id)
        3. نخزن كل رسالة في cache + نعالجها كأنها NewMessage
        """
        await asyncio.sleep(20)  # انتظر البوت يكمل الإقلاع
        logging.info(f"🔄 Active Polling Worker started — interval={self._polling_interval}s")
        # أول تشغيل: حدّث قائمة المجموعات النشطة
        await self._refresh_active_polling_chats()
        last_refresh = time.time()
        
        while self._running:
            try:
                # حدّث القائمة كل 5 دقايق
                if time.time() - last_refresh > 300:
                    await self._refresh_active_polling_chats()
                    last_refresh = time.time()
                
                # لو ما فيه مجموعات، انتظر
                if not self._active_polling_chats:
                    await asyncio.sleep(self._polling_interval)
                    continue
                
                # اختيار مراقب نشط (نلف على المراقبين)
                active_phones = [
                    p for p, c in self.user_clients.items()
                    if c and c.is_connected()
                ]
                if not active_phones:
                    await asyncio.sleep(self._polling_interval)
                    continue
                
                # قسّم المجموعات على المراقبين (كل مراقب يأخذ شريحة)
                phones_count = len(active_phones)
                tasks = []
                for idx, chat in enumerate(self._active_polling_chats):
                    phone = active_phones[idx % phones_count]
                    tasks.append(self._poll_one_chat(phone, chat))
                
                # شغّل كل الـ polling بالتوازي
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                
                await asyncio.sleep(self._polling_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[POLLING] worker error: {e}", exc_info=True)
                await asyncio.sleep(self._polling_interval)

    async def _poll_one_chat(self, phone: str, chat: dict):
        """يسحب آخر 3 رسائل من مجموعة واحدة ويستخرج أي روابط جديدة."""
        chat_id = chat.get('chat_id')
        chat_title = chat.get('chat_title', f'chat_{chat_id}')
        if not chat_id:
            return
        
        client = self.user_clients.get(phone)
        if not client or not client.is_connected():
            return
        
        # آخر msg_id شفناه لهذي المجموعة
        async with self._polling_lock:
            last_msg_id = self._polling_state.get(chat_id, 0)
        
        try:
            # اسحب آخر 3 رسائل بـ id > last_msg_id
            # limit=3 + min_id=last_msg_id = كفاءة عالية (صفر payload لو ما فيه جديد)
            messages = await client.get_messages(
                chat_id, limit=3, min_id=last_msg_id
            )
            if not messages:
                return  # ما فيه جديد
            
            # نجاح — صفّر عدّاد الإخفاقات المتتالية
            self._chat_poll_failures[chat_id] = 0
            # حدّث آخر msg_id شفناه
            new_max_id = max(m.id for m in messages)
            async with self._polling_lock:
                if new_max_id > self._polling_state.get(chat_id, 0):
                    self._polling_state[chat_id] = new_max_id
            # [B02] persist last_msg_id + last_activity to monitored_chats so a
            # restart/reschedule resumes from the real high-water mark (was
            # in-memory _polling_state only → reset to 0 → re-fetch + dup claims).
            try:
                await self.prod_db.update_monitored_chat(
                    chat_id, last_msg_id=new_max_id,
                    last_activity=datetime.utcnow().isoformat())
            except Exception as _b02e:
                logging.debug(f"[POLLING] update_monitored_chat last_msg_id failed: {_b02e}")
            
            # عالج كل رسالة جديدة
            for msg in messages:
                if not msg or not msg.raw_text:
                    continue
                # تجاهل رسائل البوت نفسه
                if msg.out:
                    continue
                
                # تحقق أنها مو مكررة في cache
                cache_key = (chat_id, msg.id)
                async with self._msg_cache_lock:
                    if cache_key in self._msg_cache:
                        continue  # سبق عالجناها via NewMessage

                # === ATOMIC CLAIM (يمنع التكرار من NewMessage + Scanner + حسابات أخرى) ===
                claim_token = None
                if self.message_claim:
                    claim_token = await self.message_claim.claim(chat_id, msg.id, 'polling', phone)
                    if claim_token is None:
                        # سبق معالجتها بواسطة NewMessage أو Scanner أو حساب آخر
                        continue

                # استخرج الروابط
                links = LinkNormalizer.extract_links(msg.raw_text)
                if not links:
                    # ما فيها روابط — سجل كـ processed
                    if self.message_claim:
                        await self.message_claim.mark_processed(chat_id, msg.id, claim_token)
                    # خزّن في cache (لو بوت حماية حذفها بعدين)
                    async with self._msg_cache_lock:
                        self._msg_cache[cache_key] = {
                            'raw_text': msg.raw_text,
                            'source_phone': phone,
                            'received_at': time.time(),
                            'chat_id': chat_id,
                            'msg_id': msg.id,
                            'sender_id': msg.sender_id or 0,
                            'chat_title': chat_title,
                            'chat_username': chat.get('username', ''),
                            'chat_link_type': 'group',
                            'sender_name': self._get_sender_name(msg.sender) if msg.sender else 'Unknown',
                            'processed': True,
                            'via_polling': True,
                        }
                    await self._journal_write(
                        chat_id, msg.id, msg.raw_text, phone,
                        chat_title=chat_title, chat_username=chat.get('username', ''),
                        sender_name=self._get_sender_name(msg.sender) if msg.sender else 'Unknown',
                        state='no_links')
                    continue
                
                # [PR-POLLING-LRB] اكتب الروابط المُستخرَجة في LRB أيضًا —
                # سابقًا كان الـpolling يكتب فقط _msg_cache (مصدر الإنقاذ #2)،
                # فلو حُذفت الرسالة قبل استهلاك الـcache (أو بعد TTL 120s)،
                # لم يكن مسار LRB (مصدر #1 الأسرع) يحتوي الروابط. الآن نُوحّد
                # مسار الالتقاط: كل من Raw hook + NewMessage + Polling يكتبون
                # LRB، فيصبح الإنقاذ مستقلاً عن أي مسار التقاط واحد.
                try:
                    await self._link_ring_put(
                        chat_id, msg.id,
                        [l.get('normalized') or l.get('raw') for l in links])
                except Exception:
                    pass  # LRB فشل — لكن cache + journal سيغطّان

                # الرسالة فيها روابط — عالجها كأنها NewMessage
                logging.info(
                    f"[POLLING] 📨🔗 New link found via polling from '{chat_title[:30]}' "
                    f"msg_id={msg.id} (last_seen={last_msg_id})"
                )
                
                # خزّن في cache
                async with self._msg_cache_lock:
                    self._msg_cache[cache_key] = {
                        'raw_text': msg.raw_text,
                        'source_phone': phone,
                        'received_at': time.time(),
                        'chat_id': chat_id,
                        'msg_id': msg.id,
                        'sender_id': msg.sender_id or 0,
                        'chat_title': chat_title,
                        'chat_username': chat.get('username', ''),
                        'chat_link_type': 'group',
                        'sender_name': self._get_sender_name(msg.sender) if msg.sender else 'Unknown',
                        'processed': False,
                        'via_polling': True,
                    }
                await self._journal_write(
                    chat_id, msg.id, msg.raw_text, phone,
                    chat_title=chat_title, chat_username=chat.get('username', ''),
                    sender_name=self._get_sender_name(msg.sender) if msg.sender else 'Unknown',
                    state='pending')
                
                try:
                    # سجل المجموعة
                    try:
                        is_new = await self.prod_db.add_monitored_chat(
                            chat_id=chat_id,
                            chat_title=chat_title,
                            username=chat.get('username', ''),
                            link_type='group',
                            monitored_by=phone,
                        )
                    except Exception as e:
                        logging.debug(f"[POLLING] add_monitored error: {e}")
                    
                    # enqueue كل رابط
                    sender_name = self._get_sender_name(msg.sender) if msg.sender else 'Unknown'
                    for link_info in links:
                        link_data = {
                            **link_info,
                            'group_name': chat_title,
                            'sender_name': sender_name,
                            'sender_contact': extract_sender_contact(msg.raw_text),
                            'source_phone': phone,
                            'message_text': msg.raw_text,
                            'message_link': f"https://t.me/c/{str(chat_id).replace('-100', '')}/{msg.id}" if chat_id else None,
                        }
                        
                        # Blacklist
                        link_raw = link_info['raw'].lower()
                        username_raw = (link_info.get('username') or '').lower()
                        full_text_check = f"{msg.raw_text} {link_raw} {username_raw}".lower()
                        is_bad, bad_reason = GulfFilter.is_blacklisted(
                            full_text_check, username_raw, link_info['raw'], chat_title
                        )
                        if is_bad:
                            logging.info(
                                f"[POLLING] 🚫 BLACKLISTED: {link_info['raw'][:50]} ({bad_reason})"
                            )
                            await self.metrics.record_skip(f'blacklist_{bad_reason}')
                            continue
                        
                        # enqueue
                        is_new = await self.prod_db.enqueue_link(link_data)
                        if is_new:
                            await self.prod_db.set_group_state(
                                link_info['normalized'], GroupState.DISCOVERED,
                                link_info['raw'], chat_title)
                            logging.info(
                                f"[POLLING-PIPELINE] ✅ Link enqueued: {link_info['raw'][:60]} "
                                f"(via polling from '{chat_title[:30]}')"
                            )
                        else:
                            await self.metrics.record_duplicate()
                            logging.info(f"[POLLING-PIPELINE] ⏭️ Duplicate: {link_info['normalized'][:60]}")

                    # === Mark as PROCESSED ===
                    if self.message_claim:
                        await self.message_claim.mark_processed(chat_id, msg.id, claim_token)
                    await self._journal_set_state_safe(chat_id, msg.id, 'processed')

                except Exception as inner_e:
                    # === Mark as FAILED (allows retry) ===
                    if self.message_claim:
                        await self.message_claim.mark_failed(chat_id, msg.id, claim_token, str(inner_e))
                    await self._journal_set_state_safe(chat_id, msg.id, 'pending', error=str(inner_e))
                    logging.error(f"[POLLING] processing error for msg ({chat_id}, {msg.id}): {inner_e}")

                # علّم الرسالة كـ معالَجة في cache
                async with self._msg_cache_lock:
                    if cache_key in self._msg_cache:
                        self._msg_cache[cache_key]['processed'] = True
                
        except FloodWaitError as e:
            # [N08] Register the FloodWait with floodwait_mgr so the joiner
            # scheduler and other workers see the account as blocked (the
            # old behavior slept here but never persisted the wait — the
            # joiner would re-pick this account moments later, doubling the
            # next FloodWait penalty).
            try:
                await self.floodwait_mgr.block(phone, e.seconds)
            except Exception as _fwe:
                logging.debug(f"[POLLING] floodwait_mgr.block failed: {_fwe}")
            logging.warning(f"[POLLING] FloodWait {e.seconds}s for chat={chat_id} ({phone}) — sleeping + registered")
            await asyncio.sleep(min(e.seconds, 30))
        except Exception as e:
            # تجاهل أخطاء "chat not found" / "private" — ما تكررها
            err_str = str(e).lower()
            if any(s in err_str for s in ['chat not found', 'channel private', 'forbidden', 'banned']):
                # الحساب فقد وصوله لهذا الشات — أزله من reader_phones (لهذا الشات فقط)
                # حتى لا يُختار مجددًا ويهدر محاولات polling
                if any(s in err_str for s in ['channel private', 'forbidden', 'banned']) and self.source_registry:
                    try:
                        removed = await self.source_registry.remove_reader(chat_id, phone)
                        if removed:
                            logging.info(
                                f"[POLLING] Removed reader {phone} from chat {chat_id} "
                                f"(account lost access)"
                            )
                    except Exception as re_err:
                        logging.debug(f"[POLLING] remove_reader error: {re_err}")
                # عدّ الإخفاقات المتتالية — بعد 5، أجّل الشات ساعتين (يوفر دورة polling)
                self._chat_poll_failures[chat_id] = self._chat_poll_failures.get(chat_id, 0) + 1
                if self._chat_poll_failures[chat_id] >= 5:
                    try:
                        await self.prod_db.update_monitored_chat(
                            chat_id,
                            next_poll_at=(datetime.now() + timedelta(hours=2)).isoformat())
                        logging.info(
                            f"[POLLING] Deferred chat '{chat_title[:30]}' by 2h "
                            f"after 5 consecutive failures"
                        )
                    except Exception:
                        pass
                    self._chat_poll_failures[chat_id] = 0
                # شيل المجموعة من قائمة الـ polling القديمة (legacy)
                if chat in self._active_polling_chats:
                    self._active_polling_chats.remove(chat)
                    logging.info(f"[POLLING] Removed chat '{chat_title[:30]}' from polling list (error: {err_str[:50]})")
            else:
                logging.debug(f"[POLLING] chat={chat_id} error: {e}")

    async def _send(self, text, retries=3, buttons=None, parse_mode='html') -> Tuple[bool, Optional[int]]:
        """يرسل رسالة للقناة ويتحقق من قبولها.

        Contract:
            - يرجع (True, message_id) فقط إذا Telegram أكد الإرسال وأرجع Message.id
            - يرجع (False, None) إذا فشلت كل المحاولات
            - لا يرجع None أبداً
            - يلتقط: FloodWaitError, RPCError, OSError, ConnectionError, Timeout,
                      disconnected client, unexpected Exception

        Args:
            text: نص الرسالة
            retries: عدد المحاولات
            buttons: أزرار اختيارية
            parse_mode: html أو md

        Returns:
            (success: bool, message_id: Optional[int])
        """
        async with self._send_lock:
            total_waited = 0.0
            max_total_wait = 120.0  # 2 minutes hard cap
            last_error = "unknown"

            for attempt in range(1, retries + 1):
                try:
                    # تحقق من اتصال البوت
                    if not self.bot_client or not self.bot_client.is_connected():
                        last_error = "bot_client not connected"
                        logging.error(f"[SEND] ❌ bot_client not connected (attempt {attempt}/{retries})")
                        await asyncio.sleep(min(5 * attempt, 30))
                        continue

                    logging.debug(f"[SEND] attempt {attempt}/{retries} → channel={self.config.channel_id}")

                    # استدعاء Telegram API
                    result = await self.bot_client.send_message(
                        self.config.channel_id, text,
                        parse_mode=parse_mode,
                        buttons=buttons,
                        link_preview=False
                    )

                    # Telegram يرجع Message object عند النجاح
                    if result and hasattr(result, 'id'):
                        message_id = result.id
                        logging.info(
                            f"[SEND] ✅ Telegram accepted message\n"
                            f"[SEND] message_id={message_id}\n"
                            f"[SEND] channel={self.config.channel_id}"
                        )
                        return True, message_id
                    else:
                        # Telegram رجع بدون Message — غير متوقع
                        last_error = f"unexpected return: {type(result).__name__}"
                        logging.error(f"[SEND] ❌ unexpected return type: {type(result)}")
                        # اعتبره فشل وأعد المحاولة
                        await asyncio.sleep(min(5 * attempt, 30))
                        continue

                except FloodWaitError as e:
                    last_error = f"FloodWaitError({e.seconds}s)"
                    wait = min(e.seconds + 1, max_total_wait - total_waited)
                    if wait <= 0:
                        logging.error(
                            f"[SEND] ❌ FloodWait cap ({max_total_wait}s) reached, giving up\n"
                            f"[SEND] channel={self.config.channel_id}\n"
                            f"[SEND] reason={last_error}\n"
                            f"[SEND] attempts={attempt}"
                        )
                        return False, None
                    total_waited += wait
                    logging.warning(f"[SEND] FloodWait {e.seconds}s (attempt {attempt}) — sleeping {wait}s")
                    await asyncio.sleep(wait)

                except (RPCError, OSError, ConnectionError) as e:
                    last_error = f"{type(e).__name__}: {str(e)[:100]}"
                    wait = min(10 * attempt, 60, max_total_wait - total_waited)
                    if wait <= 0:
                        logging.error(
                            f"[SEND] ❌ FAILED\n"
                            f"[SEND] channel={self.config.channel_id}\n"
                            f"[SEND] reason={last_error}\n"
                            f"[SEND] attempts={attempt}"
                        )
                        return False, None
                    total_waited += wait
                    logging.warning(f"[SEND] {type(e).__name__} (attempt {attempt}) — retrying in {wait}s")
                    await asyncio.sleep(wait)

                except asyncio.TimeoutError:
                    last_error = "TimeoutError"
                    logging.warning(f"[SEND] Timeout (attempt {attempt})")
                    await asyncio.sleep(min(5 * attempt, 30))

                except asyncio.CancelledError:
                    logging.warning("[SEND] Cancelled by caller")
                    raise

                except Exception as e:
                    last_error = f"Unexpected {type(e).__name__}: {str(e)[:100]}"
                    logging.error(
                        f"[SEND] ❌ unexpected exception (attempt {attempt}): {e}",
                        exc_info=True
                    )
                    await asyncio.sleep(min(5 * attempt, 30))

            # كل المحاولات فشلت
            logging.error(
                f"[SEND] ❌ FAILED\n"
                f"[SEND] channel={self.config.channel_id}\n"
                f"[SEND] reason={last_error}\n"
                f"[SEND] attempts={retries}"
            )
            return False, None

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

            # Cleanup expired login sessions on every private message
            # (cheap O(N) scan, N is small due to concurrent limit)
            self._cleanup_expired_login_sessions()

            # الأوامر
            # Boot (بدون شرطة) أو /start كلاهما يفتح القائمة الرئيسية
            if text.strip().lower() in ("boot", "/start", "/boot", "بوت", "ابدأ", "Start"):
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

            # === الأوامر الإدارية (تعمل في الخاص والقناة) ===
            # Owner only — تحقق من الصلاحية
            is_owner = (self.config.owner_id is None or sender_id == self.config.owner_id)

            if is_owner:
                # إنشاء event صناعي ليتم معالجته بواسطة _on_command
                # (يحتوي على نفس خصائص event القناة)
                cmd = text.split()[0] if text.split() else ''
                logging.info(f"[PRIVATE CMD] {sender_id}: {cmd}")

                async def private_reply(t):
                    try: await event.reply(t)
                    except Exception as e:
                        logging.error(f"[PRIVATE CMD] reply failed: {e}")

                # إعادة توجيه الأوامر الإدارية لمعالج القناة
                admin_commands = [
                    '/pause_join', '/resume_join', '/set_role',
                    '/enable_joiner', '/disable_joiner', '/join_status',
                    '/verify', '/sqlite_check', '/clear_floodwait', '/ai_mode',
                    '/leave_bad_groups', '/clean_queue', '/rejoin_published',
                    '/bulk_join', '/bulk_join_status', '/bulk_join_stop',
                    '/cleanup_preview', '/cleanup_links', '/cleanup_status',
                    '/live_audit', '/status', '/watchers', '/help',
                    '/joined_groups', '/queue', '/debug_pipeline',
                    '/pending_approvals',
                ]

                if cmd in admin_commands:
                    # معالجة مباشرة — استدعِ _on_command مع event معدّل
                    # أنشئ كائن يشبه event القناة
                    class FakeEvent:
                        def __init__(self, orig_event, text):
                            self.raw_text = text
                            self.text = text
                            self.message = orig_event.message
                            self.chat_id = orig_event.chat_id
                            self.sender_id = orig_event.sender_id
                            self._sender = None
                        async def get_sender(self):
                            return sender
                        async def reply(self, t):
                            await event.reply(t)
                        async def answer(self, msg='', alert=False):
                            try: await event.reply(msg)
                            except: pass

                    fake_event = FakeEvent(event, text)
                    await self._on_command(fake_event)
                    return

            # رسالة غير معروفة
            await event.reply(
                "🤖 أهلاً!\n\n"
                "📌 الأوامر المتاحة:\n"
                "• /start - البدء\n"
                "• /login - تسجيل الدخول بحسابك\n"
                "• /status - حالتك\n"
                "• /cancel - إلغاء العملية\n\n"
                "💡 اكتب Boot لفتح القائمة الرئيسية"
            )

        except Exception as e:
            logging.error(f"Private message error: {e}", exc_info=True)

    async def _handle_start(self, event, sender):
        """معالج أمر /start - يعرض القائمة بالأزرار"""
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

    async def _start_login_with_role(self, event, sender, role: str):
        """يبدأ تسجيل الدخول بعد اختيار الدور"""
        sender_id = sender.id

        if sender_id in self._login_sessions:
            await event.edit("⚠️ لديك عملية تسجيل قائمة. أرسل /cancel للإلغاء.")
            return

        if len(self._login_sessions) >= 3:
            await event.edit("⚠️ الخادم مشغول. حاول بعد دقيقة.")
            return

        role_name = "👁️ مراقب" if role == "monitor" else "🚀 فدائي"

        self._login_sessions[sender_id] = {
            "step": "phone",
            "temp_client": None,
            "phone": None,
            "phone_code_hash": None,
            "started_at": datetime.now(),
            "role": role,
        }

        await event.edit(
            f"🔐 تسجيل الدخول — **{role_name}**\n\n"
            "📌 أرسل رقم هاتفك بالصيغة الدولية.\n"
            "مثال: +967770309310\n\n"
            "⚠️ الرقم يجب أن يكون مرتبطاً بحساب تيليجرام.\n\n"
            "للإلغاء: /cancel",
            parse_mode='html'
        )

    async def _handle_login_start(self, event, sender):
        sender_id = sender.id

        # Cleanup expired login sessions
        self._cleanup_expired_login_sessions()

        # التحقق من عدم وجود تسجيل سابق
        if sender_id in self._login_sessions:
            await event.reply("⚠️ لديك عملية تسجيل قائمة بالفعل. أرسل /cancel للإلغاء.")
            return

        # Rate limit
        if len(self._login_sessions) >= 3:
            await event.reply("⚠️ الخادم مشغول بعدة تسجيلات. حاول بعد دقيقة.")
            return

        # عرض زرين لاختيار الدور
        buttons = [
            [Button.inline("👁️ حساب مراقب (Monitor)", b"login_monitor")],
            [Button.inline("🚀 حساب فدائي (Joiner)", b"login_joiner")],
            [Button.inline("🔙 إلغاء", b"login_cancel")],
        ]

        await event.reply(
            "🔐 **اختر دور الحساب**\n\n"
            "👤 **المراقب (Monitor):**\n"
            "• يراقب المجموعات ويلتقط الروابط\n"
            "• للقراءة فقط — ما ينضم لأي مجموعة\n"
            "• آمن — خطر الحظر منخفض\n\n"
            "🚀 **الفدائي (Joiner):**\n"
            "• ينضم تلقائياً للمجموعات المكتشفة\n"
            "• بحدود أمان: 10 مجموعات/يوم، 30-60 دقيقة فاصل\n"
            "• يتوقف تلقائياً عند انخفاض الصحة\n\n"
            "⚠️ اختر الدور المناسب للمهمة التي تريدها:",
            buttons=buttons,
            parse_mode='html'
        )

    def _cleanup_expired_login_sessions(self):
        """Remove login sessions older than TTL. Disconnects temp_client
        to prevent connection leak. Also prunes expired cooldown entries."""
        now = datetime.now()
        # Cleanup expired login sessions
        if self._login_sessions:
            expired = [
                sid for sid, sess in self._login_sessions.items()
                if (now - sess.get("started_at", now)) > self._login_session_ttl
            ]
            for sid in expired:
                sess = self._login_sessions.pop(sid, None)
                if sess and sess.get("temp_client"):
                    # Schedule disconnect (don't await — this is a sync method)
                    try:
                        asyncio.create_task(sess["temp_client"].disconnect())
                    except Exception:
                        pass
                logging.info(f"[LOGIN] Expired session for sender_id={sid}")
        # Cleanup expired cooldown entries (keep dict bounded)
        if self._login_cooldowns:
            expired_cooldowns = [
                sid for sid, next_time in self._login_cooldowns.items()
                if next_time < now
            ]
            for sid in expired_cooldowns:
                self._login_cooldowns.pop(sid, None)

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

            # Rate limit: prevent Telegram from banning the account
            # (repeated send_code_request → auto-ban)
            now = datetime.now()
            last_request = self._login_cooldowns.get(sender_id)
            if last_request and now < last_request:
                wait_sec = int((last_request - now).total_seconds())
                await event.reply(
                    f"⏳ يرجى الانتظار {wait_sec} ثانية قبل طلب كود جديد.\n"
                    f"(لمنع حظر حسابك من تيليجرام)"
                )
                return
            # Phone format validation: + followed by 7-15 digits
            if not re.match(r"^\+\d{7,15}$", phone):
                await event.reply(
                    "❌ صيغة الرقم غير صحيحة.\n"
                    "استخدم: +دول رقم (مثال: +967770309310)\n\n"
                    "أعد الإرسال أو /cancel"
                )
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
                # Set cooldown AFTER successful code request
                self._login_cooldowns[sender_id] = datetime.now() + self._login_cooldown

                await event.reply(
                    "✅ تم إرسال كود التحقق إلى حسابك في تيليجرام.\n\n"
                    "📲 تحقق من رسائل تيليجرام (من حساب Telegram الرسمي).\n\n"
                    "📌 أرسل الكود الآن (مثال: 12345):\n\n"
                    "للإلغاء: /cancel"
                )
            except Exception as e:
                logging.error(f"Login phone error: {e}")
                await event.reply(f"❌ خطأ: {e}\n\nأعد المحاولة بـ /login أو /cancel")
                if session.get("temp_client"):
                    try: await session["temp_client"].disconnect()
                    except Exception: pass
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

                # حفظ في DB مع الدور المختار
                role = session.get("role", "monitor")
                added = await self.db.add_watcher(phone, display_name, string_session, role)
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
                    f"🎉 تم تسجيل الحساب بنجاح!\n\n"
                    f"👤 الاسم: {display_name}\n"
                    f"📞 الرقم: {phone}\n"
                    f"🏷️ الدور: {'🚀 فدائي (Joiner)' if role == 'joiner' else '👁️ مراقب (Monitor)'}\n\n"
                    + (f"🚀 **حساب فدائي** — سيبدأ البوت بالانضمام التلقائي\n"
                       f"للمجموعات المكتشفة بحدود أمان:\n"
                       f"• 10 مجموعات/يوم\n"
                       f"• 30-60 دقيقة بين كل انضمام\n"
                       f"• أوقات نشطة: 8ص-12ظ، 4ع-10م\n\n"
                       if role == 'joiner'
                       else f"👁️ **حساب مراقب** — سيبدأ البوت بمراقبة\n"
                       f"مجموعاتك ويسحب الروابط تلقائياً.\n\n")
                    + f"✅ شكراً لانضمامك!"
                )

                logging.info(f"[LOGIN] New {role} registered: {phone} ({display_name})")

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
                role = session.get("role", "monitor")

                added = await self.db.add_watcher(phone, display_name, string_session, role)
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
                    f"🎉 تم تسجيل الحساب بنجاح!\n\n"
                    f"👤 الاسم: {display_name}\n"
                    f"📞 الرقم: {phone}\n"
                    f"🏷️ الدور: {'🚀 فدائي (Joiner)' if role == 'joiner' else '👁️ مراقب (Monitor)'}\n\n"
                    + (f"🚀 **حساب فدائي** — سيبدأ البوت بالانضمام التلقائي\n"
                       f"للمجموعات المكتشفة بحدود أمان.\n\n"
                       if role == 'joiner'
                       else f"👁️ **حساب مراقب** — سيبدأ البوت بمراقبة\n"
                       f"مجموعاتك ويسحب الروابط تلقائياً.\n\n")
                    + f"✅ شكراً لانضمامك!"
                )
                logging.info(f"[LOGIN] New {role} (2FA): {phone}")

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
            # Cap text length to prevent abuse (10KB max for a command)
            if len(text) > 10000:
                logging.warning(f"[CMD] Oversized command text ({len(text)} chars), ignoring")
                return
            parts = text.split()
            if not parts: return
            cmd = parts[0].lower()
            # Cap command length (prevents log injection with huge cmd strings)
            if len(cmd) > 100:
                logging.warning(f"[CMD] Command too long ({len(cmd)} chars), ignoring")
                return

            # Authorization: when OWNER_ID is configured, only the owner
            # can run channel commands. Use `is not None` (not truthiness)
            # so owner_id=0 is still treated as configured.
            if self.config.owner_id is not None:
                s = await event.get_sender()
                if getattr(s, 'id', None) != self.config.owner_id:
                    logging.warning(f"[CMD] Unauthorized command '{cmd}' from sender_id={getattr(s, 'id', None)}")
                    return

            logging.info(f"[CMD] {cmd}")

            async def reply(t):
                try: await self.bot_client.send_message(self.config.channel_id, t)
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
                    try: await self.bot_client.send_message(self.config.channel_id, t)
                    except Exception as e2:
                        logging.error(f"[CMD] reply failed after FloodWait: {type(e2).__name__}: {e2}")
                except Exception as e:
                    logging.error(f"[CMD] reply failed: {type(e).__name__}: {e}")

            if cmd == "/help": await reply(MessageFormatter.format_help())

            elif cmd == "/status":
                # === تقرير حالة شامل ===
                total = await self.db.count_requests()
                watchers = await self.db.get_active_watchers()
                monitors = [w for w in watchers if w.get('role', 'monitor') == 'monitor']
                joiners = [w for w in watchers if w.get('role') == 'joiner']
                backups = [w for w in watchers if w.get('role') == 'backup']
                connected_count = sum(1 for c in self.user_clients.values() if c and c.is_connected())
                disconnected = [w['phone'] for w in watchers
                                if not self.user_clients.get(w['phone']) or
                                not self.user_clients.get(w['phone']).is_connected()]

                # join_paused state
                pause_state = "⏸️ متوقف" if self._join_paused else "▶️ نشط"
                sim_state = "🧪 محاكاة" if self.simulation_mode else "📡 إنتاج"

                # FloodWait accounts
                blocked = await self.floodwait_mgr.get_blocked_accounts()
                blocked_lines = []
                if blocked:
                    for b in blocked:
                        wait_s = int(b['next_retry_at'] - time.time())
                        wait_min = max(wait_s // 60, 0)
                        blocked_lines.append(f"   ⚠️ {b['phone']}: {wait_min}دقيقة متبقية")
                else:
                    blocked_lines.append("   ✅ لا يوجد")

                # Join stats
                join_lines = []
                for j in joiners:
                    jphone = j['phone']
                    daily = await self.db.get_daily_join_count(jphone)
                    daily_limit = await self._get_daily_limit(jphone)
                    last_join = j.get('last_join_timestamp', 'أبداً')
                    health = j.get('health_score', 100)
                    join_lines.append(f"   {jphone}: {daily}/{daily_limit} اليوم | صحة: {health} | آخر: {last_join[:19] if last_join and last_join != 'أبداً' else 'أبداً'}")

                # Metrics
                metrics = await self.metrics.get_summary()
                total_joins = metrics.get('total_joins', 0)
                total_fw = metrics.get('total_floodwait', 0)
                total_dups = metrics.get('total_duplicates', 0)
                total_skips = metrics.get('total_skips', 0)
                queue_sz = metrics.get('queue_size', 0)

                status_msg = (
                    f"📊 تقرير حالة النظام\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📦 Supabase: {len(watchers)} حساب\n"
                    f"   👁️ مراقبين: {len(monitors)}\n"
                    f"   🚀 فدائيين: {len(joiners)}\n"
                    f"   🔄 احتياط: {len(backups)}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔗 متصل فعلياً: {connected_count}/{len(watchers)}\n"
                )
                if disconnected:
                    status_msg += f"❌ غير متصل: {', '.join(disconnected)}\n"
                status_msg += (
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔒 الانضمام: {pause_state}\n"
                    f"🔬 الوضع: {sim_state}\n"
                    f"📦 القائمة: {queue_sz} رابط معلق\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ الحسابات المحظورة (FloodWait):\n"
                    + "\n".join(blocked_lines) + "\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🚀 حسابات الفدائي:\n"
                    + ("\n".join(join_lines) if join_lines else "   لا يوجد") + "\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 الإحصائيات:\n"
                    f"   انضمامات ناجحة: {total_joins}\n"
                    f"   FloodWait: {total_fw}\n"
                    f"   روابط مكررة: {total_dups}\n"
                    f"   روابط متجاوزة: {total_skips}\n"
                    f"   إجمالي الروابط: {total}\n"
                )
                await reply(status_msg)

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

            elif cmd == "/pause_join":
                self._join_paused = True
                await self.prod_db.set_setting('join_paused', 'true')
                await reply("⏸️ تم إيقاف عمليات الانضمام (محفوظ في DB).\nللاستئناف: /resume_join")

            elif cmd == "/resume_join":
                self._join_paused = False
                await self.prod_db.set_setting('join_paused', 'false')
                await reply("▶️ تم استئناف عمليات الانضمام (محفوظ في DB).")

            elif cmd == "/enable_joiner":
                # /enable_joiner PHONE — يكتب في Supabase فقط
                parts = text.split()
                if len(parts) < 2:
                    await reply("📋 الاستخدام: /enable_joiner <phone>\nمثال: /enable_joiner +967739407274")
                else:
                    target_phone = parts[1]
                    ok = await self.db._supabase_update_watcher(target_phone, joiner_enabled=1)
                    if ok:
                        await reply(f"✅ تم تفعيل الانضمام للحساب: {target_phone}\n📦 Source: Supabase (sole source of truth)")
                    else:
                        await reply(f"❌ فشل تحديث Supabase للحساب: {target_phone}")

            elif cmd == "/disable_joiner":
                # /disable_joiner PHONE — يكتب في Supabase فقط
                parts = text.split()
                if len(parts) < 2:
                    await reply("📋 الاستخدام: /disable_joiner <phone>\nمثال: /disable_joiner +967739407274")
                else:
                    target_phone = parts[1]
                    ok = await self.db._supabase_update_watcher(target_phone, joiner_enabled=0)
                    if ok:
                        await reply(f"⏸️ تم إيقاف الانضمام للحساب: {target_phone}\n📦 Source: Supabase (sole source of truth)")
                    else:
                        await reply(f"❌ فشل تحديث Supabase للحساب: {target_phone}")

            elif cmd == "/set_role":
                # /set_role PHONE ROLE — يغير دور الحساب (monitor / joiner / backup)
                # هذه خاصية الحساب الفدائي: تحول أي حساب إلى فدائي ينضم للمجموعات
                parts = text.split()
                if len(parts) < 3:
                    await reply(
                        "📋 الاستخدام: /set_role <phone> <role>\n"
                        "الأدوار المتاحة:\n"
                        "  • monitor — يراقب فقط (لا ينضم)\n"
                        "  • joiner  — فدائي ينضم للمجموعات\n"
                        "  • backup  — احتياطي (ينضم عند الحاجة)\n\n"
                        "مثال: /set_role +967739407274 joiner\n\n"
                        "⚠️ ملاحظة: الحساب الفدائي يجب أن يكون عضواً في مجموعات مختلفة عن المراقب"
                    )
                else:
                    target_phone = parts[1]
                    new_role = parts[2].lower().strip()
                    if new_role not in ('monitor', 'joiner', 'backup'):
                        await reply(f"❌ دور غير صالح: {new_role}\nالأدوار: monitor / joiner / backup")
                    else:
                        ok = await self.db._supabase_update_watcher(target_phone, role=new_role)
                        if ok:
                            role_icon = {'monitor': '👁️', 'joiner': '🚀', 'backup': '🔄'}.get(new_role, '❓')
                            await reply(
                                f"{role_icon} تم تغيير دور الحساب {target_phone} إلى: {new_role}\n"
                                f"📦 Source: Supabase (sole source of truth)\n\n"
                                f"ℹ️ التغيير يسري بعد إعادة تشغيل البوت (أو تلقائياً خلال دقيقة)"
                            )
                            # سجل في الـ logs
                            logging.info(f"[SET_ROLE] {target_phone} → {new_role}")
                            # لو الحساب متصل، حدّث في الذاكرة (role يُقرأ من Supabase في كل عملية)
                        else:
                            await reply(f"❌ فشل تحديث Supabase للحساب: {target_phone}")

            elif cmd == "/join_status":
                # === تقرير حالة كل حساب فدائي — يقرأ من Supabase ===
                joiners = await self.db.get_watchers_by_role("joiner")
                if not joiners:
                    await reply("ℹ️ لا يوجد حسابات فدائية مسجلة.\nاستخدم /login واختر «فدائي» للتسجيل.")
                else:
                    lines = ["📊 Joiner Status (source: Supabase)", ""]
                    for j in joiners:
                        jphone = j['phone']
                        # اقرأ الحالة الكاملة من Supabase (وليس SQLite)
                        w = await self.db._supabase_get_watcher(jphone)
                        enabled = bool(w.get('joiner_enabled', 1)) if w else True
                        last_join_ts = w.get('last_join_timestamp') if w else None
                        health = w.get('health_score', 100) if w else 100

                        # FloodWait (من SQLite floodwait_tracker — جدول مؤقت)
                        is_blocked, wait = await self.floodwait_mgr.is_blocked(jphone)

                        # Daily joins (من SQLite api_operations_log — جدول مؤقت)
                        await self.db.reset_daily_joins_if_needed(jphone)
                        daily = await self.db.get_daily_join_count(jphone)
                        daily_limit = await self._get_daily_limit(jphone)

                        # Last error from group_states (SQLite — جدول مؤقت)
                        conn = await self.db._ensure_conn()
                        cursor = await conn.execute(
                            "SELECT last_error, last_attempt FROM group_states WHERE joined_by = ? ORDER BY last_attempt DESC LIMIT 1",
                            (jphone,))
                        err_row = await cursor.fetchone()
                        last_error = err_row[0] if err_row and err_row[0] else "none"
                        last_attempt = err_row[1] if err_row and err_row[1] else "none"

                        # Determine status
                        if not enabled:
                            status = "DISABLED"
                        elif is_blocked:
                            hours = wait // 3600
                            mins = (wait % 3600) // 60
                            status = f"FLOODWAIT ({hours}h {mins}m)"
                        elif daily >= daily_limit:
                            status = "DAILY_LIMIT"
                        elif health < 30:
                            status = f"LOW_HEALTH ({health})"
                        else:
                            status = "READY"

                        # Format last join
                        last_join_str = "none"
                        if last_join_ts:
                            try:
                                dt = datetime.fromisoformat(last_join_ts) if isinstance(last_join_ts, str) else last_join_ts
                                last_join_str = dt.strftime("%Y-%m-%d %H:%M")
                            except Exception:
                                last_join_str = str(last_join_ts)[:19]

                        lines.append(f"📞 {jphone}")
                        lines.append(f"   Status: {status}")
                        lines.append(f"   Enabled: {'true' if enabled else 'false'}")
                        lines.append(f"   Daily joins: {daily}/{daily_limit}")
                        lines.append(f"   Health: {health}/100")
                        lines.append(f"   Last join: {last_join_str}")
                        lines.append(f"   Last error: {last_error[:60]}")
                        lines.append("")

                    # Global state
                    pause_str = "⏸️ PAUSED" if self._join_paused else "▶️ ACTIVE"
                    sim_str = "🧪 SIMULATION" if self.simulation_mode else "📡 PRODUCTION"
                    lines.append(f"🔒 Global: {pause_str} | {sim_str}")

                    await reply("\n".join(lines))

            elif cmd == "/verify":
                # === E2E Verification: يثبت أن Supabase هو المصدر الوحيد ===
                logging.info("=" * 60)
                logging.info("[VERIFY] /verify command invoked — full E2E check")
                logging.info("=" * 60)
                try:
                    # 1. اقرأ من Supabase
                    all_accounts = await self.db.get_active_watchers()
                    supa_count = len(all_accounts)
                    monitors = [w for w in all_accounts if w.get('role', 'monitor') == 'monitor']
                    joiners = [w for w in all_accounts if w.get('role') == 'joiner']
                    supa_count_int = await self.db._supabase_count_watchers()

                    # 2. عدد الحسابات التي تم تشغيلها فعلياً
                    started_count = len(self.user_clients)
                    connected_count = sum(1 for c in self.user_clients.values() if c and c.is_connected())

                    # 3. اعرض كل حساب ورقمه ودوره
                    logging.info(f"[VERIFY] ═══════════════════════════════════════")
                    logging.info(f"[VERIFY]  E2E VERIFICATION REPORT")
                    logging.info(f"[VERIFY] ═══════════════════════════════════════")
                    logging.info(f"[VERIFY] Supabase accounts (is_active=true): {supa_count}")
                    logging.info(f"[VERIFY] Supabase count (REST count=exact): {supa_count_int}")
                    logging.info(f"[VERIFY] Monitors: {len(monitors)}")
                    logging.info(f"[VERIFY] Joiners:  {len(joiners)}")
                    logging.info(f"[VERIFY] ─────────────────────────────────────")
                    logging.info(f"[VERIFY] Started clients (in memory): {started_count}")
                    logging.info(f"[VERIFY] Connected clients:           {connected_count}")
                    logging.info(f"[VERIFY] ─────────────────────────────────────")
                    logging.info(f"[VERIFY] Account list (phone | role | connected):")
                    for w in all_accounts:
                        ph = w.get('phone', '?')
                        rl = w.get('role', 'monitor')
                        conn = self.user_clients.get(ph)
                        conn_str = "✅ connected" if (conn and conn.is_connected()) else "❌ not connected"
                        logging.info(f"[VERIFY]   → {ph} | role={rl} | {conn_str}")
                    logging.info(f"[VERIFY] ═══════════════════════════════════════")

                    # 4. قائمة جداول SQLite (لإثبات عدم وجود watchers)
                    sqlite_tables = await self.db._sqlite_list_tables()
                    has_watchers_table = 'watchers' in sqlite_tables
                    logging.info(f"[VERIFY] SQLite tables: {sqlite_tables}")
                    logging.info(f"[VERIFY] 'watchers' table in SQLite: {'❌ YES (BUG!)' if has_watchers_table else '✅ NO (correct)'}")

                    # 5. بناء رسالة الرد
                    lines = [
                        "🔍 E2E Verification Report",
                        "═══════════════════════════",
                        f"📦 Supabase accounts: {supa_count} (REST count: {supa_count_int})",
                        f"   • Monitors: {len(monitors)}",
                        f"   • Joiners:  {len(joiners)}",
                        f"🚀 Started clients (in memory): {started_count}",
                        f"🔗 Connected clients: {connected_count}",
                        "",
                        "📋 Account list:",
                    ]
                    for w in all_accounts:
                        ph = w.get('phone', '?')
                        rl = w.get('role', 'monitor')
                        conn = self.user_clients.get(ph)
                        icon = "✅" if (conn and conn.is_connected()) else "❌"
                        lines.append(f"   {icon} {ph} (role={rl})")
                    lines.append("")
                    lines.append("🗄️ SQLite tables:")
                    for t in sqlite_tables:
                        lines.append(f"   • {t}")
                    lines.append("")
                    if has_watchers_table:
                        lines.append("❌ BUG: 'watchers' table EXISTS in SQLite!")
                    else:
                        lines.append("✅ PROVEN: 'watchers' table does NOT exist in SQLite.")
                        lines.append("✅ Supabase is the SOLE source of truth for accounts.")
                    lines.append("")
                    if supa_count == started_count and not has_watchers_table:
                        lines.append("✅ E2E PASS: Supabase count == started count, no SQLite watchers.")
                    else:
                        lines.append("❌ E2E FAIL: mismatch between Supabase and started clients!")

                    msg = "\n".join(lines)
                    logging.info(f"[VERIFY] Reply sent to user")
                    await reply(msg)
                except RuntimeError as e:
                    logging.critical(f"[VERIFY] FATAL: {e}")
                    await reply(f"❌ FATAL: {e}")
                except Exception as e:
                    logging.error(f"[VERIFY] Error: {e}", exc_info=True)
                    await reply(f"❌ Verify error: {e}")

            elif cmd == "/sqlite_check":
                # === إثبات أن SQLite لا يحتوي جدول watchers ===
                logging.info("[SQLITE_CHECK] /sqlite_check command invoked")
                try:
                    tables = await self.db._sqlite_list_tables()
                    has_watchers = 'watchers' in tables
                    logging.info(f"[SQLITE_CHECK] Tables found: {tables}")
                    logging.info(f"[SQLITE_CHECK] 'watchers' present: {has_watchers}")

                    lines = [
                        "🗄️ SQLite Tables Check",
                        "═══════════════════════════",
                        f"Total tables: {len(tables)}",
                        "",
                        "Tables:",
                    ]
                    for t in tables:
                        marker = "❌" if t == 'watchers' else "✅"
                        lines.append(f"   {marker} {t}")
                    lines.append("")
                    if has_watchers:
                        lines.append("❌ BUG: 'watchers' table EXISTS in SQLite!")
                        lines.append("→ Supabase is NOT the sole source of truth.")
                    else:
                        lines.append("✅ PROVEN: 'watchers' table does NOT exist in SQLite.")
                        lines.append("✅ All account data lives ONLY in Supabase.")
                        lines.append("")
                        lines.append("Allowed SQLite tables (temporary data only):")
                        lines.append("   • link_queue, group_states, membership_cache")
                        lines.append("   • floodwait_tracker, api_operations_log, system_settings")
                        lines.append("   • target_groups, forwarded_requests, scan_state")
                    await reply("\n".join(lines))
                except Exception as e:
                    logging.error(f"[SQLITE_CHECK] Error: {e}", exc_info=True)
                    await reply(f"❌ sqlite_check error: {e}")

            elif cmd == "/clear_floodwait":
                # === مسح FloodWait القديم ===
                try:
                    conn = await self.db._ensure_conn()
                    cursor = await conn.execute("DELETE FROM floodwait_tracker")
                    await conn.commit()
                    count = cursor.rowcount
                    # امسح من الذاكرة كمان
                    if hasattr(self.rate_limiter, '_floodwait'):
                        self.rate_limiter._floodwait.clear()
                    # أعد تفعيل الانضمام
                    self._join_paused = False
                    await self.prod_db.set_setting('join_paused', 'false')
                    logging.info(f"[CLEAR_FLOODWAIT] Cleared {count} floodwait records")
                    await reply(f"✅ تم مسح {count} سجل FloodWait\n▶️ تم إعادة تفعيل الانضمام")
                except Exception as e:
                    logging.error(f"[CLEAR_FLOODWAIT] Error: {e}")
                    await reply(f"❌ خطأ: {e}")

            elif cmd == "/clean_queue":
                # === تنظيف queue من روابط الرسائل (t.me/username/123) ===
                import re as _re_clean
                try:
                    conn = await self.db._ensure_conn()
                    # اجلب كل روابط QUEUED
                    cursor = await conn.execute(
                        "SELECT id, raw_link FROM link_queue WHERE status = 'QUEUED'")
                    rows = await cursor.fetchall()

                    # حدّد روابط الرسائل (t.me/username/123)
                    msg_pattern = _re_clean.compile(
                        r'^https?://t(?:elegram)?\.me/[A-Za-z0-9_]+/\d+', _re_clean.IGNORECASE
                    )
                    # كذلك روابط +private و joinchat المرفوضة
                    bad_links = []
                    for r in rows:
                        link_id = r[0]
                        link = r[1] or ''
                        is_msg = bool(msg_pattern.match(link))
                        is_private = '/+' in link or 'joinchat' in link.lower()
                        if is_msg or is_private:
                            bad_links.append((link_id, link, 'message' if is_msg else 'private'))

                    # احذفها من queue
                    if bad_links:
                        ids_to_delete = [b[0] for b in bad_links]
                        placeholders = ','.join('?' * len(ids_to_delete))
                        cursor = await conn.execute(
                            f"DELETE FROM link_queue WHERE id IN ({placeholders})",
                            ids_to_delete
                        )
                        await conn.commit()
                        deleted = cursor.rowcount

                        await reply(
                            f"🧹 تنظيف Queue\n\n"
                            f"📊 الإحصائيات:\n"
                            f"  • إجمالي QUEUED قبل التنظيف: {len(rows)}\n"
                            f"  • روابط رسائل (t.me/user/123): {sum(1 for b in bad_links if b[2] == 'message')}\n"
                            f"  • روابط خاصة (t.me/+xxx): {sum(1 for b in bad_links if b[2] == 'private')}\n"
                            f"  • تم حذفها: {deleted}\n"
                            f"  • QUEUED المتبقي: {len(rows) - deleted}\n\n"
                            f"✅ الحين المجدول يقدر يركز على روابط المجموعات الحقيقية"
                        )
                        logging.info(f"[CLEAN_QUEUE] Deleted {deleted} bad links from queue")
                    else:
                        await reply(
                            f"✅ Queue نظيف\n"
                            f"📊 إجمالي QUEUED: {len(rows)}\n"
                            f"لا توجد روابط رسائل أو خاصة للحذف"
                        )
                except Exception as e:
                    logging.error(f"[CLEAN_QUEUE] Error: {e}", exc_info=True)
                    await reply(f"❌ خطأ: {e}")

            elif cmd == "/rejoin_published":
                # === إعادة قراءة رسائل القناة وإدخال الروابط في queue ===
                # يستخدم عندما queue فاضي والبوت ما عنده روابط جديدة
                parts = text.split()
                max_msgs = 5000
                if len(parts) >= 2:
                    try:
                        max_msgs = int(parts[1])
                    except ValueError:
                        pass

                await reply(
                    f"📖 [REJOIN] بدأ فحص رسائل القناة...\n"
                    f"   الحد الأقصى: {max_msgs} رسالة\n"
                    f"   سأعيد إدخال الروابط الصالحة في queue\n"
                    f"   (تخطّي روابط الرسائل والخاصة والمنضم لها)\n\n"
                    f"⏳ سيستغرق عدة دقائق — سأرسل تقرير عند الانتهاء"
                )

                # شغّل المهمة في background
                asyncio.create_task(self._rejoin_published_links(max_msgs))

            elif cmd == "/leave_bad_groups":
                # === مغادرة المجموعات السيئة (بيتكوين/عراقية/غير خليجية) ===
                # يفحص كل المجموعات اللي انضم لها الفدائي، ويغادر السيئة منها
                parts = text.split()
                dry_run = len(parts) < 2 or parts[1] != 'confirm'

                joiners = await self.db.get_watchers_by_role("joiner")
                if not joiners:
                    await reply("❌ ما في حساب فدائي متاح")
                    return

                await reply(
                    f"🔍 {'معاينة' if dry_run else 'تنفيذ'} — فحص المجموعات المنضم إليها...\n"
                    f"📱 الحساب الفدائي: {joiners[0]['phone']}"
                )

                total_groups = 0
                bad_groups = 0
                left_groups = 0
                errors = 0
                bad_list = []

                for joiner in joiners:
                    phone = joiner['phone']
                    client = self.user_clients.get(phone)
                    if not client or not client.is_connected():
                        continue

                    try:
                        # اجلب كل الـ dialogs (محادثات) للحساب
                        async for dialog in client.iter_dialogs():
                            if not dialog.is_group:
                                continue
                            total_groups += 1

                            # فحص المجموعة بالفلتر
                            group_name = dialog.name or ''
                            group_username = ''
                            try:
                                if dialog.entity and hasattr(dialog.entity, 'username') and dialog.entity.username:
                                    group_username = dialog.entity.username
                            except Exception:
                                pass

                            # فحص: هل المجموعة سيئة؟
                            is_bad, bad_reason = EducationalFilter.is_blacklisted(group_name, group_username)
                            is_gulf, _ = EducationalFilter.is_gulf_target(group_name, group_username)

                            if is_bad or (not is_gulf and not EducationalFilter.is_educational(group_name, group_username)[0]):
                                bad_groups += 1
                                bad_list.append(f"  • {group_name[:40]} (@{group_username or '?'}) — {bad_reason or 'غير خليجي'}")

                                if not dry_run:
                                    try:
                                        await client.delete_dialog(dialog.entity)
                                        left_groups += 1
                                        logging.info(f"[LEAVE_BAD] {phone} left: {group_name[:40]} ({bad_reason})")
                                        await asyncio.sleep(2)  # تجنب FloodWait
                                    except FloodWaitError as fe:
                                        logging.warning(f"[LEAVE_BAD] FloodWait {fe.seconds}s — pausing")
                                        await asyncio.sleep(fe.seconds + 1)
                                    except Exception as e:
                                        errors += 1
                                        logging.error(f"[LEAVE_BAD] Error leaving {group_name[:30]}: {e}")
                    except Exception as e:
                        logging.error(f"[LEAVE_BAD] Error iterating {phone}: {e}")
                        errors += 1

                # تقرير نهائي
                report = (
                    f"{'🔍 معاينة' if dry_run else '✅ تنفيذ'} — مغادرة المجموعات السيئة\n\n"
                    f"📊 الإحصائيات:\n"
                    f"  • إجمالي المجموعات: {total_groups}\n"
                    f"  • مجموعات سيئة: {bad_groups}\n"
                )
                if not dry_run:
                    report += f"  • تم مغادرتها: {left_groups}\n"
                    report += f"  • أخطاء: {errors}\n"
                report += f"\n📋 المجموعات السيئة المكتشفة:\n"
                report += '\n'.join(bad_list[:20]) if bad_list else '  (لا يوجد)'
                if len(bad_list) > 20:
                    report += f"\n  + {len(bad_list) - 20} أخرى..."

                if dry_run and bad_groups > 0:
                    report += "\n\n💡 للتأكيد والمغادرة الفعلية:\n/leave_bad_groups confirm"

                await reply(report)

            elif cmd == "/ai_mode":
                # === عرض/تبديل وضع AI Batch Mode ===
                # الاستخدام:
                #   /ai_mode         → عرض الحالة الحالية
                #   /ai_mode on      → تفعيل AI (يعطل batch mode)
                #   /ai_mode off     → تعطيل AI (يفعل batch mode — افتراضي)
                parts = text.split()
                ai_batch_mode = os.getenv("AI_BATCH_MODE", "true").lower() in ("true", "1", "yes")
                if len(parts) >= 2:
                    arg = parts[1].lower()
                    if arg in ("on", "enable", "true"):
                        os.environ["AI_BATCH_MODE"] = "false"
                        await reply(
                            "🤖 AI Verification: ENABLED\n\n"
                            "ستتم فحص كل رابط جديد قبل النشر والانضمام.\n"
                            "الروابط المرفوضة من AI ستتجاهل تلقائياً.\n\n"
                            "⚠️ ملاحظة: قد يبطئ معالجة قائمة الانتظار."
                        )
                    elif arg in ("off", "disable", "false"):
                        os.environ["AI_BATCH_MODE"] = "true"
                        await reply(
                            "⏭️ AI Verification: DISABLED (batch mode)\n\n"
                            "سيتم نشر جميع الروابط بدون فحص AI.\n"
                            "مفيد لمعالجة القائمة المتراكمة بسرعة.\n\n"
                            "أرسل /ai_mode on لإعادة التفعيل."
                        )
                    else:
                        await reply("❌ استخدام خاطئ\nالصيغة: /ai_mode on|off")
                else:
                    status = "⏭️ DISABLED (batch mode)" if ai_batch_mode else "🤖 ENABLED"
                    ai_enabled = bool(self.ai_analyzer and self.ai_analyzer.enabled)
                    providers = len(self.ai_analyzer.providers) if ai_enabled else 0
                    await reply(
                        f"🤖 AI Verification Status\n\n"
                        f"الحالة: {status}\n"
                        f"AI Provider: {'✅ متاح' if ai_enabled else '❌ غير متاح'}\n"
                        f"عدد المفاتيح: {providers}\n\n"
                        f"للتبديل:\n"
                        f"  /ai_mode on  ← تفعيل الفحص\n"
                        f"  /ai_mode off ← تعطيل (batch mode)"
                    )

            elif cmd == "/bulk_join":
                # === بدء/استئناف الانضمام الجماعي ===
                # Worker الأساسي يبدأ تلقائياً عند Startup — هذا الزر اختياري لإعادة التشغيل
                if hasattr(self, '_bulk_join_running') and self._bulk_join_running:
                    await reply("⚠️ Bulk Join يعمل بالفعل!\nأرسل /bulk_join_status لرؤية التقدم")
                elif hasattr(self, '_joiner_task') and self._joiner_task and not self._joiner_task.done():
                    # Joiner Worker الأساسي يعمل — اعرض حالته
                    if self._join_paused:
                        await reply("🔒 Joiner Worker يعمل لكن PAUSED\nأرسل /resume_join للاستئناف")
                    else:
                        await reply("✅ Joiner Worker يعمل تلقائياً\nأرسل /debug_pipeline لرؤية الحالة")
                else:
                    # Worker متوقف — أعد تشغيله
                    self._bulk_join_running = True
                    self._bulk_join_stop = False
                    self._bulk_join_stats = {'total': 0, 'joined': 0, 'already': 0, 'failed': 0, 'skipped': 0, 'current': ''}
                    self._bulk_join_task = asyncio.create_task(self._bulk_join_worker())
                    await reply(
                        "🚀 بدأ الانضمام الجماعي\n\n"
                        "📝 سيقرأ البوت روابط من القائمة ويحاول الانضمام.\n"
                        "⏱️ معدل آمن: انضمام كل 2 دقيقة\n"
                        "📊 أرسل /bulk_join_status للتقدم\n"
                        "⏹️ أرسل /bulk_join_stop للإيقاف"
                    )

            elif cmd == "/bulk_join_status":
                # === تقدم الانضمام (Scheduler + Bulk Join) ===
                # اقرأ إحصائيات الـ Scheduler الحقيقية من metrics
                metrics = await self.metrics.get_summary()
                s = getattr(self, '_bulk_join_stats', {'total': 0, 'joined': 0, 'already': 0, 'failed': 0, 'skipped': 0, 'current': ''})

                # حالة Worker
                scheduler_state = await self.prod_db.get_setting('scheduler_state', 'NOT_STARTED')
                scheduler_cycle = await self.prod_db.get_setting('scheduler_last_cycle', '0')
                scheduler_hb = await self.prod_db.get_setting('scheduler_last_heartbeat', 'NEVER')
                join_paused = await self.prod_db.get_setting('join_paused', 'false')
                queue_size = await self.prod_db.get_queue_size()
                bulk_running = getattr(self, '_bulk_join_running', False)

                worker_status = "AUTO (Scheduler)"
                if bulk_running:
                    worker_status = "MANUAL (Bulk Join)"
                if join_paused == 'true':
                    worker_status = "⏸️ PAUSED"

                # إحصائيات skips
                skip_reasons = metrics.get('skip_reasons', {})
                skip_lines = ""
                if skip_reasons:
                    skip_lines = "\nSkips by reason:\n"
                    for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1])[:5]:
                        skip_lines += f"  • {reason}: {count}\n"

                # عدد المجموعات المنضم إليها
                conn = await self.db._ensure_conn()
                cursor = await conn.execute("SELECT COUNT(*) FROM group_states WHERE state = 'JOINED'")
                joined_count = (await cursor.fetchone())[0]
                cursor = await conn.execute("SELECT COUNT(*) FROM group_states WHERE state = 'ALREADY_MEMBER'")
                already_count = (await cursor.fetchone())[0]

                await reply(
                    f"📊 Join Worker Status\n"
                    f"════════════════════\n"
                    f"⚙️ Worker: {worker_status}\n"
                    f"⚙️ Scheduler: {scheduler_state} (cycle={scheduler_cycle})\n"
                    f"⏰ Heartbeat: {scheduler_hb[:19] if scheduler_hb != 'NEVER' else 'NEVER'}\n"
                    f"🔒 Join paused: {join_paused}\n"
                    f"📋 Queue depth: {queue_size}\n"
                    f"\n"
                    f"📈 Scheduler Stats (REAL):\n"
                    f"  ✅ Joined (DB): {joined_count}\n"
                    f"  ℹ️ Already member (DB): {already_count}\n"
                    f"  🔗 Total joins (metrics): {metrics.get('total_joins', 0)}\n"
                    f"  ⏭️ Total skips: {metrics.get('total_skips', 0)}\n"
                    f"  ⚠️ FloodWait: {metrics.get('total_floodwait', 0)}\n"
                    f"  🔄 Duplicates: {metrics.get('total_duplicates', 0)}\n"
                    f"{skip_lines}"
                    f"Bulk Join Stats (manual):\n"
                    f"  🔗 Total: {s.get('total', 0)}\n"
                    f"  ✅ Joined: {s.get('joined', 0)}\n"
                    f"  📍 Current: {s.get('current', '')[:60]}"
                )

            elif cmd == "/bulk_join_stop":
                # === إيقاف الانضمام الجماعي ===
                if hasattr(self, '_bulk_join_running') and self._bulk_join_running:
                    self._bulk_join_stop = True
                    await reply("⏹️ سيتم إيقاف البوك جون بعد الرابط الحالي")
                else:
                    await reply("ℹ️ البوك جون لا يعمل")

            elif cmd == "/cleanup_preview":
                # === معاينة ما سيُحذف (dry-run) ===
                await reply("🔍 بدأ التحليل... قد يستغرق عدة دقائق لـ 22 ألف رسالة")
                asyncio.create_task(self._cleanup_worker(preview_only=True))

            elif cmd == "/cleanup_links":
                # === حذف فعلي للروابط غير التعليمية والمكررة ===
                await reply("🗑️ بدأ التنظيف الفعلي... سيتم حذف الروابط غير التعليمية والمكررة")
                asyncio.create_task(self._cleanup_worker(preview_only=False))

            elif cmd == "/cleanup_status":
                # === تقدم التنظيف ===
                s = getattr(self, '_cleanup_stats', None)
                if not s or not s.get('running', False):
                    await reply("ℹ️ التنظيف لا يعمل. أرسل /cleanup_preview أو /cleanup_links")
                else:
                    await reply(
                        f"🧹 Cleanup Status\n"
                        f"════════════════════\n"
                        f"📊 Total scanned: {s.get('total', 0)}\n"
                        f"✅ Educational: {s.get('educational', 0)}\n"
                        f"❌ Non-educational: {s.get('non_educational', 0)}\n"
                        f"🔄 Duplicates: {s.get('duplicates', 0)}\n"
                        f"🗑️ Deleted: {s.get('deleted', 0)}\n"
                        f"📍 Current: {s.get('current', '')[:60]}"
                    )

            elif cmd == "/live_audit":
                # === فحص شامل للنظام (بديل live_audit.py للـ Free Tier) ===
                logging.info("[LIVE_AUDIT] /live_audit command invoked")
                audit_lines = []
                audit_lines.append("🔍 LIVE AUDIT REPORT")
                audit_lines.append("═══════════════════════════")

                # 1. Environment Variables
                audit_lines.append("")
                audit_lines.append("📋 ENVIRONMENT:")
                env_required = [
                    ('SUPABASE_URL', 'SUPABASE_URL'),
                    ('SUPABASE_KEY', 'SUPABASE_KEY'),
                    ('BOT_TOKEN', 'BOT_TOKEN'),
                    ('API_ID', 'API_ID'),
                    ('API_HASH', 'API_HASH'),
                    ('CHANNEL_ID', 'CHANNEL_ID'),
                ]
                for var, display in env_required:
                    val = os.getenv(var, '')
                    status = "✅ SET" if val else "❌ MISSING"
                    audit_lines.append(f"  {display:25s} = {status}")

                env_optional = ['OPENAI_API_KEY', 'AI_KEY_1', 'AI_KEY_2', 'OWNER_ID', 'DAILY_JOIN_LIMIT']
                for var in env_optional:
                    val = os.getenv(var, '')
                    status = "✅" if val else "⚠️"
                    audit_lines.append(f"  {var:25s} = {status}")

                # 2. Supabase LIVE
                audit_lines.append("")
                audit_lines.append("🗄️ SUPABASE:")
                try:
                    supa_count = await self.db._supabase_count_watchers()
                    if supa_count >= 0:
                        audit_lines.append(f"  Connection: ✅ OK")
                        audit_lines.append(f"  Accounts: {supa_count}")
                        watchers = await self.db.get_active_watchers()
                        monitors = sum(1 for w in watchers if w.get('role', 'monitor') == 'monitor')
                        joiners = sum(1 for w in watchers if w.get('role') == 'joiner')
                        audit_lines.append(f"  Monitors: {monitors}")
                        audit_lines.append(f"  Joiners: {joiners}")
                        # Schema check
                        w_sample = watchers[0] if watchers else {}
                        schema_ok = all(k in w_sample or True for k in ['role', 'joiner_enabled'])
                        audit_lines.append(f"  Schema: {'✅ OK' if schema_ok else '⚠️ CHECK'}")
                    else:
                        audit_lines.append(f"  Connection: ❌ FAILED")
                except Exception as e:
                    audit_lines.append(f"  Connection: ❌ ERROR: {type(e).__name__}")

                # 3. SQLite
                audit_lines.append("")
                audit_lines.append("🗃️ SQLITE:")
                try:
                    tables = await self.db._sqlite_list_tables()
                    has_watchers = 'watchers' in tables
                    audit_lines.append(f"  watchers table: {'❌ EXISTS (BUG!)' if has_watchers else '✅ ABSENT'}")
                    audit_lines.append(f"  Tables ({len(tables)}): {', '.join(tables[:8])}")
                except Exception as e:
                    audit_lines.append(f"  Error: {type(e).__name__}")

                # 4. Telegram Accounts
                audit_lines.append("")
                audit_lines.append("🤖 TELEGRAM:")
                audit_lines.append(f"  Bot: {'✅ connected' if (self.bot_client and self.bot_client.is_connected()) else '❌ disconnected'}")
                connected = 0
                total = len(self.user_clients)
                for ph, cl in self.user_clients.items():
                    if cl and cl.is_connected():
                        connected += 1
                audit_lines.append(f"  Accounts: {connected}/{total} connected")

                # 5. Workers
                audit_lines.append("")
                audit_lines.append("⚙️ WORKERS:")
                sched_state = await self.prod_db.get_setting('scheduler_state', 'NOT_STARTED')
                sched_hb = await self.prod_db.get_setting('scheduler_last_heartbeat', 'NEVER')
                sched_cycle = await self.prod_db.get_setting('scheduler_last_cycle', '0')
                join_paused = await self.prod_db.get_setting('join_paused', 'false')
                audit_lines.append(f"  Scheduler: {sched_state}")
                audit_lines.append(f"  Last cycle: {sched_cycle}")
                audit_lines.append(f"  Heartbeat: {sched_hb}")
                audit_lines.append(f"  Join paused: {join_paused}")

                # 6. Queue
                try:
                    queue_size = await self.prod_db.get_queue_size()
                    audit_lines.append(f"  Queue depth: {queue_size}")
                except Exception:
                    audit_lines.append(f"  Queue depth: ?")

                # 7. FloodWait
                try:
                    blocked = await self.floodwait_mgr.get_blocked_accounts()
                    audit_lines.append(f"  FloodWait blocked: {len(blocked)}")
                except Exception:
                    audit_lines.append(f"  FloodWait: ?")

                # 8. Bulk Join / Cleanup
                bulk_running = getattr(self, '_bulk_join_running', False)
                cleanup_running = getattr(self, '_cleanup_stats', {}).get('running', False) if hasattr(self, '_cleanup_stats') and self._cleanup_stats else False
                audit_lines.append(f"  Bulk Join: {'RUNNING' if bulk_running else 'IDLE'}")
                audit_lines.append(f"  Cleanup: {'RUNNING' if cleanup_running else 'IDLE'}")

                # Summary
                audit_lines.append("")
                audit_lines.append("═══════════════════════════")
                audit_lines.append(f"Commit: 5b4a925")
                audit_lines.append(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

                await reply("\n".join(audit_lines))

            elif cmd == "/debug_pipeline":
                # === تشخيص مشكلة توقف السحب ===
                logging.info("[DEBUG_PIPELINE] /debug_pipeline command invoked")
                try:
                    conn = await self.db._ensure_conn()
                    lines = ["🔧 Pipeline Debug", "═══════════════════════════"]

                    # 1. Scheduler state
                    sched_state = await self.prod_db.get_setting('scheduler_state', 'NOT_STARTED')
                    sched_cycle = await self.prod_db.get_setting('scheduler_last_cycle', '0')
                    sched_hb = await self.prod_db.get_setting('scheduler_last_heartbeat', 'NEVER')
                    join_paused = await self.prod_db.get_setting('join_paused', 'false')
                    lines.append(f"⚙️ Scheduler:")
                    lines.append(f"  state={sched_state}")
                    lines.append(f"  cycle={sched_cycle}")
                    lines.append(f"  heartbeat={sched_hb}")
                    lines.append(f"  join_paused={join_paused}")
                    lines.append("")

                    # 2. Queue items
                    cursor = await conn.execute(
                        "SELECT id, raw_link, status, enqueued_at, next_retry_at, attempt_count, last_error "
                        "FROM link_queue ORDER BY id DESC LIMIT 10")
                    queue_rows = await cursor.fetchall()
                    lines.append(f"📋 Queue ({len(queue_rows)} items):")
                    for r in queue_rows:
                        lines.append(f"  id={r[0]} status={r[2]} attempts={r[5]}")
                        lines.append(f"    link={r[1][:50]}")
                        if r[4]:
                            lines.append(f"    next_retry={r[4][:19]}")
                        if r[6]:
                            lines.append(f"    error={r[6][:60]}")
                    lines.append("")

                    # 3. Group states distribution
                    cursor = await conn.execute(
                        "SELECT state, COUNT(*) as cnt FROM group_states GROUP BY state ORDER BY cnt DESC")
                    state_rows = await cursor.fetchall()
                    lines.append(f"📊 Group States:")
                    for s, c in state_rows:
                        lines.append(f"  {s}: {c}")
                    lines.append("")

                    # 4. Recent group_states (last 5)
                    cursor = await conn.execute(
                        "SELECT normalized_link, state, last_error, last_seen "
                        "FROM group_states ORDER BY last_seen DESC LIMIT 5")
                    recent_states = await cursor.fetchall()
                    lines.append(f"📊 Recent group_states (last 5):")
                    for r in recent_states:
                        lines.append(f"  {r[1]:15s} {r[0][:40]}")
                        if r[2]:
                            lines.append(f"    error={r[2][:50]}")
                    lines.append("")

                    # 5. AI analyzer status
                    ai_keys = sum(1 for i in range(1, 9) if os.getenv(f"AI_KEY_{i}", "") or (i == 1 and os.getenv("OPENAI_API_KEY", "")))
                    lines.append(f"🤖 AI Analyzer:")
                    lines.append(f"  keys_available={ai_keys}")
                    lines.append(f"  simulation_mode={self.simulation_mode}")
                    lines.append("")

                    # 6. Connected accounts
                    connected = sum(1 for c in self.user_clients.values() if c and c.is_connected())
                    total = len(self.user_clients)
                    lines.append(f"🤖 Telegram:")
                    lines.append(f"  accounts={connected}/{total} connected")
                    lines.append(f"  bot={'✅' if (self.bot_client and self.bot_client.is_connected()) else '❌'}")
                    lines.append("")

                    # 7. FloodWait
                    blocked = await self.floodwait_mgr.get_blocked_accounts()
                    lines.append(f"⚠️ FloodWait: {len(blocked)} blocked")
                    lines.append("")

                    # 8. Diagnosis
                    lines.append("═══════════════════════════")
                    lines.append("🔍 Diagnosis:")
                    if join_paused == 'true':
                        lines.append("  ❌ Join PAUSED — send /resume_join")
                    if sched_state != 'RUNNING':
                        lines.append("  ❌ Scheduler NOT RUNNING")
                    if connected == 0:
                        lines.append("  ❌ No accounts connected")
                    if ai_keys == 0:
                        lines.append("  ❌ No AI keys configured")
                    if len(queue_rows) == 0:
                        lines.append("  ℹ️ Queue empty — no links waiting")
                    elif any(r[2] == 'QUEUED' for r in queue_rows):
                        queued_count = sum(1 for r in queue_rows if r[2] == 'QUEUED')
                        lines.append(f"  ⚠️ {queued_count} links in QUEUED state — check Scheduler")
                    # Check for REJECTED links (AI rejecting)
                    cursor = await conn.execute(
                        "SELECT COUNT(*) FROM link_queue WHERE status = 'REJECTED'")
                    rejected = (await cursor.fetchone())[0]
                    if rejected > 0:
                        lines.append(f"  ⚠️ {rejected} links REJECTED by AI — check AI keys/config")

                    await reply("\n".join(lines))
                except Exception as e:
                    logging.error(f"[DEBUG_PIPELINE] Error: {e}", exc_info=True)
                    await reply(f"❌ خطأ: {e}")

            elif cmd == "/joined_groups":
                # === عرض كل المجموعات المنضم إليها فعلياً ===
                logging.info("[JOINED_GROUPS] /joined_groups command invoked")
                try:
                    conn = await self.db._ensure_conn()
                    # المجموعات المنضم إليها (state = JOINED)
                    cursor = await conn.execute(
                        "SELECT normalized_link, raw_link, joined_by, member_count, last_seen, last_error "
                        "FROM group_states WHERE state = ? ORDER BY last_seen DESC LIMIT 50",
                        (GroupState.JOINED,))
                    joined_rows = await cursor.fetchall()

                    # المجموعات المنضم إليها سابقاً (state = ALREADY_MEMBER)
                    cursor = await conn.execute(
                        "SELECT normalized_link, raw_link, joined_by, last_seen "
                        "FROM group_states WHERE state = ? ORDER BY last_seen DESC LIMIT 50",
                        (GroupState.ALREADY_MEMBER,))
                    already_rows = await cursor.fetchall()

                    # [REQAUDIT-2] المجموعات بانتظار موافقة المشرف (PENDING_APPROVAL)
                    cursor = await conn.execute(
                        "SELECT normalized_link, raw_link, joined_by, last_seen "
                        "FROM group_states WHERE state = ? ORDER BY last_seen DESC LIMIT 50",
                        (GroupState.PENDING_APPROVAL,))
                    pending_rows = await cursor.fetchall()

                    # إحصائيات
                    cursor = await conn.execute(
                        "SELECT state, COUNT(*) FROM group_states GROUP BY state")
                    state_counts = await cursor.fetchall()

                    lines = [
                        f"📊 المجموعات المنضم إليها",
                        f"═══════════════════════════",
                        f"",
                    ]

                    # إحصائيات الحالات
                    lines.append("📈 توزيع الحالات:")
                    for s, c in state_counts:
                        lines.append(f"  • {s}: {c}")
                    lines.append("")

                    # المجموعات المنضم إليها
                    if joined_rows:
                        lines.append(f"✅ منضم إليها ({len(joined_rows)}):")
                        for i, r in enumerate(joined_rows, 1):
                            raw = r[1] or r[0] or '?'
                            joined_by = r[2] or '?'
                            masked = joined_by[:4] + '***' + joined_by[-4:] if len(joined_by) > 8 else joined_by
                            members = r[3] or '?'
                            when = r[4][:19] if r[4] else '?'
                            lines.append(f"  {i}. {raw[:60]}")
                            lines.append(f"     by={masked} members={members} at={when}")
                    else:
                        lines.append("❌ لا توجد مجموعات منضم إليها بعد")
                    lines.append("")

                    # المجموعات المنضم إليها سابقاً
                    if already_rows:
                        lines.append(f"ℹ️ عضو سابقاً ({len(already_rows)}):")
                        for i, r in enumerate(already_rows[:10], 1):
                            raw = r[1] or r[0] or '?'
                            lines.append(f"  {i}. {raw[:60]}")
                        if len(already_rows) > 10:
                            lines.append(f"  ... و {len(already_rows) - 10} أخرى")

                    # [REQAUDIT-2] المجموعات بانتظار موافقة المشرف
                    if pending_rows:
                        lines.append("")
                        lines.append(f"✉️ بانتظار موافقة المشرف ({len(pending_rows)}):")
                        for i, r in enumerate(pending_rows[:10], 1):
                            raw = r[1] or r[0] or '?'
                            joined_by = r[2] or '?'
                            masked = joined_by[:4] + '***' + joined_by[-4:] if len(joined_by) > 8 else joined_by
                            when = r[3][:19] if r[3] else '?'
                            lines.append(f"  {i}. {raw[:60]}")
                            lines.append(f"     by={masked} at={when} (طلب مُرسل، ينتظر القبول — فحص تلقائي كل 30د)")
                        if len(pending_rows) > 10:
                            lines.append(f"  ... و {len(pending_rows) - 10} أخرى")

                    lines.append("")
                    lines.append("═══════════════════════════")

                    await reply("\n".join(lines))
                except Exception as e:
                    logging.error(f"[JOINED_GROUPS] Error: {e}", exc_info=True)
                    await reply(f"❌ خطأ: {e}")

            elif cmd == "/pending_approvals":
                # === [REQAUDIT-2] عرض المجموعات بانتظار موافقة المشرف ===
                logging.info("[PENDING_APPROVALS] /pending_approvals command invoked")
                try:
                    conn = await self.db._ensure_conn()
                    cursor = await conn.execute(
                        "SELECT normalized_link, raw_link, joined_by, last_seen, last_error "
                        "FROM group_states WHERE state = ? ORDER BY last_seen DESC LIMIT 100",
                        (GroupState.PENDING_APPROVAL,))
                    rows = await cursor.fetchall()
                    cursor = await conn.execute(
                        "SELECT COUNT(*) FROM group_states WHERE state = ?",
                        (GroupState.PENDING_APPROVAL,))
                    total = (await cursor.fetchone())[0]
                    lines = [
                        f"✉️ مجموعات بانتظار موافقة المشرف (المجموع: {total})",
                        f"═══════════════════════════",
                        f"فحص تلقائي كل 30 دقيقة — عند القبول تتحول تلقائياً إلى JOINED",
                        f"",
                    ]
                    if not rows:
                        lines.append("✅ لا توجد طلبات بانتظار الموافقة حالياً")
                    else:
                        for i, r in enumerate(rows[:50], 1):
                            raw = r[1] or r[0] or '?'
                            joined_by = r[2] or '?'
                            masked = joined_by[:4] + '***' + joined_by[-4:] if len(joined_by) > 8 else joined_by
                            when = r[3][:19] if r[3] else '?'
                            lines.append(f"{i}. {raw[:60]}")
                            lines.append(f"   by={masked} at={when}")
                        if total > 50:
                            lines.append(f"... و {total - 50} أخرى (استخدم /cleanup_preview لإدارة القائمة)")
                    lines.append("")
                    lines.append("═══════════════════════════")
                    await reply("\n".join(lines))
                except Exception as e:
                    logging.error(f"[PENDING_APPROVALS] Error: {e}", exc_info=True)
                    await reply(f"❌ خطأ: {e}")

            elif cmd == "/queue":
                # === عرض محتويات القائمة + الأولوية ===
                logging.info("[QUEUE] /queue command invoked")
                try:
                    conn = await self.db._ensure_conn()
                    # إحصائيات الأولوية
                    cursor = await conn.execute(
                        "SELECT priority, COUNT(*) FROM link_queue WHERE status = 'QUEUED' GROUP BY priority ORDER BY priority")
                    priority_stats = await cursor.fetchall()

                    # أحدث 20 رابط
                    cursor = await conn.execute(
                        "SELECT id, raw_link, status, enqueued_at, next_retry_at, attempt_count, last_error, member_count, priority "
                        "FROM link_queue ORDER BY priority ASC, member_count DESC NULLS LAST, id DESC LIMIT 20")
                    rows = await cursor.fetchall()

                    queue_size = await self.prod_db.get_queue_size()

                    lines = [
                        f"📋 Queue (depth={queue_size})",
                        f"═══════════════════════════",
                    ]

                    # إحصائيات الأولوية
                    if priority_stats:
                        lines.append("📊 توزيع الأولوية:")
                        for p, count in priority_stats:
                            label = {1: '🔴 HIGH (5K+)', 2: '🟡 MEDIUM (1K+)', 3: '⚪ LOW (500+)'}.get(p, f'?{p}')
                            lines.append(f"   {label}: {count}")
                        lines.append("")

                    if rows:
                        for r in rows:
                            mc = r[7] if r[7] else '?'
                            pr = r[8] if r[8] else 3
                            pr_label = {1: '🔴', 2: '🟡', 3: '⚪'}.get(pr, '?')
                            lines.append(f"  {pr_label} id={r[0]} status={r[2]} members={mc}")
                            lines.append(f"    link={r[1][:50]}")
                            lines.append(f"    attempts={r[5]} enqueued={r[3][:19] if r[3] else '?'}")
                            if r[4]:
                                lines.append(f"    next_retry={r[4][:19]}")
                            if r[6]:
                                lines.append(f"    error={r[6][:50]}")
                    else:
                        lines.append("  (فارغة)")

                    await reply("\n".join(lines))
                except Exception as e:
                    await reply(f"❌ خطأ: {e}")

            else: await reply(f"❓ أمر غير معروف: {cmd}\nاكتب /help")

        except Exception as e:
            logging.error(f"CMD error: {e}", exc_info=True)

    def is_scan_running(self):
        return any(not t.done() for t in self._current_scan_tasks)

    def stop_scan(self):
        for s in self._current_scanners.values(): s.cancel()

    async def _start_scan_all(self, days, cmd_name):
        """بدء مسح لكل المستخدمين المراقبين فقط (وليس الفدائيين)"""
        if self.is_scan_running():
            await self._send("⚠️ يوجد مسح قيد التنفيذ\nأرسل /scan_stop لإيقافه")  # noqa: ignore result
            return
        all_watchers = await self.db.get_active_watchers()
        # فلترة: monitors فقط — Joiner لا يجب أن يُمسح
        watchers = [w for w in all_watchers if w.get('role', 'monitor') == 'monitor']
        if not watchers:
            await self._send("❌ لا يوجد مستخدمون مراقبون")  # noqa: ignore result
            return
        d = f"{days} يوم" if days else "كامل"
        await self._send(f"🚀 بدء المسح ({cmd_name}) لـ {len(watchers)} مراقب\n📅 الفترة: {d}\n⏳ جاري...")  # noqa: ignore result
        logging.info(f"[SCAN] Starting scan for {len(watchers)} monitors (filtered from {len(all_watchers)} total accounts)")
        for w in watchers:
            logging.info(f"[SCAN] Will scan: {w['phone']} (role={w.get('role', 'monitor')})")
        # Prune completed tasks from previous scans (prevents unbounded list growth)
        self._current_scan_tasks = [t for t in self._current_scan_tasks if not t.done()]
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
                self.config.history_skip_channel_posts, phone, watcher.get('display_name', ''), p,
                message_claim=self.message_claim, prod_db=self.prod_db)
            self._current_scanners[phone] = scanner
            await scanner.scan()
        except asyncio.CancelledError: pass
        except Exception as e: logging.error(f"Scan error {watcher['phone']}: {e}", exc_info=True)
        finally:
            self._current_scanners.pop(watcher['phone'], None)
            # Remove this task from _current_scan_tasks (prevents unbounded growth)
            # Note: the task may not be in the list if _start_scan_all was called
            # concurrently — use try/except to be safe.
            try:
                current_task = asyncio.current_task()
                if current_task and current_task in self._current_scan_tasks:
                    self._current_scan_tasks.remove(current_task)
            except (ValueError, RuntimeError):
                pass

    async def _alert_terminal_failure(self, phone: str, reason: str, detail: str = ""):
        """[REQAUDIT-3] Send a ONE-TIME Telegram alert to the owner when a
        joiner/monitor phone hits a terminal session failure
        (not_authorized / invalid_session_string / invalid_session /
        client_creation_error). Subsequent hits for the same phone are
        suppressed by self._alerted_terminal_phones so we don't spam the
        owner every 1h while they're fixing the session in the DB.

        The alert goes to OWNER_ID via the bot_client. If bot_client is
        None / not connected / OWNER_ID unset, we still log (always) and
        just skip the Telegram send.
        """
        logging.error(
            f"[ACCOUNT] {phone} STATUS=FAILED\n"
            f"[ACCOUNT] reason={reason}\n"
            f"[ACCOUNT] action=re-login or session_string update required"
            + (f"\n[ACCOUNT] detail={detail}" if detail else "")
        )
        if phone in self._alerted_terminal_phones:
            return  # already alerted — avoid 1h-cadence spam
        self._alerted_terminal_phones.add(phone)
        oid = self.config.owner_id
        if oid is None:
            return  # OWNER_ID unset — log is the only channel
        try:
            if self.bot_client and self.bot_client.is_connected():
                msg = (
                    f"⚠️ *Joiner Fleet Alert*\n"
                    f"Phone: `{phone}`\n"
                    f"Reason: `{reason}`\n"
                    f"Action: re-login or update session_string in watchers DB\n"
                    f"The account is now OFFLINE until fixed."
                )
                try:
                    await self.bot_client.send_message(oid, msg, parse_mode='Markdown')
                except Exception:
                    # Markdown parse failure → retry as plain text
                    try:
                        await self.bot_client.send_message(oid, msg)
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"[ACCOUNT] {phone} alert send failed: {e}")

    async def _run_user_client(self, watcher):
        """تشغيل user_client — المراقبون فقط يستمعون للرسائل، الفدائيون لا

        Startup Contract:
            - لا تعتبر الحساب READY حتى: connect → authorize → register handlers
            - لو فشل أي خطوة، سجل STATUS=FAILED مع السبب

        [REQAUDIT-3] NON-TERMINAL refactor: previously, terminal session
        failures (invalid_session_string / invalid_session /
        client_creation_error / not_authorized) did `return` — the loop
        died, the phone stayed `not_connected` forever, and the supervisor
        didn't restart it (user_tasks weren't supervised). Now the loop:
          1. Re-fetches the watcher from the DB on every iteration so an
             operator-updated session_string is picked up without a
             process restart.
          2. On terminal failures: alerts the owner ONCE, then sleeps 1h
             and `continue`s — never `return`s. This gives the operator
             time to fix the DB and lets the supervisor / next iteration
             recover the account automatically.
        """
        phone = watcher['phone']
        backoff = 5
        while self._running:
            try:
                # [REQAUDIT-3] Re-fetch watcher from DB so updated
                # session_string / role is picked up without a restart.
                fresh = None
                try:
                    fresh = await self.db._supabase_get_watcher(phone)
                except Exception:
                    fresh = None
                if fresh and fresh.get('session_string'):
                    session_string = fresh['session_string']
                    role = fresh.get('role', watcher.get('role', 'monitor'))
                else:
                    session_string = watcher.get('session_string')
                    role = watcher.get('role', 'monitor')

                client = self.user_clients.get(phone)
                if client is None:
                    # حماية من الجلسات التالفة
                    if not session_string or not isinstance(session_string, str) or len(session_string) < 50:
                        await self._alert_terminal_failure(phone, 'invalid_session_string')
                        self._cleanup_user_client(phone)
                        await asyncio.sleep(3600)  # 1h cooldown — operator fix window
                        continue
                    try:
                        client = self._create_user_client(session_string, phone)
                    except ValueError as ve:
                        await self._alert_terminal_failure(phone, 'invalid_session', str(ve))
                        self._cleanup_user_client(phone)
                        await asyncio.sleep(3600)
                        continue
                    except Exception as ce:
                        await self._alert_terminal_failure(phone, 'client_creation_error', str(ce))
                        await asyncio.sleep(3600)
                        continue
                    self.user_clients[phone] = client
                    # [REQAUDIT-3] session recovered → clear the alert flag
                    self._alerted_terminal_phones.discard(phone)

                if not client.is_connected():
                    logging.info(f"[ACCOUNT] {phone} connecting...")
                    await client.connect()

                    # === VERIFY AUTHORIZATION ===
                    if not await client.is_user_authorized():
                        await self._alert_terminal_failure(phone, 'not_authorized', 're-login required')
                        self._cleanup_user_client(phone)
                        await asyncio.sleep(3600)
                        continue

                    # === REGISTER HANDLERS (monitors only) ===
                    if role == 'monitor':
                        self._register_user_handlers(phone)
                        logging.info(
                            f"[ACCOUNT] {phone} STATUS=READY\n"
                            f"[ACCOUNT] role=monitor\n"
                            f"[ACCOUNT] handlers=registered"
                        )
                    else:
                        logging.info(
                            f"[ACCOUNT] {phone} STATUS=READY_FOR_JOIN\n"
                            f"[ACCOUNT] role=joiner\n"
                            f"[ACCOUNT] handlers=none (joiner only)"
                        )
                    # === Notify SourceRegistry of phone status ===
                    if self.source_registry:
                        self.source_registry.update_phone_status(phone, True)
                    backoff = 5
                await client.run_until_disconnected()
            except FloodWaitError as e: await asyncio.sleep(e.seconds + 1)
            except (RPCError, ConnectionError, OSError) as e:
                logging.error(f"[ACCOUNT] {phone} error: {type(e).__name__}: {e}")
            except asyncio.CancelledError: raise
            except Exception as e:
                logging.error(f"[ACCOUNT] {phone} unexpected: {e}", exc_info=True)
            finally:
                # === Notify SourceRegistry of disconnect ===
                if self.source_registry:
                    self.source_registry.update_phone_status(phone, False)
                client = self.user_clients.get(phone)
                if client and client.is_connected():
                    try: await client.disconnect()
                    except Exception: pass
            if not self._running: break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 600)

    def _cleanup_user_client(self, phone: str):
        """Remove a user_client from active tracking when its session is invalid.
        Prevents memory leak of orphaned client objects."""
        client = self.user_clients.pop(phone, None)
        if client:
            logging.info(f"[CLEANUP] Removed user_client for {phone}")
        # Also invalidate dialog cache for this phone
        self.db.invalidate_dialogs_cache(phone)

    def _get_any_user_client(self):
        """يجلب أي user_client متصل (للاستخدام في عمليات get_messages/delete_messages).

        Bot API لا يدعم get_messages (GetHistoryRequest) على القنوات.
        نحتاج حساب User حقيقي لقراءة وحذف رسائل القناة.

        Returns:
            TelegramClient متصل، أو None لو لا يوجد
        """
        for phone, client in self.user_clients.items():
            if client and client.is_connected():
                return client
        return None

    async def _run_startup_scan(self, watcher):
        try:
            await asyncio.sleep(5)
            scanner = HistoryScanner(
                self.user_clients[watcher['phone']], self.bot_client, self.db,
                self.config.channel_id, self.config.startup_scan_days,
                self.config.history_max_per_chat, self.config.history_batch_size,
                self.config.history_skip_channel_posts, watcher['phone'],
                watcher.get('display_name', ''),
                message_claim=self.message_claim, prod_db=self.prod_db)
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
                    except Exception: pass
            if not self._running: break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 600)

    async def _joiner_worker(self):
        """Production Scheduler — يعمل كل 60 ثانية.
        
        المنطق:
        1. اجلب رابط QUEUED واحد
        2. تحقق من FloodWait لكل الحسابات
        3. اختر حساب فدائي غير محظور
        4. فحص العضوية (Hybrid Cache: DB → Memory → API)
        5. AI فحص الرابط (فقط لو جديد)
        6. Rate Limiter يسمح؟
        7. انضمام عبر Rate Limiter
        8. حدّث State Machine
        9. انتظر 60 ثانية قبل المهمة التالية
        """
        await asyncio.sleep(30)  # انتظر البوت يكمل الإقلاع
        logging.info("🔄 Production Scheduler started — runs every 60s")
        # === WORKER HEALTH STATE ===
        await self.prod_db.set_setting('scheduler_state', 'RUNNING')
        await self.prod_db.set_setting('scheduler_last_heartbeat', datetime.now().isoformat())
        # ملاحظة: STARTUP RECOVERY تم نقله إلى start() — لا حاجة لتكراره هنا

        cycle = 0
        while self._running:
            cycle += 1
            # === HEARTBEAT ===
            await self.prod_db.set_setting('scheduler_last_heartbeat', datetime.now().isoformat())
            await self.prod_db.set_setting('scheduler_last_cycle', str(cycle))
            try:
                # Emergency Control: لو الانضمام متوقف → انتظر بس
                if self._join_paused:
                    logging.info(f"[SCHED] cycle={cycle} ⏸️ Join PAUSED — sleeping 60s (send /resume_join or /clear_floodwait)")
                    await asyncio.sleep(60)
                    continue

                # [REQAUDIT-3] Fleet-health backoff gate — if ALL joiners
                # are unavailable (FloodWait / disconnected / safety-guard
                # blocked), DON'T pick a link. Previously the scheduler
                # picked a link every cycle, ran PIPELINE-6 joiner
                # iteration, found no eligible joiner, marked the link
                # QUEUED+5min, and re-enqueued it 5 min later — burning
                # cycles on every stuck link in the queue (96+ links ×
                # every 5 min = wasted storm). Now we skip the cycle
                # entirely; when a joiner comes back (detected by
                # _joiner_fleet_health_loop), the next cycle resumes.
                fleet = getattr(self, '_fleet_health', None) or {}
                if fleet.get('connected_joiners', 0) == 0:
                    logging.info(
                        f"[SCHED] cycle={cycle} 🛑 [FLEET] all joiners "
                        f"unavailable (floodwait={len(fleet.get('floodwait_joiners', []))}, "
                        f"disconnected={len(fleet.get('disconnected_joiners', []))}, "
                        f"safety_guard={fleet.get('safety_guard_blocked_joiners', 0)}) "
                        f"— skipping cycle, sleeping 60s"
                    )
                    await asyncio.sleep(60)
                    continue

                # تحديث حجم القائمة في الإحصائيات
                queue_size = await self.prod_db.get_queue_size()
                await self.metrics.update_queue_size(queue_size)

                # 1. اجلب رابط QUEUED واحد (لا burst)
                queued = await self.prod_db.get_queued_links(limit=1)
                if not queued:
                    logging.debug(f"[SCHED] cycle={cycle} Queue empty — waiting for new links from monitors")
                    await asyncio.sleep(60)
                    continue

                link_data = queued[0]
                normalized = link_data['normalized_link']
                raw_link = link_data['raw_link']
                link_type = link_data['link_type']

                # === PIPELINE STAGE 3: Scheduler read link from queue ===
                link_id = link_data.get('id', '?')
                logging.info(f"[LINK id={link_id}] [PIPELINE-3] 🔄 cycle={cycle} Scheduler picked link: {raw_link[:60]} (type={link_type})")

                # لا ننتظر Scorer — عالج فوراً

                # 2. تحقق من حالة المجموعة في State Machine
                state = await self.prod_db.get_group_state(normalized)
                # [REQAUDIT-2] PENDING_APPROVAL = request already sent — do NOT
                # re-issue ImportChatInviteRequest (pointless + PeerFlood risk).
                if state in (GroupState.JOINED, GroupState.ALREADY_MEMBER, GroupState.PENDING_APPROVAL):
                    logging.info(f"[LINK id={link_id}] [PIPELINE-3] ⏭️ already {state} — skipping")
                    await self.prod_db.update_queue_status(link_data['id'], 'DONE')
                    await self.metrics.record_skip('already_joined')
                    continue

                if state == GroupState.BANNED:
                    logging.info(f"[LINK id={link_id}] [PIPELINE-3] ⏭️ BANNED — skipping")
                    await self.prod_db.update_queue_status(link_data['id'], 'DONE')
                    await self.metrics.record_skip('banned')
                    continue

                # 3. AI فحص الرابط — يخضع لمتغير البيئة AI_BATCH_MODE
                # AI_BATCH_MODE=true  (افتراضي): يتخطى الذكاء الاصطناعي لمعالجة قائمة الانتظار المتراكمة بسرعة
                # AI_BATCH_MODE=false         : يعيد تفعيل فحص الذكاء الاصطناعي لكل رابط
                ai_batch_mode = os.getenv("AI_BATCH_MODE", "true").lower() in ("true", "1", "yes")
                if state == GroupState.DISCOVERED or state is None:
                    ai_approved = None
                    ai_description = None
                    ai_country = None
                    ai_is_ad = None

                    if not ai_batch_mode and self.ai_analyzer and self.ai_analyzer.enabled:
                        # === PIPELINE STAGE 4: AI verification ===
                        try:
                            ai_text = (link_data.get('message_text') or '') + ' ' + (link_data.get('group_name') or '')
                            ai_result = await self.ai_analyzer.analyze_message(ai_text[:1500])
                            if ai_result:
                                ai_approved = ai_result.get('should_save', True)
                                ai_description = ai_result.get('description')
                                ai_country = ai_result.get('country')
                                ai_is_ad = ai_result.get('is_advertisement', False)
                                logging.info(
                                    f"[LINK id={link_id}] [PIPELINE-4] 🤖 AI verdict: "
                                    f"approved={ai_approved} country={ai_country} ad={ai_is_ad} desc={ai_description}"
                                )
                                # إذا رفض الذكاء الاصطناعي الرابط، تخطي النشر والانضمام
                                if ai_approved is False:
                                    logging.info(f"[LINK id={link_id}] [PIPELINE-4] ❌ AI REJECTED — skipping link")
                                    await self.prod_db.set_group_state(normalized, GroupState.BANNED, raw_link, error='ai_rejected')
                                    await self.prod_db.update_queue_status(link_data['id'], 'DONE')
                                    await self.metrics.record_skip('ai_rejected')
                                    continue
                            else:
                                logging.info(f"[LINK id={link_id}] [PIPELINE-4] ⚠️ AI returned empty — treating as approved")
                        except Exception as ai_err:
                            logging.warning(f"[LINK id={link_id}] [PIPELINE-4] ⚠️ AI error: {ai_err} — proceeding without AI")
                    else:
                        logging.info(f"[LINK id={link_id}] [PIPELINE-4] ⏭️ AI SKIPPED (batch mode={ai_batch_mode})")

                    # [Req-3] PRE-PUBLISH channel/user exclusion — the user wants
                    # student GROUPS only, not channels or user/bot profile links.
                    # For public telegram username links, resolve the entity; if it's
                    # a broadcast channel or a User/Bot (not a real group), mark
                    # BANNED and skip publish+join entirely. Best-effort: on
                    # resolution failure/timeout, proceed with publish so the
                    # pipeline isn't blocked (the scorer + join-time check catch
                    # it later). Private invite links (+hash/joinchat) and WhatsApp
                    # links are skipped here (can't resolve without joining / N/A).
                    if link_type == 'telegram':
                        try:
                            _pp_client = next((c for c in self.user_clients.values() if c and c.is_connected()), None)
                            if _pp_client:
                                from telethon.tl.types import Channel as _PPCh, Chat as _PPCt, User as _PPUs
                                try:
                                    _ent = await asyncio.wait_for(_pp_client.get_entity(raw_link), timeout=15)
                                    _is_broadcast = bool(getattr(_ent, 'broadcast', False))
                                    _is_user = isinstance(_ent, _PPUs) or (not isinstance(_ent, (_PPCh, _PPCt)) and hasattr(_ent, 'first_name'))
                                    if _is_broadcast or _is_user:
                                        _ban_reason = 'is_channel_broadcast' if _is_broadcast else 'not_a_group'
                                        await self.prod_db.set_group_state(normalized, GroupState.BANNED, raw_link, error=_ban_reason)
                                        await self.prod_db.update_queue_status(link_data['id'], 'DONE')
                                        logging.info(f"[LINK id={link_id}] [PIPELINE-5] ⏭️ excluded ({_ban_reason}) before publish — not a student group")
                                        await self.metrics.record_skip(_ban_reason)
                                        continue
                                except asyncio.TimeoutError:
                                    logging.debug(f"[LINK id={link_id}] [PIPELINE-5] pre-publish entity resolve timed out — proceeding with publish")
                                except Exception as _pp_e:
                                    logging.debug(f"[LINK id={link_id}] [PIPELINE-5] pre-publish entity resolve failed ({type(_pp_e).__name__}) — proceeding with publish")
                        except Exception as _ppc_e:
                            logging.debug(f"[LINK id={link_id}] [PIPELINE-5] pre-publish channel check skipped: {_ppc_e}")

                    await self.prod_db.set_group_state(normalized, GroupState.QUEUED, raw_link)

                    # === PIPELINE STAGE 5: Publish to channel ===
                    inserted = await self.db.insert_request(
                        raw_link, datetime.now(),
                        link_data.get('group_name', ''), link_data.get('sender_name', ''),
                        link_data.get('source_phone', ''), link_data.get('message_link'),
                        message_text=link_data.get('message_text', ''),
                        sender_contact=link_data.get('sender_contact', ''),
                        link_type=link_data.get('link_type', 'other'),
                        ai_approved=ai_approved,
                        ai_description=ai_description,
                        ai_country=ai_country,
                        ai_is_ad=ai_is_ad)
                    if inserted:
                        formatted = MessageFormatter.format_link_message(
                            link_data.get('group_name', ''), link_data.get('sender_name', ''),
                            link_data.get('sender_contact', ''), datetime.now(),
                            raw_link, link_data.get('message_text', ''),
                            link_data.get('source_phone', ''), link_data.get('message_link'))
                        buttons = MessageFormatter.get_link_buttons(raw_link)
                        logging.info(f"[LINK id={link_id}] [PUBLISH] started link={raw_link[:60]}")
                        published, msg_id = await self._send(formatted, buttons=buttons)
                        if published:
                            logging.info(f"[LINK id={link_id}] [PUBLISH] success message_id={msg_id}")
                            logging.info(f"[LINK id={link_id}] [PIPELINE-5] ✅ PUBLISHED_VERIFIED message_id={msg_id}")
                            # [PR-METRICS-PUBLISH] increment link_forwarded_total —
                            # سابقًا كان record_link_forwarded() معرّف في link_system.py:608
                            # لكن لا أحد يستدعيه في bot.py، فكان الميتريك يُخرج 0 دائمًا
                            # في /metrics رغم أن الروابط كانت تُنشَر فعليًا. الآن
                            # يُزاد عند كل PUBLISHED_VERIFIED موثّق.
                            try:
                                await self.metrics.record_link_forwarded()
                            except Exception as _lf_e:
                                logging.debug(f"[METRIC] record_link_forwarded failed: {_lf_e}")
                        else:
                            logging.error(f"[LINK id={link_id}] [PUBLISH] failed reason=send_failed link={raw_link[:60]}")
                            logging.error(f"[LINK id={link_id}] [PIPELINE-5] ❌ PUBLISH_FAILED — rolling back phantom publish row + retry in 2 min")
                            # [Req-8 / PUBLISH-VERIFY] insert_request() wrote a dedup row to
                            # forwarded_requests BEFORE _send() ran. _send() failed, so that row
                            # is a PHANTOM (DB says published; channel never got the message).
                            # Without rollback: next cycle sees state=QUEUED → publish block
                            # skipped AND insert_request returns False (duplicate) → link never
                            # published yet proceeds to JOIN. Delete the phantom row + reset
                            # state to DISCOVERED so the next cycle re-attempts the full publish.
                            try:
                                await self.db.delete_forwarded_request(raw_link)
                            except Exception as _del_e:
                                logging.warning(f"[LINK id={link_id}] [PUBLISH] phantom-row rollback failed: {_del_e}")
                            try:
                                await self.prod_db.set_group_state(normalized, GroupState.DISCOVERED, raw_link)
                            except Exception as _st_e:
                                logging.warning(f"[LINK id={link_id}] [PUBLISH] state reset to DISCOVERED failed: {_st_e}")
                            await self.prod_db.update_queue_status(link_data['id'], 'QUEUED',
                                                                   next_retry=datetime.now() + timedelta(minutes=2))
                            await self.metrics.record_skip('publish_failed_send')
                            continue
                    else:
                        logging.info(f"[LINK id={link_id}] [PIPELINE-5] ⏭️ Already published (duplicate)")
                else:
                    logging.debug(f"[LINK id={link_id}] [PIPELINE-5] ⏭️ state={state} — publish block skipped (already queued, no re-publish)")

                # 4. اختر حساب فدائي
                logging.info(f"[LINK id={link_id}] [PIPELINE-6] Selecting joiner...")

                # === GULF FILTER — فحص صارم قبل اختيار الفدائي ===
                # نستخرج username من الرابط لفحصه
                filter_username = ''
                filter_text = ''
                try:
                    # استخراج username من t.me/username أو @username
                    import re as _re
                    m = _re.search(r'(?:t\.me/|@)([A-Za-z0-9_]{3,})', raw_link or '')
                    if m:
                        filter_username = m.group(1)
                    filter_text = (link_data.get('message_text') or '') + ' ' + (link_data.get('group_name') or '')
                except Exception:
                    pass

                # مصدر الرسالة — مهم لتحديد السياق الخليجي
                source_group_name = link_data.get('group_name', '') or ''
                source_phone = link_data.get('source_phone', '') or ''

                should_join, filter_reason = EducationalFilter.should_join(
                    filter_text, filter_username, raw_link,
                    source_group_name=source_group_name,
                    source_phone=source_phone,
                )

                if not should_join:
                    # الرابط مرفوض — لا تنضم
                    logging.warning(
                        f"[LINK id={link_id}] [PIPELINE-6] 🚫 GULF FILTER REJECTED: "
                        f"{raw_link[:60]} (reason={filter_reason}, username={filter_username}, "
                        f"source_group={source_group_name[:30]})"
                    )
                    await self.prod_db.set_group_state(
                        normalized, GroupState.BANNED, raw_link, error=f'gulf_filter_{filter_reason}'
                    )
                    await self.prod_db.update_queue_status(link_data['id'], 'DONE')
                    await self.metrics.record_skip(f'gulf_filter_{filter_reason}')
                    continue

                logging.info(
                    f"[LINK id={link_id}] [PIPELINE-6] ✅ GULF FILTER PASSED: "
                    f"{raw_link[:60]} (reason={filter_reason})"
                )

                # === MEMBER COUNT CHECK ===
                # لو member_count معروف و < 500 → ارفض
                # لو member_count = 0 أو NULL → اقبل (لا توقف)
                member_count = link_data.get('member_count')
                if member_count is not None and member_count > 0 and member_count < 500:
                    logging.info(
                        f"[LINK id={link_id}] [PIPELINE-6] 🚫 LOW MEMBER COUNT: "
                        f"{raw_link[:60]} (members={member_count}, threshold=500) — skipping"
                    )
                    await self.prod_db.set_group_state(
                        normalized, GroupState.BANNED, raw_link,
                        error=f'low_member_count_{member_count}'
                    )
                    await self.prod_db.update_queue_status(link_data['id'], 'DONE')
                    await self.metrics.record_skip('low_member_count')
                    continue

                if member_count is not None:
                    logging.info(
                        f"[LINK id={link_id}] [PIPELINE-6] 👥 Member count: {member_count if member_count else 'unknown'}"
                    )

                joiners = await self.db.get_watchers_by_role("joiner")
                if not joiners:
                    logging.warning(f"[LINK id={link_id}] [JOINER] no joiner accounts configured — retry in 5 min")
                    await self.prod_db.update_queue_status(link_data['id'], 'QUEUED',
                                                           next_retry=datetime.now() + timedelta(minutes=5))
                    await asyncio.sleep(30)
                    continue

                # [FIX-LOG-NOISE] Pre-filter disabled joiners ONCE before the
                # membership check + selection loop. Previously a disabled
                # joiner passed connection/rate-limiter/safety-guard checks,
                # got SELECTED, then was rejected inside _join_group_safe
                # (JOINER_DISABLED) — wasting ~6 Supabase calls, a safety
                # guard run, a metric increment, and re-queuing the link with
                # only 1min retry → perpetual wasteful cycle every 60s.
                _enabled_joiners = [j for j in joiners if j.get('joiner_enabled', 1)]
                _disabled_count = len(joiners) - len(_enabled_joiners)
                if _disabled_count:
                    logging.info(
                        f"[LINK id={link_id}] [PIPELINE-6] {_disabled_count} joiner(s) disabled — filtered before selection"
                    )
                    joiners = _enabled_joiners
                if not joiners:
                    logging.warning(
                        f"[LINK id={link_id}] [JOINER] all joiner accounts disabled (joiner_enabled=0) — retry in 5 min"
                    )
                    await self.prod_db.update_queue_status(link_data['id'], 'QUEUED',
                                                           next_retry=datetime.now() + timedelta(minutes=5))
                    await asyncio.sleep(30)
                    continue

                # 5. Membership Check — across ALL joiners BEFORE selecting one.
                # This prevents a second joiner from re-joining a group that
                # another joiner already joined.
                if link_type == 'telegram':
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] Checking membership across ALL joiners...")
                    already_joined_by = None
                    for j in joiners:
                        jphone = j['phone']
                        jclient = self.user_clients.get(jphone)
                        if not jclient or not jclient.is_connected():
                            continue
                        try:
                            is_member = await self.membership_cache.check_membership(jphone, normalized, jclient)
                            if is_member is True:
                                already_joined_by = jphone
                                break
                        except Exception as e:
                            logging.debug(f"[MEMBERSHIP] check failed for {jphone}: {e}")
                            continue
                    if already_joined_by:
                        logging.info(
                            f"[LINK id={link_id}] [PIPELINE-6] 🚫 Already joined by {already_joined_by} — skipping"
                        )
                        await self.prod_db.set_group_state(
                            normalized, GroupState.ALREADY_MEMBER, raw_link,
                            joined_by=already_joined_by
                        )
                        await self.prod_db.update_queue_status(link_data['id'], 'DONE')
                        await self.metrics.record_skip('already_member')
                        continue

                # 6. Joiner selection — try EACH joiner until one passes ALL checks.
                #
                # FIX (PUBLISH-INCIDENT-1): connection check, rate limiter, and
                # safety guard are INSIDE the loop. Previously the connection
                # check was AFTER the loop and only tested the FIRST selected
                # joiner. If that joiner was disconnected, the scheduler would
                # NOT try any other joiner — it set next_retry=+2min, slept 60s,
                # and re-picked the SAME link with the SAME disconnected joiner
                # on the next cycle → infinite retry loop, no JOIN, no PUBLISH.
                # The same anti-pattern existed for rate-limiter and safety-guard
                # (aborting the whole cycle instead of trying the next joiner).
                selected_joiner = None
                selected_client = None
                last_skip_reason = 'none'
                for joiner in joiners:
                    jphone = joiner['phone']

                    # 6a. FloodWait DB check
                    is_blocked, wait = await self.floodwait_mgr.is_blocked(jphone)
                    if is_blocked:
                        logging.info(
                            f"[LINK id={link_id}] [JOINER] unavailable account={jphone} reason=floodwait wait={wait}s"
                        )
                        last_skip_reason = f'floodwait_{jphone}'
                        continue

                    # 6b. Daily join limit
                    await self.db.reset_daily_joins_if_needed(jphone)
                    daily_joins = await self.db.get_daily_join_count(jphone)
                    daily_limit = await self._get_daily_limit(jphone)
                    if daily_joins >= daily_limit:
                        logging.info(
                            f"[LINK id={link_id}] [JOINER] unavailable account={jphone} reason=daily_limit ({daily_joins}/{daily_limit})"
                        )
                        last_skip_reason = f'daily_limit_{jphone}'
                        continue

                    # 6c. Connection check — MOVED INSIDE LOOP (root-cause fix)
                    # [FIX-LOG-NOISE] Downgraded WARNING→INFO: a disconnected
                    # joiner is an expected per-cycle state, not an incident.
                    # The fleet-health loop already alerts when ALL joiners are
                    # down (5min sustained). WARNING here spammed 1 line per
                    # disconnected joiner every 60s.
                    jclient = self.user_clients.get(jphone)
                    if not jclient or not jclient.is_connected():
                        logging.info(
                            f"[LINK id={link_id}] [JOINER] unavailable account={jphone} reason=not_connected"
                        )
                        last_skip_reason = f'not_connected_{jphone}'
                        continue

                    # 6d. Rate limiter — MOVED INSIDE LOOP (same anti-pattern fix)
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] Rate limiter check for {jphone}...")
                    allowed = await self.rate_limiter.check(jphone, 'join')
                    if not allowed:
                        logging.info(
                            f"[LINK id={link_id}] [JOINER] unavailable account={jphone} reason=rate_limited"
                        )
                        last_skip_reason = f'rate_limited_{jphone}'
                        continue

                    # 6e. Safety Guard — MOVED INSIDE LOOP (same anti-pattern fix)
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] 🛡️ Safety Guard checking {jphone}...")
                    guard_ok, guard_reason = await self._safety_guard(jphone, normalized, link_data)
                    if not guard_ok:
                        logging.info(
                            f"[LINK id={link_id}] [JOINER] unavailable account={jphone} reason=safety_guard ({guard_reason})"
                        )
                        last_skip_reason = f'guard_{guard_reason}_{jphone}'
                        continue

                    # All checks passed — select this joiner
                    selected_joiner = joiner
                    selected_client = jclient
                    break

                if not selected_joiner:
                    logging.info(
                        f"[LINK id={link_id}] [JOINER] no eligible joiner (last_reason={last_skip_reason}) — retry in 5 min"
                    )
                    logging.info(
                        f"[LINK id={link_id}] [RETRY] state=QUEUED "
                        f"retry_count={link_data.get('attempt_count', 0) + 1} reason=no_joiner next_retry=+5min"
                    )
                    await self.metrics.record_skip(f'no_joiner_{last_skip_reason}')
                    await self.prod_db.update_queue_status(link_data['id'], 'QUEUED',
                                                           next_retry=datetime.now() + timedelta(minutes=5))
                    await asyncio.sleep(30)
                    continue

                phone = selected_joiner['phone']
                client = selected_client
                logging.info(f"[LINK id={link_id}] [JOINER] selected account={phone}")
                logging.info(f"[LINK id={link_id}] [PIPELINE-6] ✅ Safety Guard PASSED for {phone}")

                # 7. Join attempt
                await self.metrics.record_join_attempt(phone)
                await self.prod_db.set_group_state(normalized, GroupState.JOINING, raw_link)
                await self.prod_db.update_queue_status(link_data['id'], 'PROCESSING')

                logging.info(f"[LINK id={link_id}] [JOIN] started account={phone} link={raw_link[:60]}")
                success, status, member_count = await self._join_group_safe(client, link_data, phone)

                # === SINGLE QUEUE STATE UPDATE ===
                # متغيرات نهائية — تحدّث مرة واحدة فقط في نهاية المعالجة
                final_status = None
                next_retry = None
                state_to_set = None
                state_error = None

                if success and status == "JOINED_VERIFIED":
                    # ✅ نجاح مؤكد عبر GetParticipantRequest
                    state_to_set = GroupState.JOINED
                    final_status = 'DONE'
                    await self.metrics.record_join_success(phone)
                    await self.db.increment_joiner_stats(phone, success=True)
                    logging.info(f"[LINK id={link_id}] [JOIN] success account={phone} members={member_count}")
                    logging.info(
                        f"[LINK id={link_id}] [PIPELINE-6] ✅✅ {phone} JOINED_VERIFIED: {raw_link[:60]} "
                        f"(members={member_count})"
                    )

                elif success and status == "JOIN_UNVERIFIED":
                    # ⚠️ Join API نجح لكن التحقق تعذر — احذر
                    state_to_set = GroupState.JOINED  # اعتبره منضم لكن سجّل التحذير
                    state_error = 'join_unverified'
                    final_status = 'DONE'
                    await self.metrics.record_join_success(phone)
                    await self.db.increment_joiner_stats(phone, success=True)
                    logging.warning(
                        f"[LINK id={link_id}] [PIPELINE-6] ⚠️ {phone} JOIN_UNVERIFIED: {raw_link[:60]} "
                        f"(Telegram accepted but membership not confirmed)"
                    )

                elif status == "ALREADY_MEMBER":
                    state_to_set = GroupState.ALREADY_MEMBER
                    final_status = 'DONE'
                    await self.metrics.record_membership_skip()
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] ℹ️ {phone} already member: {raw_link[:60]}")

                elif status == "PENDING_APPROVAL":
                    # [REQAUDIT-2] Join request sent, awaiting admin approval.
                    # NOT joined yet — but the request succeeded, so mark DONE
                    # (do NOT retry as FAILED: re-issuing ImportChatInviteRequest
                    # or JoinChannelRequest is pointless once a request is
                    # pending, and risks PeerFlood). The background
                    # _pending_approval_recheck_loop will detect when the admin
                    # approves and transition state to JOINED automatically
                    # (self-healing — no operator action needed).
                    state_to_set = GroupState.PENDING_APPROVAL
                    state_error = 'pending_admin_approval'
                    final_status = 'DONE'
                    await self.metrics.record_join_success(phone)
                    await self.db.increment_joiner_stats(phone, success=True)
                    logging.info(
                        f"[LINK id={link_id}] [PIPELINE-6] ✉️ {phone} PENDING_APPROVAL: {raw_link[:60]} "
                        f"(request sent, awaiting admin approval — not retried; recheck loop will detect approval)"
                    )

                elif status == "FLOODWAIT":
                    state_to_set = GroupState.FLOODWAIT
                    state_error = 'FloodWait'
                    final_status = 'QUEUED'
                    next_retry = datetime.now() + timedelta(minutes=30)
                    await self.metrics.record_floodwait(phone)
                    # لا توقف النظام كامل — فقط أوقف هذا الحساب مؤقتاً
                    # FloodWait لرابط واحد لا يوقف 127 رابط آخر
                    logging.warning(f"[FLOODWAIT] {phone} got FloodWait — link requeued in 30 min (system continues)")
                    logging.info(
                        f"[LINK id={link_id}] [RETRY] state=FLOODWAIT "
                        f"retry_count={link_data.get('attempt_count', 0) + 1} reason=floodwait next_retry=+30min"
                    )

                elif status == "BANNED":
                    state_to_set = GroupState.BANNED
                    state_error = 'PeerFlood/Banned'
                    final_status = 'DONE'  # فشل نهائي — لا إعادة محاولة
                    await self.metrics.record_floodwait(phone)
                    self._join_paused = True
                    await self.prod_db.set_setting('join_paused', 'true')
                    logging.warning(f"[AUTO-PAUSE] PeerFlood/Ban detected → join_paused=true in DB")

                elif status == "ACCOUNT_SATURATED":
                    # [PR-CHANNELS-TOO-MUCH] Account has joined too many
                    # channels/supergroups (ChannelsTooMuchError from Telegram).
                    # This is account-level saturation — NOT transient:
                    # retrying in 30 min would just hit the same limit and
                    # burn another API call. Disable the account for join
                    # (joiner_enabled=0) so the joiner selector skips it for
                    # future links, and retry only after 24h to give the
                    # operator time to leave some channels manually.
                    state_to_set = GroupState.FAILED
                    state_error = 'channels_too_much'
                    final_status = 'QUEUED'
                    next_retry = datetime.now() + timedelta(hours=24)
                    await self.metrics.record_skip('channels_too_much')
                    # Disable the account for join (async, best-effort)
                    try:
                        await self.db._supabase_update_watcher(
                            phone, joiner_enabled=0)
                        logging.error(
                            f"[AUTO-DISABLE] {phone} ChannelsTooMuchError "
                            f"→ joiner_enabled=0 (account saturated — re-enable "
                            f"manually after leaving some channels)"
                        )
                    except Exception as dis_e:
                        logging.error(
                            f"[AUTO-DISABLE] failed to disable {phone} "
                            f"after ChannelsTooMuch: {dis_e} — account may "
                            f"keep being selected for join"
                        )
                    logging.warning(
                        f"[LINK id={link_id}] [PIPELINE-6] 🚫 {phone} "
                        f"ACCOUNT_SATURATED: {raw_link[:60]} — account "
                        f"disabled for join, retry in 24h"
                    )

                elif status == "RATE_LIMITED":
                    final_status = 'QUEUED'
                    next_retry = datetime.now() + timedelta(minutes=10)
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] ⏳ {phone} rate limited — retry in 10 min")

                elif status == "TIMEOUT":
                    state_to_set = GroupState.FAILED
                    state_error = 'TIMEOUT'
                    final_status = 'QUEUED'
                    next_retry = datetime.now() + timedelta(minutes=5)
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] ⏰ {phone} join timed out — retry in 5 min")

                elif status == "DISCONNECTED":
                    final_status = 'QUEUED'
                    next_retry = datetime.now() + timedelta(minutes=2)
                    logging.warning(f"[LINK id={link_id}] [PIPELINE-6] ❌ {phone} client disconnected — retry in 2 min")

                elif status in ("MONITOR_NO_JOIN", "JOINER_DISABLED", "PAUSED", "SIMULATION"):
                    final_status = 'QUEUED'
                    next_retry = datetime.now() + timedelta(minutes=1)
                    # [FIX-LOG-NOISE] Downgraded WARNING→INFO: these are
                    # configuration states (disabled/paused/sim/monitor-role),
                    # not runtime incidents. After the pre-filter fix above,
                    # JOINER_DISABLED rarely reaches here (only if a joiner is
                    # disabled between the fleet snapshot and selection).
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] ⚠️ {phone} {status} — skipping")

                elif status == "INVALID":
                    state_to_set = GroupState.BANNED  # لا إعادة محاولة
                    state_error = 'invalid_link'
                    final_status = 'DONE'  # فشل نهائي
                    await self.metrics.record_skip('invalid_link')
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] ❌ invalid link (no username) — skipping")

                elif status == "SKIP":
                    final_status = 'DONE'  # WhatsApp — لا انضمام
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] ⏭️ WhatsApp link — no join needed")

                elif status == "IS_CHANNEL":
                    state_to_set = GroupState.BANNED  # غيرت من FAILED لـ BANNED — لا إعادة محاولة
                    state_error = 'is_channel'
                    final_status = 'DONE'
                    await self.metrics.record_skip('is_channel')
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] 📢 Skipped channel (broadcast): {raw_link[:50]}")

                elif status == "PRIVATE":
                    state_to_set = GroupState.PRIVATE
                    state_error = 'Channel private'
                    final_status = 'DONE'  # فشل نهائي
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] 🔒 private channel: {raw_link[:50]}")

                else:  # FAILED أو أي حال أخرى
                    state_to_set = GroupState.FAILED
                    state_error = status
                    final_status = 'QUEUED'
                    next_retry = datetime.now() + timedelta(minutes=30)
                    logging.warning(f"[LINK id={link_id}] [PIPELINE-6] ❌ {phone} {status}: {raw_link[:60]}")

                # === UPDATE STATE MACHINE ONCE ===
                if state_to_set:
                    await self.prod_db.set_group_state(
                        normalized, state_to_set, raw_link,
                        joined_by=phone if success else None,
                        member_count=member_count if success else None,
                        error=state_error
                    )

                # === UPDATE QUEUE STATUS ONCE ===
                if final_status:
                    await self.prod_db.update_queue_status(
                        link_data['id'], final_status, next_retry=next_retry
                    )

                # 12. انتظر 10 ثواني قبل المهمة التالية (الـ Rate Limiter يتحكم بالسرعة الفعلية)
                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[SCHED] Error: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def _priority_scorer(self):
        """مهمة خلفية: تجلب member_count للروابط QUEUED بدون member_count.

        تستخدم حساب المراقب (مو الفدائي) لـ get_entity على الروابط.
        هذا يفصل بين:
          - السحب (مراقب)
          - فحص الأولوية (مراقب)
          - الانضمام (فدائي)

        الأولوية:
          1 = HIGH: member_count >= 10,000 (تجمع عالي)
          2 = MEDIUM: member_count >= 1,000
          3 = LOW: < 1,000 أو غير معروف
        """
        await asyncio.sleep(60)  # انتظر البوت يكمل الإقلاع
        logging.info("📊 Priority Scorer started — scores unscored links every 30s")
        while self._running:
            try:
                # اجلب روابط بدون member_count
                unscored = await self.prod_db.get_unscored_links(limit=5)
                if not unscored:
                    await asyncio.sleep(30)
                    continue

                # استخدم أول مراقب متصل
                monitor_client = None
                for phone, client in self.user_clients.items():
                    if client and client.is_connected():
                        # تأكد إنه مراقب مو فدائي
                        try:
                            w = await self.db._supabase_get_watcher(phone)
                            if w and w.get('role') == 'monitor':
                                monitor_client = client
                                break
                        except Exception:
                            # fallback: استخدم أي حساب متصل
                            monitor_client = client
                            break

                if not monitor_client:
                    logging.debug("[SCORER] No monitor client connected — sleeping 60s")
                    await asyncio.sleep(60)
                    continue

                for link_data in unscored:
                    try:
                        link_id = link_data['id']
                        raw_link = link_data['raw_link']
                        link_type = link_data.get('link_type', '')
                        username = link_data.get('username', '')

                        # WhatsApp — ما نقدر نجيب member_count
                        if link_type == 'whatsapp' or 'chat.whatsapp.com' in (raw_link or ''):
                            await self.prod_db.update_link_priority(link_id, 0)
                            logging.debug(f"[SCORER] {link_id} WhatsApp — priority=3")
                            continue

                        # Telegram — استخرج username لو ما موجود
                        if not username:
                            import re as _re
                            m = _re.search(r'(?:t\.me/|@)([A-Za-z0-9_]{3,})', raw_link or '')
                            if m:
                                username = m.group(1)

                        if not username:
                            await self.prod_db.update_link_priority(link_id, 0)
                            continue

                        # جلب entity للحصول على member_count
                        from telethon.tl.functions.channels import GetFullChannelRequest
                        from telethon.tl.functions.users import GetFullUserRequest
                        from telethon.tl.types import Channel, Chat

                        try:
                            entity = await monitor_client.get_entity(username)
                            # [Req-3] EXCLUDE broadcast channels — the user wants
                            # student GROUPS only, not channels. A channel has
                            # entity.broadcast=True. Mark BANNED so the scheduler
                            # skips it (no publish, no join) on this and future cycles.
                            from telethon.tl.types import User as _TLUser
                            _is_broadcast = bool(getattr(entity, 'broadcast', False))
                            if _is_broadcast:
                                from link_system import LinkNormalizer as _LN2
                                _norm2 = _LN2.extract_links(raw_link)
                                _norm_link2 = _norm2[0].get('normalized', raw_link.lower()) if _norm2 else raw_link.lower()
                                await self.prod_db.set_group_state(_norm_link2, GroupState.BANNED, raw_link, error='is_channel_broadcast')
                                await self.prod_db.update_link_priority(link_id, 0)
                                logging.info(
                                    f"[SCORER] {link_id} @{username}: 📢 broadcast CHANNEL — "
                                    f"marking BANNED (is_channel_broadcast) title='{(getattr(entity,'title','') or '')[:40]}'"
                                )
                                continue
                            # [Req-3] EXCLUDE non-group entities (users/bots) —
                            # t.me/<username> can resolve to a User or Bot, which is
                            # not a real group. Mark BANNED so it's never published/joined.
                            if isinstance(entity, _TLUser) or (not isinstance(entity, (Channel, Chat)) and hasattr(entity, 'first_name')):
                                from link_system import LinkNormalizer as _LN3
                                _norm3 = _LN3.extract_links(raw_link)
                                _norm_link3 = _norm3[0].get('normalized', raw_link.lower()) if _norm3 else raw_link.lower()
                                await self.prod_db.set_group_state(_norm_link3, GroupState.BANNED, raw_link, error='not_a_group')
                                await self.prod_db.update_link_priority(link_id, 0)
                                logging.info(
                                    f"[SCORER] {link_id} @{username}: 👤 not a group (User/Bot) — "
                                    f"marking BANNED (not_a_group)"
                                )
                                continue
                            member_count = 0
                            group_title = ''
                            if hasattr(entity, 'title') and entity.title:
                                group_title = entity.title
                            if hasattr(entity, 'participants_count') and entity.participants_count:
                                member_count = entity.participants_count
                            elif isinstance(entity, (Channel, Chat)):
                                # جرّب GetFullChannel للمجموعات الكبيرة
                                try:
                                    full = await monitor_client(GetFullChannelRequest(entity))
                                    if full and full.full_chat:
                                        member_count = full.full_chat.participants_count or 0
                                except Exception:
                                    pass

                            # === فحص فلتر قوي باستخدام group_title الحقيقي ===
                            # نفحص: username + group_title + raw_link
                            is_bad, bad_reason = EducationalFilter.is_blacklisted(
                                group_title, username, raw_link, ''
                            )
                            if is_bad:
                                # المجموعة سيئة (بيتكوين/عراقي/إلخ) — احظرها فوراً
                                logging.warning(
                                    f"[SCORER] {link_id} @{username}: 🚫 BLACKLISTED ({bad_reason}) "
                                    f"title='{group_title[:40]}' — marking BANNED"
                                )
                                await self.prod_db.update_link_priority(link_id, 0)
                                # حدّث group_states كـ BANNED
                                from link_system import LinkNormalizer
                                norm = LinkNormalizer.extract_links(raw_link)
                                if norm:
                                    norm_link = norm[0].get('normalized', raw_link.lower())
                                    await self.prod_db.set_group_state(
                                        norm_link, GroupState.BANNED, raw_link,
                                        error=f'scorer_blacklist_{bad_reason}'
                                    )
                                continue

                            await self.prod_db.update_link_priority(link_id, member_count)
                            priority_label = 'HIGH' if member_count >= 5000 else ('MEDIUM' if member_count >= 1000 else ('LOW' if member_count >= 500 else 'REJECT'))
                            logging.info(
                                f"[SCORER] {link_id} @{username}: {member_count:,} members "
                                f"title='{group_title[:30]}' → priority={priority_label}"
                            )
                            await asyncio.sleep(1)  # تجنب rate limit
                        except Exception as e:
                            err_str = str(e)[:100]
                            # لو المجموعة خاصة أو محذوفة → priority=3 (low) ونكمل
                            if 'UsernameNotOccupied' in err_str or 'CHANNEL_PRIVATE' in err_str or 'Nobody is using' in err_str:
                                await self.prod_db.update_link_priority(link_id, 0)
                                logging.debug(f"[SCORER] {link_id} @{username}: private/deleted/unknown → priority=3")
                            else:
                                logging.debug(f"[SCORER] {link_id} @{username}: skip ({err_str[:60]})")
                                await self.prod_db.update_link_priority(link_id, 0)

                    except Exception as e:
                        logging.error(f"[SCORER] link {link_data.get('id')}: {e}")
                        await self.prod_db.update_link_priority(link_data.get('id'), 0)

                await asyncio.sleep(5)  # فاصل قصير قبل الجولة التالية

            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[SCORER] outer error: {e}", exc_info=True)
                await asyncio.sleep(30)

    async def _sync_monitored_chats(self):
        """يفحص كل مجموعات المراقبين ويسجلها في monitored_chats.

        - يستبعد المكرر (UNIQUE chat_id)
        - يسجل كل المجموعات والقنوات من كل المراقبين
        - ما ينتظر رسالة — يفحص الـ dialogs مباشرة
        """
        logging.info("🔄 Syncing monitored chats from all accounts...")
        try:
            watchers = await self.db.get_active_watchers()
            # كل الحسابات (مراقبين + فدائيين) — الفدائيين عندهم مجموعات بعد
            total_added = 0
            total_existing = 0

            for w in watchers:
                phone = w['phone']
                client = self.user_clients.get(phone)
                if not client or not client.is_connected():
                    logging.warning(f"[SYNC] {phone} not connected — skipping")
                    continue

                try:
                    added = 0
                    existing = 0
                    async for dialog in client.iter_dialogs():
                        # سجل كل المجموعات والقنوات (تجاهل المحادثات الخاصة فقط)
                        if not dialog.is_group and not dialog.is_channel:
                            continue

                        chat_id = dialog.id
                        chat_title = dialog.title or f'chat_{chat_id}'
                        username = ''
                        try:
                            if dialog.entity and hasattr(dialog.entity, 'username') and dialog.entity.username:
                                username = dialog.entity.username
                        except Exception:
                            pass

                        link_type = 'group'
                        try:
                            if hasattr(dialog.entity, 'broadcast') and dialog.entity.broadcast:
                                link_type = 'channel'
                        except Exception:
                            pass

                        is_new = await self.prod_db.add_monitored_chat(
                            chat_id=chat_id,
                            chat_title=chat_title,
                            username=username,
                            link_type=link_type,
                            monitored_by=phone,
                        )
                        if is_new:
                            added += 1
                        else:
                            existing += 1

                    total_added += added
                    total_existing += existing
                    logging.info(f"[SYNC] {phone}: +{added} new, {existing} existing")
                except Exception as e:
                    logging.error(f"[SYNC] Error for {phone}: {e}")

            logging.info(f"[SYNC] Done: +{total_added} new, {total_existing} existing (no duplicates)")
        except Exception as e:
            logging.error(f"[SYNC] Fatal error: {e}", exc_info=True)

    async def _periodic_sync(self):
        """يكرر sync كل ساعة لضمان تسجيل كل المجموعات الجديدة."""
        while self._running:
            await asyncio.sleep(3600)  # كل ساعة
            try:
                await self._sync_monitored_chats()
                # [B04] refresh the in-memory SourceRegistry from the freshly-synced
                # monitored_chats table. Without this, sources discovered this cycle
                # (e.g. UQU_Medicine1) are NOT visible to PollingScheduler until a
                # full process restart — a 1h discovery→polling gap per new source.
                try:
                    await self.source_registry.load_from_db()
                except Exception as _b04e:
                    logging.warning(f"[PERIODIC_SYNC] source_registry.load_from_db failed: {_b04e}")
            except Exception as e:
                logging.error(f"[PERIODIC_SYNC] Error: {e}")

    async def _chat_classifier(self):
        """مهمة خلفية: تصنيف المجموعات المراقبة بالذكاء الاصطناعي.

        كل 60 ثانية، يفحص مجموعات ما تصنفت بعد، ويصنفها بـ AI.
        - is_educational: هل المجموعة تعليمية؟
        - group_type: group/channel/unknown
        - country: الدولة
        - relevance_score: 0-100
        - should_monitor: هل يتابع مراقبتها؟
        """
        await asyncio.sleep(90)  # انتظر البوت يكمل الإقلاع
        logging.info("🧠 Chat Classifier started — classifies monitored chats every 60s")
        while self._running:
            try:
                # اجلب مجموعات ما تصنفت
                unclassified = await self.prod_db.get_unclassified_chats(limit=15)
                if not unclassified:
                    await asyncio.sleep(60)
                    continue

                for chat in unclassified:
                    try:
                        chat_id = chat['chat_id']
                        title = chat.get('chat_title', '')
                        username = chat.get('username', '')
                        member_count = chat.get('member_count', 0)

                        # استخدم AI لتصنيف المجموعة
                        if hasattr(self, 'ai_analyzer') and self.ai_analyzer:
                            classification = await self.ai_analyzer.classify_group(
                                title, username, member_count
                            )

                            # حدّث السجل بالتصنيف
                            await self.prod_db.update_monitored_chat(
                                chat_id,
                                ai_classification=classification.get('group_type', 'unknown'),
                                ai_country=classification.get('country', 'أخرى'),
                                ai_relevance=classification.get('relevance_score', 0),
                                ai_description=classification.get('description', ''),
                                should_monitor=1 if classification.get('should_monitor', True) else 0,
                            )

                            logging.info(
                                f"[CLASSIFIER] chat_id={chat_id} '{title[:30]}': "
                                f"type={classification.get('group_type')} "
                                f"country={classification.get('country')} "
                                f"score={classification.get('relevance_score')} "
                                f"monitor={classification.get('should_monitor')}"
                            )
                        else:
                            # ما عندنا AI — حدّث كـ unknown
                            await self.prod_db.update_monitored_chat(
                                chat_id,
                                ai_classification='unknown',
                                ai_country='أخرى',
                                ai_relevance=0,
                                ai_description='no AI',
                            )

                        await asyncio.sleep(2)  # تجنب rate limit على AI

                    except Exception as e:
                        logging.error(f"[CLASSIFIER] chat {chat.get('chat_id')}: {e}")
                        # حدّث كـ failed عشان ما يعيد المحاولة
                        await self.prod_db.update_monitored_chat(
                            chat.get('chat_id'),
                            ai_classification='error',
                            ai_description=str(e)[:100],
                        )

                await asyncio.sleep(10)  # فاصل قصير قبل الجولة التالية

            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[CLASSIFIER] outer error: {e}", exc_info=True)
                await asyncio.sleep(30)

    async def _rejoin_published_links(self, max_messages: int = 5000):
        """يقرأ رسائل القناة المنشورة سابقاً ويعيد إدخال الروابط الصالحة في queue.

        يستخدم عندما queue فاضي والبوت ما عنده روابط جديدة للانضمام.
        - يقرأ رسائل القناة من الأحدث للأقدم
        - يستخرج الروابط منها
        - يفلتر روابط الرسائل (t.me/user/123) والروابط الخاصة (t.me/+xxx)
        - يفحص: هل المجموعة منضم لها بالفعل؟ (تخطّى)
        - يدخل الرابط الصالح في queue (لو ما هو مكرر)
        - Priority Scorer سيجلبه member_count، والمجدول سيعالجه
        """
        logging.info(f"[REJOIN] Starting — reading up to {max_messages} channel messages")
        try:
            # ابحث عن حساب مراقب متصل (مو فدائي)
            monitor_client = None
            monitor_phone = None
            for phone, client in self.user_clients.items():
                w = await self.db._supabase_get_watcher(phone)
                if w and w.get('role') == 'monitor' and client and client.is_connected():
                    monitor_client = client
                    monitor_phone = phone
                    break
            if not monitor_client:
                # fallback: أي حساب متصل
                for phone, client in self.user_clients.items():
                    if client and client.is_connected():
                        monitor_client = client
                        monitor_phone = phone
                        break
            if not monitor_client:
                logging.error("[REJOIN] No connected client — aborting")
                return 0

            # اقرأ رسائل القناة
            channel_id = self.config.channel_id
            offset_id = 0
            batch_size = 1000
            total_readded = 0
            total_scanned = 0
            total_skipped_msg_links = 0
            total_skipped_already_joined = 0
            total_skipped_private = 0
            total_skipped_duplicate = 0  # روابط QUEUED بالفعل

            await self._send(f"📖 [REJOIN] بدأ فحص رسائل القناة (حد {max_messages} رسالة)...")  # noqa: ignore result

            while total_scanned < max_messages:
                try:
                    messages = await monitor_client.get_messages(
                        channel_id, limit=batch_size, offset_id=offset_id, reverse=False
                    )
                    if not messages:
                        logging.info("[REJOIN] No more messages")
                        break

                    for msg in messages:
                        total_scanned += 1
                        if total_scanned > max_messages:
                            break

                        text = msg.message or ''
                        if not text:
                            continue

                        # استخرج الروابط من النص
                        from link_system import LinkNormalizer, GroupState
                        links_data = LinkNormalizer.extract_links(text)
                        if not links_data:
                            continue

                        for link_info in links_data:
                            link = link_info.get('raw', '')
                            if not link:
                                continue
                            normalized = link_info.get('normalized', link.lower())
                            link_lower = link.lower()

                            # تخطّي روابط WhatsApp (ما نقدر ننضم)
                            if 'chat.whatsapp.com' in link_lower or 'wa.me' in link_lower:
                                continue

                            # تخطّي روابط الرسائل (t.me/user/123)
                            import re as _re_msg
                            if _re_msg.search(r'^https?://t(?:elegram)?\.me/[A-Za-z0-9_]+/\d+', link, _re_msg.IGNORECASE):
                                total_skipped_msg_links += 1
                                continue

                            # تخطّي روابط خاصة (t.me/+xxx, joinchat)
                            if '/+' in link or 'joinchat' in link_lower:
                                total_skipped_private += 1
                                continue

                            # تحقق من group_states — هل المجموعة منضم لها بالفعل؟
                            state = await self.prod_db.get_group_state(normalized)
                            # [REQAUDIT-2] PENDING_APPROVAL = request sent; skip
                            if state in (GroupState.JOINED, GroupState.ALREADY_MEMBER, GroupState.PENDING_APPROVAL):
                                total_skipped_already_joined += 1
                                continue
                            if state == GroupState.BANNED:
                                # ممنوعة — لا تعيد إدخالها
                                continue

                            # استخرج username
                            username = link_info.get('username', '')
                            if not username:
                                m_user = _re_msg.search(r'(?:t\.me/|@)([A-Za-z0-9_]{3,})', link)
                                if m_user:
                                    username = m_user.group(1)

                            if not username:
                                continue

                            # أعد إدخال الرابط في queue (بدون requeue — تجنب duplicates)
                            # allow_requeue=False: ما يعيد روابط QUEUED أو DONE
                            # فقط روابط جديدة تنضاف
                            try:
                                link_data_for_queue = {
                                    'raw': link,
                                    'normalized': normalized,
                                    'link_type': 'telegram',
                                    'username': username,
                                    'group_name': '(من القناة المنشورة)',
                                    'source_phone': monitor_phone,
                                    'message_text': text[:200],
                                }
                                # allow_requeue=False: فقط روابط جديدة (ما يعيد QUEUED)
                                enqueued = await self.prod_db.enqueue_link(
                                    link_data_for_queue, allow_requeue=False
                                )
                                if enqueued:
                                    total_readded += 1
                                    if total_readded % 50 == 0:
                                        logging.info(f"[REJOIN] Re-added {total_readded} links so far")
                                        await self._send(f"📖 [REJOIN] تمت إعادة {total_readded} رابط للقائمة")  # noqa: ignore result
                                else:
                                    total_skipped_duplicate += 1
                            except Exception as e:
                                logging.debug(f"[REJOIN] enqueue error for {link[:50]}: {e}")

                    # حدّث offset_id
                    offset_id = messages[-1].id
                    if len(messages) < batch_size:
                        break

                    # انتظر قليل لتجنب FloodWait
                    await asyncio.sleep(2)

                except Exception as e:
                    logging.error(f"[REJOIN] Error reading batch: {e}")
                    break

            report = (
                f"✅ [REJOIN] اكتمل الفحص\n\n"
                f"📊 الإحصائيات:\n"
                f"  • رسائل مُفحوصة: {total_scanned}\n"
                f"  • روابط أُعيدت للقائمة: {total_readded}\n"
                f"  • تخطّى روابط رسائل (t.me/u/123): {total_skipped_msg_links}\n"
                f"  • تخطّى روابط خاصة (t.me/+xxx): {total_skipped_private}\n"
                f"  • تخطّى منضم مسبقاً: {total_skipped_already_joined}\n"
                f"  • تخطّى QUEUED بالفعل: {total_skipped_duplicate}\n\n"
                f"🎯 المجدول سيبدأ معالجة الروابط الجديدة تلقائياً\n"
                f"📊 Queue depth الحالي: {await self.prod_db.get_queue_size()}"
            )
            logging.info(f"[REJOIN] Done: {total_readded} re-added")
            await self._send(report)  # noqa: ignore result
            return total_readded

        except Exception as e:
            logging.error(f"[REJOIN] Fatal error: {e}", exc_info=True)
            await self._send(f"❌ [REJOIN] خطأ: {e}")  # noqa: ignore result
            return 0

    async def _bulk_join_worker(self):
        """Bulk Join Worker — يقرأ روابط القناة ويحاول الانضمام لكل واحد.

        الاستراتيجية:
        1. اقرأ رسائل القناة من الأحدث للأقدم (1000 رسالة في كل batch)
        2. استخرج الروابط من كل رسالة
        3. لكل رابط:
           a. تجاوز لو ليس تيليجرام (WhatsApp لا يمكن الانضمام)
           b. تجاوز لو الحالة = JOINED أو ALREADY_MEMBER في group_states
           c. حاول الانضمام عبر _join_group_safe
           d. انتظر 120 ثانية بين كل انضمام (آمن)
        4. توقف عند /bulk_join_stop أو FloodWait
        """
        logging.info("[BULK_JOIN] Worker started — reading channel history")
        joiner_phone = None
        joiner_client = None

        # ابحث عن حساب الفدائي المتصل
        for phone, client in self.user_clients.items():
            w = await self.db._supabase_get_watcher(phone)
            if w and w.get('role') == 'joiner' and client and client.is_connected():
                joiner_phone = phone
                joiner_client = client
                break

        if not joiner_phone or not joiner_client:
            logging.error("[BULK_JOIN] No connected joiner account found!")
            self._bulk_join_running = False
            await self._send("❌ لا يوجد حساب فدائي متصل. أرسل /bulk_join بعد ربط حساب فدائي.")  # noqa: ignore result
            return

        logging.info(f"[BULK_JOIN] Using joiner: {joiner_phone}")

        # Worker Supervisor state
        worker_state = 'RUNNING'

        try:
            # بدلاً من قراءة رسائل القناة، اقرأ من link_queue مباشرة
            # هذا أسرع وأكثر موثوقية — الروابط موجودة بالفعل في القائمة
            offset_id = 0
            batch_size = 50

            while self._bulk_join_running and not self._bulk_join_stop:
                try:
                    # === CHECK _join_paused BEFORE EACH LINK ===
                    if self._join_paused:
                        worker_state = 'PAUSED'
                        logging.info("[BULK_JOIN] PAUSED — waiting for /resume_join or /clear_floodwait")
                        await self._send("[BULK] PAUSED — waiting for resume")  # noqa: ignore result
                        # انتظر حتى يُرفع الإيقاف
                        while self._join_paused and self._bulk_join_running and not self._bulk_join_stop:
                            await asyncio.sleep(30)
                        if not self._bulk_join_running or self._bulk_join_stop:
                            break
                        worker_state = 'RUNNING'
                        logging.info("[BULK_JOIN] Resumed — continuing")

                    # تحقق من اتصال الـ joiner
                    if not joiner_client or not joiner_client.is_connected():
                        logging.error("[BULK_JOIN] ❌ joiner client disconnected — pausing 60s")
                        worker_state = 'FAILED'
                        await asyncio.sleep(60)
                        # حاول إعادة الاتصال
                        joiner_client = self.user_clients.get(joiner_phone)
                        if not joiner_client or not joiner_client.is_connected():
                            logging.error("[BULK_JOIN] ❌ joiner still disconnected — stopping")
                            break
                        worker_state = 'RUNNING'
                        continue

                    # اقرأ روابط من link_queue (QUEUED فقط، بدون next_retry مستقبلي)
                    conn = await self.db._ensure_conn()
                    cursor = await conn.execute(
                        """SELECT id, raw_link, normalized_link, link_type, username, invite_hash,
                                  group_name, sender_name, message_text, message_link, source_phone
                           FROM link_queue
                           WHERE status = 'QUEUED'
                           AND (next_retry_at IS NULL OR next_retry_at <= ?)
                           ORDER BY id ASC LIMIT ?""",
                        (datetime.now().isoformat(), batch_size))
                    rows = await cursor.fetchall()

                    if not rows:
                        logging.info("[BULK_JOIN] No queued links — done!")
                        worker_state = 'COMPLETED'
                        break

                    logging.info(f"[BULK_JOIN] Processing {len(rows)} queued links")

                    for row in rows:
                        if self._bulk_join_stop:
                            logging.info("[BULK_JOIN] Stop requested — exiting")
                            worker_state = 'STOPPED'
                            break

                        # تحقق من _join_paused لكل رابط
                        if self._join_paused:
                            logging.info("[BULK_JOIN] PAUSED mid-batch — waiting")
                            break

                        link_id = row[0]
                        raw_link = row[1]
                        normalized = row[2]
                        link_type = row[3]

                        self._bulk_join_stats['current'] = raw_link
                        self._bulk_join_stats['total'] += 1

                        # a. تجاوز غير Telegram
                        if link_type not in ('telegram', 'telegram_private'):
                            self._bulk_join_stats['skipped'] += 1
                            await self.prod_db.update_queue_status(link_id, 'DONE')
                            continue

                        # b. تجاوز لو الحالة معروفة في group_states
                        state = await self.prod_db.get_group_state(normalized)
                        # [REQAUDIT-2] PENDING_APPROVAL = request already sent
                        if state in (GroupState.JOINED, GroupState.ALREADY_MEMBER, GroupState.PENDING_APPROVAL, GroupState.BANNED, GroupState.PRIVATE):
                            self._bulk_join_stats['skipped'] += 1
                            await self.prod_db.update_queue_status(link_id, 'DONE')
                            continue

                        # c. تحقق من FloodWait
                        is_blocked, wait = await self.floodwait_mgr.is_blocked(joiner_phone)
                        if is_blocked:
                            logging.warning(f"[BULK_JOIN] Joiner in FloodWait ({wait}s) — pausing")
                            await self._send(f"⏳ FloodWait {wait}s — Bulk join paused\n📊 {self._bulk_join_stats}")  # noqa: ignore result
                            await asyncio.sleep(min(wait + 10, 3600))
                            continue

                        # d. حاول الانضمام
                        logging.info(f"[BULK_JOIN] ({self._bulk_join_stats['total']}) Joining: {raw_link[:60]}")
                        link_data = {
                            'raw': raw_link,
                            'raw_link': raw_link,
                            'normalized_link': normalized,
                            'link_type': link_type,
                            'username': row[4] or '',
                            'invite_hash': row[5] or '',
                        }

                        success, status, member_count = await self._join_group_safe(
                            joiner_client, link_data, joiner_phone)

                        if success:
                            self._bulk_join_stats['joined'] += 1
                            await self.prod_db.set_group_state(normalized, GroupState.JOINED, raw_link,
                                                               joined_by=joiner_phone, member_count=member_count)
                            logging.info(f"[BULK_JOIN] ✅ {status}: {raw_link[:50]}")
                        elif status == "ALREADY_MEMBER":
                            self._bulk_join_stats['already'] += 1
                            await self.prod_db.set_group_state(normalized, GroupState.ALREADY_MEMBER, raw_link,
                                                               joined_by=joiner_phone)
                            logging.info(f"[BULK_JOIN] ℹ️ Already member: {raw_link[:50]}")
                        elif status == "PENDING_APPROVAL":
                            # [REQAUDIT-2] request sent, awaiting admin approval
                            self._bulk_join_stats['joined'] += 1
                            await self.prod_db.set_group_state(normalized, GroupState.PENDING_APPROVAL, raw_link,
                                                               joined_by=joiner_phone, error='pending_admin_approval')
                            logging.info(f"[BULK_JOIN] ✉️ Pending approval: {raw_link[:50]}")
                        elif status == "IS_CHANNEL":
                            self._bulk_join_stats['skipped'] += 1
                            await self.prod_db.set_group_state(normalized, GroupState.FAILED, raw_link,
                                                               error='is_channel')
                            logging.info(f"[BULK_JOIN] 📢 Skipped channel: {raw_link[:50]}")
                            continue
                        elif status == "FLOODWAIT":
                            self._bulk_join_stats['failed'] += 1
                            logging.warning(f"[BULK_JOIN] ⚠️ FloodWait — pausing")
                            break
                        else:
                            self._bulk_join_stats['failed'] += 1
                            await self.prod_db.set_group_state(normalized, GroupState.FAILED, raw_link,
                                                               error=status)
                            logging.warning(f"[BULK_JOIN] ❌ {status}: {raw_link[:50]}")

                        # e. انتظر 120 ثانية بين كل انضمام (آمن)
                        await asyncio.sleep(120)

                    # تقرير كل 1000 رسالة
                    if self._bulk_join_stats['total'] % 50 == 0 and self._bulk_join_stats['total'] > 0:
                        s = self._bulk_join_stats
                        logging.info(f"[BULK_JOIN] Progress: {s['total']} processed, {s['joined']} joined, "
                                     f"{s['already']} already, {s['failed']} failed, {s['skipped']} skipped")

                except asyncio.CancelledError:
                    worker_state = 'STOPPED'
                    break
                except Exception as e:
                    worker_state = 'FAILED'
                    logging.error(
                        f"[BULK_JOIN] ❌ WORKER ERROR\n"
                        f"[BULK_JOIN] state={worker_state}\n"
                        f"[BULK_JOIN] current_link={self._bulk_join_stats.get('current', 'none')}\n"
                        f"[BULK_JOIN] error={type(e).__name__}: {e}",
                        exc_info=True
                    )
                    await asyncio.sleep(60)
                    worker_state = 'RUNNING'  # استئناف بعد الـ sleep

            # انتهى
            self._bulk_join_running = False
            s = self._bulk_join_stats
            final_msg = (
                f"🏁 Bulk Join Finished [{worker_state}]\n"
                f"════════════════════\n"
                f"🔗 Total: {s['total']}\n"
                f"✅ Joined: {s['joined']}\n"
                f"ℹ️ Already: {s['already']}\n"
                f"❌ Failed: {s['failed']}\n"
                f"⏭️ Skipped: {s['skipped']}"
            )
            logging.info(f"[BULK_JOIN] {final_msg}")
            published, _ = await self._send(final_msg)
            if not published:
                logging.error("[BULK_JOIN] ❌ Failed to send final report to channel")

        except asyncio.CancelledError:
            self._bulk_join_running = False
            logging.info(f"[BULK_JOIN] Cancelled [state={worker_state}]")
        except Exception as e:
            self._bulk_join_running = False
            worker_state = 'FAILED'
            logging.error(
                f"[BULK_JOIN] ❌ FATAL WORKER ERROR\n"
                f"[BULK_JOIN] state=FAILED\n"
                f"[BULK_JOIN] current_link={self._bulk_join_stats.get('current', 'none')}\n"
                f"[BULK_JOIN] error={type(e).__name__}: {e}",
                exc_info=True
            )

    async def _cleanup_worker(self, preview_only: bool = True):
        """عامل التنظيف — يحلل روابط القناة ويحذف غير التعليمية والمكررة.

        Args:
            preview_only: True = معاينة فقط (بدون حذف)، False = حذف فعلي
        """
        mode = "PREVIEW" if preview_only else "DELETE"
        logging.info(f"[CLEANUP] {mode} mode — scanning channel messages")
        self._cleanup_stats = {
            'running': True, 'total': 0, 'educational': 0,
            'non_educational': 0, 'duplicates': 0, 'deleted': 0, 'current': '',
            'worker_state': 'RUNNING'
        }

        # set لتتبع الروابط التعليمية المرئية (لاكتشاف التكرار)
        seen_educational_links = set()
        offset_id = 0
        batch_size = 200
        deleted_count = 0
        worker_state = 'RUNNING'

        try:
            while self._cleanup_stats['running']:
                try:
                    # تحقق من اتصال البوت
                    if not self.bot_client or not self.bot_client.is_connected():
                        logging.error("[CLEANUP] ❌ bot_client not connected — pausing 30s")
                        worker_state = 'FAILED'
                        self._cleanup_stats['worker_state'] = worker_state
                        await asyncio.sleep(30)
                        worker_state = 'RUNNING'
                        self._cleanup_stats['worker_state'] = worker_state
                        continue

                    # اجلب batch من رسائل القناة — استخدم user_client (Bot API لا يدعم get_messages)
                    history_client = self._get_any_user_client()
                    if not history_client:
                        logging.error("[CLEANUP] ❌ No connected user client for get_messages — pausing 30s")
                        worker_state = 'FAILED'
                        self._cleanup_stats['worker_state'] = worker_state
                        await asyncio.sleep(30)
                        worker_state = 'RUNNING'
                        self._cleanup_stats['worker_state'] = worker_state
                        continue
                    messages = await asyncio.wait_for(
                        history_client.get_messages(
                            self.config.channel_id,
                            limit=batch_size,
                            offset_id=offset_id,
                            reverse=True  # الأقدم أولاً
                        ),
                        timeout=60
                    )

                    if not messages:
                        logging.info(f"[CLEANUP] {mode} — No more messages")
                        worker_state = 'COMPLETED'
                        self._cleanup_stats['worker_state'] = worker_state
                        break

                    offset_id = messages[-1].id

                    for msg in messages:
                        self._cleanup_stats['current'] = f"msg_{msg.id}"
                        raw_text = msg.raw_text or ''
                        if not raw_text:
                            continue

                        # استخرج الروابط من الرسالة
                        links = LinkNormalizer.extract_links(raw_text)
                        if not links:
                            continue

                        self._cleanup_stats['total'] += 1

                        for link_info in links:
                            raw_link = link_info['raw']
                            normalized = link_info['normalized']
                            username = link_info.get('username', '')

                            # فحص تعليمي — استخدم username فقط (الرسالة المنشورة منسقة)
                            is_edu, reason = EducationalFilter.is_educational('', username)

                            if not is_edu:
                                # غير تعليمي → احذف
                                self._cleanup_stats['non_educational'] += 1
                                logging.info(f"[CLEANUP] {mode} non-educational msg={msg.id}: {raw_link[:50]} ({reason})")
                                if not preview_only:
                                    # === VERIFY BEFORE DELETE ===
                                    # 1. message exists (we have it)
                                    # 2. contains target link (we extracted it)
                                    # 3. matches cleanup policy (non-educational)
                                    try:
                                        await history_client.delete_messages(
                                            self.config.channel_id, [msg.id])
                                        deleted_count += 1
                                        self._cleanup_stats['deleted'] = deleted_count
                                        logging.info(f"[CLEANUP] ✅ DELETED msg={msg.id} (verified)")
                                        await asyncio.sleep(0.5)  # تجنب FloodWait
                                    except FloodWaitError as e:
                                        logging.warning(f"[CLEANUP] FloodWait {e.seconds}s — pausing")
                                        await asyncio.sleep(e.seconds + 1)
                                    except Exception as e:
                                        logging.error(
                                            f"[CLEANUP] ❌ Delete FAILED msg={msg.id}\n"
                                            f"[CLEANUP] error={type(e).__name__}: {e}"
                                        )
                                break  # لا تفحص روابط أخرى في نفس الرسالة

                            # تعليمي → تحقق من التكرار
                            if normalized in seen_educational_links:
                                # مكرر → احذف
                                self._cleanup_stats['duplicates'] += 1
                                logging.info(f"[CLEANUP] {mode} duplicate msg={msg.id}: {raw_link[:50]}")
                                if not preview_only:
                                    try:
                                        await history_client.delete_messages(
                                            self.config.channel_id, [msg.id])
                                        deleted_count += 1
                                        self._cleanup_stats['deleted'] = deleted_count
                                        logging.info(f"[CLEANUP] ✅ DELETED msg={msg.id} (duplicate, verified)")
                                        await asyncio.sleep(0.5)
                                    except FloodWaitError as e:
                                        logging.warning(f"[CLEANUP] FloodWait {e.seconds}s — pausing")
                                        await asyncio.sleep(e.seconds + 1)
                                    except Exception as e:
                                        logging.error(
                                            f"[CLEANUP] ❌ Delete FAILED msg={msg.id}\n"
                                            f"[CLEANUP] error={type(e).__name__}: {e}"
                                        )
                                break
                            else:
                                # تعليمي جديد → سجل
                                seen_educational_links.add(normalized)
                                self._cleanup_stats['educational'] += 1

                        # تقرير دوري
                        if self._cleanup_stats['total'] % 100 == 0:
                            s = self._cleanup_stats
                            logging.info(f"[CLEANUP] {mode} progress: {s['total']} scanned, "
                                         f"{s['educational']} edu, {s['non_educational']} non-edu, "
                                         f"{s['duplicates']} dup, {s['deleted']} deleted")

                except FloodWaitError as e:
                    logging.warning(f"[CLEANUP] FloodWait {e.seconds}s — pausing")
                    await asyncio.sleep(e.seconds + 1)
                except asyncio.TimeoutError:
                    logging.error("[CLEANUP] ❌ get_messages TIMEOUT (60s) — pausing 30s")
                    worker_state = 'FAILED'
                    self._cleanup_stats['worker_state'] = worker_state
                    await asyncio.sleep(30)
                    worker_state = 'RUNNING'
                    self._cleanup_stats['worker_state'] = worker_state
                except asyncio.CancelledError:
                    worker_state = 'STOPPED'
                    self._cleanup_stats['worker_state'] = worker_state
                    break
                except Exception as e:
                    worker_state = 'FAILED'
                    self._cleanup_stats['worker_state'] = worker_state
                    logging.error(
                        f"[CLEANUP] ❌ WORKER ERROR\n"
                        f"[CLEANUP] state={worker_state}\n"
                        f"[CLEANUP] current={self._cleanup_stats.get('current', 'none')}\n"
                        f"[CLEANUP] error={type(e).__name__}: {e}",
                        exc_info=True
                    )
                    await asyncio.sleep(5)
                    worker_state = 'RUNNING'
                    self._cleanup_stats['worker_state'] = worker_state

            # انتهى
            self._cleanup_stats['running'] = False
            self._cleanup_stats['worker_state'] = worker_state
            s = self._cleanup_stats
            # لا تقل "COMPLETE" إلا إذا انتهت فعلياً بنجاح
            if worker_state == 'COMPLETED':
                action = "🔍 PREVIEW RESULTS" if preview_only else "✅ CLEANUP COMPLETE"
            elif worker_state == 'STOPPED':
                action = "⏹️ CLEANUP STOPPED"
            else:
                action = f"❌ CLEANUP FAILED [{worker_state}]"
            final_msg = (
                f"{action}\n"
                f"════════════════════\n"
                f"📊 Total scanned: {s['total']}\n"
                f"✅ Educational: {s['educational']}\n"
                f"❌ Non-educational: {s['non_educational']}\n"
                f"🔄 Duplicates: {s['duplicates']}\n"
                f"🗑️ Deleted: {s['deleted']}"
            )
            if not preview_only and worker_state == 'COMPLETED':
                final_msg += f"\n\n✨ تم تنظيف القناة!\nالآن أرسل /bulk_join للانضمام للمجموعات التعليمية المتبقية"
            logging.info(f"[CLEANUP] {final_msg}")
            published, _ = await self._send(final_msg)
            if not published:
                logging.error("[CLEANUP] ❌ Failed to send final report to channel")

        except asyncio.CancelledError:
            self._cleanup_stats['running'] = False
            self._cleanup_stats['worker_state'] = 'STOPPED'
            logging.info("[CLEANUP] Cancelled")
        except Exception as e:
            self._cleanup_stats['running'] = False
            self._cleanup_stats['worker_state'] = 'FAILED'
            logging.error(
                f"[CLEANUP] ❌ FATAL WORKER ERROR\n"
                f"[CLEANUP] state=FAILED\n"
                f"[CLEANUP] error={type(e).__name__}: {e}",
                exc_info=True
            )
            await self._send(f"❌ خطأ في التنظيف: {e}")  # noqa: ignore result

    async def _safety_guard(self, phone: str, normalized_link: str, link_data: dict) -> Tuple[bool, str]:
        """Safety Guard — 6 فحوصات صارمة قبل أي Join API call.

        الترتيب (الأرخص أولاً):
        1. FloodWait DB check
        2. Daily Join Budget
        3. Hourly Join Limit (DB-backed)
        4. Group Reputation
        5. Attempt history (هل تمت محاولة الانضمام سابقاً؟)
        6. Last join timestamp for this account

        Returns: (True, '') لو مسموح، (False, reason) لو ممنوع
        """
        # 1. FloodWait DB check
        is_blocked, wait = await self.floodwait_mgr.is_blocked(phone)
        if is_blocked:
            return False, f'floodwait_{wait}s'

        # 2. Daily Join Budget (DB-backed)
        await self.db.reset_daily_joins_if_needed(phone)
        daily_joins = await self.db.get_daily_join_count(phone)
        daily_limit = await self._get_daily_limit(phone)
        if daily_limit == 0:
            return False, 'role_no_join'
        if daily_joins >= daily_limit:
            return False, f'daily_limit_{daily_joins}/{daily_limit}'

        # 3. Hourly Join Limit (DB-backed, survives restart) — محافظ جداً
        # تيليجرام يlimitk لو تجاوزت 5 انضمامات/ساعة على UserBot
        hourly_joins = await self.prod_db.count_operations(phone, 'join', 3600)
        if hourly_joins >= 5:  # 5/hour max (آمن ضد FloodWait)
            return False, f'hourly_limit_{hourly_joins}/5'

        # 4. Group Reputation — تم إزالته (تخفيف)
        # كان يمنع الانضمام للمجموعات الجديدة، الآن مسموح

        # 5. Attempt history — تم تخفيف (فقط لو انضم بالفعل)
        state = await self.prod_db.get_group_state(normalized_link)
        # JOINING = محاولة حالية (هذا الـ Scheduler نفسه)، لا ترفض
        # [REQAUDIT-2] JOINED/ALREADY_MEMBER/PENDING_APPROVAL = we already
        # sent a join for this group (succeeded or pending approval) — block
        # a second attempt to avoid duplicate requests / PeerFlood.
        if state in (GroupState.JOINED, GroupState.ALREADY_MEMBER, GroupState.PENDING_APPROVAL):
            return False, f'already_attempted_{state}'
        # تم إزالة فحص attempt_count >= 3 (تخفيف)

        # 6. Last join timestamp for this account — 120s (2 min) minimum between joins
        # اقرأ من Supabase (المصدر الوحيد) — ليس من SQLite watchers
        w = await self.db._supabase_get_watcher(phone)
        if w and w.get('last_join_timestamp'):
            try:
                last_join_ts = w['last_join_timestamp']
                last_join = datetime.fromisoformat(str(last_join_ts).replace('Z', '+00:00')) if isinstance(last_join_ts, str) else last_join_ts
                elapsed = (datetime.now() - last_join.replace(tzinfo=None)).total_seconds()
                if elapsed < 120:  # 120s cooldown (آمن ضد FloodWait)
                    return False, f'join_cooldown_{int(120-elapsed)}s'
            except Exception:
                pass

        return True, ''

    async def _get_daily_limit(self, phone: str) -> int:
        """يجلب الحد اليومي للانضمام حسب نوع الحساب.

        تحذير: تيليجرام يفرض FloodWait على UserBot لو تجاوز ~30 انضمام/يوم.
        الأرقام السابقة (200/day) كانت تسبب FloodWait 16+ ساعة.

        القيم الحالية محافظة جداً:
        - joiner: 25/day (آمن)
        - backup: 5/day
        - monitor: 0
        """
        w = await self.db._supabase_get_watcher(phone)
        role = w.get('role', 'monitor') if w else 'monitor'

        if role == 'joiner':
            return int(os.getenv('DAILY_JOIN_LIMIT', '45'))  # 45/day
        elif role == 'backup':
            return int(os.getenv('DAILY_BACKUP_LIMIT', '5'))  # 5/day
        else:
            return 0  # monitor: لا انضمام

    async def _calculate_group_reputation(self, normalized_link: str, link_data: dict) -> int:
        """يحسب تقييم المجموعة (0-100) قبل محاولة الانضمام.

        العوامل:
        - عدد مرات ظهور الرابط (أكثر = أعلى)
        - نجاح AI (state = QUEUED)
        - محاولات الانضمام السابقة (فشل كثير = أقل)
        - صلاحية الرابط (telegram public أعلى من private)
        """
        score = 0

        # 1. نوع الرابط: public > private
        if link_data.get('link_type') == 'telegram':
            score += 30  # public username
        elif link_data.get('link_type') == 'telegram_private':
            score += 15  # invite hash (أقل ثقة)
        elif link_data.get('link_type') == 'whatsapp':
            score += 20

        # 2. حالة المجموعة في State Machine
        state = await self.prod_db.get_group_state(normalized_link)
        if state == GroupState.QUEUED:
            score += 25  # AI وافق
        elif state == GroupState.DISCOVERED:
            score += 10  # جديد، لم يُفحص بـ AI بعد

        # 3. محاولات الانضمام السابقة
        conn = await self.db._ensure_conn()
        cursor = await conn.execute(
            "SELECT attempt_count, last_error FROM group_states WHERE normalized_link = ?",
            (normalized_link,))
        row = await cursor.fetchone()
        if row:
            attempt_count = row[0] or 0
            last_error = row[1] or ''
            # محاولات كثيرة فاشلة = تقييم أقل
            if attempt_count > 3:
                score -= 20
            if 'FloodWait' in last_error or 'BANNED' in last_error:
                score -= 30

        # 4. مصدر الرابط: من مجموعة جامعية معروفة
        group_name = link_data.get('group_name', '')
        if any(kw in group_name.lower() for kw in ['university', 'جامعة', 'كلية', 'college', 'edu']):
            score += 15

        return max(0, min(100, score))

    async def _verify_membership(self, client, entity, phone: str, raw_link: str) -> Tuple[bool, Optional[int]]:
        """يتحقق من العضوية بعد Join API call.

        Returns:
            (True, member_count) — العضوية مؤكدة
            (False, None) — العضوية غير مؤكدة أو فشل التحقق
        """
        try:
            from telethon.tl.functions.channels import GetParticipantRequest
            from telethon.errors import UserNotParticipantError, ChannelPrivateError
        except ImportError:
            logging.error("[JOIN] Cannot import GetParticipantRequest — verification impossible")
            return False, None

        try:
            await asyncio.wait_for(
                client(GetParticipantRequest(channel=entity, participant="me")),
                timeout=15
            )
            member_count = None
            if hasattr(entity, 'participants_count'):
                member_count = entity.participants_count
            logging.info(
                f"[JOIN] ✅ MEMBERSHIP VERIFIED\n"
                f"[JOIN] phone={phone}\n"
                f"[JOIN] link={raw_link[:50]}\n"
                f"[JOIN] members={member_count}"
            )
            return True, member_count
        except UserNotParticipantError:
            logging.error(
                f"[JOIN] ❌ Membership verification FAILED\n"
                f"[JOIN] reason=UserNotParticipant\n"
                f"[JOIN] phone={phone}\n"
                f"[JOIN] link={raw_link[:50]}"
            )
            return False, None
        except asyncio.TimeoutError:
            logging.error(
                f"[JOIN] ❌ Membership verification TIMEOUT\n"
                f"[JOIN] phone={phone}\n"
                f"[JOIN] link={raw_link[:50]}"
            )
            return False, None
        except ChannelPrivateError:
            logging.error(
                f"[JOIN] ❌ Membership verification FAILED\n"
                f"[JOIN] reason=ChannelPrivate\n"
                f"[JOIN] phone={phone}\n"
                f"[JOIN] link={raw_link[:50]}"
            )
            return False, None
        except FloodWaitError as e:
            logging.warning(
                f"[JOIN] ❌ Membership verification FloodWait\n"
                f"[JOIN] seconds={e.seconds}\n"
                f"[JOIN] phone={phone}"
            )
            await self.rate_limiter.record_floodwait(phone, e.seconds)
            return False, None
        except Exception as e:
            logging.error(
                f"[JOIN] ❌ Membership verification error\n"
                f"[JOIN] error={type(e).__name__}: {str(e)[:80]}\n"
                f"[JOIN] phone={phone}\n"
                f"[JOIN] link={raw_link[:50]}"
            )
            return False, None

    async def _join_group_safe(self, client, link_data: dict, phone: str):
        """ينضم لمجموعة ويتحقق من العضوية فعلياً.

        Contract:
            - returns (True, "JOINED_VERIFIED", member_count) فقط بعد GetParticipantRequest
            - returns (True, "JOIN_UNVERIFIED", None) لو Join نجح لكن التحقق تعذر
            - returns (False, reason, None) للفشل بأنواعه

        EXPLICIT PROTECTION: Monitor accounts can NEVER join.
        """
        raw_link = link_data.get('raw', link_data.get('raw_link', ''))

        # تحقق من اتصال الـ client
        if not client or not client.is_connected():
            logging.error(f"[JOIN] ❌ client not connected for {phone}")
            return False, "DISCONNECTED", None

        # === EXPLICIT MONITOR PROTECTION ===
        w = await self.db._supabase_get_watcher(phone)
        role = w.get('role', 'monitor') if w else 'monitor'
        if role == 'monitor':
            logging.error(f"[JOIN] BLOCKED: {phone} is monitor — join_permission=false")
            return False, "MONITOR_NO_JOIN", None

        # === Per-account joiner_enabled check ===
        joiner_enabled = w.get('joiner_enabled', 1) if w else 1
        if not joiner_enabled or joiner_enabled == 0:
            logging.info(f"[JOIN] {phone} joiner_enabled=false — skipping")
            return False, "JOINER_DISABLED", None

        # === Emergency pause check ===
        if self._join_paused:
            logging.info(f"[JOIN] Blocked: join paused via /pause_join")
            return False, "PAUSED", None

        # === SIMULATION_MODE ===
        if self.simulation_mode:
            logging.info(f"[SIM] Would join: {raw_link[:50]} via {phone}")
            return False, "SIMULATION", None

        try:
            from telethon.tl.functions.channels import JoinChannelRequest
            from telethon.tl.functions.messages import ImportChatInviteRequest
            from telethon.errors import (
                UserAlreadyParticipantError, FloodWaitError,
                ChannelPrivateError, InviteHashExpiredError,
                PeerFloodError, UserBannedInChannelError,
                ChatWriteForbiddenError,
                # [REQAUDIT-2] raised when a group requires admin approval to
                # join. Telegram's message is literally "You have successfully
                # requested to join this chat or channel" — the request SUCCEEDED
                # and is pending approval. Without this import the outer
                # `except Exception` swallowed it as FAILED → infinite retry.
                InviteRequestSentError,
                # [PR-CHANNELS-TOO-MUCH] raised when the user account has joined
                # too many channels/supergroups (Telegram account-level limit).
                # This is NOT a transient failure: retrying in 30 min won't help
                # — the account itself is saturated. Must disable the account for
                # join (set joiner_enabled=0) and retry only after 24h to give
                # the operator time to leave some channels manually.
                ChannelsTooMuchError,
            )

            link_type = link_data['link_type']

            if link_type == 'telegram_private':
                invite_hash = link_data.get('invite_hash', '')
                if not invite_hash:
                    return False, "INVALID", None

                allowed = await self.rate_limiter.acquire(phone, 'import_invite')
                if not allowed:
                    logging.info(f"[JOIN] {phone} rate limited on import_invite — will retry later")
                    return False, "RATE_LIMITED", None

                logging.info(f"[JOIN] API request started: IMPORT_INVITE phone={phone} link={raw_link[:50]}")
                try:
                    await asyncio.wait_for(client(ImportChatInviteRequest(invite_hash)), timeout=30)
                    await self.metrics.record_api_call(phone)
                    logging.info(f"[JOIN] Telegram accepted IMPORT_INVITE request for {phone}")
                except asyncio.TimeoutError:
                    logging.error(f"[JOIN] ❌ TIMEOUT (30s) phone={phone} op=IMPORT_INVITE link={raw_link[:50]}")
                    return False, "TIMEOUT", None
                except UserAlreadyParticipantError:
                    return False, "ALREADY_MEMBER", None
                except InviteRequestSentError:
                    # [REQAUDIT-2] Telegram raised "You have successfully
                    # requested to join this chat or channel" — the join
                    # request was SENT and is pending admin approval. This
                    # is NOT a failure: the API call succeeded. Mark as
                    # PENDING_APPROVAL so the link is NOT retried as FAILED
                    # (which would waste API calls re-issuing
                    # ImportChatInviteRequest and risk PeerFlood/ban). The
                    # background _pending_approval_recheck_loop will detect
                    # when the admin approves and transition to JOINED.
                    await self.rate_limiter.record_success(phone, 'import_invite')
                    logging.info(
                        f"[JOIN] ✉️ {phone} PENDING_APPROVAL: join request sent, "
                        f"awaiting admin approval link={raw_link[:50]}"
                    )
                    return True, "PENDING_APPROVAL", None
                except FloodWaitError as e:
                    logging.warning(f"[JOIN] ❌ FloodWait phone={phone} seconds={e.seconds} link={raw_link[:50]}")
                    await self.rate_limiter.record_floodwait(phone, e.seconds)
                    return False, "FLOODWAIT", None
                except (ChannelPrivateError, InviteHashExpiredError) as e:
                    logging.warning(f"[JOIN] ❌ {type(e).__name__} phone={phone} link={raw_link[:50]}")
                    return False, "PRIVATE", None
                except (PeerFloodError, UserBannedInChannelError) as e:
                    logging.error(f"[JOIN] ❌ {type(e).__name__} phone={phone} link={raw_link[:50]}")
                    await self.rate_limiter.record_floodwait(phone, 3600)
                    return False, "BANNED", None
                except ChatWriteForbiddenError:
                    logging.error(f"[JOIN] ❌ ChatWriteForbidden phone={phone} link={raw_link[:50]}")
                    return False, "PRIVATE", None
                except ChannelsTooMuchError as e:
                    # [PR-CHANNELS-TOO-MUCH] Account has joined too many
                    # channels/supergroups. This is account-level saturation —
                    # NOT transient. Caller must disable the account for join
                    # and retry only after 24h.
                    logging.error(
                        f"[JOIN] ❌ ChannelsTooMuch phone={phone} link={raw_link[:50]} "
                        f"— account saturated (joined too many channels)"
                    )
                    return False, "ACCOUNT_SATURATED", None

                # === POST-JOIN VERIFICATION ===
                logging.info(f"[JOIN] Verifying membership after IMPORT_INVITE for {phone}...")
                # For private invite, we don't have entity easily
                # Mark as UNVERIFIED — Telegram accepted but membership not confirmed
                logging.warning(
                    f"[JOIN] ⚠️ JOIN_UNVERIFIED — Telegram accepted IMPORT_INVITE but "
                    f"verification not possible for private invite (no entity)\n"
                    f"[JOIN] phone={phone} link={raw_link[:50]}"
                )
                # سجل نجاح العملية في Rate Limiter DB
                await self.rate_limiter.record_success(phone, 'import_invite')
                return True, "JOIN_UNVERIFIED", None

            elif link_type == 'telegram':
                username = link_data.get('username', '')
                if not username:
                    return False, "INVALID", None

                allowed = await self.rate_limiter.acquire(phone, 'join_channel')
                if not allowed:
                    logging.info(f"[JOIN] {phone} rate limited on join_channel — will retry later")
                    return False, "RATE_LIMITED", None

                logging.info(f"[JOIN] API request started: JOIN_CHANNEL phone={phone} link={raw_link[:50]}")
                try:
                    entity = await asyncio.wait_for(client.get_entity(username), timeout=30)

                    # تحقق: هل الكيان قناة (channel) وليس مجموعة؟
                    is_channel = False
                    if hasattr(entity, 'broadcast') and entity.broadcast:
                        is_channel = True
                    elif hasattr(entity, 'megagroup') and entity.megagroup:
                        is_channel = False  # megagroup = مجموعة كبيرة (مناسب)
                    elif hasattr(entity, 'gigagroup') and entity.gigagroup:
                        is_channel = False  # gigagroup = مجموعة عملاقة (مناسب)
                    elif (not hasattr(entity, 'megagroup') and
                          hasattr(entity, 'broadcast') and not entity.broadcast):
                        is_channel = False  # مجموعة عادية

                    if is_channel:
                        logging.info(f"[JOIN] {phone} skipped CHANNEL (broadcast): {raw_link[:50]}")
                        return False, "IS_CHANNEL", None

                    logging.info(f"[JOIN] Telegram accepted JoinChannelRequest for {phone}")
                    await asyncio.wait_for(client(JoinChannelRequest(entity)), timeout=30)
                    await self.metrics.record_api_call(phone)

                    member_count = None
                    if hasattr(entity, 'participants_count'):
                        member_count = entity.participants_count

                    # === POST-JOIN VERIFICATION ===
                    logging.info(f"[JOIN] Verifying membership for {phone} link={raw_link[:50]}...")
                    verified, verified_count = await self._verify_membership(client, entity, phone, raw_link)
                    if verified:
                        # سجل نجاح العملية في Rate Limiter DB
                        await self.rate_limiter.record_success(phone, 'join_channel')
                        return True, "JOINED_VERIFIED", verified_count if verified_count is not None else member_count
                    else:
                        # Join API نجح لكن التحقق فشل — لا نعتبرها نجاح كامل
                        # لكن سجل في Rate Limiter لأن Telegram استقبل الـ API call
                        await self.rate_limiter.record_success(phone, 'join_channel')
                        logging.warning(
                            f"[JOIN] ⚠️ JOIN_UNVERIFIED — Telegram accepted but membership not confirmed\n"
                            f"[JOIN] phone={phone} link={raw_link[:50]}"
                        )
                        return True, "JOIN_UNVERIFIED", member_count

                except asyncio.TimeoutError:
                    logging.error(f"[JOIN] ❌ TIMEOUT (30s) phone={phone} op=JOIN link={raw_link[:50]}")
                    return False, "TIMEOUT", None
                except UserAlreadyParticipantError:
                    return False, "ALREADY_MEMBER", None
                except InviteRequestSentError:
                    # [REQAUDIT-2] Public group/channel with join-approval
                    # enabled — Telegram accepted the request and it's
                    # pending admin approval. NOT a failure (same semantics
                    # as the ImportChatInviteRequest path above). The
                    # background recheck loop will detect approval → JOINED.
                    await self.rate_limiter.record_success(phone, 'join_channel')
                    logging.info(
                        f"[JOIN] ✉️ {phone} PENDING_APPROVAL: join request sent, "
                        f"awaiting admin approval link={raw_link[:50]}"
                    )
                    return True, "PENDING_APPROVAL", None
                except FloodWaitError as e:
                    logging.warning(f"[JOIN] ❌ FloodWait phone={phone} seconds={e.seconds} link={raw_link[:50]}")
                    await self.rate_limiter.record_floodwait(phone, e.seconds)
                    return False, "FLOODWAIT", None
                except (ChannelPrivateError, InviteHashExpiredError) as e:
                    logging.warning(f"[JOIN] ❌ {type(e).__name__} phone={phone} link={raw_link[:50]}")
                    return False, "PRIVATE", None
                except (PeerFloodError, UserBannedInChannelError) as e:
                    logging.error(f"[JOIN] ❌ {type(e).__name__} phone={phone} link={raw_link[:50]}")
                    await self.rate_limiter.record_floodwait(phone, 3600)
                    return False, "BANNED", None
                except ChatWriteForbiddenError:
                    logging.error(f"[JOIN] ❌ ChatWriteForbidden phone={phone} link={raw_link[:50]}")
                    return False, "PRIVATE", None
                except ChannelsTooMuchError as e:
                    # [PR-CHANNELS-TOO-MUCH] Same semantics as ImportChatInvite
                    # path above — account saturated, disable + 24h retry.
                    logging.error(
                        f"[JOIN] ❌ ChannelsTooMuch phone={phone} link={raw_link[:50]} "
                        f"— account saturated (joined too many channels)"
                    )
                    return False, "ACCOUNT_SATURATED", None
                except Exception as e:
                    logging.error(f"[JOIN] ❌ {type(e).__name__}: {str(e)[:80]} phone={phone} link={raw_link[:50]}")
                    return False, "FAILED", None

            else:
                # WhatsApp links — no join needed
                return False, "SKIP", None

        except Exception as e:
            logging.error(f"[JOIN] {phone} unexpected: {e}", exc_info=True)
            return False, "FAILED", None
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
                except Exception: pass

    async def start(self):
        self._running = True
        self.bot_client = self._create_bot_client()
        self._register_handlers()
        # بدء البوت أولاً
        self._bot_task = asyncio.create_task(self._run_bot())
        await asyncio.sleep(3)
        # بدء كل المستخدمين المراقبين
        watchers = await self.db.get_active_watchers()
        monitors = [w for w in watchers if w.get('role', 'monitor') == 'monitor']
        joiners = [w for w in watchers if w.get('role') == 'joiner']
        logging.info(f"Starting {len(watchers)} accounts ({len(monitors)} monitors, {len(joiners)} joiners)")
        for w in watchers:
            role = w.get('role', 'monitor')
            logging.info(f"  → {w['phone']} (role={role})")
            self._user_tasks[w['phone']] = asyncio.create_task(self._run_user_client(w))
        self._keep_alive_task = asyncio.create_task(self._keep_alive())

        # === SYNC MONITORED CHATS: فحص كل مجموعات المراقبين ===
        # يشتغل عند بدء التشغيل + كل ساعة لضمان تسجيل كل المجموعات
        await asyncio.sleep(15)  # انتظر اتصال كل الحسابات
        asyncio.create_task(self._sync_monitored_chats())
        # كرر كل ساعة
        asyncio.create_task(self._periodic_sync())

        # === STARTUP HISTORY SCAN [Req-2] ===
        # Discover groups from RECENT message history of every connected monitor
        # account — not just wait for new messages containing a link. Without
        # this, a group mentioned in an older message (before the watcher connected,
        # or during a bot downtime) is missed forever. _run_startup_scan was
        # previously dead code (defined, never called); this wires it in.
        # Gated on STARTUP_SCAN_DAYS env so the operator opts in (default: off).
        if self.config.startup_scan_days is not None:
            _monitors_for_scan = [w for w in watchers if w.get('role', 'monitor') == 'monitor']
            logging.info(
                f"[STARTUP-SCAN] scheduling history scan for {len(_monitors_for_scan)} "
                f"monitors ({self.config.startup_scan_days} days each)"
            )
            for w in _monitors_for_scan:
                if w['phone'] not in self._startup_scan_done:
                    self._startup_scan_done.add(w['phone'])
                    asyncio.create_task(self._run_startup_scan(w))

        # === SOURCE REGISTRY + POLLING SCHEDULER + MESSAGE CLAIM ===
        # 1. أنشئ MessageClaim (atomic dedup مع claim_token + lease)
        self.message_claim = MessageClaim(self.prod_db)
        # 2. أنشئ SourceRegistry + تحميل سريع من DB (موافق للـ startup)
        self.source_registry = SourceRegistry(self.prod_db, watchers)
        await self.source_registry.load_from_db()  # < 1 ثانية — لا يوقف startup
        # 3. ابدأ discovery في الخلفية (يحدّث reader_phones تدريجياً)
        self._registry_task = asyncio.create_task(
            self.source_registry.discover_all_sources_background(self.user_clients)
        )
        # 4. ابدأ PollingScheduler (يستخدم المصادر المحملة من DB)
        self.polling_scheduler = PollingScheduler(
            self.source_registry, self.prod_db, self.rate_limiter,
            self.floodwait_mgr, self.message_claim, self
        )
        self._polling_scheduler_task = asyncio.create_task(self.polling_scheduler.run())
        logging.info("🔄 PollingScheduler + SourceRegistry + MessageClaim started")
        # 5. ابدأ cleanup task للـ processed_messages (كل ساعة)
        self._claim_cleanup_task = asyncio.create_task(self._cleanup_processed_messages_loop())

        # === STARTUP RECOVERY: إعادة الروابط العالقة ===
        try:
            conn = await self.db._ensure_conn()
            # روابط في group_states بحالة JOINING — أعد لـ QUEUED
            cursor = await conn.execute(
                "UPDATE group_states SET state = 'QUEUED' WHERE state = 'JOINING'")
            stuck_joining = cursor.rowcount
            # روابط في link_queue بحالة PROCESSING — أعد لـ QUEUED
            cursor = await conn.execute(
                "UPDATE link_queue SET status = 'QUEUED' WHERE status = 'PROCESSING'")
            stuck_processing = cursor.rowcount
            await conn.commit()
            if stuck_joining or stuck_processing:
                logging.warning(
                    f"[STARTUP RECOVERY] Reset {stuck_joining} JOINING + {stuck_processing} PROCESSING → QUEUED"
                )
        except Exception as e:
            logging.error(f"[STARTUP RECOVERY] Error: {e}")

        # === AUTO-START JOIN WORKER ===
        # اقرأ join_paused من DB — لو paused، Worker يبدأ لكن يبقى PAUSED
        db_paused = await self.prod_db.get_setting('join_paused', 'false')
        self._join_paused = (db_paused == 'true')
        if self._join_paused:
            # تحقق: هل في FloodWait حقيقي؟ لو لا، auto-resume
            blocked = await self.floodwait_mgr.get_blocked_accounts()
            if not blocked:
                logging.info("▶️ Auto-resume: join_paused was true but no FloodWait — resuming")
                self._join_paused = False
                await self.prod_db.set_setting('join_paused', 'false')
            else:
                logging.info("🔒 Join PAUSED — accounts in FloodWait, Worker will wait")

        # ابدأ Join Worker تلقائياً ( Independent Task)
        if not hasattr(self, '_joiner_task') or self._joiner_task is None or self._joiner_task.done():
            self._joiner_task = asyncio.create_task(self._joiner_worker())
            if self._join_paused:
                logging.info("🚀 Join Worker started (PAUSED — waiting for /resume_join)")
            else:
                logging.info("🚀 Join Worker started (AUTO — will process QUEUED links)")
        else:
            logging.info("🚀 Join Worker already running — skip duplicate")

        # ابدأ Priority Scorer — يجلب member_count للروابط بدون score
        # هذا يخلّي المجدول يختار المجموعات الكبيرة (10K+) أولاً
        if not hasattr(self, '_priority_scorer_task') or self._priority_scorer_task is None or self._priority_scorer_task.done():
            self._priority_scorer_task = asyncio.create_task(self._priority_scorer())
            logging.info("📊 Priority Scorer started (will fetch member_count for queue links)")
        else:
            logging.info("📊 Priority Scorer already running — skip duplicate")

        # ابدأ Message Cache Cleanup — ينظف الرسائل القديمة من cache كل 30 ثانية
        # هذا يمنع تضخم الذاكرة بسبب الرسائل التي ما تحوي روابط
        if not hasattr(self, '_msg_cache_cleanup_task') or self._msg_cache_cleanup_task is None or self._msg_cache_cleanup_task.done():
            self._msg_cache_cleanup_task = asyncio.create_task(self._msg_cache_cleanup())
            logging.info("🧹 Message Cache Cleanup started (anti-delete protection active)")
        else:
            logging.info("🧹 Message Cache Cleanup already running — skip duplicate")

        # === JOURNAL RECOVERY — استرجاع رسائل انهار النظام قبل معالجتها ===
        # [PERSISTENCE Option C] First restore at-risk journal rows from
        # Supabase snapshot (an ephemeral-disk restart wipes the local SQLite,
        # so this is what keeps fast-deleted messages rescuable across
        # Render free-tier restarts WITHOUT buying a persistent disk).
        try:
            restored_n = await self._restore_journal_from_supabase()
            if restored_n:
                logging.info(f"📓 [JOURNAL-SNAPSHOT] pre-recovery: restored {restored_n} row(s) from Supabase")
        except Exception as _restore_e:
            logging.warning(f"📓 [JOURNAL-SNAPSHOT] restore at startup failed: {_restore_e}")
        if self.config.journal_recovery_enabled:
            if not hasattr(self, '_journal_recovery_task') or self._journal_recovery_task is None or self._journal_recovery_task.done():
                self._journal_recovery_task = asyncio.create_task(self._journal_recovery())
                logging.info("📓 Journal Recovery started (crash-safe message rescue)")

        # === [PERSISTENCE Option C] JOURNAL SNAPSHOT LOOP (30s) ===
        # Mirrors at-risk journal rows to Supabase message_journal_snapshot.
        # Survives ephemeral-disk restart; supervisor will resurrect on death.
        if not hasattr(self, '_journal_snapshot_task') or self._journal_snapshot_task is None or self._journal_snapshot_task.done():
            self._journal_snapshot_task = asyncio.create_task(self._journal_snapshot_loop())
            logging.info("📓 Journal Snapshot started (30s cycle — Supabase durability mirror)")

        # === [N07] AI DRAINER (30s) — processes ai_pending backlog ===
        # Self-disables when AI_DRAIN_ENABLED != 'true' (default), so it's
        # always safe to start. Operator flips AI_DRAIN_ENABLED=true to
        # start draining the 26K backlog; supervisor resurrects on death.
        if not hasattr(self, '_ai_drainer_task') or self._ai_drainer_task is None or self._ai_drainer_task.done():
            self._ai_drainer_task = asyncio.create_task(self._ai_drainer_worker())
            drain_on = os.getenv('AI_DRAIN_ENABLED', 'false').lower() in ('true', '1', 'yes')
            if drain_on:
                logging.info("🤖 AI Drainer started (AI_DRAIN_ENABLED=true — processing ai_pending backlog)")
            else:
                logging.info("🤖 AI Drainer started (idle — AI_DRAIN_ENABLED unset/false)")

        # === [B07] SUPERVISOR — recreates dead critical background tasks (60s) ===
        if not hasattr(self, '_supervisor_task') or self._supervisor_task is None or self._supervisor_task.done():
            self._supervisor_task = asyncio.create_task(self._supervisor_loop())
            logging.info("🛡️ Supervisor started (60s cycle — watches polling/journal_recovery/joiner)")

        # === [L03] POLLING-WATCHDOG — dedicated 30s scheduler restart ===
        if not hasattr(self, '_polling_watchdog_task') or self._polling_watchdog_task is None or self._polling_watchdog_task.done():
            self._polling_watchdog_task = asyncio.create_task(self._polling_watchdog_loop())
            logging.info("🐕 Polling Watchdog started (30s cycle — scheduler-only)")

        # === [REQAUDIT-2] PENDING-APPROVAL RECHECK — self-healing 30-min cycle ===
        # Transitions PENDING_APPROVAL → JOINED when the group admin approves
        # the join request. Without this, groups whose join-request was sent
        # (InviteRequestSentError) would stay PENDING forever. Supervisor
        # resurrects on death.
        if not hasattr(self, '_pending_approval_recheck_task') or self._pending_approval_recheck_task is None or self._pending_approval_recheck_task.done():
            self._pending_approval_recheck_task = asyncio.create_task(self._pending_approval_recheck_loop())
            logging.info("✉️ Pending-Approval Recheck started (30-min cycle — self-heal PENDING_APPROVAL → JOINED)")

        # [REQAUDIT-3] Joiner Fleet Health monitor — 60s cycle. Computes
        # the live fleet-health snapshot (connected/floodwait/disconnected/
        # safety-guard counts) for /ready + /api/joined_groups + the
        # _joiner_worker backoff gate, and pushes a Telegram alert to
        # OWNER_ID when ALL joiners are unavailable for >5 min. Supervisor
        # resurrects on death.
        if not hasattr(self, '_joiner_fleet_health_task') or self._joiner_fleet_health_task is None or self._joiner_fleet_health_task.done():
            self._joiner_fleet_health_task = asyncio.create_task(self._joiner_fleet_health_loop())
            logging.info("🛡️ Joiner Fleet Health monitor started (60s cycle — alerts on full-fleet outage)")

        # [QUIET-DIGEST] Quiet-source watch — 30-min cycle. Aggregates the
        # last 30 days of per-group link activity (shared helper with
        # /api/top_groups) and pushes a Telegram alert to OWNER_ID when an
        # important source (≥20 links/30d) goes quiet for 2+ days: ONE
        # consolidated digest on the first cycle (restart-safe), then
        # individual alerts for new quiet events + 7-day re-alerts +
        # recovery notes. Best-effort (OWNER_ID unset → log-only).
        if not hasattr(self, '_quiet_source_task') or self._quiet_source_task is None or self._quiet_source_task.done():
            self._quiet_source_task = asyncio.create_task(self._quiet_source_watch_loop())
            logging.info("📴 Quiet-source watch started (30-min cycle — alerts OWNER_ID when a top source goes quiet)")

        # === LEGACY POLLING WORKER — DISABLED ===
        # The legacy _active_polling_worker is superseded by PollingScheduler
        # (which covers ALL sources, not just Top-200, and uses fair scheduling).
        # The function itself is kept for backward compatibility, but we no longer
        # start it. Both workers running together would cause duplicate API calls
        # and duplicate MessageClaim attempts on the same chats.
        # To re-enable: uncomment the block below.
        # if not hasattr(self, '_active_polling_task') or self._active_polling_task is None or self._active_polling_task.done():
        #     self._active_polling_task = asyncio.create_task(self._active_polling_worker())
        #     logging.info("🔄 Legacy Active Polling Worker started (top 200 groups)")
        # else:
        #     logging.info("🔄 Legacy Active Polling Worker already running — skip duplicate")
        logging.info("⏸️ Legacy Active Polling Worker DISABLED — PollingScheduler handles polling (covers all sources)")

        # NOTE: Chat Classifier معطل — يستهلك AI بدون فائدة
        # الفلتر الحالي يعتمد على BLACKLIST فقط (كافي)

    async def stop(self):
        """إيقاف نظيف لمنع تسريب الذاكرة

        Uses asyncio.wait_for with a 10s timeout per task to ensure
        shutdown completes even if a task is stuck in a network call
        or has a buggy CancelledError handler.
        """
        self._running = False
        self.stop_scan()

        # إغلاق جلسة AI Analyzer (يمنع Unclosed client session)
        if hasattr(self, 'ai_analyzer') and self.ai_analyzer:
            try:
                await asyncio.wait_for(self.ai_analyzer.close(), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass

        # إغلاق البوت
        if self.bot_client and self.bot_client.is_connected():
            try: await self.bot_client.disconnect()
            except Exception: pass

        # إغلاق حسابات المراقبة
        for c in self.user_clients.values():
            if c and c.is_connected():
                try: await c.disconnect()
                except Exception: pass

        # إلغاء المهام مع timeout لمنع التعليق
        scorer_task = getattr(self, '_priority_scorer_task', None)
        cache_cleanup_task = getattr(self, '_msg_cache_cleanup_task', None)
        polling_task = getattr(self, '_active_polling_task', None)
        registry_task = getattr(self, '_registry_task', None)
        polling_scheduler_task = getattr(self, '_polling_scheduler_task', None)
        claim_cleanup_task = getattr(self, '_claim_cleanup_task', None)
        journal_recovery_task = getattr(self, '_journal_recovery_task', None)
        # [B07]/[L03] supervisor + polling-watchdog tasks
        supervisor_task = getattr(self, '_supervisor_task', None)
        polling_watchdog_task = getattr(self, '_polling_watchdog_task', None)
        # [PERSISTENCE Option C] journal snapshot + [N07] ai drainer tasks
        journal_snapshot_task = getattr(self, '_journal_snapshot_task', None)
        ai_drainer_task = getattr(self, '_ai_drainer_task', None)
        # [REQAUDIT-2] pending-approval recheck task
        pending_recheck_task = getattr(self, '_pending_approval_recheck_task', None)
        # [REQAUDIT-3] joiner fleet health task
        fleet_health_task = getattr(self, '_joiner_fleet_health_task', None)
        # [QUIET-DIGEST] quiet-source watch task
        quiet_source_task = getattr(self, '_quiet_source_task', None)
        tasks = [self._bot_task, self._keep_alive_task, self._joiner_task,
                 scorer_task, cache_cleanup_task, polling_task,
                 registry_task, polling_scheduler_task, claim_cleanup_task,
                 journal_recovery_task, supervisor_task, polling_watchdog_task,
                 journal_snapshot_task, ai_drainer_task, pending_recheck_task,
                 fleet_health_task, quiet_source_task,
                 ] + list(self._user_tasks.values()) + self._current_scan_tasks
        for t in tasks:
            if t and not t.done():
                t.cancel()
                try:
                    await asyncio.wait_for(t, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass


# -------------------------------------------------------------------
# HTTP Server (for Render)
# -------------------------------------------------------------------


async def health_handler(request):
    """Liveness probe — returns 200 if the process is alive."""
    return web.Response(text="✅ Bot is running", status=200)


async def api_joined_groups_handler(request):
    """API endpoint: returns all joined groups from SQLite group_states.

    This is the REAL data source for the dashboard — not Supabase target_groups.
    The bot stores join results in SQLite group_states table.
    """
    monitor = request.app.get("monitor")
    db = request.app.get("db")
    if not monitor or not db:
        return web.json_response({"error": "not ready"}, status=503)

    try:
        conn = await db._ensure_conn()

        # Joined groups
        cursor = await conn.execute(
            "SELECT normalized_link, raw_link, joined_by, member_count, last_seen, last_error, state "
            "FROM group_states WHERE state IN ('JOINED', 'ALREADY_MEMBER') ORDER BY last_seen DESC LIMIT 100")
        joined_rows = await cursor.fetchall()

        # [Security / Req-1] Mask joiner phones when the dashboard is open.
        _show_full = _api_should_show_full_pii()
        groups = []
        for r in joined_rows:
            groups.append({
                "id": len(groups) + 1,
                "group_title": r[0] or '',
                "group_link": r[1] or '',
                "status": r[6] or 'JOINED',
                "joined_by_phone": (r[2] or '') if _show_full else _redact_phone(r[2]),
                "join_date": r[4] or '',
                "member_count": r[3] or 0,
            })

        # Stats
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM group_states WHERE state = 'JOINED'")
        total_joined = (await cursor.fetchone())[0]

        cursor = await conn.execute(
            "SELECT COUNT(*) FROM link_queue WHERE status = 'QUEUED'")
        pending = (await cursor.fetchone())[0]

        # [REQAUDIT-2] Pending-approval count (requests sent, awaiting admin)
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM group_states WHERE state = 'PENDING_APPROVAL'")
        total_pending_approval = (await cursor.fetchone())[0]

        # Active joiners from Supabase
        try:
            joiners = await db.get_watchers_by_role("joiner")
            active_joiners = len(joiners)
        except Exception:
            active_joiners = 0

        # [REQAUDIT-3] Live fleet health from the monitor's snapshot —
        # surfaces connected vs. floodwait vs. disconnected vs.
        # safety-guard-blocked joiner counts so the dashboard can show
        # "joins are flowing" vs. "fleet is down" at a glance.
        fleet = {}
        if monitor:
            fh = getattr(monitor, '_fleet_health', None) or {}
            fleet = {
                "connected_joiners": fh.get('connected_joiners', 0),
                "floodwait_joiners_count": len(fh.get('floodwait_joiners', [])),
                "disconnected_joiners_count": len(fh.get('disconnected_joiners', [])),
                "safety_guard_blocked_joiners": fh.get('safety_guard_blocked_joiners', 0),
                "all_joiners_unavailable": (fh.get('connected_joiners', 0) == 0),
            }

        return web.json_response({
            "joined_groups": groups,
            "stats": {
                "total_joined": total_joined,
                "pending_groups": pending,
                "active_joiners": active_joiners,
                "pending_approval": total_pending_approval,
                "fleet_health": fleet,
            }
        }, status=200, headers={"Access-Control-Allow-Origin": "*"})

    except Exception as e:
        logging.error(f"[API] joined_groups error: {e}")
        return web.json_response({"error": str(e)}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})


async def api_pending_approvals_handler(request):
    """[REQAUDIT-2] API endpoint: returns all groups in state PENDING_APPROVAL
    (join request sent, awaiting admin approval). Includes the joiner phone
    (masked when dashboard is open) and timestamp. The dashboard surfaces
    this so the operator can see which groups are pending — and trust the
    self-healing recheck loop to flip them to JOINED on approval.
    """
    monitor = request.app.get("monitor")
    db = request.app.get("db")
    if not monitor or not db:
        return web.json_response({"error": "not ready"}, status=503)
    try:
        conn = await db._ensure_conn()
        cursor = await conn.execute(
            "SELECT normalized_link, raw_link, joined_by, last_seen, last_error "
            "FROM group_states WHERE state = ? ORDER BY last_seen DESC LIMIT 200",
            ('PENDING_APPROVAL',))
        rows = await cursor.fetchall()
        _show_full = _api_should_show_full_pii()
        groups = []
        for r in rows:
            groups.append({
                "id": len(groups) + 1,
                "normalized_link": r[0] or '',
                "group_link": r[1] or '',
                "status": "PENDING_APPROVAL",
                "joined_by_phone": (r[2] or '') if _show_full else _redact_phone(r[2]),
                "since": r[3] or '',
                "last_error": r[4] or '',
            })
        return web.json_response({
            "pending_approvals": groups,
            "stats": {
                "total_pending_approval": len(groups),
                "recheck_interval_seconds": int(os.getenv("PENDING_RECHECK_INTERVAL_S", "1800")),
                "self_healing": True,
            }
        }, status=200, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logging.error(f"[API] pending_approvals error: {e}")
        return web.json_response({"error": str(e)}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})


async def api_joiners_status_handler(request):
    """API endpoint: يعرض حالة كل الفدائيين والمجموعات المنضم لها.

    Returns:
        - joiners: قائمة الفدائيين مع stats (daily_joins, last_join, connected)
        - joined_groups: قائمة المجموعات المنضم لها (مع joined_by)
        - summary: إحصائيات إجمالية
    """
    monitor = request.app.get("monitor")
    db = request.app.get("db")
    if not monitor or not db:
        return web.json_response({"error": "not ready"}, status=503,
                                 headers={"Access-Control-Allow-Origin": "*"})

    try:
        # اجلب كل الفدائيين من Supabase
        joiners = await db.get_watchers_by_role("joiner")
        # [Security / Req-1] Mask phones in the open-dashboard mode so a public
        # scrape of /api/joiners_status cannot enumerate every joiner phone.
        _show_full = _api_should_show_full_pii()
        joiners_data = []
        for j in joiners:
            jphone = j['phone']
            w = await db._supabase_get_watcher(jphone) if hasattr(db, '_supabase_get_watcher') else j
            daily_joins = await db.get_daily_join_count(jphone) if hasattr(db, 'get_daily_join_count') else 0
            daily_limit = await monitor._get_daily_limit(jphone) if hasattr(monitor, '_get_daily_limit') else 25
            client = monitor.user_clients.get(jphone)
            is_connected = bool(client and client.is_connected())
            joiners_data.append({
                'phone': jphone if _show_full else _redact_phone(jphone),
                'display_name': w.get('display_name', '') if w else '',
                'connected': is_connected,
                'daily_joins': daily_joins,
                'daily_limit': daily_limit,
                'last_join_timestamp': str(w.get('last_join_timestamp', '')) if w else '',
                'joiner_enabled': w.get('joiner_enabled', 1) if w else 1,
            })

        # اجلب كل المجموعات المنضم لها من group_states
        conn = await db._ensure_conn()
        cursor = await conn.execute(
            "SELECT normalized_link, raw_link, group_title, state, joined_by, member_count, last_seen "
            "FROM group_states WHERE state IN ('JOINED', 'ALREADY_MEMBER') "
            "ORDER BY last_seen DESC LIMIT 200"
        )
        joined_rows = await cursor.fetchall()
        joined_groups = []
        for r in joined_rows:
            joined_groups.append({
                'group_link': r[1] or '',
                'group_title': r[2] or 'غير معروف',
                'state': r[3] or 'JOINED',
                'joined_by_phone': (r[4] or '') if _show_full else _redact_phone(r[4]),
                'member_count': r[5] or 0,
                'join_date': r[6] or '',
            })

        # إحصائيات
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM group_states WHERE state = 'JOINED'"
        )
        total_joined = (await cursor.fetchone())[0]
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM group_states WHERE state = 'ALREADY_MEMBER'"
        )
        total_already = (await cursor.fetchone())[0]
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM group_states WHERE state = 'BANNED'"
        )
        total_banned = (await cursor.fetchone())[0]

        # اجلب المجموعات الممنوعة مع روابطها وأسباب المنع
        cursor = await conn.execute(
            "SELECT normalized_link, raw_link, group_title, state, joined_by, member_count, last_seen, last_error "
            "FROM group_states WHERE state = 'BANNED' "
            "ORDER BY last_seen DESC LIMIT 200"
        )
        banned_rows = await cursor.fetchall()
        banned_groups = []
        for r in banned_rows:
            banned_groups.append({
                'group_link': r[1] or '',
                'group_title': r[2] or 'غير معروف',
                'state': r[3] or 'BANNED',
                'joined_by_phone': (r[4] or '') if _show_full else _redact_phone(r[4]),
                'member_count': r[5] or 0,
                'join_date': r[6] or '',
                'last_error': r[7] or '',
            })

        return web.json_response({
            'joiners': joiners_data,
            'joined_groups': joined_groups,
            'banned_groups': banned_groups,
            'summary': {
                'total_joiners': len(joiners_data),
                'connected_joiners': sum(1 for j in joiners_data if j['connected']),
                'total_joined_groups': total_joined,
                'total_already_member': total_already,
                'total_banned': total_banned,
            }
        }, status=200, headers={"Access-Control-Allow-Origin": "*"})

    except Exception as e:
        logging.error(f"[API] joiners_status error: {e}")
        return web.json_response({"error": str(e)}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})


async def api_monitored_chats_handler(request):
    """API endpoint: returns all monitored chats with AI classification.

    Returns:
        - chats: list of monitored chats with AI classification
        - summary: total chats, classified, educational, by country
    """
    monitor = request.app.get("monitor")
    db = request.app.get("db")
    if not monitor or not db:
        return web.json_response({"error": "not ready"}, status=503,
                                 headers={"Access-Control-Allow-Origin": "*"})

    try:
        chats = await monitor.prod_db.get_monitored_chats(limit=5000)

        # إحصائيات
        total = len(chats)
        classified = sum(1 for c in chats if c.get('ai_classification') and c['ai_classification'] not in ('', 'unknown', 'error'))
        educational = sum(1 for c in chats if c.get('ai_relevance', 0) >= 50)
        high_relevance = sum(1 for c in chats if c.get('ai_relevance', 0) >= 80)

        # حسب الدولة
        by_country: dict = {}
        for c in chats:
            country = c.get('ai_country', 'أخرى')
            by_country[country] = by_country.get(country, 0) + 1

        # حسب النوع
        by_type: dict = {}
        for c in chats:
            t = c.get('ai_classification', 'unknown')
            by_type[t] = by_type.get(t, 0) + 1

        return web.json_response({
            'chats': chats,
            'summary': {
                'total': total,
                'classified': classified,
                'unclassified': total - classified,
                'educational': educational,
                'high_relevance': high_relevance,
                'by_country': by_country,
                'by_type': by_type,
            }
        }, status=200, headers={"Access-Control-Allow-Origin": "*"})

    except Exception as e:
        logging.error(f"[API] monitored_chats error: {e}")
        return web.json_response({"error": str(e)}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})


async def api_link_source_check_handler(request):
    """API endpoint: يتحقق هل مصدر رابط معيّن من ضمن المجموعات المراقبة.
    
    Query params:
        ?message_link=https://t.me/c/123456/789  → رابط الرسالة الأصلية
        ?chat_id=-1001234567890                  → chat_id مباشر
        ?group_name=S_boot                       → اسم المجموعة المصدر
    
    Returns:
        - is_monitored: bool
        - chat: معلومات المجموعة (لو موجودة)
        - total_monitored: عدد كل المجموعات المراقبة
    """
    monitor = request.app.get("monitor")
    db = request.app.get("db")
    if not monitor or not db:
        return web.json_response({"error": "not ready"}, status=503,
                                 headers={"Access-Control-Allow-Origin": "*"})

    try:
        message_link = request.query.get("message_link", "").strip()
        chat_id_str = request.query.get("chat_id", "").strip()
        group_name_query = request.query.get("group_name", "").strip()

        # استخراج chat_id من message_link (t.me/c/X/Y → -100X)
        chat_id = None
        if chat_id_str:
            try:
                chat_id = int(chat_id_str)
            except ValueError:
                pass
        if not chat_id and message_link:
            # pattern: t.me/c/123456/789 → chat_id = -100123456
            import re as _re
            m = _re.search(r'/c/(\d+)', message_link)
            if m:
                chat_id = int(f"-100{m.group(1)}")

        # ابحث عن المجموعة في monitored_chats
        chat_info = None
        if chat_id:
            chats = await monitor.prod_db.get_monitored_chats(limit=5000)
            for c in chats:
                if c.get('chat_id') == chat_id:
                    chat_info = c
                    break
        if not chat_info and group_name_query:
            # ابحث بالاسم لو ما لقينا بالـ id
            chats = await monitor.prod_db.get_monitored_chats(limit=5000)
            for c in chats:
                if c.get('chat_title', '') == group_name_query:
                    chat_info = c
                    break

        total_monitored = len(chats) if 'chats' in locals() else 0
        is_monitored = chat_info is not None

        return web.json_response({
            'is_monitored': is_monitored,
            'chat': chat_info,
            'query': {
                'message_link': message_link,
                'chat_id': chat_id,
                'group_name': group_name_query,
            },
            'total_monitored': total_monitored,
        }, status=200, headers={"Access-Control-Allow-Origin": "*"})

    except Exception as e:
        logging.error(f"[API] link_source_check error: {e}")
        return web.json_response({"error": str(e)}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})


async def api_polling_status_handler(request):
    """API endpoint: يعرض حالة Active Polling Worker.

    [B03] reads due-chats directly from the monitored_chats DB table instead
    of the in-memory `_active_polling_chats` set (which was always empty
    because the legacy _active_polling_worker is DISABLED — superseded by
    PollingScheduler). The old handler reported active_chats_count=0 even
    while 800+ sources were being polled. Now it reports the count of chats
    whose next_poll_at <= now (i.e. due/being polled right now) plus a real
    scheduler_running flag derived from the PollingScheduler task state.

    Returns:
        - polling_enabled: bool
        - polling_interval: int (seconds)
        - active_chats_count: int (due-chats from DB)
        - active_chats: list of {chat_id, chat_title, last_msg_id}
        - scheduler_running: bool
        - cache_size: int (messages currently in cache)
    """
    monitor = request.app.get("monitor")
    db = request.app.get("db")
    if not monitor:
        return web.json_response({"error": "not ready"}, status=503,
                                 headers={"Access-Control-Allow-Origin": "*"})

    try:
        # [B03] scheduler_running: derived from the real PollingScheduler task,
        # not a static True. A dead scheduler must be visible to operators.
        sched_task = getattr(monitor, '_polling_scheduler_task', None)
        scheduler_running = bool(
            sched_task is not None and not sched_task.done()
            and getattr(monitor, 'polling_scheduler', None) is not None)

        active_chats = []
        active_chats_count = 0
        if db:
            try:
                conn = await db._ensure_conn()
                now_iso = datetime.utcnow().isoformat()
                # [Task 6a] Count chats whose next_poll_at is due (<= now) OR NULL.
                # MUST mirror SourceRegistry.select_due_chats's predicate
                # `next_poll_at IS NULL OR next_poll_at <= ?` — otherwise chats
                # freshly added by add_monitored_chat (which now seeds
                # next_poll_at=now()) AND pre-existing rows still carrying a
                # NULL next_poll_at (pre-fix inserts) are invisible to the
                # status endpoint even while the scheduler IS polling them.
                # This is the root cause of the historical active_chats_count=0.
                cur = await conn.execute(
                    "SELECT COUNT(*) FROM monitored_chats "
                    "WHERE (next_poll_at IS NULL OR next_poll_at <= ?)",
                    (now_iso,))
                row = await cur.fetchone()
                active_chats_count = int(row[0]) if row else 0
                # Surface up to 250 due chats for the dashboard list.
                cur = await conn.execute(
                    "SELECT chat_id, chat_title, last_msg_id FROM monitored_chats "
                    "WHERE (next_poll_at IS NULL OR next_poll_at <= ?) "
                    "ORDER BY last_activity DESC LIMIT 250",
                    (now_iso,))
                for r in await cur.fetchall():
                    active_chats.append({
                        'chat_id': r[0],
                        'chat_title': r[1] or '',
                        'last_msg_id': r[2] or 0,
                    })
            except Exception as db_e:
                logging.warning(f"[API] polling_status DB query failed: {db_e}")

        cache_size = len(monitor._msg_cache)

        return web.json_response({
            'polling_enabled': True,
            'polling_interval': monitor._polling_interval,
            'active_chats_count': active_chats_count,
            'active_chats': active_chats,
            'scheduler_running': scheduler_running,
            'cache_size': cache_size,
            'cache_ttl': monitor._msg_cache_ttl,
        }, status=200, headers={"Access-Control-Allow-Origin": "*"})

    except Exception as e:
        logging.error(f"[API] polling_status error: {e}")
        return web.json_response({"error": str(e)}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})


async def api_links_handler(request):
    """API endpoint: returns recent published links from Supabase.

    Supports optional filtering by AI status via query params:
      ?ai_approved=true    → only AI-approved links
      ?ai_approved=false   → only AI-rejected links
      ?ai_is_ad=true       → only flagged as ads
      ?link_type=whatsapp  → filter by link type
    Returns all columns including ai_approved, ai_description, ai_country, ai_is_ad.

    Note: Supabase REST API has a hard limit of 1000 rows per request.
    This handler implements pagination to fetch up to the requested limit
    by making multiple requests of 1000 each.
    """
    db = request.app.get("db")
    if not db:
        return web.json_response({"error": "not ready"}, status=503)

    try:
        total_limit = int(request.query.get("limit", "50"))
        offset = int(request.query.get("offset", "0"))

        if not db.supabase_url or not db.supabase_key:
            return web.json_response({"links": [], "error": "supabase not configured"},
                                     status=200, headers={"Access-Control-Allow-Origin": "*"})

        # Build query with explicit column selection
        columns = (
            "id,link,link_type,message_text,group_name,sender_name,"
            "sender_contact,source_phone,message_link,created_at,"
            "ai_approved,ai_description,ai_country,ai_is_ad"
        )

        ai_approved = request.query.get("ai_approved")  # 'true' | 'false' | None
        ai_is_ad = request.query.get("ai_is_ad")
        link_type = request.query.get("link_type")

        # Build filter parts (without limit/offset — we'll handle pagination manually)
        filter_parts = [f"select={columns}", f"order=created_at.desc"]
        if ai_approved is not None:
            val = "true" if ai_approved.lower() == "true" else "false"
            filter_parts.append(f"ai_approved=eq.{val}")
        if ai_is_ad is not None:
            val = "true" if ai_is_ad.lower() == "true" else "false"
            filter_parts.append(f"ai_is_ad=eq.{val}")
        if link_type in ("whatsapp", "telegram", "other"):
            filter_parts.append(f"link_type=eq.{link_type}")

        session = await db._get_supabase_session()

        # === PAGINATION: fetch in batches ===
        # Supabase REST API: استخدم limit + offset بدل Range header
        # Range header يسبب خطأ 416 لو الـ offset يتجاوز عدد الصفوف
        all_links = []
        current_offset = offset
        batch_size = 1000  # Supabase max per request
        remaining = total_limit

        while remaining > 0:
            batch_limit = min(batch_size, remaining)
            # استخدم offset parameter بدل Range header
            batch_url = f"{db.supabase_url}/rest/v1/links?" + "&".join(filter_parts) + f"&limit={batch_limit}&offset={current_offset}"

            try:
                async with session.get(batch_url) as resp:
                    if resp.status == 200:
                        batch_data = await resp.json()
                        if not batch_data or len(batch_data) == 0:
                            break  # No more data
                        all_links.extend(batch_data)
                        # If we got fewer than requested, we've reached the end
                        if len(batch_data) < batch_limit:
                            break
                        current_offset += len(batch_data)
                        remaining -= len(batch_data)
                    elif resp.status == 416:
                        # Range not satisfiable — no more data
                        break
                    else:
                        logging.error(f"[API] /api/links batch fetch failed: {resp.status}")
                        break
            except Exception as e:
                logging.error(f"[API] /api/links batch error: {e}")
                break

        # [Security / PII] /api/links used to return source_phone (the
        # WATCHER account phone) and sender_contact (phones posted inside
        # messages) UNMASKED — inconsistent with the trusted-origin posture
        # where every other endpoint masks PII via _api_should_show_full_pii().
        # The dashboard never displays source_phone, so redacting it costs
        # nothing; sender_contact phones get masked while @usernames stay.
        _show_full = _api_should_show_full_pii()
        if not _show_full:
            for row in all_links:
                if row.get("source_phone"):
                    row["source_phone"] = _redact_phone(row.get("source_phone"))
                if row.get("sender_contact"):
                    row["sender_contact"] = _redact_sender_contact(
                        row.get("sender_contact"))

        return web.json_response({"links": all_links, "count": len(all_links)},
                                status=200, headers={"Access-Control-Allow-Origin": "*"})

    except Exception as e:
        logging.error(f"[API] /api/links error: {e}")
        return web.json_response({"links": [], "error": str(e)},
                                status=200, headers={"Access-Control-Allow-Origin": "*"})


# [TREND-VIEW] /api/links_daily — daily link-capture aggregation for the
# dashboard trend chart. Fetches ONLY (created_at, link_type) columns for
# the requested window from Supabase (paginated in 1000-row batches), then
# aggregates by calendar day (UTC) in Python — PostgREST has no GROUP BY.
# Result is cached in-process for 60s: the dashboard polls every 60s and a
# 14-day window can be ~28K rows (~1.6MB) — without the cache this would
# hammer Supabase on every dashboard refresh.
_LINKS_DAILY_CACHE = {"key": None, "payload": None, "ts": 0.0}


def _calendar_window_bounds(days: int):
    """[WINDOW-ALIGN] Return (since_iso, window_start_iso, today_iso) for the
    last ``days`` CALENDAR days ending today (UTC).

    The fetch window is aligned to start-of-day so it matches the daily
    series keys exactly: a plain ``now - N days`` timestamp would also pull
    a partial first calendar day whose rows land in counters (or hourly
    buckets) but outside the series — making totals disagree across
    endpoints for the same ?days value (see group_detail's consistency
    guarantee, commit 0802340).
    """
    today_d = datetime.utcnow().date()
    window_start = today_d - timedelta(days=days - 1)
    since_iso = datetime.combine(
        window_start, datetime.min.time()
    ).strftime("%Y-%m-%dT%H:%M:%S")
    return since_iso, window_start.isoformat(), today_d.isoformat()


async def api_links_daily_handler(request):
    """API endpoint: daily link counts for the dashboard trend chart.

    Query params:
      ?days=N   → window size in days (default 14, clamped 1..30)

    Returns:
      {
        "days": 14,
        "generated_at": "<ISO>",
        "daily": [
          {"date": "2026-08-28", "total": 3412,
           "whatsapp": 120, "telegram": 3292},
          ...
        ],
        "totals": {"total": N, "whatsapp": N, "telegram": N,
                   "best_day": {"date": "...", "count": N}, "avg_per_day": N}
      }
    """
    db = request.app.get("db")
    if not db:
        return web.json_response({"error": "not ready"}, status=503)

    try:
        days = max(1, min(30, int(request.query.get("days", "14"))))
        # Cache key includes the window so different windows don't collide.
        cache_key = f"d{days}"
        now_ts = time.time()
        cached = _LINKS_DAILY_CACHE
        if (cached["key"] == cache_key and cached["payload"] is not None
                and (now_ts - cached["ts"]) < 60.0):
            return web.json_response(cached["payload"], status=200,
                                     headers={"Access-Control-Allow-Origin": "*"})

        daily: dict = {}  # "YYYY-MM-DD" → {"whatsapp": n, "telegram": n, "other": n}
        # [HEATMAP-VIEW] hour-of-day buckets (0..23) over the same window —
        # answers "WHEN do links get posted?" without a second Supabase scan.
        hourly: dict = {}  # int hour → {"whatsapp": n, "telegram": n, "other": n}

        if db.supabase_url and db.supabase_key:
            try:
                session = await db._get_supabase_session()
                # [WINDOW-ALIGN] fetch from start-of-day(today-(N-1)) so the
                # fetched rows match the calendar series window exactly.
                since_iso, window_start_iso, today_iso = \
                    _calendar_window_bounds(days)
                offset = 0
                batch_size = 1000
                fetched = 0
                while True:
                    url = (
                        f"{db.supabase_url}/rest/v1/links"
                        f"?select=created_at,link_type"
                        f"&created_at=gte.{since_iso}"
                        f"&order=created_at.asc"
                        f"&limit={batch_size}&offset={offset}"
                    )
                    async with session.get(url) as r:
                        if r.status == 200:
                            rows = await r.json()
                        elif r.status == 416:
                            break
                        else:
                            logging.error(
                                f"[API] /api/links_daily batch failed: {r.status}")
                            break
                    if not rows:
                        break
                    for row in rows:
                        created = str(row.get("created_at") or "")
                        # created_at may be "2026-08-28T05:40:00.123456+00:00"
                        day_key = created[:10]
                        if len(day_key) != 10 or not day_key.startswith("20"):
                            continue
                        # [WINDOW-ALIGN] only count rows inside the calendar
                        # series window — daily series, totals AND hourly
                        # buckets then agree by construction.
                        if day_key < window_start_iso or day_key > today_iso:
                            continue
                        bucket = daily.setdefault(
                            day_key, {"whatsapp": 0, "telegram": 0, "other": 0})
                        lt = row.get("link_type") or "other"
                        if lt in ("whatsapp", "telegram"):
                            bucket[lt] += 1
                        else:
                            bucket["other"] += 1
                        # [HEATMAP-VIEW] hour bucket — "...T05:40:00..." → 05.
                        # Rows with a date but no time component are skipped
                        # here (they still count toward the daily series).
                        hh = created[11:13]
                        if hh.isdigit():
                            h = int(hh)
                            if 0 <= h <= 23:
                                hb = hourly.setdefault(
                                    h, {"whatsapp": 0, "telegram": 0, "other": 0})
                                if lt in ("whatsapp", "telegram"):
                                    hb[lt] += 1
                                else:
                                    hb["other"] += 1
                    fetched += len(rows)
                    if len(rows) < batch_size:
                        break
                    offset += batch_size
                logging.info(
                    f"[API] /api/links_daily aggregated {fetched} rows over "
                    f"{days}d → {len(daily)} days")
            except Exception as e:
                logging.warning(f"[API] /api/links_daily supabase fetch failed: {e}")

        # Build a continuous date series (fill gaps with zeros) oldest→newest.
        today = datetime.utcnow().date()
        series = []
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            key = d.isoformat()
            b = daily.get(key, {"whatsapp": 0, "telegram": 0, "other": 0})
            series.append({
                "date": key,
                "whatsapp": b["whatsapp"],
                "telegram": b["telegram"],
                "other": b.get("other", 0),
                "total": b["whatsapp"] + b["telegram"] + b.get("other", 0),
            })

        tot_wa = sum(s["whatsapp"] for s in series)
        tot_tg = sum(s["telegram"] for s in series)
        tot_all = tot_wa + tot_tg + sum(s["other"] for s in series)
        best = max(series, key=lambda s: s["total"]) if series else None

        # [HEATMAP-VIEW] continuous 24-bucket hourly series (zeros for quiet
        # hours) + peak-hour detection (None when the window is empty).
        hourly_series = []
        for h in range(24):
            hb = hourly.get(h, {"whatsapp": 0, "telegram": 0, "other": 0})
            hourly_series.append({
                "hour": h,
                "whatsapp": hb["whatsapp"],
                "telegram": hb["telegram"],
                "other": hb.get("other", 0),
                "total": hb["whatsapp"] + hb["telegram"] + hb.get("other", 0),
            })
        peak = max(hourly_series, key=lambda x: x["total"]) if hourly_series else None
        peak_hour = ({"hour": peak["hour"], "count": peak["total"]}
                     if peak and peak["total"] > 0 else None)

        payload = {
            "days": days,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "daily": series,
            "hourly": hourly_series,
            "totals": {
                "total": tot_all,
                "whatsapp": tot_wa,
                "telegram": tot_tg,
                "best_day": ({"date": best["date"], "count": best["total"]}
                             if best else None),
                "avg_per_day": round(tot_all / days, 1) if days else 0,
                "peak_hour": peak_hour,
            },
        }

        # Refresh cache (single slot — most recent window wins; acceptable
        # because the dashboard always requests the same window).
        _LINKS_DAILY_CACHE.update(
            {"key": cache_key, "payload": payload, "ts": now_ts})

        return web.json_response(payload, status=200,
                                 headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logging.error(f"[API] /api/links_daily error: {e}")
        return web.json_response({"error": str(e), "daily": []}, status=200,
                                 headers={"Access-Control-Allow-Origin": "*"})


# [SOURCE-VIEW] /api/top_groups — link-source attribution for the dashboard:
# which groups produced the links captured in the window? Answers the
# operator question the trend chart raised ("why did capture drop — which
# sources went quiet?"). Fetches ONLY (created_at, group_name, link_type)
# columns from Supabase (paginated in 1000-row batches), aggregates by
# group_name in Python, returns the top-N with WA/TG split + first/last
# seen + share of window total. Cached in-process for 60s (the dashboard
# polls on the same 60s cadence as the trend chart).
_TOP_GROUPS_CACHE = {"key": None, "payload": None, "ts": 0.0}

# Group names may be NULL/empty in the links table (e.g. links captured
# outside a monitored chat). They are bucketed instead of dropped so the
# per-group totals still sum to the window total.
_UNNAMED_GROUP_LABEL = "غير محدد"


async def _fetch_window_group_activity(db, days):
    """[QUIET-DIGEST] Shared aggregation: group_name → counters over the
    calendar window (last N days ending today, aligned to start-of-day —
    exactly the series window /api/links_daily shows).

    Extracted from api_top_groups_handler so the quiet-source watch loop
    (Monitor._quiet_source_watch_loop) can read the SAME data with the
    SAME guards without going through the HTTP layer. Returns a dict:
      {group_name: {"total": N, "whatsapp": N, "telegram": N, "other": N,
                    "first": "YYYY-MM-DD", "last": "YYYY-MM-DD"}}
    Supabase unconfigured / fetch failure → {} (callers treat as "no
    data this cycle" — the dashboard handler additionally degrades to an
    empty payload exactly as before).
    """
    groups: dict = {}
    if not (getattr(db, "supabase_url", None) and getattr(db, "supabase_key", None)):
        return groups
    try:
        session = await db._get_supabase_session()
        # [WINDOW-ALIGN] calendar-day window (same as links_daily /
        # group_detail) so top_groups totals agree with the trend
        # chart's totals for the same ?days value.
        since_iso, window_start_iso, today_iso = \
            _calendar_window_bounds(days)
        offset = 0
        batch_size = 1000
        while True:
            url = (
                f"{db.supabase_url}/rest/v1/links"
                f"?select=created_at,group_name,link_type"
                f"&created_at=gte.{since_iso}"
                f"&order=created_at.asc"
                f"&limit={batch_size}&offset={offset}"
            )
            async with session.get(url) as r:
                if r.status == 200:
                    rows = await r.json()
                elif r.status == 416:
                    break
                else:
                    logging.error(
                        f"[API] group-activity batch failed: {r.status}")
                    break
            if not rows:
                break
            for row in rows:
                created = str(row.get("created_at") or "")
                day_key = created[:10]
                if len(day_key) != 10 or not day_key.startswith("20"):
                    continue
                # [WINDOW-ALIGN] defensive bounds check — keeps the
                # aggregated window exactly the calendar series window
                # even with clock-skewed or future-dated rows.
                if day_key < window_start_iso or day_key > today_iso:
                    continue
                gname = (row.get("group_name") or "").strip() \
                    or _UNNAMED_GROUP_LABEL
                g = groups.setdefault(gname, {
                    "total": 0, "whatsapp": 0, "telegram": 0,
                    "other": 0, "first": day_key, "last": day_key})
                g["total"] += 1
                lt = row.get("link_type") or "other"
                if lt in ("whatsapp", "telegram"):
                    g[lt] += 1
                else:
                    g["other"] += 1
                # created_at is ordered asc → first is the earliest.
                if day_key < g["first"]:
                    g["first"] = day_key
                if day_key > g["last"]:
                    g["last"] = day_key
            if len(rows) < batch_size:
                break
            offset += batch_size
    except Exception as e:
        logging.warning(f"[API] group-activity supabase fetch failed: {e}")
        return {}
    return groups


# ===================================================================
# [QUIET-DIGEST] Quiet-source watch — push a Telegram alert to OWNER_ID
# when an IMPORTANT link source (group) stops producing. Operational
# lesson from the Aug-19 cluster: five university groups went silent on
# the same day and the capture drop was only noticed DAYS later on the
# dashboard trend chart. The operator doesn't stare at the dashboard
# 24/7 — the bot does, and it can say something the moment a top source
# dries up.
#
# Semantics (constants below are module-level so tests can reason
# about them without instantiating a Monitor):
#   * qualifies as "important"  → ≥ _QUIET_SOURCE_MIN_VOLUME links in
#     the last 30 days (tiny one-off groups must not spam the owner).
#   * "went quiet"              → no link for ≥ _QUIET_SOURCE_DAYS full
#     days (2 = two overnight gaps — same threshold as the dashboard's
#     quiet-sources card, QUIET_AFTER_DAYS=2).
#   * alert                     → ONE message per source when it FIRST
#     crosses the threshold; re-alert only every
#     _QUIET_SOURCE_REALERT_DAYS while it stays quiet (escalation, not
#     spam).
#   * recovery                  → a short "active again" note when a
#     previously-alerted source produces links again.
#   * startup digest            → after a (re)start the in-memory alert
#     state is empty; the FIRST cycle sends ONE consolidated digest of
#     currently-quiet sources (instead of N individual alerts) and
#     seeds the state so subsequent alerts are only for NEW events.
#   * OWNER_ID unset / bot disconnected → log-only (same best-effort
#     posture as _send_fleet_down_alert).
# ===================================================================
_QUIET_SOURCE_CHECK_INTERVAL = 1800   # 30 min between checks
_QUIET_SOURCE_MIN_VOLUME = 20         # links in 30d to qualify as important
_QUIET_SOURCE_DAYS = 2                # full days of silence before alerting
_QUIET_SOURCE_REALERT_DAYS = 7        # re-alert cadence while still quiet
_QUIET_SOURCE_WINDOW_DAYS = 30        # look-back window


def _compute_quiet_alerts(groups, today, alerted):
    """[QUIET-DIGEST] Pure decision function for the quiet-source watch
    loop — no I/O, fully unit-testable.

    Args:
      groups:   {name: {"total": N, "last": "YYYY-MM-DD", ...}} — the
                30-day aggregation from _fetch_window_group_activity.
      today:    datetime.date — "now".
      alerted:  {name: {"day": "YYYY-MM-DD", "volume": N}} — sources
                already alerted (and when), persisted in memory across
                cycles by the loop.

    Returns a 4-tuple of lists:
      new_alerts:  [(name, volume, last_seen, silence_days)] — crossed
                   the quiet threshold, never alerted before.
      re_alerts:   same shape — still quiet, last alert ≥ REALERT days
                   ago (periodic escalation).
      seed:        same shape — currently quiet but already covered by
                   the alert state (first-cycle digest content; the
                   loop seeds these instead of alerting them).
      recovered:   [(name, volume)] — were alerted, produced links
                   again within the last _QUIET_SOURCE_DAYS days.
    """
    new_alerts, re_alerts, seed, recovered = [], [], [], []
    for gname, g in groups.items():
        volume = g.get("total", 0)
        last_seen = g.get("last") or ""
        try:
            last_date = datetime.strptime(last_seen, "%Y-%m-%d").date()
        except ValueError:
            continue  # malformed/missing date — cannot judge quietness
        silence_days = (today - last_date).days
        was_alerted = gname in alerted

        if silence_days >= _QUIET_SOURCE_DAYS:
            if volume < _QUIET_SOURCE_MIN_VOLUME:
                continue  # not important enough to alert about
            if not was_alerted:
                new_alerts.append((gname, volume, last_seen, silence_days))
            else:
                try:
                    prev_day = datetime.strptime(
                        alerted[gname]["day"], "%Y-%m-%d").date()
                except (KeyError, ValueError):
                    prev_day = None
                if prev_day is None or \
                        (today - prev_day).days >= _QUIET_SOURCE_REALERT_DAYS:
                    re_alerts.append(
                        (gname, volume, last_seen, silence_days))
                else:
                    seed.append(
                        (gname, volume, last_seen, silence_days))
        elif was_alerted:
            # silence broke (source produced a link again) → recovery
            recovered.append((gname, volume))
    return new_alerts, re_alerts, seed, recovered


async def api_top_groups_handler(request):
    """API endpoint: top link-producing groups for the dashboard.

    Query params:
      ?days=N    → window size in days (default 14, clamped 1..30)
      ?limit=N   → number of groups returned (default 10, clamped 1..50)

    Returns:
      {
        "days": 14, "limit": 10,
        "generated_at": "<ISO>",
        "groups": [
          {"group": "...", "total": N, "whatsapp": N, "telegram": N,
           "other": N, "share": 12.3,
           "first_seen": "YYYY-MM-DD", "last_seen": "YYYY-MM-DD"},
          ...
        ],
        "totals": {"total": N, "distinct_groups": N}
      }
    """
    db = request.app.get("db")
    if not db:
        return web.json_response({"error": "not ready"}, status=503)

    try:
        days = max(1, min(30, int(request.query.get("days", "14"))))
        limit = max(1, min(50, int(request.query.get("limit", "10"))))
        cache_key = f"d{days}l{limit}"
        now_ts = time.time()
        cached = _TOP_GROUPS_CACHE
        if (cached["key"] == cache_key and cached["payload"] is not None
                and (now_ts - cached["ts"]) < 60.0):
            return web.json_response(cached["payload"], status=200,
                                     headers={"Access-Control-Allow-Origin": "*"})

        # [QUIET-DIGEST] aggregation extracted to the shared helper —
        # the quiet-source watch loop reads the SAME data with the SAME
        # guards (one query shape, one set of window-alignment tests).
        groups = await _fetch_window_group_activity(db, days)
        if groups:
            logging.info(
                f"[API] /api/top_groups aggregated window over "
                f"{days}d → {len(groups)} groups")

        window_total = sum(g["total"] for g in groups.values())
        ranked = sorted(groups.items(), key=lambda kv: kv[1]["total"],
                        reverse=True)[:limit]
        top = []
        for gname, g in ranked:
            top.append({
                "group": gname,
                "total": g["total"],
                "whatsapp": g["whatsapp"],
                "telegram": g["telegram"],
                "other": g.get("other", 0),
                "share": round(g["total"] * 100.0 / window_total, 1)
                         if window_total else 0.0,
                "first_seen": g["first"],
                "last_seen": g["last"],
            })

        payload = {
            "days": days,
            "limit": limit,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "groups": top,
            "totals": {
                "total": window_total,
                "distinct_groups": len(groups),
            },
        }

        _TOP_GROUPS_CACHE.update(
            {"key": cache_key, "payload": payload, "ts": now_ts})

        return web.json_response(payload, status=200,
                                 headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logging.error(f"[API] /api/top_groups error: {e}")
        return web.json_response({"error": str(e), "groups": []}, status=200,
                                 headers={"Access-Control-Allow-Origin": "*"})


# [SENDERS-VIEW] /api/top_senders — WHO posts the links? The dashboard
# already answers WHAT (trend), WHEN (hourly), WHERE (top groups); this
# endpoint completes the picture with the top link POSTERS aggregated by
# sender_name over the window. Deliberately PII-free: sender_contact (which
# can contain phones posted inside messages) is NEVER selected or returned —
# display names are public inside the groups anyway, so the leaderboard
# needs no masking logic at all. Same efficiency pattern as top_groups:
# narrow Supabase select (created_at, sender_name, group_name, link_type),
# 1000-row paginated batches, 60s in-process cache keyed d{days}l{limit}.
_TOP_SENDERS_CACHE = {"key": None, "payload": None, "ts": 0.0}

_UNNAMED_SENDER_LABEL = "غير محدد"


async def api_top_senders_handler(request):
    """API endpoint: top link-posting senders for the dashboard.

    Query params:
      ?days=N    → window size in days (default 14, clamped 1..30)
      ?limit=N   → number of senders returned (default 10, clamped 1..50)

    Returns:
      {
        "days": 14, "limit": 10,
        "generated_at": "<ISO>",
        "senders": [
          {"sender": "...", "total": N, "whatsapp": N, "telegram": N,
           "other": N, "share": 12.3, "groups_count": N,
           "top_group": "...", "first_seen": "YYYY-MM-DD",
           "last_seen": "YYYY-MM-DD"},
          ...
        ],
        "totals": {"total": N, "distinct_senders": N}
      }
    """
    db = request.app.get("db")
    if not db:
        return web.json_response({"error": "not ready"}, status=503)

    try:
        days = max(1, min(30, int(request.query.get("days", "14"))))
        limit = max(1, min(50, int(request.query.get("limit", "10"))))
        cache_key = f"d{days}l{limit}"
        now_ts = time.time()
        cached = _TOP_SENDERS_CACHE
        if (cached["key"] == cache_key and cached["payload"] is not None
                and (now_ts - cached["ts"]) < 60.0):
            return web.json_response(cached["payload"], status=200,
                                     headers={"Access-Control-Allow-Origin": "*"})

        # sender_name → counters (+ per-group counter for top_group)
        senders: dict = {}

        if db.supabase_url and db.supabase_key:
            try:
                session = await db._get_supabase_session()
                # [WINDOW-ALIGN] calendar-day window (same as links_daily /
                # top_groups / group_detail).
                since_iso, window_start_iso, today_iso = \
                    _calendar_window_bounds(days)
                offset = 0
                batch_size = 1000
                fetched = 0
                while True:
                    url = (
                        f"{db.supabase_url}/rest/v1/links"
                        f"?select=created_at,sender_name,group_name,link_type"
                        f"&created_at=gte.{since_iso}"
                        f"&order=created_at.asc"
                        f"&limit={batch_size}&offset={offset}"
                    )
                    async with session.get(url) as r:
                        if r.status == 200:
                            rows = await r.json()
                        elif r.status == 416:
                            break
                        else:
                            logging.error(
                                f"[API] /api/top_senders batch failed: {r.status}")
                            break
                    if not rows:
                        break
                    for row in rows:
                        created = str(row.get("created_at") or "")
                        day_key = created[:10]
                        if len(day_key) != 10 or not day_key.startswith("20"):
                            continue
                        # [WINDOW-ALIGN] defensive bounds check (see
                        # top_groups).
                        if day_key < window_start_iso or day_key > today_iso:
                            continue
                        sname = (row.get("sender_name") or "").strip() \
                            or _UNNAMED_SENDER_LABEL
                        gname = (row.get("group_name") or "").strip() \
                            or _UNNAMED_GROUP_LABEL
                        s = senders.setdefault(sname, {
                            "total": 0, "whatsapp": 0, "telegram": 0,
                            "other": 0, "first": day_key, "last": day_key,
                            "groups": {}})
                        s["total"] += 1
                        lt = row.get("link_type") or "other"
                        if lt in ("whatsapp", "telegram"):
                            s[lt] += 1
                        else:
                            s["other"] += 1
                        if day_key < s["first"]:
                            s["first"] = day_key
                        if day_key > s["last"]:
                            s["last"] = day_key
                        s["groups"][gname] = s["groups"].get(gname, 0) + 1
                    fetched += len(rows)
                    if len(rows) < batch_size:
                        break
                    offset += batch_size
                logging.info(
                    f"[API] /api/top_senders aggregated {fetched} rows over "
                    f"{days}d → {len(senders)} senders")
            except Exception as e:
                logging.warning(f"[API] /api/top_senders supabase fetch failed: {e}")

        window_total = sum(s["total"] for s in senders.values())
        ranked = sorted(senders.items(), key=lambda kv: kv[1]["total"],
                        reverse=True)[:limit]
        top = []
        for sname, s in ranked:
            top_group = (max(s["groups"].items(), key=lambda kv: kv[1])[0]
                         if s["groups"] else _UNNAMED_GROUP_LABEL)
            top.append({
                "sender": sname,
                "total": s["total"],
                "whatsapp": s["whatsapp"],
                "telegram": s["telegram"],
                "other": s.get("other", 0),
                "share": round(s["total"] * 100.0 / window_total, 1)
                         if window_total else 0.0,
                "groups_count": len(s["groups"]),
                "top_group": top_group,
                "first_seen": s["first"],
                "last_seen": s["last"],
            })

        payload = {
            "days": days,
            "limit": limit,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "senders": top,
            "totals": {
                "total": window_total,
                "distinct_senders": len(senders),
            },
        }

        _TOP_SENDERS_CACHE.update(
            {"key": cache_key, "payload": payload, "ts": now_ts})

        return web.json_response(payload, status=200,
                                 headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logging.error(f"[API] /api/top_senders error: {e}")
        return web.json_response({"error": str(e), "senders": []}, status=200,
                                 headers={"Access-Control-Allow-Origin": "*"})


async def api_stats_handler(request):
    """API endpoint: returns system stats for dashboard.

    Includes AI stats (ai_approved, ai_rejected, ai_ads, ai_pending)
    so the dashboard can show AI verification coverage.
    """
    monitor = request.app.get("monitor")
    db = request.app.get("db")
    if not monitor or not db:
        return web.json_response({"error": "not ready"}, status=503)

    try:
        # Total links from Supabase
        total_links = await db.count_requests() if db else 0

        # Watchers
        watchers = await db.get_active_watchers()
        active_watchers = len(watchers)

        # Connected accounts
        connected = sum(1 for c in monitor.user_clients.values() if c and c.is_connected())

        # AI stats — query Supabase for each count using Prefer: count=exact
        ai_approved_count = 0
        ai_rejected_count = 0
        ai_ads_count = 0
        ai_pending_count = 0
        wa_count = 0
        tg_count = 0

        if db.supabase_url and db.supabase_key:
            try:
                session = await db._get_supabase_session()
                count_headers = {**session.headers, "Prefer": "count=exact", "Range": "0-0"}

                async def _count(url: str) -> int:
                    try:
                        async with session.get(url, headers=count_headers) as r:
                            if r.status in (200, 206):
                                cr = r.headers.get("content-range", "*/0")
                                return int(cr.split("/")[-1] or "0")
                    except Exception:
                        pass
                    return 0

                wa_count = await _count(f"{db.supabase_url}/rest/v1/links?link_type=eq.whatsapp&select=id")
                tg_count = await _count(f"{db.supabase_url}/rest/v1/links?link_type=eq.telegram&select=id")
                ai_approved_count = await _count(f"{db.supabase_url}/rest/v1/links?ai_approved=eq.true&select=id")
                # ai_rejected = بالفعل رفضها AI (ai_approved=false AND ai_description not null)
                # لو ai_description فاضي → ما تم فحصها (pending) — لا ت counted كـ rejected
                ai_rejected_count = await _count(f"{db.supabase_url}/rest/v1/links?ai_approved=eq.false&ai_description=not.is.null&select=id")
                ai_ads_count = await _count(f"{db.supabase_url}/rest/v1/links?ai_is_ad=eq.true&select=id")
                # Pending = total - (approved + rejected)
                ai_pending_count = max(0, total_links - ai_approved_count - ai_rejected_count)
            except Exception as e:
                logging.warning(f"[API] ai stats fetch failed: {e}")

        # AI batch mode status (controlled by env var AI_BATCH_MODE)
        ai_batch_mode = os.getenv("AI_BATCH_MODE", "true").lower() in ("true", "1", "yes")

        return web.json_response({
            "total_links": total_links,
            "whatsapp_links": wa_count,
            "telegram_links": tg_count,
            "active_watchers": active_watchers,
            "connected_accounts": connected,
            "bot_connected": bool(monitor.bot_client and monitor.bot_client.is_connected()),
            "ai_stats": {
                "ai_approved": ai_approved_count,
                "ai_rejected": ai_rejected_count,
                "ai_ads": ai_ads_count,
                "ai_pending": ai_pending_count,
                "ai_batch_mode": ai_batch_mode,
            },
        }, status=200, headers={"Access-Control-Allow-Origin": "*"})

    except Exception as e:
        logging.error(f"[API] /api/stats error: {e}")
        return web.json_response({"error": str(e)}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})


# [GROUP-DRILL] /api/group_detail — per-group drill-down for the dashboard.
# Clicking a row in the top-groups card (or a quiet source) opens a detail
# view: the group's OWN daily series + its top senders. Answers "WHO posts
# in THIS group" and "when exactly did it dry up" — the drill-down behind
# the Aug-19 cluster finding (five university groups all went quiet the
# same day; now the operator can inspect each one's senders + last days).
# Deliberately PII-free like top_senders: only sender_name is selected —
# sender_contact and source_phone are NEVER fetched. Same efficiency
# pattern: narrow select, 1000-row batches, 60s in-process cache keyed by
# (group, days). Unnamed groups are queryable too (the dashboard passes
# the raw label; rows store NULL/'' and are aggregated as غير محدد — the
# filter here matches the raw stored value).
_GROUP_DETAIL_CACHE = {"key": None, "payload": None, "ts": 0.0}

_UNNAMED_SENDER_LABEL_GD = "غير محدد"


async def api_group_detail_handler(request):
    """API endpoint: per-group drill-down (daily series + top senders).

    Query params:
      ?group=X   → exact group_name (required, non-empty)
      ?days=N    → window size in days (default 14, clamped 1..30)

    Returns:
      {
        "group": "...", "days": 14, "generated_at": "<ISO>",
        "totals": {"total": N, "whatsapp": N, "telegram": N, "other": N,
                   "distinct_senders": N},
        "first_seen": "YYYY-MM-DD", "last_seen": "YYYY-MM-DD",
        "daily": [{"date": "...", "whatsapp": N, "telegram": N,
                   "other": N, "total": N}, ...continuous window],
        "senders": [{"sender": "...", "total": N, "whatsapp": N,
                     "telegram": N, "share": 12.3, "first_seen": "...",
                     "last_seen": "..."}, ... top 20 by total]
      }
    """
    group_name = (request.query.get("group") or "").strip()
    if not group_name:
        return web.json_response(
            {"error": "missing group query param"}, status=400,
            headers={"Access-Control-Allow-Origin": "*"})
    db = request.app.get("db")
    if not db:
        return web.json_response({"error": "not ready"}, status=503)

    try:
        days = max(1, min(30, int(request.query.get("days", "14"))))
        # Cap the key at 80 chars — group names can be long; cache identity
        # stays unambiguous for realistic names and bounded in memory.
        cache_key = f"g:{group_name[:80]}:d{days}"
        now_ts = time.time()
        cached = _GROUP_DETAIL_CACHE
        if (cached["key"] == cache_key and cached["payload"] is not None
                and (now_ts - cached["ts"]) < 60.0):
            return web.json_response(cached["payload"], status=200,
                                     headers={"Access-Control-Allow-Origin": "*"})

        daily: dict = {}   # "YYYY-MM-DD" → {"whatsapp", "telegram", "other"}
        senders: dict = {}  # sender_name → counters
        first_seen: str | None = None
        last_seen: str | None = None

        if db.supabase_url and db.supabase_key:
            try:
                from urllib.parse import quote as _urlquote
                session = await db._get_supabase_session()
                # Align the fetch window with the SERIES window exactly
                # ([WINDOW-ALIGN] shared helper — see its docstring for why
                # a plain now - N days timestamp would break consistency).
                since_iso, window_start_iso, today_iso = \
                    _calendar_window_bounds(days)
                group_eq = _urlquote(group_name, safe="")
                offset = 0
                batch_size = 1000
                fetched = 0
                while True:
                    url = (
                        f"{db.supabase_url}/rest/v1/links"
                        f"?select=created_at,sender_name,link_type"
                        f"&group_name=eq.{group_eq}"
                        f"&created_at=gte.{since_iso}"
                        f"&order=created_at.asc"
                        f"&limit={batch_size}&offset={offset}"
                    )
                    async with session.get(url) as r:
                        if r.status == 200:
                            rows = await r.json()
                        elif r.status == 416:
                            break
                        else:
                            logging.error(
                                f"[API] /api/group_detail batch failed: {r.status}")
                            break
                    if not rows:
                        break
                    for row in rows:
                        created = str(row.get("created_at") or "")
                        day_key = created[:10]
                        if len(day_key) != 10 or not day_key.startswith("20"):
                            continue
                        # Consistency guarantee: only count rows whose
                        # day_key lands inside the series window — the
                        # series, totals and sender counters then agree
                        # BY CONSTRUCTION (Σ senders == totals.total).
                        if day_key < window_start_iso or day_key > today_iso:
                            continue
                        bucket = daily.setdefault(
                            day_key, {"whatsapp": 0, "telegram": 0, "other": 0})
                        sname = (row.get("sender_name") or "").strip() \
                            or _UNNAMED_SENDER_LABEL_GD
                        s = senders.setdefault(sname, {
                            "total": 0, "whatsapp": 0, "telegram": 0,
                            "other": 0, "first": day_key, "last": day_key})
                        s["total"] += 1
                        lt = row.get("link_type") or "other"
                        if lt in ("whatsapp", "telegram"):
                            bucket[lt] += 1
                            s[lt] += 1
                        else:
                            bucket["other"] = bucket.get("other", 0) + 1
                            s["other"] += 1
                        # created_at ordered asc → track window first/last.
                        if first_seen is None or day_key < first_seen:
                            first_seen = day_key
                        if last_seen is None or day_key > last_seen:
                            last_seen = day_key
                        if day_key < s["first"]:
                            s["first"] = day_key
                        if day_key > s["last"]:
                            s["last"] = day_key
                    fetched += len(rows)
                    if len(rows) < batch_size:
                        break
                    offset += batch_size
                logging.info(
                    f"[API] /api/group_detail aggregated {fetched} rows for "
                    f"'{group_name[:40]}' over {days}d → {len(senders)} senders")
            except Exception as e:
                logging.warning(f"[API] /api/group_detail supabase fetch failed: {e}")

        # Continuous date series over the window (gaps = zero days — a dry
        # day inside the window is itself signal for the drill-down chart).
        today = datetime.utcnow().date()
        series = []
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            key = d.isoformat()
            b = daily.get(key, {"whatsapp": 0, "telegram": 0, "other": 0})
            series.append({
                "date": key,
                "whatsapp": b["whatsapp"],
                "telegram": b["telegram"],
                "other": b.get("other", 0),
                "total": b["whatsapp"] + b["telegram"] + b.get("other", 0),
            })

        tot_wa = sum(s["whatsapp"] for s in series)
        tot_tg = sum(s["telegram"] for s in series)
        tot_other = sum(s["other"] for s in series)
        tot_all = tot_wa + tot_tg + tot_other

        ranked = sorted(senders.items(), key=lambda kv: kv[1]["total"],
                        reverse=True)[:20]
        top_senders = []
        for sname, s in ranked:
            top_senders.append({
                "sender": sname,
                "total": s["total"],
                "whatsapp": s["whatsapp"],
                "telegram": s["telegram"],
                "other": s.get("other", 0),
                "share": round(s["total"] * 100.0 / tot_all, 1)
                         if tot_all else 0.0,
                "first_seen": s["first"],
                "last_seen": s["last"],
            })

        payload = {
            "group": group_name,
            "days": days,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "totals": {
                "total": tot_all,
                "whatsapp": tot_wa,
                "telegram": tot_tg,
                "other": tot_other,
                "distinct_senders": len(senders),
            },
            "first_seen": first_seen,
            "last_seen": last_seen,
            "daily": series,
            "senders": top_senders,
        }

        _GROUP_DETAIL_CACHE.update(
            {"key": cache_key, "payload": payload, "ts": now_ts})

        return web.json_response(payload, status=200,
                                 headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logging.error(f"[API] /api/group_detail error: {e}")
        return web.json_response({"error": str(e), "senders": [], "daily": []},
                                 status=200,
                                 headers={"Access-Control-Allow-Origin": "*"})


_SENDER_DETAIL_CACHE = {"key": None, "payload": None, "ts": 0.0}

_UNNAMED_GROUP_LABEL_SD = "غير محدد"


async def api_sender_detail_handler(request):
    """API endpoint: per-sender drill-down (daily series + top groups).

    Query params:
      ?sender=X  → exact sender_name (required, non-empty)
      ?days=N    → window size in days (default 14, clamped 1..30)

    Returns:
      {
        "sender": "...", "days": 14, "generated_at": "<ISO>",
        "totals": {"total": N, "whatsapp": N, "telegram": N, "other": N,
                   "distinct_groups": N},
        "first_seen": "YYYY-MM-DD", "last_seen": "YYYY-MM-DD",
        "daily": [{"date": "...", "whatsapp": N, "telegram": N,
                   "other": N, "total": N}, ...continuous window],
        "groups": [{"group": "...", "total": N, "whatsapp": N,
                    "telegram": N, "share": 12.3, "first_seen": "...",
                    "last_seen": "..."}, ... top 20 by total]
      }

    PII-free by construction: selects ONLY created_at, group_name,
    link_type — never contacts or phones (TS-4b posture).
    """
    sender_name = (request.query.get("sender") or "").strip()
    if not sender_name:
        return web.json_response(
            {"error": "missing sender query param"}, status=400,
            headers={"Access-Control-Allow-Origin": "*"})
    db = request.app.get("db")
    if not db:
        return web.json_response({"error": "not ready"}, status=503)

    try:
        days = max(1, min(30, int(request.query.get("days", "14"))))
        # Cap the key at 80 chars — sender names can be long; cache identity
        # stays unambiguous for realistic names and bounded in memory.
        cache_key = f"s:{sender_name[:80]}:d{days}"
        now_ts = time.time()
        cached = _SENDER_DETAIL_CACHE
        if (cached["key"] == cache_key and cached["payload"] is not None
                and (now_ts - cached["ts"]) < 60.0):
            return web.json_response(cached["payload"], status=200,
                                     headers={"Access-Control-Allow-Origin": "*"})

        daily: dict = {}   # "YYYY-MM-DD" → {"whatsapp", "telegram", "other"}
        groups: dict = {}  # group_name → counters
        first_seen: str | None = None
        last_seen: str | None = None

        if db.supabase_url and db.supabase_key:
            try:
                from urllib.parse import quote as _urlquote
                session = await db._get_supabase_session()
                # [WINDOW-ALIGN] shared helper — fetch window == series
                # window (last N calendar days ending today).
                since_iso, window_start_iso, today_iso = \
                    _calendar_window_bounds(days)
                sender_eq = _urlquote(sender_name, safe="")
                offset = 0
                batch_size = 1000
                fetched = 0
                while True:
                    url = (
                        f"{db.supabase_url}/rest/v1/links"
                        f"?select=created_at,group_name,link_type"
                        f"&sender_name=eq.{sender_eq}"
                        f"&created_at=gte.{since_iso}"
                        f"&order=created_at.asc"
                        f"&limit={batch_size}&offset={offset}"
                    )
                    async with session.get(url) as r:
                        if r.status == 200:
                            rows = await r.json()
                        elif r.status == 416:
                            break
                        else:
                            logging.error(
                                f"[API] /api/sender_detail batch failed: {r.status}")
                            break
                    if not rows:
                        break
                    for row in rows:
                        created = str(row.get("created_at") or "")
                        day_key = created[:10]
                        if len(day_key) != 10 or not day_key.startswith("20"):
                            continue
                        # Consistency guarantee (mirrors group_detail): only
                        # count rows whose day_key lands inside the series
                        # window — series, totals and group counters agree
                        # BY CONSTRUCTION (Σ groups == totals.total).
                        if day_key < window_start_iso or day_key > today_iso:
                            continue
                        bucket = daily.setdefault(
                            day_key, {"whatsapp": 0, "telegram": 0, "other": 0})
                        gname = (row.get("group_name") or "").strip() \
                            or _UNNAMED_GROUP_LABEL_SD
                        g = groups.setdefault(gname, {
                            "total": 0, "whatsapp": 0, "telegram": 0,
                            "other": 0, "first": day_key, "last": day_key})
                        g["total"] += 1
                        lt = row.get("link_type") or "other"
                        if lt in ("whatsapp", "telegram"):
                            bucket[lt] += 1
                            g[lt] += 1
                        else:
                            bucket["other"] = bucket.get("other", 0) + 1
                            g["other"] += 1
                        # created_at ordered asc → track window first/last.
                        if first_seen is None or day_key < first_seen:
                            first_seen = day_key
                        if last_seen is None or day_key > last_seen:
                            last_seen = day_key
                        if day_key < g["first"]:
                            g["first"] = day_key
                        if day_key > g["last"]:
                            g["last"] = day_key
                    fetched += len(rows)
                    if len(rows) < batch_size:
                        break
                    offset += batch_size
                logging.info(
                    f"[API] /api/sender_detail aggregated {fetched} rows for "
                    f"'{sender_name[:40]}' over {days}d → {len(groups)} groups")
            except Exception as e:
                logging.warning(f"[API] /api/sender_detail supabase fetch failed: {e}")

        # Continuous date series over the window (gaps = zero days — a dry
        # day inside the window is itself signal for the drill-down chart).
        today = datetime.utcnow().date()
        series = []
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            key = d.isoformat()
            b = daily.get(key, {"whatsapp": 0, "telegram": 0, "other": 0})
            series.append({
                "date": key,
                "whatsapp": b["whatsapp"],
                "telegram": b["telegram"],
                "other": b.get("other", 0),
                "total": b["whatsapp"] + b["telegram"] + b.get("other", 0),
            })

        tot_wa = sum(s["whatsapp"] for s in series)
        tot_tg = sum(s["telegram"] for s in series)
        tot_other = sum(s["other"] for s in series)
        tot_all = tot_wa + tot_tg + tot_other

        ranked = sorted(groups.items(), key=lambda kv: kv[1]["total"],
                        reverse=True)[:20]
        top_groups = []
        for gname, g in ranked:
            top_groups.append({
                "group": gname,
                "total": g["total"],
                "whatsapp": g["whatsapp"],
                "telegram": g["telegram"],
                "other": g.get("other", 0),
                "share": round(g["total"] * 100.0 / tot_all, 1)
                         if tot_all else 0.0,
                "first_seen": g["first"],
                "last_seen": g["last"],
            })

        payload = {
            "sender": sender_name,
            "days": days,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "totals": {
                "total": tot_all,
                "whatsapp": tot_wa,
                "telegram": tot_tg,
                "other": tot_other,
                "distinct_groups": len(groups),
            },
            "first_seen": first_seen,
            "last_seen": last_seen,
            "daily": series,
            "groups": top_groups,
        }

        _SENDER_DETAIL_CACHE.update(
            {"key": cache_key, "payload": payload, "ts": now_ts})

        return web.json_response(payload, status=200,
                                 headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logging.error(f"[API] /api/sender_detail error: {e}")
        return web.json_response({"error": str(e), "groups": [], "daily": []},
                                 status=200,
                                 headers={"Access-Control-Allow-Origin": "*"})


async def ready_handler(request):
    """Readiness probe — returns 200 only if DB is reachable AND bot is connected.

    Render/Load balancers should use this to decide whether to route traffic.
    A 503 means the bot is alive but not ready to process (still starting up,
    DB is down, etc.).
    """
    # Check DB connectivity
    db_ok = True
    db_error = ""
    try:
        # The db instance is attached to the app for health checks
        db = request.app.get("db")
        if db and db._conn:
            cursor = await db._conn.execute("SELECT 1")
            await cursor.fetchone()
        else:
            db_ok = False
            db_error = "DB not initialized"
    except Exception as e:
        db_ok = False
        db_error = str(e)[:100]

    # Check bot connectivity
    monitor = request.app.get("monitor")
    bot_ok = bool(
        monitor
        and monitor.bot_client
        and monitor.bot_client.is_connected()
    )
    active_watchers = len(monitor.user_clients) if monitor else 0

    # [REQAUDIT-3] Surface live joiner-fleet health so the operator (and
    # the dashboard) can see at a glance whether joins are actually being
    # processed. Without this, /ready returned "ready" even when ALL
    # joiners were in FloodWait / disconnected — masking the real state.
    fleet = {}
    if monitor:
        fh = getattr(monitor, '_fleet_health', None) or {}
        fleet = {
            "connected_joiners": fh.get('connected_joiners', 0),
            "floodwait_joiners_count": len(fh.get('floodwait_joiners', [])),
            "disconnected_joiners_count": len(fh.get('disconnected_joiners', [])),
            "safety_guard_blocked_joiners": fh.get('safety_guard_blocked_joiners', 0),
            "all_joiners_unavailable": (fh.get('connected_joiners', 0) == 0),
        }

    if db_ok and bot_ok:
        return web.json_response({
            "status": "ready",
            "bot_connected": bot_ok,
            "db_connected": db_ok,
            "active_watchers": active_watchers,
            "scan_running": monitor.is_scan_running() if monitor else False,
            "fleet_health": fleet,
        }, status=200)
    else:
        return web.json_response({
            "status": "not_ready",
            "bot_connected": bot_ok,
            "db_connected": db_ok,
            "db_error": db_error,
            "active_watchers": active_watchers,
            "fleet_health": fleet,
        }, status=503)


async def metrics_handler(request):
    """Prometheus-style metrics endpoint for observability."""
    monitor = request.app.get("monitor")
    db = request.app.get("db")
    if not monitor:
        return web.Response(text="# monitor not initialized\n", status=503, content_type="text/plain")

    try:
        total_links = await db.count_requests() if db else 0
    except Exception:
        total_links = -1

    active_watchers = len(monitor.user_clients)
    scan_running = 1 if monitor.is_scan_running() else 0
    bot_connected = 1 if (monitor.bot_client and monitor.bot_client.is_connected()) else 0
    pending_scan_tasks = len(monitor._current_scan_tasks)
    login_sessions = len(monitor._login_sessions)

    # [PR-1/PR-2/observability] link-capture metrics (from Metrics singleton)
    m = getattr(monitor, 'metrics', None)
    msum = {}
    if m is not None:
        try:
            msum = await m.get_summary()
        except Exception:
            msum = {}
    link_capture_total = msum.get('link_capture_total', 0)
    link_ring_hits = msum.get('link_ring_hits', 0)
    delete_miss_total = msum.get('delete_miss_total', 0)
    delete_rescued_total = msum.get('delete_rescued_total', 0)
    reconcile_rescued_total = msum.get('reconcile_rescued_total', 0)
    duplicate_links_skipped = msum.get('total_duplicates', 0)
    link_forwarded_total = msum.get('link_forwarded_total', 0)
    floodwait_total = msum.get('total_floodwait', 0)
    # [PR-METRICS-SKIP-EXPOSE] اكشف سبب كل skip في /metrics — سابقًا كان
    # skip_reasons dict يعيش في الذاكرة فقط ولا يظهر في /metrics. المُشغّل
    # كان يعجز عن رؤية لماذا تُتجاهل الروابط (blacklist/already_joined/
    # publish_failed_send/ai_rejected/no_joiner_*/gulf_filter_*/...).
    skip_reasons = msum.get('skip_reasons', {}) or {}
    # [PR-METRICS-QUEUE] اكشف queue depth في /metrics — سابقًا كان مرئيًا
    # فقط عبر /api/stats (المُحمي بـ DASHBOARD_API_KEY). المُشغّل يحتاج
    # رؤية التراكم دون مصادقة ليعرف هل هناك backlog.
    link_queue_pending = -1
    try:
        if getattr(monitor, 'prod_db', None) is not None:
            link_queue_pending = await monitor.prod_db.get_queue_size()
    except Exception as _q_e:
        link_queue_pending = -1
    # [PR-LRB-TTL] عدّادات eviction لمراقبة صحة الـLRB
    link_ring_evicted_size = getattr(monitor, '_link_ring_evicted', 0)
    link_ring_evicted_ttl = getattr(monitor, '_link_ring_ttl_evicted', 0)
    link_ring_size = len(getattr(monitor, '_link_ring', {}) or {})
    # Fleet health (connected vs disconnected joiners)
    fh = getattr(monitor, '_fleet_health', {}) or {}
    connected_joiners = fh.get('connected_joiners', 0)
    disconnected_accounts = len(fh.get('disconnected_joiners', []))
    high_risk_chats = len(getattr(monitor, '_tight_poll_chats', set()) or set())
    tight_poll_active = 1 if getattr(monitor, '_tight_poll_task', None) and \
        not getattr(monitor._tight_poll_task, 'done', lambda: True)() else 0

    metrics = f"""# HELP monitor_total_links Total links stored in database
# TYPE monitor_total_links gauge
monitor_total_links {total_links}
# HELP monitor_active_watchers Active watcher connections
# TYPE monitor_active_watchers gauge
monitor_active_watchers {active_watchers}
# HELP monitor_scan_running Whether a scan is currently running
# TYPE monitor_scan_running gauge
monitor_scan_running {scan_running}
# HELP monitor_bot_connected Whether the bot is connected to Telegram
# TYPE monitor_bot_connected gauge
monitor_bot_connected {bot_connected}
# HELP monitor_pending_scan_tasks Number of pending scan tasks
# TYPE monitor_pending_scan_tasks gauge
monitor_pending_scan_tasks {pending_scan_tasks}
# HELP monitor_login_sessions Number of active login sessions
# TYPE monitor_login_sessions gauge
monitor_login_sessions {login_sessions}
# HELP link_capture_total Total links captured (NewMessage + Raw + Polling)
# TYPE link_capture_total counter
link_capture_total {link_capture_total}
# HELP link_ring_hits Links rescued from Link Ring Buffer after delete (LRB-only path)
# TYPE link_ring_hits counter
link_ring_hits {link_ring_hits}
# [PR-LRB-TTL] new observability counters for LRB health
# HELP link_ring_size Current number of entries in Link Ring Buffer
# TYPE link_ring_size gauge
link_ring_size {link_ring_size}
# HELP link_ring_evicted_size_total Entries evicted from LRB by size-based fallback (cap reached)
# TYPE link_ring_evicted_size_total counter
link_ring_evicted_size_total {link_ring_evicted_size}
# HELP link_ring_evicted_ttl_total Entries evicted from LRB by TTL (older than 300s)
# TYPE link_ring_evicted_ttl_total counter
link_ring_evicted_ttl_total {link_ring_evicted_ttl}
# HELP delete_miss_total Messages deleted before any delivery (no rescue possible)
# TYPE delete_miss_total counter
delete_miss_total {delete_miss_total}
# HELP delete_rescued_total Messages rescued after delete (cache/journal/LRB/get_messages)
# TYPE delete_rescued_total counter
delete_rescued_total {delete_rescued_total}
# HELP reconcile_rescued_total Messages rescued via reconcile
# TYPE reconcile_rescued_total counter
reconcile_rescued_total {reconcile_rescued_total}
# HELP duplicate_links_skipped Duplicate links skipped by central dedup
# TYPE duplicate_links_skipped counter
duplicate_links_skipped {duplicate_links_skipped}
# HELP link_forwarded_total Links successfully published to channel
# TYPE link_forwarded_total counter
link_forwarded_total {link_forwarded_total}
# HELP floodwait_total Total FloodWait events
# TYPE floodwait_total counter
floodwait_total {floodwait_total}
# HELP connected_joiners Joiner accounts currently connected
# TYPE connected_joiners gauge
connected_joiners {connected_joiners}
# HELP disconnected_accounts Accounts currently disconnected/skipped
# TYPE disconnected_accounts gauge
disconnected_accounts {disconnected_accounts}
# HELP high_risk_chats Chats flagged for tight polling
# TYPE high_risk_chats gauge
high_risk_chats {high_risk_chats}
# HELP tight_poll_active 1 if tight-poll loop is active
# TYPE tight_poll_active gauge
tight_poll_active {tight_poll_active}
# [PR-METRICS-QUEUE] queue depth (links waiting for processing)
# HELP link_queue_pending Links currently queued for processing
# TYPE link_queue_pending gauge
link_queue_pending {link_queue_pending}
# [PR-METRICS-SKIP-EXPOSE] per-reason skip breakdown — lets operator see
# WHY links are being dropped (blacklist, already_joined, ai_rejected,
# publish_failed_send, no_joiner_*, gulf_filter_*, low_member_count, etc.)
# HELP link_skip_total Links skipped by central dedup/policy, by reason
# TYPE link_skip_total counter
{chr(10).join(f'link_skip_total{{reason="{r}"}} {c}' for r, c in sorted(skip_reasons.items(), key=lambda x: -x[1])) if skip_reasons else '# (no skips recorded yet)'}
"""
    return web.Response(text=metrics, content_type="text/plain")


async def api_deploy_check_handler(request):
    """Diagnostic endpoint: full deployment health check.

    Returns the status of every critical dependency:
      - Python version
      - Environment variables presence (not values)
      - Supabase key type (anon vs service_role)
      - Supabase connectivity (live ping)
      - SQLite tables
      - Telegram bot connection
      - User clients (monitor vs joiner) — phones REDACTED (Task 5a / A3)
      - Recent link count + queue size
    Useful for debugging "why is dashboard empty / 401 errors".

    [Task 5a / A3] Phone numbers in user_clients are masked (keep country
    prefix + last 2 digits) — a diagnostic endpoint should not leak account
    PII even with the dashboard open. Other operator-facing endpoints
    (/api/joiners_status, /api/joined_groups, /api/monitored_chats,
    /api/links) still return raw phones because the operator needs to
    identify which account is which in the dashboard; if those endpoints
    are public (DASHBOARD_API_KEY unset), the operator should set the key
    + frontend X-Api-Key to gate them. Documented in FINAL_REPORT.md.
    """
    monitor = request.app.get("monitor")
    db = request.app.get("db")
    if not monitor or not db:
        return web.json_response({"error": "not ready"}, status=503,
                                 headers={"Access-Control-Allow-Origin": "*"})

    import sys as _sys
    report = {
        "timestamp": datetime.now().isoformat(),
        "python_version": _sys.version.split()[0],
        "env_vars": {},
        "supabase": {},
        "sqlite": {},
        "telegram": {},
        "queue": {},
        "issues": [],
    }

    # 1. Environment variables — presence only (mask values for security)
    required_envs = ["API_ID", "API_HASH", "BOT_TOKEN", "CHANNEL_ID",
                     "SUPABASE_URL", "SUPABASE_KEY", "OPENAI_API_KEY",
                     "AI_BATCH_MODE", "OWNER_ID", "STARTUP_SCAN_DAYS"]
    for var in required_envs:
        val = os.getenv(var, "")
        if not val:
            report["env_vars"][var] = "❌ MISSING"
            if var in ("API_ID", "API_HASH", "BOT_TOKEN", "CHANNEL_ID", "SUPABASE_URL", "SUPABASE_KEY"):
                report["issues"].append(f"Missing required env var: {var}")
        else:
            # إظهار أول 4 أحرف فقط للأمان
            masked = val[:4] + "..." + val[-2:] if len(val) > 8 else "***"
            report["env_vars"][var] = f"✅ set ({masked})"

    # 2. Supabase key type + connectivity test
    if db.supabase_url and db.supabase_key:
        # [Security / Req-1] Mask the Supabase project host so the dashboard
        # does not advertise the backend project URL publicly. Keep the scheme
        # + a short prefix so the operator can still confirm it is set.
        _supa_host = db.supabase_url.replace('https://', '').replace('http://', '')
        _supa_masked = ('https://' + (_supa_host[:8] + '•••' if len(_supa_host) > 12 else '•••'))
        report["supabase"]["url"] = _supa_masked
        report["supabase"]["key_type"] = "service_role" if db._supabase_key_is_service_role else "anon"
        if not db._supabase_key_is_service_role:
            report["issues"].append(
                "🚨 SUPABASE_KEY is anon role — will cause 401 on writes. "
                "Replace with service_role secret."
            )
        # Live ping — try to fetch 1 row from links table
        try:
            session = await db._get_supabase_session()
            test_headers = {**session.headers, "Range": "0-0"}
            async with session.get(
                f"{db.supabase_url}/rest/v1/links?select=id&limit=1",
                headers=test_headers
            ) as resp:
                report["supabase"]["ping_status"] = resp.status
                if resp.status == 200:
                    report["supabase"]["reachable"] = True
                elif resp.status == 401:
                    report["supabase"]["reachable"] = False
                    report["issues"].append("Supabase returned 401 — key lacks permission (RLS blocking)")
                elif resp.status == 404:
                    report["supabase"]["reachable"] = False
                    report["issues"].append("Supabase returned 404 — table 'links' does not exist")
                else:
                    report["supabase"]["reachable"] = False
                    report["issues"].append(f"Supabase ping returned {resp.status}")
        except Exception as e:
            report["supabase"]["reachable"] = False
            report["supabase"]["error"] = str(e)[:200]
            report["issues"].append(f"Supabase connection error: {e}")
    else:
        report["supabase"]["configured"] = False
        report["issues"].append("Supabase not configured (SUPABASE_URL or SUPABASE_KEY missing)")

    # 3. SQLite tables
    try:
        conn = await db._ensure_conn()
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [r[0] for r in await cursor.fetchall()]
        report["sqlite"]["tables"] = tables
        report["sqlite"]["has_watchers_table"] = "watchers" in tables
        if "watchers" in tables:
            report["issues"].append(
                "🚨 SQLite has 'watchers' table — should be Supabase-only! "
                "Architecture violation detected."
            )
    except Exception as e:
        report["sqlite"]["error"] = str(e)[:200]
        report["issues"].append(f"SQLite error: {e}")

    # 4. Telegram status
    report["telegram"]["bot_connected"] = bool(
        monitor.bot_client and monitor.bot_client.is_connected()
    )
    report["telegram"]["user_clients"] = {}
    for phone, client in monitor.user_clients.items():
        # [Task 5a / A3] redact phone in the diagnostic endpoint to avoid
        # leaking account PII when the dashboard is open. Keys stay unique
        # (operator can still distinguish accounts by the masked suffix).
        report["telegram"]["user_clients"][_redact_phone(phone)] = {
            "connected": bool(client and client.is_connected()),
        }
    if not report["telegram"]["bot_connected"]:
        report["issues"].append("Bot client not connected to Telegram")
    active_monitors = 0
    active_joiners = 0
    try:
        watchers = await db.get_active_watchers() if hasattr(db, 'get_active_watchers') else []
        for w in watchers:
            role = w.get('role', 'monitor') if isinstance(w, dict) else getattr(w, 'role', 'monitor')
            if role == 'joiner':
                active_joiners += 1
            else:
                active_monitors += 1
    except Exception:
        pass
    report["telegram"]["monitors_count"] = active_monitors
    report["telegram"]["joiners_count"] = active_joiners
    if active_joiners == 0:
        report["issues"].append("No joiner accounts — links won't be auto-joined")

    # 5. Queue + link count
    try:
        total_links = await db.count_requests()
        report["queue"]["total_links"] = total_links
    except Exception:
        report["queue"]["total_links"] = -1
    try:
        queue_size = await monitor.prod_db.get_queue_size()
        report["queue"]["pending_links"] = queue_size
    except Exception:
        report["queue"]["pending_links"] = -1

    # 6. AI batch mode status
    ai_batch_mode = os.getenv("AI_BATCH_MODE", "true").lower() in ("true", "1", "yes")
    report["ai"] = {
        "batch_mode": ai_batch_mode,
        "analyzer_enabled": bool(getattr(monitor, 'ai_analyzer', None) and monitor.ai_analyzer.enabled),
        "providers_count": len(monitor.ai_analyzer.providers) if hasattr(monitor, 'ai_analyzer') and monitor.ai_analyzer else 0,
    }

    # 7. Final verdict
    report["verdict"] = "HEALTHY" if not report["issues"] else "ISSUES_FOUND"
    report["issues_count"] = len(report["issues"])

    status_code = 200 if report["verdict"] == "HEALTHY" else 200  # always 200 so dashboard can show it
    return web.json_response(report, status=status_code,
                             headers={"Access-Control-Allow-Origin": "*"})


# -------------------------------------------------------------------
# [B06 / PR-7] DASHBOARD_API_KEY shared-secret for /api/* routes
# -------------------------------------------------------------------
# If DASHBOARD_API_KEY env is SET (non-empty), every /api/* request must
# carry an `X-Api-Key` header matching it (constant-time compare);
# mismatch -> 401 JSON. When DASHBOARD_API_KEY is UNSET, /api/* routes
# are OPEN (no Origin check, no fail-closed) per operator request
# (pre-PR-7 behavior). To re-secure, set DASHBOARD_API_KEY.
#
# Health endpoints (/health, /ready, /metrics) are NEVER gated — probes
# must stay open for Render/Prometheus.

_DASHBOARD_API_KEY_WARNED = {"open": False}


def _get_dashboard_api_key() -> Optional[str]:
    """Return the configured shared secret, or None if unset."""
    return os.environ.get("DASHBOARD_API_KEY") or None


# [OPEN-MODE] Per operator request, /api/* is OPEN when DASHBOARD_API_KEY is
# unset (dashboard_api_key_middleware passes through). The previous
# trusted-origin allowlist and fail-open escape-hatch were removed. To
# re-secure, set DASHBOARD_API_KEY (X-Api-Key then required).


def _warn_dashboard_api_key_open_once() -> None:
    """Emit a single WARNING if the dashboard API is running open (no key)."""
    if _DASHBOARD_API_KEY_WARNED["open"]:
        return
    _DASHBOARD_API_KEY_WARNED["open"] = True
    if _get_dashboard_api_key() is None:
        logging.warning(
            "[DASHBOARD] DASHBOARD_API_KEY is UNSET - /api/* endpoints are OPEN "
            "(no auth; dashboard reads data freely). Set DASHBOARD_API_KEY to "
            "require an X-Api-Key header and lock them down.")


def _api_should_show_full_pii() -> bool:
    """[Security / Req-1] Return True only when the dashboard is behind a
    shared secret (DASHBOARD_API_KEY set). When the key is UNSET, /api/* is
    OPEN per operator request, but full phones are still masked (the caller
    is unauthenticated). When the key IS set, the middleware already rejected
    any request lacking a valid X-Api-Key, so the caller is the authenticated
    operator and may see full phones.
    """
    return _get_dashboard_api_key() is not None


def _redact_phone(phone) -> str:
    """[Task 5a / A3] Mask the middle digits of a phone number for safe
    inclusion in diagnostic API responses.

    - "+96651234567"  → "+9665•••••67"
    - "96651234567"   → "9665•••••67"
    - "" / None / non-str → "" (no PII leak on edge cases)
    - Short strings (<=4 chars) → "••••" (don't leak the whole value)

    Rationale: the operator can still distinguish accounts by the country
    prefix + last 2 digits, but a publicly-readable dashboard (when
    DASHBOARD_API_KEY is unset) does not leak the full phone number to
    the internet. Used in /api/deploy_check (the most leak-prone endpoint
    because it lists every connected account). Other operator-facing
    endpoints (/api/joiners_status, etc.) still return raw phones because
    the operator needs full identification in the dashboard — those
    endpoints should be gated via DASHBOARD_API_KEY + frontend X-Api-Key
    if the deployment is public.
    """
    if phone is None:
        return ""
    s = str(phone)
    if not s:
        return ""
    if len(s) <= 4:
        return "••••"
    # Keep the first 4 chars (usually the country prefix like "+966")
    # and the last 2 chars; mask the middle.
    return f"{s[:4]}{'•' * (len(s) - 6)}{s[-2:]}"


def _redact_sender_contact(contact) -> str:
    """[Security / PII] Redact a sender_contact value for open-dashboard mode.

    sender_contact comes from extract_sender_contact() and is either:
      - "📱 +9665xxxxxxx"  → a phone posted in the message → MASK the digits
      - "✈️ @username"      → a public Telegram handle → keep as-is (not PII)
      - "" / None          → ""

    Legacy rows may store bare digits without the emoji prefix — those are
    treated as phones and masked too. Never raises.
    """
    if contact is None:
        return ""
    s = str(contact).strip()
    if not s:
        return ""
    if s.startswith("\u2708\ufe0f"):
        # Telegram @username — a public handle, not PII.
        return s
    if s.startswith("\U0001F4F1"):
        # "📱 <phone>" — strip the emoji, mask the phone, re-attach.
        phone_part = s[2:].strip()
        return "\U0001F4F1 " + _redact_phone(phone_part)
    # Bare value: mask if it looks like a phone (digits with optional +),
    # otherwise return unchanged (free-text usernames etc.).
    cleaned = s.lstrip("+").replace(" ", "").replace("-", "")
    if cleaned.isdigit() and len(cleaned) >= 7:
        return _redact_phone(s)
    return s


@web.middleware
async def dashboard_api_key_middleware(request, handler):
    """Gates /api/* routes behind an optional X-Api-Key shared secret.

    [Task 5a / A1] Comparison uses secrets.compare_digest (constant-time) -
    was `provided != key` (string inequality, timing-attack vulnerable).

    [OPEN-MODE] When DASHBOARD_API_KEY is UNSET, /api/* routes are OPEN (no
    Origin check, no fail-closed) - the dashboard frontend reads data freely,
    per operator request (pre-PR-7 behavior). To re-secure, set
    DASHBOARD_API_KEY (then X-Api-Key is required).

    Health endpoints (/health, /ready, /metrics) are NOT under /api/* and
    are NEVER gated (Render's health probe + Prometheus must stay open).

    CORS preflight (OPTIONS) is ALWAYS allowed for /api/* so the browser can
    issue the actual authenticated GET/POST.
    """
    path = request.path
    if path.startswith("/api/"):
        # [DASHBOARD-RESTORE / CORS] OPTIONS preflight — always allow.
        # The browser sends OPTIONS before any cross-origin request that has
        # a non-safelisted header (e.g. X-Api-Key). Rejecting preflight makes
        # the actual request impossible, so we short-circuit here with the
        # CORS headers the browser needs. getattr is defensive: some test
        # mocks and malformed requests may omit .method; we treat that as
        # GET (the safe default — only OPTIONS is special-cased).
        if getattr(request, "method", "GET") == "OPTIONS":
            return web.json_response(
                {},
                status=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers":
                        "Accept, Content-Type, X-Api-Key, X-API-Key",
                    "Access-Control-Max-Age": "86400",
                },
            )
        key = _get_dashboard_api_key()
        if key is not None:
            provided = request.headers.get("X-Api-Key") or request.headers.get("X-API-Key")
            # [Task 5a / A1] constant-time compare — was `provided != key`
            # (timing attack). secrets.compare_digest returns False for
            # non-ASCII or non-str inputs without raising.
            try:
                ok = bool(provided) and secrets.compare_digest(
                    str(provided), str(key))
            except (TypeError, ValueError):
                ok = False
            if not ok:
                return web.json_response(
                    {"error": "unauthorized: missing or invalid X-Api-Key"},
                    status=401, headers={"Access-Control-Allow-Origin": "*"})
        else:
            # DASHBOARD_API_KEY UNSET -> OPEN (no Origin check, no fail-closed).
            # The dashboard frontend reads /api/* freely, per operator request
            # (pre-PR-7 behavior). To re-secure, set DASHBOARD_API_KEY.
            pass
    return await handler(request)


# ---------------------------------------------------------------------
# [DELETED-LINKS] /api/deleted_links — CRUD for the dashboard
# "لوحة الروابط المحذوفة من قبل الإدارة" panel.
# ---------------------------------------------------------------------

async def api_deleted_links_handler(request):
    """API endpoint: list + create deleted links.

    GET /api/deleted_links?admin=&reason=&search=&restored=&limit=
        -> {"links": [...], "stats": {"total": N, "by_admin": {...}, "by_reason": {...}}}
    POST /api/deleted_links (JSON body: original_link, link_type, source_group,
        sender_name, message_text, deleted_by, reason, note)
        -> {"id": N, "ok": true}
    """
    db = request.app.get("db")
    if not db:
        return web.json_response({"error": "not ready"}, status=503,
                                headers={"Access-Control-Allow-Origin": "*"})

    try:
        if request.method == "POST":
            try:
                body = await request.json()
            except Exception:
                return web.json_response(
                    {"error": "invalid JSON body"},
                    status=400,
                    headers={"Access-Control-Allow-Origin": "*"},
                )
            if not isinstance(body, dict):
                return web.json_response(
                    {"error": "body must be a JSON object"},
                    status=400,
                    headers={"Access-Control-Allow-Origin": "*"},
                )
            original_link = (body.get("original_link") or "").strip()
            if not original_link:
                return web.json_response(
                    {"error": "original_link is required"},
                    status=400,
                    headers={"Access-Control-Allow-Origin": "*"},
                )
            deleted_by = (body.get("deleted_by") or "manual").strip() or "manual"
            reason = (body.get("reason") or "manual").strip() or "manual"
            link_type = (body.get("link_type") or "other").strip() or "other"
            source_group = (body.get("source_group") or "").strip() or None
            sender_name = (body.get("sender_name") or "").strip() or None
            message_text = (body.get("message_text") or "").strip() or None
            note = (body.get("note") or "").strip() or None
            new_id = await db.insert_deleted_link(
                original_link=original_link,
                link_type=link_type,
                source_group=source_group,
                sender_name=sender_name,
                message_text=message_text,
                deleted_by=deleted_by,
                reason=reason,
                note=note,
            )
            return web.json_response(
                {"id": new_id, "ok": True},
                status=201,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        # GET
        admin = request.query.get("admin") or None
        reason = request.query.get("reason") or None
        search = request.query.get("search") or None
        restored_param = request.query.get("restored")
        restored: Optional[bool] = None
        if restored_param is not None:
            if restored_param.lower() in ("1", "true", "yes"):
                restored = True
            elif restored_param.lower() in ("0", "false", "no"):
                restored = False
        try:
            limit = int(request.query.get("limit", "100"))
        except ValueError:
            limit = 100
        links = await db.get_deleted_links(
            admin=admin, reason=reason, search=search,
            restored=restored, limit=limit,
        )
        stats = await db.get_deleted_links_stats()
        return web.json_response(
            {"links": links, "stats": stats, "count": len(links)},
            status=200,
            headers={"Access-Control-Allow-Origin": "*"},
        )
    except Exception as e:
        logging.error(f"[API] /api/deleted_links error: {e}")
        return web.json_response(
            {"error": str(e)},
            status=500,
            headers={"Access-Control-Allow-Origin": "*"},
        )


async def api_deleted_link_restore_handler(request):
    """POST /api/deleted_links/{id}/restore -> mark restored."""
    db = request.app.get("db")
    if not db:
        return web.json_response({"error": "not ready"}, status=503,
                                headers={"Access-Control-Allow-Origin": "*"})
    try:
        link_id_str = request.match_info.get("id")
        try:
            link_id = int(link_id_str)
        except (TypeError, ValueError):
            return web.json_response(
                {"error": "invalid id"},
                status=400,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        ok = await db.restore_deleted_link(link_id)
        if not ok:
            return web.json_response(
                {"ok": False, "error": "not found"},
                status=404,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        return web.json_response(
            {"ok": True, "id": link_id},
            status=200,
            headers={"Access-Control-Allow-Origin": "*"},
        )
    except Exception as e:
        logging.error(f"[API] restore deleted_link error: {e}")
        return web.json_response(
            {"error": str(e)},
            status=500,
            headers={"Access-Control-Allow-Origin": "*"},
        )


async def api_deleted_link_purge_handler(request):
    """DELETE /api/deleted_links/{id} -> permanent purge."""
    db = request.app.get("db")
    if not db:
        return web.json_response({"error": "not ready"}, status=503,
                                headers={"Access-Control-Allow-Origin": "*"})
    try:
        link_id_str = request.match_info.get("id")
        try:
            link_id = int(link_id_str)
        except (TypeError, ValueError):
            return web.json_response(
                {"error": "invalid id"},
                status=400,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        ok = await db.purge_deleted_link(link_id)
        if not ok:
            return web.json_response(
                {"ok": False, "error": "not found"},
                status=404,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        return web.json_response(
            {"ok": True, "id": link_id},
            status=200,
            headers={"Access-Control-Allow-Origin": "*"},
        )
    except Exception as e:
        logging.error(f"[API] purge deleted_link error: {e}")
        return web.json_response(
            {"error": str(e)},
            status=500,
            headers={"Access-Control-Allow-Origin": "*"},
        )


async def start_http_server(monitor=None, db=None):
    port = int(os.getenv("PORT", "10000"))
    app = web.Application()
    # [B06] register the optional X-Api-Key middleware (gates /api/* only)
    app.middlewares.append(dashboard_api_key_middleware)
    # Emit the one-time "API is open" warning if no key is configured.
    _warn_dashboard_api_key_open_once()
    # Attach monitor and db for health/readiness checks
    if monitor:
        app["monitor"] = monitor
    if db:
        app["db"] = db
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)      # liveness
    app.router.add_get("/ready", ready_handler)        # readiness
    app.router.add_get("/metrics", metrics_handler)    # Prometheus metrics
    app.router.add_get("/api/joined_groups", api_joined_groups_handler)
    app.router.add_get("/api/pending_approvals", api_pending_approvals_handler)  # [REQAUDIT-2]
    app.router.add_get("/api/links", api_links_handler)
    app.router.add_get("/api/links_daily", api_links_daily_handler)  # [TREND-VIEW]
    app.router.add_get("/api/top_groups", api_top_groups_handler)  # [SOURCE-VIEW]
    app.router.add_get("/api/top_senders", api_top_senders_handler)  # [SENDERS-VIEW]
    app.router.add_get("/api/group_detail", api_group_detail_handler)  # [GROUP-DRILL]
    app.router.add_get("/api/sender_detail", api_sender_detail_handler)  # [SENDER-DRILL]
    app.router.add_get("/api/stats", api_stats_handler)
    app.router.add_get("/api/deploy_check", api_deploy_check_handler)  # diagnostic
    app.router.add_get("/api/joiners_status", api_joiners_status_handler)  # joiners + groups
    app.router.add_get("/api/monitored_chats", api_monitored_chats_handler)  # monitored chats + AI
    app.router.add_get("/api/link_source_check", api_link_source_check_handler)  # check if source is monitored
    app.router.add_get("/api/polling_status", api_polling_status_handler)  # active polling status
    # [DELETED-LINKS] admin-moderated deleted links CRUD
    app.router.add_get("/api/deleted_links", api_deleted_links_handler)
    app.router.add_post("/api/deleted_links", api_deleted_links_handler)  # method-dispatch
    app.router.add_post("/api/deleted_links/{id}/restore", api_deleted_link_restore_handler)
    app.router.add_delete("/api/deleted_links/{id}", api_deleted_link_purge_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"HTTP server listening on port {port} (endpoints: /health /ready /metrics /api/joined_groups /api/pending_approvals /api/links /api/stats /api/deploy_check /api/monitored_chats /api/link_source_check)")
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
    # Log only a non-reversible prefix to confirm the token loaded, never the full token
    if config.bot_token:
        # [Security / Req-1] Log only the length — never any prefix/suffix of the
        # secret. (The redacting filter would catch the pattern below too, but we
        # avoid emitting any portion of the secret at the source.)
        logging.info(f"Bot token: loaded (len={len(config.bot_token)})")
    else:
        logging.warning("Bot token: NOT SET")
    logging.info(f"Channel ID: {config.channel_id}")
    if config.startup_scan_days is not None:
        logging.info(f"Startup scan: {config.startup_scan_days} days for each watcher")

    Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

    db = DatabaseManager()
    await db.init_db()
    # تهيئة جداول النظام الإنتاجي
    await init_production_tables(db)

    # ===== Migration: تأكد من وجود أعمدة role و joiner_enabled في Supabase =====
    logging.info("━" * 60)
    logging.info("[MIGRATION] Checking Supabase watchers schema (role, joiner_enabled)...")
    await db._supabase_ensure_schema()
    # اطبع جداول SQLite لإثبات عدم وجود watchers
    sqlite_tables_init = await db._sqlite_list_tables()
    logging.info(f"[MIGRATION] SQLite tables after init_db: {sqlite_tables_init}")
    if 'watchers' in sqlite_tables_init:
        logging.critical("[MIGRATION] ❌ BUG: 'watchers' table EXISTS in SQLite — should NOT!")
    else:
        logging.info("[MIGRATION] ✅ PROVEN: 'watchers' table does NOT exist in SQLite")
    logging.info("━" * 60)

    # ملاحظة: لا نضيف المالك تلقائياً - سيستخدم /login للتسجيل
    # هذا يحل مشكلة "فشل الحفظ" عند إضافة رقم المالك

    monitor = Monitor(config, db)

    # ملاحظة: Recovery Mode و join_paused تم نقلهما إلى monitor.start()
    # حيث تتم معالجتها بشكل صحيح بعد تشغيل الحسابات

    # 2. اقرأ floodwait_tracker — سجل الحسابات المحظورة
    blocked = await monitor.floodwait_mgr.get_blocked_accounts()
    if blocked:
        logging.warning(f"⚠️ {len(blocked)} accounts in FloodWait:")
        for b in blocked:
            wait = int(b['next_retry_at'] - time.time())
            logging.warning(f"   {b['phone']}: {wait}s remaining ({b.get('reason', 'unknown')})")
    else:
        logging.info("✅ No accounts in FloodWait")

    # 3. SIMULATION_MODE
    if monitor.simulation_mode:
        logging.info("🧪 SIMULATION MODE: All operations logged only, zero Telegram API")
    else:
        logging.info("📡 Production Mode: Real Telegram API calls enabled")

    # 4. Join limits (optimized to AVOID FloodWait)
    daily_limit = os.getenv('DAILY_JOIN_LIMIT', '45')
    logging.info(f"📊 Daily Join Limit: {daily_limit}/day")
    logging.info(f"📊 Hourly Join Limit: 5/hour (safe)")
    logging.info(f"📊 Join Cooldown: 120s (2 min between joins)")
    logging.info(f"⚠️  These limits are intentionally conservative to prevent FloodWait")

    # ===== Startup Verification — تأكد من وجود حسابات في Supabase =====
    logging.info("━" * 60)
    logging.info("[STARTUP VERIFICATION] Supabase = SOLE source of truth for accounts")
    logging.info("━" * 60)
    try:
        all_accounts = await db.get_active_watchers()
        monitors = [w for w in all_accounts if w.get('role', 'monitor') == 'monitor']
        joiners = [w for w in all_accounts if w.get('role') == 'joiner']
        supa_count_rest = await db._supabase_count_watchers()
        sqlite_tables = await db._sqlite_list_tables()
        has_watchers_table = 'watchers' in sqlite_tables

        logging.info(f"[STARTUP VERIFICATION] ════════════════════════════════════")
        logging.info(f"[STARTUP VERIFICATION]  Supabase accounts (is_active=true): {len(all_accounts)}")
        logging.info(f"[STARTUP VERIFICATION]  Supabase count (REST count=exact): {supa_count_rest}")
        logging.info(f"[STARTUP VERIFICATION]  Monitors: {len(monitors)}")
        logging.info(f"[STARTUP VERIFICATION]  Joiners:  {len(joiners)}")
        logging.info(f"[STARTUP VERIFICATION] ────────────────────────────────────")
        logging.info(f"[STARTUP VERIFICATION]  Account list (phone | role):")
        for w in all_accounts:
            logging.info(f"[STARTUP VERIFICATION]    → {w['phone']} (role={w.get('role', 'monitor')})")
        logging.info(f"[STARTUP VERIFICATION] ────────────────────────────────────")
        logging.info(f"[STARTUP VERIFICATION]  SQLite tables: {sqlite_tables}")
        logging.info(f"[STARTUP VERIFICATION]  'watchers' in SQLite: {'❌ YES (BUG!)' if has_watchers_table else '✅ NO (correct)'}")
        logging.info(f"[STARTUP VERIFICATION] ════════════════════════════════════")

        if has_watchers_table:
            logging.critical("[STARTUP VERIFICATION] ❌ FATAL: 'watchers' table exists in SQLite!")
            logging.critical("[STARTUP VERIFICATION] ❌ Supabase is NOT the sole source of truth. Aborting.")
            sys.exit(1)
    except RuntimeError as e:
        logging.critical("━" * 60)
        logging.critical(f"[STARTUP VERIFICATION] ❌ FATAL: {e}")
        logging.critical("[STARTUP VERIFICATION] ❌ Bot will NOT start with 0 accounts.")
        logging.critical("[STARTUP VERIFICATION] ❌ Add accounts via /login first, then redeploy.")
        logging.critical("━" * 60)
        sys.exit(1)
    logging.info("━" * 60)

    await monitor.start()
    http_runner = await start_http_server(monitor=monitor, db=db)

    # انتظر قليلاً ثم تحقق من الاتصال الفعلي
    await asyncio.sleep(5)
    connected_count = sum(1 for c in monitor.user_clients.values() if c and c.is_connected())
    total_accounts = len(monitor.user_clients)
    if connected_count == total_accounts and total_accounts > 0:
        logging.info(f"✅ Monitor started — {connected_count}/{total_accounts} accounts connected")
    elif connected_count > 0:
        logging.warning(f"⚠️ Monitor started — {connected_count}/{total_accounts} accounts connected (some failed)")
    else:
        logging.error(f"❌ Monitor started but 0 accounts connected — check sessions")

    shutdown = asyncio.Event()
    def sh(): shutdown.set()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, sh)
        except Exception:
            try: signal.signal(sig, lambda *_: sh())
            except Exception: pass
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
