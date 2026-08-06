#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سكريبت إنشاء ملف monitor_v5.py تلقائياً
ينفذ مرة واحدة لكتابة ملف البوت بشكل صحيح
"""

# محتوى ملف monitor_v5.py - الجزء الكامل
BOT_CODE = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram WhatsApp Link Monitor - v5
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
'''

# باقي الكود سيُضاف في الأجزاء التالية
# هذا الجزء الأول فقط - لا تشغله بعد!

print("ملف create_bot.py - الجزء 1 من 3 جاهز")
print("لا تشغل السكريبت بعد - انتظر إضافة الجزء 2 و 3")
