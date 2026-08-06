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
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Set
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
حلل هذه الرسالة بدقة كأنك إنسان:

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

القواعد الصارمة:
- should_save = true فقط إذا كان الرابط لمجموعة أو قناة تعليمية جامعية
- should_save = false إذا كان الرابط غير تعليمي (تسوق، ترفيه، أخبار، إلخ)
- should_save = false إذا كان دردشة مباشرة (wa.me/رقم بدون /message)
- is_advertisement = true إذا كانت الرسالة ترويج لخدمات مدفوعة أو إعلان
- is_advertisement = true إذا كان الرابط لخدمة مدفوعة (مكتب، مركز، شركة)
- sender_contact = رقم الهاتف أو @اليوزر المذكور في الرسالة
- استخرج الرابط الكامل بشكل صحيح من أي صيغة
- فحص كل أنواع الروابط: chat.whatsapp.com, wa.me, t.me, telegram.me
- إذا لم يوجد رابط واتساب أو تيليجرام، should_save = false
- country: حدد الدولة بدقة من سياق الرسالة"""

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

    async def _supabase_add_watcher(self, phone, display_name, session_string):
        """إرسال المراقب إلى Supabase"""
        if not self.supabase_url or not self.supabase_key:
            return
        try:
            session = await self._get_supabase_session()
            data = {
                "phone": phone,
                "display_name": display_name,
                "session_string": session_string,
                "is_active": True
            }
            # محاولة إدراج
            async with session.post(f"{self.supabase_url}/rest/v1/watchers", json=data) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    if "duplicate" in text.lower():
                        # تحديث الموجود (upsert semantics)
                        # URL-encode phone to prevent query-string injection
                        # (e.g. phone="+966&is_active=false" would break the filter)
                        safe_phone = url_quote(phone, safe='')
                        async with session.patch(
                            f"{self.supabase_url}/rest/v1/watchers?phone=eq.{safe_phone}",
                            json={"display_name": display_name, "session_string": session_string, "is_active": True}
                        ):
                            pass
        except Exception as e:
            logging.error(f"Supabase watcher exception: {e}")

    async def _supabase_get_watchers(self):
        """جلب المراقبين من Supabase (المصدر الأساسي)"""
        if not self.supabase_url or not self.supabase_key:
            return None  # استخدم المحلي
        try:
            session = await self._get_supabase_session()
            async with session.get(
                f"{self.supabase_url}/rest/v1/watchers?is_active=eq.true&select=phone,display_name,session_string"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        return data
                    return []
                return None
        except Exception as e:
            logging.error(f"Supabase get watchers: {e}")
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
        """إضافة مستخدم مراقب جديد - يحفظ في SQLite + Supabase"""
        # حفظ في Supabase أولاً (المصدر الأساسي الدائم)
        await self._supabase_add_watcher(phone, display_name, session_string)
        # حفظ محلياً أيضاً (للسرعة)
        async with self._lock:
            conn = await self._ensure_conn()
            try:
                cursor = await conn.execute("SELECT phone FROM watchers WHERE phone = ?", (phone,))
                existing = await cursor.fetchone()
                if existing:
                    await conn.execute(
                        """UPDATE watchers SET display_name = ?, session_string = ?, is_active = 1 WHERE phone = ?""",
                        (display_name, session_string, phone))
                else:
                    await conn.execute(
                        """INSERT INTO watchers (phone, display_name, session_string, is_active) VALUES (?, ?, ?, 1)""",
                        (phone, display_name, session_string))
                await conn.commit()
                return True
            except Exception as e:
                logging.error(f"Add watcher error: {e}")
                return False

    async def get_active_watchers(self) -> List[Dict]:
        """جلب المراقبين - من Supabase أولاً، ثم المحلي"""
        # محاولة Supabase أولاً
        supabase_watchers = await self._supabase_get_watchers()
        if supabase_watchers is not None:
            return supabase_watchers
        # fallback للمحلي
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
                # Use Monitor._send for retry logic + FloodWait cap + lock
                # (prevents retry storms and concurrent batch flooding)
                # We need to access it via the bot_client's parent monitor...
                # But HistoryScanner doesn't have a reference to Monitor.
                # Instead, replicate the same retry pattern with cap.
                await self._send_with_retry(formatted, buttons)
            except Exception as e:
                logging.error(f"[SCAN] send error: {e}")

    async def _send_with_retry(self, formatted, buttons, retries=3):
        """Send with retry + FloodWait cap (mirrors Monitor._send logic)."""
        total_waited = 0.0
        max_total_wait = 120.0
        for a in range(1, retries + 1):
            try:
                await self.bot_client.send_message(
                    self.channel_id, formatted,
                    parse_mode='html',
                    buttons=buttons,
                    link_preview=False
                )
                await asyncio.sleep(0.5)  # تجنب الفلو
                return
            except FloodWaitError as e:
                wait = min(e.seconds + 1, max_total_wait - total_waited)
                if wait <= 0:
                    return
                total_waited += wait
                await asyncio.sleep(wait)
            except (RPCError, OSError, ConnectionError):
                wait = min(10 * a, 60, max_total_wait - total_waited)
                if wait <= 0:
                    return
                total_waited += wait
                await asyncio.sleep(wait)
            except Exception as e:
                logging.error(f"[SCAN] send retry error: {e}")
                return

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
        # Cache for dialog lists (per-watcher) used by membership check.
        # Key: phone, Value: (dict of {username_lower: phone}, timestamp)
        self._dialogs_cache: Dict[str, Tuple[Dict[str, str], datetime]] = {}

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
        يفحص العضوية بدقة عالية وسرعة.
        يعيد: {phone: True(مشترك) / False(غير مشترك) / None(تعذر الفحص)}

        Optimization: dialog lists are cached per-watcher with a 10-minute TTL.
        Without the cache, every link triggered N watchers × 500 dialogs = O(N×M) API calls.
        """
        results = {}

        # 1. استخراج username وتنظيفه
        link_lower = link.lower()
        if "t.me/+" in link or "joinchat" in link_lower:
            return {}  # روابط خاصة لا يمكن فحصها

        username = None
        if "t.me/" in link_lower:
            parts = link.split("t.me/", 1)
            if len(parts) > 1:
                username = parts[1].split("/")[0].split("?")[0].strip()

        if not username or len(username) < 5:
            return {}  # غالباً رابط مستخدم أو غير صالح

        # 2. بناء/استخدام قاموس Cache لكل المراقبين: {username_lowercase: phone}
        # Use TTL cache to avoid re-fetching 500 dialogs on every link check
        cache_ttl = timedelta(minutes=10)
        now = datetime.now()
        watchers_dialogs: Dict[str, str] = {}

        # _dialogs_cache is initialized in __init__ — no need for hasattr check here
        for phone, client in self.user_clients.items():
            if not client or not client.is_connected():
                results[phone] = None
                continue

            # Try cache first
            cached = self._dialogs_cache.get(phone)
            if cached and (now - cached[1]) < cache_ttl:
                watchers_dialogs.update(cached[0])
                continue

            # Cache miss: fetch fresh dialog list
            phone_dialogs: Dict[str, str] = {}
            try:
                async for dialog in client.iter_dialogs(limit=500):
                    if hasattr(dialog.entity, 'username') and dialog.entity.username:
                        phone_dialogs[dialog.entity.username.lower()] = phone
                    if hasattr(dialog.entity, 'id'):
                        phone_dialogs[f"id_{dialog.entity.id}"] = phone
                self._dialogs_cache[phone] = (phone_dialogs, now)
                watchers_dialogs.update(phone_dialogs)
            except FloodWaitError as e:
                logging.warning(f"[MEMBERSHIP] FloodWait {e.seconds}s for {phone}, marking unknown")
                results[phone] = None
            except Exception as e:
                logging.warning(f"[MEMBERSHIP] Failed to fetch dialogs for {phone}: {e}")
                results[phone] = None

        # 3. الآن نفحص الرابط المحدد
        username_lower = username.lower()

        # Lazy import to avoid startup cost
        from telethon.tl.functions.channels import GetParticipantRequest
        from telethon.errors import UserNotParticipantError

        for phone, client in self.user_clients.items():
            if phone in results and results[phone] is None:
                continue  # تم تعليمه كغير متصل

            try:
                # محاولة الفحص المباشر عبر API (الأدق)
                try:
                    entity = await client.get_entity(username)

                    # التأكد أنه ليس مستخدم عادي
                    if hasattr(entity, 'first_name') and not hasattr(entity, 'megagroup') and not hasattr(entity, 'broadcast'):
                        results[phone] = True  # مستخدم، اعتبره مشترك لتجاهله
                        continue

                    try:
                        await client(GetParticipantRequest(channel=entity, participant="me"))
                        results[phone] = True  # مشترك تأكيداً
                    except UserNotParticipantError:
                        results[phone] = False  # تأكد عدم الاشتراك
                    except Exception:
                        results[phone] = False  # أي خطأ آخر = غير مشترك
                except Exception:
                    # لو فشل الـ API، نعتمد على الـ Cache الذي بنيناه
                    if watchers_dialogs.get(username_lower) == phone:
                        results[phone] = True
                    else:
                        results[phone] = False

            except Exception as e:
                logging.error(f"[MEMBERSHIP] Error checking {phone}: {e}")
                results[phone] = None

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
        """معالج رسائل المستخدم - نظام عبقري للسحب"""
        try:
            msg = event.message
            if not msg or not msg.text: return
            chat = await event.get_chat()
            if hasattr(chat, 'id') and chat.id == self.config.channel_id: return
            group_name = self._get_chat_name(chat)
            sender = await event.get_sender()
            sender_name = self._get_sender_name(sender)

            # فحص سريع: هل توجد كلمات مفتاحية لروابط؟
            text_lower = msg.text.lower()
            if not any(x in text_lower for x in ["whatsapp.com", "wa.me", "t.me", "telegram.me"]):
                return

            # 🤖 استخدام الذكاء الاصطناعي لتحليل الرسالة
            ai_result = await self.ai_analyzer.analyze_message(msg.text)

            # إذا قرر AI عدم الحفظ → تجاهل
            if not ai_result.get("should_save", False):
                logging.info(f"[AI {source_phone}] Skipped: should_save=False in {group_name}")
                return

            link = ai_result.get("link", "").strip()
            if not link:
                return

            link_type = ai_result.get("link_type", "other")
            sender_contact = ai_result.get("sender_contact", "")
            country = ai_result.get("country", "")
            description = ai_result.get("description", "")
            logging.debug(
                f"[AI {source_phone}] link_type={link_type} country={country!r} desc={description!r}"
            )

            # إذا لم يجد AI بيانات تواصل، يستخدم يوزر مرسل الرسالة
            if not sender_contact and sender and hasattr(sender, 'username') and sender.username:
                sender_contact = f"✈️ @{sender.username}"

            # محاولة الحصول على رابط الرسالة
            msg_link = None
            try:
                msg_link = f"https://t.me/c/{str(chat.id).replace('-100', '')}/{msg.id}"
            except Exception: pass

            # 🔍 فحص العضوية للروابط التيليجرامية
            non_members = []
            if link_type == "telegram" and "t.me/+" not in link:
                logging.info(f"[SMART] Checking membership for: {link[:80]}")
                membership = await self._check_telegram_membership(link)
                
                if membership:
                    # معالجة دقيقة للنتائج
                    confirmed_members = 0
                    for phone, is_member in membership.items():
                        if is_member is True:
                            confirmed_members += 1
                        elif is_member is False:
                            # غير مشترك تأكيداً → أضفه للتوصية
                            non_members.append(phone)
                        # لو None (تعذر الفحص) → لا نضيفه ولا نحسبه مشترك
                    
                    # لو كل المراقبين المشتركين (True) → تجاهل الرابط
                    # (نتجاهل الـ None لأننا لا نعرف حالتهم)
                    if confirmed_members > 0 and not non_members:
                        logging.info(f"[SMART {source_phone}] All connected watchers are members - skipping: {link[:50]}")
                        return
                    
                    # لو لا يوجد أحد للتوصية → تجاهل
                    if not non_members:
                        logging.info(f"[SMART {source_phone}] No non-members to recommend - skipping: {link[:50]}")
                        return
            
            # إدراج في DB (يمنع التكرار)
            inserted = await self.db.insert_request(
                link, msg.date.replace(tzinfo=None) if msg.date else datetime.now(),
                group_name, sender_name, source_phone, msg_link,
                message_text=msg.text, sender_contact=sender_contact, link_type=link_type)
            if not inserted:
                return  # مكرر

            # جلب أسماء المراقبين
            watchers = await self.db.get_active_watchers()
            watchers_names = {w['phone']: w.get('display_name', w['phone']) for w in watchers}

            # تنسيق ونشر مع قائمة غير المشتركين
            formatted = MessageFormatter.format_link_message(
                group_name, sender_name, sender_contact, msg.date,
                link, msg.text, source_phone, msg_link,
                non_members=non_members, watchers_names=watchers_names)
            buttons = MessageFormatter.get_link_buttons(link)
            await self._send(formatted, buttons=buttons)
            logging.info(f"[SMART {source_phone}] ✅ Forwarded {link_type} link from {group_name}: {link[:50]}")

            if non_members:
                logging.info(f"[SMART] Non-members listed in message: {non_members}")

        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"User message error: {e}", exc_info=True)

    async def _send(self, text, retries=3, buttons=None, parse_mode='html'):
        """إرسال رسالة للقناة مع دعم HTML والأزرار

        Caps total wait time at 120s to prevent retry storms under
        sustained FloodWait. If Telegram keeps asking us to wait, we
        give up rather than blocking the bot indefinitely.
        """
        async with self._send_lock:
            total_waited = 0.0
            max_total_wait = 120.0  # 2 minutes hard cap
            for a in range(1, retries + 1):
                try:
                    await self.bot_client.send_message(
                        self.config.channel_id, text,
                        parse_mode=parse_mode,
                        buttons=buttons,
                        link_preview=False
                    )
                    return
                except FloodWaitError as e:
                    wait = min(e.seconds + 1, max_total_wait - total_waited)
                    if wait <= 0:
                        logging.error(f"_send: total wait cap ({max_total_wait}s) reached, giving up")
                        return
                    total_waited += wait
                    await asyncio.sleep(wait)
                except (RPCError, OSError, ConnectionError):
                    wait = min(10 * a, 60, max_total_wait - total_waited)
                    if wait <= 0:
                        logging.error("_send: total wait cap reached, giving up")
                        return
                    total_waited += wait
                    await asyncio.sleep(wait)
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

            # Cleanup expired login sessions on every private message
            # (cheap O(N) scan, N is small due to concurrent limit)
            self._cleanup_expired_login_sessions()

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

        # Cleanup expired login sessions (TTL-based, prevents memory leak)
        self._cleanup_expired_login_sessions()

        # التحقق من عدم وجود تسجيل سابق
        if sender_id in self._login_sessions:
            await event.reply("⚠️ لديك عملية تسجيل قائمة بالفعل. أرسل /cancel للإلغاء.")
            return

        # Rate limit: max 3 concurrent login sessions globally
        # (prevents an attacker from spawning hundreds of temp_clients)
        if len(self._login_sessions) >= 3:
            await event.reply("⚠️ الخادم مشغول بعدة تسجيلات. حاول بعد دقيقة.")
            logging.warning(f"[LOGIN] Rejected login from {sender_id}: too many concurrent sessions")
            return

        # بدء جلسة تسجيل جديدة
        self._login_sessions[sender_id] = {
            "step": "phone",
            "temp_client": None,
            "phone": None,
            "phone_code_hash": None,
            "started_at": datetime.now(),
        }

        await event.reply(
            "🔐 تسجيل الدخول\n\n"
            "📌 أرسل رقم هاتفك بالصيغة الدولية.\n"
            "مثال: +967770309310\n\n"
            "⚠️ الرقم يجب أن يكون مرتبطاً بحساب تيليجرام.\n\n"
            "للإلغاء: /cancel"
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
                    except Exception: pass
                except Exception: pass

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
        """تشغيل user_client لمستخدم مراقب"""
        phone = watcher['phone']
        session_string = watcher['session_string']
        backoff = 5
        while self._running:
            try:
                client = self.user_clients.get(phone)
                if client is None:
                    # حماية من الجلسات التالفة
                    if not session_string or not isinstance(session_string, str) or len(session_string) < 50:
                        logging.error(f"User {phone} has invalid/corrupted session string! Skipping.")
                        self._cleanup_user_client(phone)
                        return
                    try:
                        client = self._create_user_client(session_string, phone)
                    except ValueError as ve:
                        logging.error(f"User {phone} session string is invalid: {ve}. Skipping this user.")
                        self._cleanup_user_client(phone)
                        return
                    except Exception as ce:
                        logging.error(f"User {phone} failed to create client: {ce}")
                        return
                    self.user_clients[phone] = client
                    self._register_user_handlers(phone)
                if not client.is_connected():
                    logging.info(f"Connecting user {phone}...")
                    await client.connect()
                    if not await client.is_user_authorized():
                        logging.error(f"User {phone} session not authorized! Please re-login.")
                        self._cleanup_user_client(phone)
                        return
                    logging.info(f"User {phone} connected")
                    backoff = 5
                    # مسح البدء لهذا المستخدم
                    if phone not in self._startup_scan_done:
                        self._startup_scan_done.add(phone)
                        if self.config.startup_scan_days is not None:
                            # Keep a reference to prevent GC of the task
                            # (Python may GC fire-and-forget tasks under memory pressure)
                            task = asyncio.create_task(self._run_startup_scan(watcher))
                            self._current_scan_tasks.append(task)
                await client.run_until_disconnected()
            except FloodWaitError as e: await asyncio.sleep(e.seconds + 1)
            except (RPCError, ConnectionError, OSError) as e: logging.error(f"User {phone} error: {e}")
            except asyncio.CancelledError: raise
            except Exception as e: logging.error(f"User {phone} unexpected: {e}", exc_info=True)
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
        # Remove from startup scan tracking (so a re-login can re-scan)
        self._startup_scan_done.discard(phone)

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
        logging.info(f"Starting {len(watchers)} watchers")
        for w in watchers:
            self._user_tasks[w['phone']] = asyncio.create_task(self._run_user_client(w))
        self._keep_alive_task = asyncio.create_task(self._keep_alive())

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
        tasks = [self._bot_task, self._keep_alive_task] + list(self._user_tasks.values()) + self._current_scan_tasks
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
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"HTTP server listening on port {port} (endpoints: /health /ready /metrics)")
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

    # ملاحظة: لا نضيف المالك تلقائياً - سيستخدم /login للتسجيل
    # هذا يحل مشكلة "فشل الحفظ" عند إضافة رقم المالك

    monitor = Monitor(config, db)
    await monitor.start()
    http_runner = await start_http_server(monitor=monitor, db=db)

    logging.info("✅ Monitor started. Send /help to channel.")

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
