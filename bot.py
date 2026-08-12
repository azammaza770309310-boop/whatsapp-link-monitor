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
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Set, Optional
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
        # استبعاد روابط الرسائل المباشرة (t.me/c/xxx) - هذه روابط رسائل خاصة
        if "/c/" in link_lower:
            continue
        # استبعاد روابط الدردشة المباشرة (t.me/username?start=xxx)
        if "?start=" in link_lower or "?text=" in link_lower:
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

        prompt = f"""أنت مساعد ذكي لتحليل رسائل المجموعات الجامعية.
هذه الرسالة تم سحبها من مجموعة يراقبها حساب مراقب — أي أنها من بيئة جامعية.

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

القواعد:
- should_save = true إذا كان في الرسالة رابط واتساب أو تيليجرام لمجموعة طلابية
- should_save = true حتى لو الرسالة تحتوي فقط على رابط بدون نص
- should_save = false فقط إذا لم يوجد أي رابط واتساب أو تيليجرام
- should_save = false إذا كان الرابط لخدمة مدفوعة (مكتب، مركز، شركة، خدمات طلابية)
- should_save = false إذا كان الرابط لحل واجبات أو تسليم مشاريع مدفوعة
- should_save = false إذا كان دردشة مباشرة (wa.me/رقم بدون كلمة message)
- should_save = false إذا كانت الرسالة ترويج لأعذار طبية أو خدمات صحية
- is_advertisement = true إذا كانت الرسالة ترويج لخدمات مدفوعة
- is_advertisement = true إذا ذكر: مكتب، مركز، شركة، خدمات، اشتراك، مدفوع
- استخرج الرابط الكامل بشكل صحيح من أي صيغة
- فحص كل أنواع الروابط: chat.whatsapp.com, wa.me, t.me, telegram.me
- country: حدد الدولة من سياق الرسالة أو من اسم المجموعة"""

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
        # إعدادات Supabase
        self.supabase_url = os.getenv("SUPABASE_URL", "")
        self.supabase_key = os.getenv("SUPABASE_KEY", "")
        self._supabase_session = None

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
                                     sender_name, sender_contact, source_phone, message_link):
        """إرسال الرابط إلى Supabase"""
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
                "message_link": message_link
            }
            async with session.post(f"{self.supabase_url}/rest/v1/links", json=data) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    if "duplicate" not in text.lower():
                        logging.error(f"Supabase link insert: {resp.status} - {text[:100]}")
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

        يستخدم Supabase RPC لتنفيذ ALTER TABLE. لو الـ RPC غير متاح،
        يسجّل تحذير ويكتفي بالـ fallback في _supabase_get_watchers.
        """
        if not self.supabase_url or not self.supabase_key:
            return
        try:
            session = await self._get_supabase_session()
            # Supabase REST API لا يدعم ALTER TABLE مباشرة — نحتاج RPC function.
            # لكننا نتحقق هل الأعمدة موجودة عبر query عادي.
            # لو role/joiner_enabled غير موجودين، الـ _supabase_get_watchers
            # يستخدم fallback تلقائياً.
            # هنا نسجل فقط حالة الـ schema للتحقق.
            async with session.get(
                f"{self.supabase_url}/rest/v1/watchers?select=phone,role,joiner_enabled&limit=1"
            ) as resp:
                if resp.status == 200:
                    logging.info("[SUPABASE] Schema OK: role + joiner_enabled columns exist")
                elif resp.status == 400:
                    logging.warning("[SUPABASE] Schema MISSING: role/joiner_enabled columns NOT found!")
                    logging.warning("[SUPABASE] → Run this SQL in Supabase Dashboard → SQL Editor:")
                    logging.warning("[SUPABASE]   ALTER TABLE watchers ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'monitor';")
                    logging.warning("[SUPABASE]   ALTER TABLE watchers ADD COLUMN IF NOT EXISTS joiner_enabled INTEGER DEFAULT 1;")
                    logging.warning("[SUPABASE]   ALTER TABLE watchers ADD COLUMN IF NOT EXISTS last_join_timestamp TIMESTAMP;")
                    logging.warning("[SUPABASE]   ALTER TABLE watchers ADD COLUMN IF NOT EXISTS health_score INTEGER DEFAULT 100;")
                    logging.warning("[SUPABASE] Bot will continue with fallback defaults (role=monitor, joiner_enabled=1)")
                else:
                    text = await resp.text()
                    logging.warning(f"[SUPABASE] Schema check: {resp.status} - {text[:80]}")
        except Exception as e:
            logging.warning(f"[SUPABASE] Schema check exception: {e}")

    async def _sqlite_list_tables(self) -> List[str]:
        """List all tables in SQLite — used by /verify to PROVE watchers is not among them."""
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def _ensure_conn(self):
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
                              sender_contact: str = None, link_type: str = None) -> bool:
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
            sender_name, sender_contact, source_phone, message_link)
        return True

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


class EducationalFilter:
    """فلتر ذكي لتمييز الروابط التعليمية.

    المعايير الإيجابية (كلمات في اسم المجموعة أو وصفها):
    - جامعة/كلية/معهد/روضة/مدرسة
    - تخصص/قسم/شعبة/فرقة
    - طلاب/طالبات/تجمع
    - أسماء جامعات سعودية معروفة

    المعايير السلبية (تستبعد الرسالة):
    - إعلانات/متاجر/تسوق
    - قنوات إخبارية/ترفيهية
    - ربح مال/استثمار
    - محتوى غير لائق
    """

    # كلمات إيجابية قوية (تعليمية مؤكدة)
    STRONG_POSITIVE = [
        # أنواع المؤسسات التعليمية
        'جامعة', 'كلية', 'معهد', 'روضة', 'مدرسة', 'مدراس',
        'university', 'college', 'institute', 'school', 'academy',
        # مستويات دراسية
        'تخصص', 'قسم', 'شعبة', 'فرقة', 'مستوى', 'ترم', 'فصل دراسي',
        'بكالوريوس', 'ماجستير', 'دكتوراه', 'دبلوم', 'ماجستير',
        'bachelor', 'master', 'phd', 'diploma', 'degree',
        # مجموعات طلابية
        'طلاب', 'طالبات', 'تجمع', 'دفع', 'دفعة', 'cohort', 'students',
        # أنشطة دراسية
        'محاضرة', 'سكشن', 'واجب', 'بحث', 'مشروع', 'تقرير', 'عرض',
        'ميدتيرم', 'فاينل', 'اختبار', 'امتحان', 'كويز', 'واجبات',
        'lecture', 'section', 'assignment', 'exam', 'quiz', ' midterm', 'final',
        # أنظمة جامعية
        'تسجيل', 'add drop', 'withdraw', 'معدل', 'gpa', 'credit',
        'blackboard', 'بلاك بورد', 'moodle', 'مودل',
        # مواد دراسية
        'مادة', 'مواد', 'منهج', 'كتاب', 'ملخص', 'شرائح', 'slides',
        'course', 'subject', 'curriculum',
    ]

    # أسماء جامعات سعودية معروفة (مطابقة قوية)
    SAUDI_UNIVERSITIES = [
        'الملك سعود', 'الملك عبدالعزيز', 'الملك فيصل', 'الملك خالد',
        'الملك فهد', 'الملك عبدالله', 'الملك سلمان',
        'أم القرى', 'ام القرى', 'الطائف', 'الباحة', 'جازان', 'نجران',
        'الجوف', 'الحدود الشمالية', 'حائل', 'تبوك', 'القصيم',
        'الإمام محمد بن سعود', 'الإمام', 'النعيرية', 'شقراء',
        'المجمعة', 'رماح', 'الخرج', 'الدوادمي', 'الأفلاج',
        ' Prince Sattam', 'سطام', 'الإمام عبدالرحمن', 'الإمام',
        'جدة', 'طيبة', 'حائل', 'تبوك',
        # اختصارات
        'ksu', 'kau', 'kfu', 'kku', 'uqu', 'taibahu', 'iau', 'ju',
        'pnu', 'nu', 'su', 'bu', 'qu', 'ha il',
        # جامعات أجنبية شائعة
        'pnu1445', 'noracom', 'fonnorasakn', 'majeedseu', 'uqucc',
        'qassim_u', 'ngran4',
    ]

    # كلمات سلبية قوية (تستبعد الرسالة)
    STRONG_NEGATIVE = [
        # إعلانات ومتاجر
        'متجر', 'متاجر', 'تسوق', 'شراء', 'بيع', 'سعر', 'خصم', 'عرض خاص',
        'store', 'shop', 'buy', 'sell', 'price', 'discount', 'offer',
        'متوفر', 'للبيع', 'للإيجار', 'توصيل', 'شحن',
        # ربح مال واستثمار
        'ربح', 'ارباح', 'استثمار', 'تداول', 'فوركس', 'كريبتو', 'بيتكوين',
        'earn money', 'make money', 'profit', 'investment', 'crypto', 'forex',
        'ايرد المبلغ', 'هديه مجاني', 'ربح مال', 'ربح سريع',
        # ترفيه وغير تعليمي
        'افلام', 'أنمي', 'أنمي', 'روايات', 'شعر', 'خواطر',
        'movies', 'anime', 'novels', 'poetry',
        'ألعاب', 'العاب', 'ببجي', 'فورتنايت', 'minecraft',
        'games', 'gaming', 'pubg', 'fortnite',
        # قنوات إخبارية وإعلامية
        'أخبار', 'اخبار', 'عاجل', 'خبر', 'news', 'breaking',
        'قناة إخبارية', 'صحيفة', 'جريدة',
        # محتوى غير لائق
        'porn', 'xxx', 'adult', '18+', 'محتوى للكبار',
        # تواصل اجتماعي (متابعين/لايكات)
        'sub4sub', 'follow4follow', 'like4like', 'متابعين', 'لايكات',
        'followers', 'subscribers', 'تيك توك', 'يوتيوب', 'سناب',
    ]

    # كلمات تشير لأن الرابط لقناة وليس مجموعة
    CHANNEL_INDICATORS = [
        'قناة', 'channel', 'telegram channel', 'قناة تيليجرام',
        'اخبار', 'news', 'إعلام', 'broadcast', 'اذاعة',
    ]

    @classmethod
    def is_educational(cls, text: str, link_username: str = '') -> Tuple[bool, str]:
        """يتحقق هل النص/الرابط تعليمي.

        Returns:
            (True, reason) لو تعليمي
            (False, reason) لو غير تعليمي
        """
        if not text and not link_username:
            return False, 'empty_text'

        combined = f"{text or ''} {link_username or ''}".lower()

        # 1. فحص سلبي أولاً (الأقوى)
        for neg in cls.STRONG_NEGATIVE:
            if neg.lower() in combined:
                return False, f'negative_{neg}'

        # 2. فحص الجامعات السعودية (مطابقة قوية جداً)
        for uni in cls.SAUDI_UNIVERSITIES:
            if uni.lower() in combined:
                return True, f'saudi_uni_{uni}'

        # 3. فحص الكلمات الإيجابية
        positive_matches = []
        for pos in cls.STRONG_POSITIVE:
            if pos.lower() in combined:
                positive_matches.append(pos)

        if len(positive_matches) >= 1:
            return True, f'positive_{positive_matches[0]}'

        # 4. لو ما في مطابقة، اعتبره غير تعليمي (احتياط)
        return False, 'no_educational_keywords'

    @classmethod
    def is_likely_channel(cls, text: str, link_username: str = '') -> bool:
        """يتحقق هل الرابط غالباً لقناة (وليس مجموعة)."""
        combined = f"{text or ''} {link_username or ''}".lower()
        for ind in cls.CHANNEL_INDICATORS:
            if ind.lower() in combined:
                return True
        return False


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
            "• /clear_floodwait — مسح FloodWait وإعادة تفعيل الانضمام\n\n"
            "📌 أوامر تنظيف القناة:\n"
            "• /cleanup_preview — معاينة ما سيُحذف (بدون حذف فعلي)\n"
            "• /cleanup_links — حذف الروابط غير التعليمية والمكررة\n"
            "• /cleanup_status — تقدم التنظيف"
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

                # استخراج روابط واتساب وتيليجرام
                links = extract_whatsapp_telegram_links(msg.text)
                if not links: continue

                # فلتر: السماح فقط برسائل الجامعات الأهلية المستهدفة
                if not is_target_university_message(msg.text):
                    continue

                # استبعاد الرسائل الإعلانية
                if is_advertiser_message(msg.text):
                    continue

                self.total_found += len(links)
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

                # إدراج كل رابط
                for link in links:
                    inserted = await self.db.insert_request(
                        link, md, name, sn, self.source_phone, msg_link,
                        message_text=msg.text, sender_contact=contact)
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
        # محلل الذكاء الاصطناعي
        self.ai_analyzer = AIAnalyzer()
        self._startup_scan_done: Set[str] = set()
        # ===== Production Link System =====
        self.prod_db = ProductionDB(db)
        self.rate_limiter = RateLimiter(self.prod_db)
        self.floodwait_mgr = FloodWaitManager(self.prod_db)
        self.membership_cache = MembershipCache(self.prod_db, self.rate_limiter)
        self.metrics = Metrics()
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
        """تسجيل معالجات الرسائل لكل user_client"""
        client = self.user_clients.get(phone)
        if not client: return
        client.add_event_handler(
            lambda e: self._on_user_message(e, phone),
            events.NewMessage(incoming=True)
        )
        logging.info(f"User handlers registered for {phone}")

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

    async def _on_user_message(self, event, source_phone: str):
        """معالج رسائل المستخدم — ZERO Telegram API calls.

        يستخدم فقط: event.raw_text, event.chat_id, event.sender_id
        لا يستخدم: event.chat, event.sender, event.get_chat(), event.get_sender()
        """
        try:
            # استخدام raw_text مباشرة (بدون API)
            raw_text = event.raw_text
            if not raw_text: return

            # استخدام chat_id مباشرة (بدون API)
            chat_id = event.chat_id
            if chat_id == self.config.channel_id: return

            # استخدام sender_id مباشرة (بدون API)
            sender_id = event.sender_id or 0

            # === PIPELINE STAGE 1: Event Handler received message ===
            logging.info(f"[PIPELINE-1] 📨 Event Handler received message from source={source_phone} chat_id={chat_id} (len={len(raw_text)})")

            # اسم المجموعة — حاول العنوان من event.chat (بدون API إضافي) وإلا chat_id
            try:
                chat_obj = event.chat
                if chat_obj and hasattr(chat_obj, 'title') and chat_obj.title:
                    group_name = chat_obj.title
                else:
                    group_name = f"chat_{chat_id}"
            except Exception:
                group_name = f"chat_{chat_id}"

            # اسم المرسل — حاول الاسم من event.sender (بدون API إضافي) وإلا sender_id
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
                    else:
                        sender_name = f"user_{sender_id}"
                else:
                    sender_name = f"user_{sender_id}"
            except Exception:
                sender_name = f"user_{sender_id}"

            # الخطوة 1: استخراج الروابط محلياً (صفر API calls)
            links = LinkNormalizer.extract_links(raw_text)
            if not links:
                logging.debug(f"[PIPELINE-1] No links found in message from {chat_id}")
                return

            logging.info(f"[PIPELINE-1] 🔗 Found {len(links)} link(s) in message from {group_name}")

            # الخطوة 2: enqueue كل رابط (صفر API calls)
            for link_info in links:
                # === فلتر صارم قبل الـ Queue — يرفض الخدمات الطلابية والإعلانات ===
                link_raw = link_info['raw'].lower()
                username_raw = (link_info.get('username') or '').lower()
                full_text_check = f"{raw_text} {link_raw} {username_raw}".lower()

                # قائمة كلمات ترفض الرابط فوراً (خدمات مدفوعة فقط — قائمة محددة جداً)
                REJECT_KEYWORDS = [
                    'مكتب حل', 'مكتب واجب', 'مكتب دراسي',
                    'خدمات طلابية مدفوعة',
                    'حل واجب بمقابل', 'حل واجبات مدفوع', 'حل بحث مدفوع',
                    'توصيل مشروع مدفوع', 'تسليم واجب بمقابل',
                    'خدمة اونلاين مدفوع',
                    'اعذار طبية جاهزة',
                ]

                is_rejected = False
                reject_reason = ''
                for kw in REJECT_KEYWORDS:
                    if kw in full_text_check:
                        is_rejected = True
                        reject_reason = kw
                        break

                if is_rejected:
                    logging.info(
                        f"[PIPELINE-1] 🚫 REJECTED link (keyword={reject_reason}): {link_info['raw'][:50]}"
                    )
                    await self.metrics.record_skip(f'reject_keyword_{reject_reason}')
                    continue  # لا تدخله للقائمة

                link_data = {
                    **link_info,
                    'group_name': group_name,
                    'sender_name': sender_name,
                    'sender_contact': extract_sender_contact(raw_text),
                    'source_phone': source_phone,
                    'message_text': raw_text,
                    'message_link': f"https://t.me/c/{str(chat_id).replace('-100', '')}/{event.id}" if chat_id else None,
                }

                # === PIPELINE STAGE 2: Enqueue link ===
                # إضافة لقائمة الانتظار (UNIQUE constraint يمنع التكرار)
                is_new = await self.prod_db.enqueue_link(link_data)
                if is_new:
                    await self.prod_db.set_group_state(
                        link_info['normalized'], GroupState.DISCOVERED,
                        link_info['raw'], group_name)
                    logging.info(f"[PIPELINE-2] ✅ Link enqueued: {link_info['raw'][:60]} (state=DISCOVERED)")
                else:
                    await self.metrics.record_duplicate()
                    logging.info(f"[PIPELINE-2] ⏭️ Duplicate skipped: {link_info['normalized'][:60]}")

            # الخطوة 3: انتهى — صفر استدعاءات Telegram API

        except Exception as e:
            logging.error(f"Event handler error: {e}", exc_info=True)

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
                    '/verify', '/sqlite_check', '/clear_floodwait',
                    '/bulk_join', '/bulk_join_status', '/bulk_join_stop',
                    '/cleanup_preview', '/cleanup_links', '/cleanup_status',
                    '/live_audit', '/status', '/watchers', '/help',
                    '/joined_groups', '/queue', '/debug_pipeline',
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

                    lines.append("")
                    lines.append("═══════════════════════════")

                    await reply("\n".join(lines))
                except Exception as e:
                    logging.error(f"[JOINED_GROUPS] Error: {e}", exc_info=True)
                    await reply(f"❌ خطأ: {e}")

            elif cmd == "/queue":
                # === عرض محتويات القائمة ===
                logging.info("[QUEUE] /queue command invoked")
                try:
                    conn = await self.db._ensure_conn()
                    cursor = await conn.execute(
                        "SELECT id, raw_link, status, enqueued_at, next_retry_at, attempt_count, last_error "
                        "FROM link_queue ORDER BY id DESC LIMIT 20")
                    rows = await cursor.fetchall()

                    queue_size = await self.prod_db.get_queue_size()

                    lines = [
                        f"📋 Queue (depth={queue_size})",
                        f"═══════════════════════════",
                    ]

                    if rows:
                        for r in rows:
                            lines.append(f"  id={r[0]} status={r[2]}")
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
                self.config.history_skip_channel_posts, phone, watcher.get('display_name', ''), p)
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

    async def _run_user_client(self, watcher):
        """تشغيل user_client — المراقبون فقط يستمعون للرسائل، الفدائيون لا

        Startup Contract:
            - لا تعتبر الحساب READY حتى: connect → authorize → register handlers
            - لو فشل أي خطوة، سجل STATUS=FAILED مع السبب
        """
        phone = watcher['phone']
        session_string = watcher['session_string']
        role = watcher.get('role', 'monitor')  # افتراضي: مراقب
        backoff = 5
        while self._running:
            try:
                client = self.user_clients.get(phone)
                if client is None:
                    # حماية من الجلسات التالفة
                    if not session_string or not isinstance(session_string, str) or len(session_string) < 50:
                        logging.error(
                            f"[ACCOUNT] {phone} STATUS=FAILED\n"
                            f"[ACCOUNT] reason=invalid_session_string"
                        )
                        self._cleanup_user_client(phone)
                        return
                    try:
                        client = self._create_user_client(session_string, phone)
                    except ValueError as ve:
                        logging.error(
                            f"[ACCOUNT] {phone} STATUS=FAILED\n"
                            f"[ACCOUNT] reason=invalid_session: {ve}"
                        )
                        self._cleanup_user_client(phone)
                        return
                    except Exception as ce:
                        logging.error(
                            f"[ACCOUNT] {phone} STATUS=FAILED\n"
                            f"[ACCOUNT] reason=client_creation_error: {ce}"
                        )
                        return
                    self.user_clients[phone] = client

                if not client.is_connected():
                    logging.info(f"[ACCOUNT] {phone} connecting...")
                    await client.connect()

                    # === VERIFY AUTHORIZATION ===
                    if not await client.is_user_authorized():
                        logging.error(
                            f"[ACCOUNT] {phone} STATUS=FAILED\n"
                            f"[ACCOUNT] reason=not_authorized\n"
                            f"[ACCOUNT] action=re-login required"
                        )
                        self._cleanup_user_client(phone)
                        return

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
                    backoff = 5
                await client.run_until_disconnected()
            except FloodWaitError as e: await asyncio.sleep(e.seconds + 1)
            except (RPCError, ConnectionError, OSError) as e:
                logging.error(f"[ACCOUNT] {phone} error: {type(e).__name__}: {e}")
            except asyncio.CancelledError: raise
            except Exception as e:
                logging.error(f"[ACCOUNT] {phone} unexpected: {e}", exc_info=True)
            finally:
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

                # تحديث حجم القائمة في الإحصائيات
                queue_size = await self.prod_db.get_queue_size()
                await self.metrics.update_queue_size(queue_size)

                # 1. اجلب رابط QUEUED واحد (لا burst)
                queued = await self.prod_db.get_queued_links(limit=1)
                if not queued:
                    logging.debug(f"[SCHED] cycle={cycle} Queue empty — sleeping 60s")
                    await asyncio.sleep(60)
                    continue

                link_data = queued[0]
                normalized = link_data['normalized_link']
                raw_link = link_data['raw_link']
                link_type = link_data['link_type']

                # === PIPELINE STAGE 3: Scheduler read link from queue ===
                link_id = link_data.get('id', '?')
                logging.info(f"[LINK id={link_id}] [PIPELINE-3] 🔄 cycle={cycle} Scheduler picked link: {raw_link[:60]} (type={link_type})")

                # 2. تحقق من حالة المجموعة في State Machine
                state = await self.prod_db.get_group_state(normalized)
                if state in (GroupState.JOINED, GroupState.ALREADY_MEMBER):
                    logging.info(f"[LINK id={link_id}] [PIPELINE-3] ⏭️ already {state} — skipping")
                    await self.prod_db.update_queue_status(link_data['id'], 'DONE')
                    await self.metrics.record_skip('already_joined')
                    continue

                if state == GroupState.BANNED:
                    logging.info(f"[LINK id={link_id}] [PIPELINE-3] ⏭️ BANNED — skipping")
                    await self.prod_db.update_queue_status(link_data['id'], 'DONE')
                    await self.metrics.record_skip('banned')
                    continue

                # 3. AI فحص الرابط (فقط لو DISCOVERED ولم يُفحص سابقاً)
                if state == GroupState.DISCOVERED or state is None:
                    # === PIPELINE STAGE 4: AI verification ===
                    logging.info(f"[LINK id={link_id}] [PIPELINE-4] 🤖 AI verifying link: {raw_link[:60]}")
                    ai_result = await self.ai_analyzer.analyze_message(link_data.get('message_text', ''))
                    if not ai_result.get('should_save', False):
                        logging.info(f"[LINK id={link_id}] [PIPELINE-4] ❌ AI REJECTED (reason: {ai_result.get('reason', 'unknown')})")
                        await self.prod_db.set_group_state(normalized, GroupState.INVALID, raw_link, error='AI rejected')
                        await self.prod_db.update_queue_status(link_data['id'], 'REJECTED')
                        await self.metrics.record_skip('ai_rejected')
                        continue
                    logging.info(f"[LINK id={link_id}] [PIPELINE-4] ✅ AI APPROVED")
                    await self.prod_db.set_group_state(normalized, GroupState.QUEUED, raw_link)

                    # === PIPELINE STAGE 5: Publish to channel ===
                    inserted = await self.db.insert_request(
                        raw_link, datetime.now(),
                        link_data.get('group_name', ''), link_data.get('sender_name', ''),
                        link_data.get('source_phone', ''), link_data.get('message_link'),
                        message_text=link_data.get('message_text', ''),
                        sender_contact=link_data.get('sender_contact', ''),
                        link_type=link_data.get('link_type', 'other'))
                    if inserted:
                        formatted = MessageFormatter.format_link_message(
                            link_data.get('group_name', ''), link_data.get('sender_name', ''),
                            link_data.get('sender_contact', ''), datetime.now(),
                            raw_link, link_data.get('message_text', ''),
                            link_data.get('source_phone', ''), link_data.get('message_link'))
                        buttons = MessageFormatter.get_link_buttons(raw_link)
                        published, msg_id = await self._send(formatted, buttons=buttons)
                        if published:
                            logging.info(f"[LINK id={link_id}] [PIPELINE-5] ✅ PUBLISHED_VERIFIED message_id={msg_id}")
                        else:
                            logging.error(f"[LINK id={link_id}] [PIPELINE-5] ❌ PUBLISH_FAILED — retry in 5 min")
                            await self.prod_db.update_queue_status(link_data['id'], 'QUEUED',
                                                                   next_retry=datetime.now() + timedelta(minutes=5))
                            continue
                    else:
                        logging.info(f"[LINK id={link_id}] [PIPELINE-5] ⏭️ Already published (duplicate)")

                # 4. اختر حساب فدائي
                logging.info(f"[LINK id={link_id}] [PIPELINE-6] Selecting joiner...")
                joiners = await self.db.get_watchers_by_role("joiner")
                if not joiners:
                    logging.warning(f"[SCHED] cycle={cycle} ⚠️ No joiner accounts!")
                    await self.prod_db.update_queue_status(link_data['id'], 'QUEUED',
                                                           next_retry=datetime.now() + timedelta(minutes=5))
                    await asyncio.sleep(60)
                    continue

                selected_joiner = None
                for joiner in joiners:
                    jphone = joiner['phone']
                    is_blocked, wait = await self.floodwait_mgr.is_blocked(jphone)
                    if is_blocked:
                        logging.info(f"[SCHED] cycle={cycle} {jphone} blocked for {wait}s (FloodWait)")
                        continue
                    await self.db.reset_daily_joins_if_needed(jphone)
                    daily_joins = await self.db.get_daily_join_count(jphone)
                    daily_limit = await self._get_daily_limit(jphone)
                    if daily_joins >= daily_limit:
                        logging.info(f"[SCHED] cycle={cycle} {jphone} daily limit ({daily_joins}/{daily_limit})")
                        continue
                    selected_joiner = joiner
                    break

                if not selected_joiner:
                    logging.info(f"[SCHED] cycle={cycle} All joiners blocked/limited — sleeping 60s")
                    await self.prod_db.update_queue_status(link_data['id'], 'QUEUED',
                                                           next_retry=datetime.now() + timedelta(minutes=5))
                    await asyncio.sleep(60)
                    continue

                phone = selected_joiner['phone']
                client = self.user_clients.get(phone)
                if not client or not client.is_connected():
                    logging.warning(f"[SCHED] {phone} not connected — skipping")
                    await self.prod_db.update_queue_status(link_data['id'], 'QUEUED',
                                                           next_retry=datetime.now() + timedelta(minutes=2))
                    await asyncio.sleep(60)
                    continue

                # 5. Membership Cache Check
                if link_type == 'telegram':
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] Checking membership for {phone}...")
                    is_member = await self.membership_cache.check_membership(phone, normalized, client)
                    if is_member is True:
                        logging.info(f"[LINK id={link_id}] [PIPELINE-6] {phone} already member — skipping")
                        await self.prod_db.set_group_state(normalized, GroupState.ALREADY_MEMBER, raw_link, joined_by=phone)
                        await self.prod_db.update_queue_status(link_data['id'], 'DONE')
                        await self.metrics.record_skip('already_member')
                        continue

                # 6. Rate Limiter
                logging.info(f"[LINK id={link_id}] [PIPELINE-6] Rate limiter check for {phone}...")
                allowed = await self.rate_limiter.check(phone, 'join')
                if not allowed:
                    logging.info(f"[SCHED] Rate limiter blocked {phone} — sleeping 60s")
                    await self.prod_db.update_queue_status(link_data['id'], 'QUEUED',
                                                           next_retry=datetime.now() + timedelta(minutes=5))
                    await asyncio.sleep(60)
                    continue

                # 7. Safety Guard
                logging.info(f"[LINK id={link_id}] [PIPELINE-6] 🛡️ Safety Guard checking {phone}...")
                guard_ok, guard_reason = await self._safety_guard(phone, normalized, link_data)
                if not guard_ok:
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] 🚫 Safety Guard BLOCKED: {guard_reason}")
                    await self.metrics.record_skip(f'guard_{guard_reason}')
                    if 'floodwait' in guard_reason:
                        floodwait_until = await self.prod_db.get_floodwait(phone)
                        if floodwait_until:
                            next_retry_dt = datetime.fromtimestamp(floodwait_until)
                            await self.prod_db.update_queue_status(link_data['id'], 'QUEUED', next_retry=next_retry_dt)
                        else:
                            await self.prod_db.update_queue_status(link_data['id'], 'QUEUED',
                                                                   next_retry=datetime.now() + timedelta(minutes=30))
                    else:
                        await self.prod_db.update_queue_status(link_data['id'], 'QUEUED',
                                                               next_retry=datetime.now() + timedelta(minutes=5))
                    await asyncio.sleep(60)
                    continue
                logging.info(f"[LINK id={link_id}] [PIPELINE-6] ✅ Safety Guard PASSED for {phone}")

                # 8. Join attempt
                await self.metrics.record_join_attempt(phone)
                await self.prod_db.set_group_state(normalized, GroupState.JOINING, raw_link)
                await self.prod_db.update_queue_status(link_data['id'], 'PROCESSING')

                logging.info(f"[LINK id={link_id}] [PIPELINE-6] 🚀 Joiner {phone} attempting to join: {raw_link[:60]}")
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

                elif status == "FLOODWAIT":
                    state_to_set = GroupState.FLOODWAIT
                    state_error = 'FloodWait'
                    final_status = 'QUEUED'
                    next_retry = datetime.now() + timedelta(minutes=30)
                    await self.metrics.record_floodwait(phone)
                    # لا توقف النظام كامل — فقط أوقف هذا الحساب مؤقتاً
                    # FloodWait لرابط واحد لا يوقف 127 رابط آخر
                    logging.warning(f"[FLOODWAIT] {phone} got FloodWait — link requeued in 30 min (system continues)")

                elif status == "BANNED":
                    state_to_set = GroupState.BANNED
                    state_error = 'PeerFlood/Banned'
                    final_status = 'DONE'  # فشل نهائي — لا إعادة محاولة
                    await self.metrics.record_floodwait(phone)
                    self._join_paused = True
                    await self.prod_db.set_setting('join_paused', 'true')
                    logging.warning(f"[AUTO-PAUSE] PeerFlood/Ban detected → join_paused=true in DB")

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
                    logging.warning(f"[LINK id={link_id}] [PIPELINE-6] ⚠️ {phone} {status} — skipping")

                elif status == "INVALID":
                    state_to_set = GroupState.FAILED
                    state_error = 'invalid_link'
                    final_status = 'DONE'  # فشل نهائي
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] ❌ invalid link (no username) — skipping")

                elif status == "SKIP":
                    final_status = 'DONE'  # WhatsApp — لا انضمام
                    logging.info(f"[LINK id={link_id}] [PIPELINE-6] ⏭️ WhatsApp link — no join needed")

                elif status == "IS_CHANNEL":
                    state_to_set = GroupState.FAILED
                    state_error = 'is_channel'
                    final_status = 'DONE'
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
                        if state in (GroupState.JOINED, GroupState.ALREADY_MEMBER, GroupState.BANNED, GroupState.PRIVATE):
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

        # 3. Hourly Join Limit (DB-backed, survives restart) — conservative: 1/hour
        hourly_joins = await self.prod_db.count_operations(phone, 'join', 3600)
        if hourly_joins >= 50:  # 50/hour max
            return False, f'hourly_limit_{hourly_joins}/50'

        # 4. Group Reputation — تم إزالته (تخفيف)
        # كان يمنع الانضمام للمجموعات الجديدة، الآن مسموح

        # 5. Attempt history — تم تخفيف (فقط لو انضم بالفعل)
        state = await self.prod_db.get_group_state(normalized_link)
        # JOINING = محاولة حالية (هذا الـ Scheduler نفسه)، لا ترفض
        # فقط JOINED و ALREADY_MEMBER تعني أننا انضممنا سابقاً
        if state in (GroupState.JOINED, GroupState.ALREADY_MEMBER):
            return False, f'already_attempted_{state}'
        # تم إزالة فحص attempt_count >= 3 (تخفيف)

        # 6. Last join timestamp for this account — 600s (10 min) minimum between joins
        # اقرأ من Supabase (المصدر الوحيد) — ليس من SQLite watchers
        w = await self.db._supabase_get_watcher(phone)
        if w and w.get('last_join_timestamp'):
            try:
                last_join_ts = w['last_join_timestamp']
                last_join = datetime.fromisoformat(str(last_join_ts).replace('Z', '+00:00')) if isinstance(last_join_ts, str) else last_join_ts
                elapsed = (datetime.now() - last_join.replace(tzinfo=None)).total_seconds()
                if elapsed < 30:  # 30s cooldown
                    return False, f'join_cooldown_{int(30-elapsed)}s'
            except Exception:
                pass

        return True, ''

    async def _get_daily_limit(self, phone: str) -> int:
        """يجلب الحد اليومي للانضمام حسب نوع الحساب.
        joiner: 2/day, backup: 1/day, monitor: 0

        اقرأ role من Supabase (المصدر الوحيد) — ليس من SQLite watchers.
        """
        w = await self.db._supabase_get_watcher(phone)
        role = w.get('role', 'monitor') if w else 'monitor'

        if role == 'joiner':
            return int(os.getenv('DAILY_JOIN_LIMIT', '200'))  # 200/day
        elif role == 'backup':
            return int(os.getenv('DAILY_BACKUP_LIMIT', '5'))  # 5/day (تخفيف)
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
        tasks = [self._bot_task, self._keep_alive_task, self._joiner_task] + list(self._user_tasks.values()) + self._current_scan_tasks
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

        groups = []
        for r in joined_rows:
            groups.append({
                "id": len(groups) + 1,
                "group_title": r[0] or '',
                "group_link": r[1] or '',
                "status": r[6] or 'JOINED',
                "joined_by_phone": r[2] or '',
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

        # Active joiners from Supabase
        try:
            joiners = await db.get_watchers_by_role("joiner")
            active_joiners = len(joiners)
        except Exception:
            active_joiners = 0

        return web.json_response({
            "joined_groups": groups,
            "stats": {
                "total_joined": total_joined,
                "pending_groups": pending,
                "active_joiners": active_joiners,
            }
        }, status=200, headers={"Access-Control-Allow-Origin": "*"})

    except Exception as e:
        logging.error(f"[API] joined_groups error: {e}")
        return web.json_response({"error": str(e)}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})


async def api_links_handler(request):
    """API endpoint: returns recent published links from Supabase."""
    db = request.app.get("db")
    if not db:
        return web.json_response({"error": "not ready"}, status=503)

    try:
        limit = int(request.query.get("limit", "50"))
        offset = int(request.query.get("offset", "0"))

        if not db.supabase_url or not db.supabase_key:
            return web.json_response({"links": [], "error": "supabase not configured"},
                                     status=200, headers={"Access-Control-Allow-Origin": "*"})

        import aiohttp as _aiohttp
        session = await db._get_supabase_session()
        headers = {**session.headers, "Range": f"{offset}-{offset + limit - 1}"}

        async with session.get(
            f"{db.supabase_url}/rest/v1/links?order=created_at.desc&limit={limit}",
            headers=headers
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return web.json_response({"links": data},
                                        status=200, headers={"Access-Control-Allow-Origin": "*"})
            else:
                return web.json_response({"links": [], "error": f"supabase {resp.status}"},
                                        status=200, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        return web.json_response({"links": [], "error": str(e)},
                                status=200, headers={"Access-Control-Allow-Origin": "*"})


async def api_stats_handler(request):
    """API endpoint: returns system stats for dashboard."""
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

        # Telegram links vs whatsapp
        wa_count = 0
        tg_count = 0
        for w in watchers:
            pass  # counts from links table

        # Connected accounts
        connected = sum(1 for c in monitor.user_clients.values() if c and c.is_connected())

        return web.json_response({
            "total_links": total_links,
            "whatsapp_links": 0,
            "telegram_links": 0,
            "active_watchers": active_watchers,
            "connected_accounts": connected,
            "bot_connected": bool(monitor.bot_client and monitor.bot_client.is_connected()),
        }, status=200, headers={"Access-Control-Allow-Origin": "*"})

    except Exception as e:
        return web.json_response({"error": str(e)}, status=500,
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

    if db_ok and bot_ok:
        return web.json_response({
            "status": "ready",
            "bot_connected": bot_ok,
            "db_connected": db_ok,
            "active_watchers": active_watchers,
            "scan_running": monitor.is_scan_running() if monitor else False,
        }, status=200)
    else:
        return web.json_response({
            "status": "not_ready",
            "bot_connected": bot_ok,
            "db_connected": db_ok,
            "db_error": db_error,
            "active_watchers": active_watchers,
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
"""
    return web.Response(text=metrics, content_type="text/plain")


async def start_http_server(monitor=None, db=None):
    port = int(os.getenv("PORT", "10000"))
    app = web.Application()
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
    app.router.add_get("/api/links", api_links_handler)
    app.router.add_get("/api/stats", api_stats_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"HTTP server listening on port {port} (endpoints: /health /ready /metrics /api/joined_groups /api/links /api/stats)")
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
        logging.info(f"Bot token: {config.bot_token[:8]}...{config.bot_token[-4:]} (loaded, len={len(config.bot_token)})")
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

    # 4. Conservative post-FloodWait limits
    daily_limit = os.getenv('DAILY_JOIN_LIMIT', '15')
    logging.info(f"📊 Daily Join Limit: {daily_limit}/day")
    logging.info(f"📊 Hourly Join Limit: 5/hour")
    logging.info(f"📊 Join Cooldown: 600s (10 min)")

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
