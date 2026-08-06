#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سكريبت إضافة مستخدم مراقب جديد للبوت v7

كيف يعمل:
1. كل مستخدم جديد يشغّل هذا السكريبت على هاتفه
2. يسجل الدخول بحسابه (يطلب كود تيليجرام)
3. يحصل على StringSession
4. يرسل الـ StringSession للمالك
5. المالك يضيفه عبر /add_watcher أو يدوياً في DB
"""

import asyncio
import os
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv(dotenv_path='accounts.env')

API_ID = int(os.getenv("API_ID", "0") or "0")
API_HASH = os.getenv("API_HASH", "") or ""

if not API_ID or not API_HASH:
    print("❌ API_ID أو API_HASH غير موجود في accounts.env")
    sys.exit(1)


async def main():
    print("=" * 60)
    print("👤 سكريبت إضافة مستخدم مراقب جديد")
    print("=" * 60)
    print()
    print("⚠️ هذا السكريبت سيسجل دخول بحسابك الشخصي على تيليجرام")
    print("   ويولّد StringSession لإضافته للبوت.")
    print()

    # إنشاء جلسة جديدة (مؤقتة)
    session_name = f"sessions/new_watcher_temp"

    client = TelegramClient(session_name, API_ID, API_HASH)
    await client.start()

    me = await client.get_me()
    print(f"✅ متصل باسم: {me.first_name} (@{me.username or 'بدون'})")
    print(f"📞 الرقم: {me.phone}")
    print()

    # توليد StringSession
    string_session = StringSession.save(client.session)

    print("=" * 60)
    print("🎉 تم إنشاء String Session بنجاح!")
    print("=" * 60)
    print()
    print("📋 انسخ النص التالي بالكامل:")
    print()
    print("-" * 60)
    print(string_session)
    print("-" * 60)
    print()
    print("📌 الخطوات التالية:")
    print(f"1. أرسل هذا النص + رقم هاتفك ({me.phone}) للمالك")
    print("2. المالك سيضيفك عبر:")
    print(f"   /add_watcher {me.phone} {string_session[:50]}...")
    print("3. بعد الإضافة، ستُسحب طلبات المساعدة من مجموعاتك")
    print("   وتُنشر في القناة المشتركة")
    print()
    print("⚠️ احتفظ بهذا النص بسرية تامة!")
    print("   يمكنه منح وصول كامل لحسابك!")

    await client.disconnect()


asyncio.run(main())
