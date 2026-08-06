#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سكريبت تحويل جلسة تيليجرام إلى String Session
يسمح بنقل الجلسة من Pydroid 3 إلى Render/Railway بدون رفع ملف
"""

import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv(dotenv_path='accounts.env')

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
PHONE = os.getenv("PHONE", "")


async def main():
    print("=" * 60)
    print("🔐 تحويل الجلسة إلى String Session")
    print("=" * 60)
    print()

    # استخدام نفس جلسة البوت
    session_path = f"sessions/user_{PHONE}"

    print(f"📂 الجلسة: {session_path}")
    print(f"📞 الحساب: {PHONE}")
    print()

    client = TelegramClient(session_path, API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        print("❌ الجلسة غير مصرّحة!")
        print("شغّل monitor_v6.py أولاً على هاتفك لتسجيل الدخول")
        return

    me = await client.get_me()
    print(f"✅ متصل باسم: {me.first_name} (@{me.username or 'بدون'})")
    print()

    # تحويل الجلسة إلى string
    string_session = StringSession.save(client.session)

    print("=" * 60)
    print("🎉 تم إنشاء String Session بنجاح!")
    print("=" * 60)
    print()
    print("📋 انسخ النص التالي (بدون مسافات في البداية/النهاية):")
    print()
    print("-" * 60)
    print(string_session)
    print("-" * 60)
    print()
    print("📌 الخطوات التالية:")
    print("1. انسخ النص أعلاه")
    print("2. في Render → Environment Variables")
    print("3. أضف متغيراً جديداً:")
    print("   Key: USER_SESSION_STRING")
    print(f"   Value: [النص المنسوخ]")
    print("4. انشر البوت على Render")
    print()
    print("⚠️ احتفظ بهذا النص بسرية تامة!")
    print("   يمكنه منح وصول كامل لحسابك!")

    await client.disconnect()


asyncio.run(main())
