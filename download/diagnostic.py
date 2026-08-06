#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سكريبت تشخيصي لفحص القنوات والمجموعات التي يشترك فيها حساب البوت
ويجد المعرف الصحيح للقناة الوجهة
"""

import asyncio
import os
from telethon import TelegramClient
from dotenv import load_dotenv

load_dotenv(dotenv_path='accounts.env')

API_ID = int(os.getenv("ACCOUNT_1_API_ID"))
API_HASH = os.getenv("ACCOUNT_1_API_HASH")
PHONE = os.getenv("ACCOUNT_1_PHONE")
TARGET_CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

async def main():
    print("=" * 60)
    print("🔍 تشخيص القنوات والمجموعات")
    print("=" * 60)
    print(f"📞 الحساب: {PHONE}")
    print(f"🎯 CHANNEL_ID المطلوب: {TARGET_CHANNEL_ID}")
    print()

    client = TelegramClient('sessions/diagnostic_session', API_ID, API_HASH)
    await client.start(phone=PHONE)
    print("✅ تم تسجيل الدخول بنجاح")
    print()

    print("=" * 60)
    print("📋 جميع المحادثات (آخر 50):")
    print("=" * 60)

    found_target = False
    count = 0

    async for dialog in client.iter_dialogs(limit=50):
        count += 1
        chat = dialog.entity
        chat_id = dialog.id
        # المعرف الكامل (يبدأ بـ -100 للقنوات والمجموعات الخارقة)
        full_id = f"-100{chat_id}" if chat_id > 0 and not str(chat_id).startswith('-') else str(chat_id)

        is_channel = False
        is_group = False
        try:
            if hasattr(chat, 'megagroup') and chat.megagroup:
                is_group = True
            elif hasattr(chat, 'broadcast') and chat.broadcast:
                is_channel = True
        except:
            pass

        type_str = "📺 قناة" if is_channel else ("👥 مجموعة" if is_group else "💬 دردشة")

        username = getattr(chat, 'username', None) or "بدون"
        title = dialog.name or "بدون اسم"

        # التحقق إن كانت هذه هي القناة المطلوبة
        marker = ""
        try:
            if int(full_id) == TARGET_CHANNEL_ID:
                marker = " ◄◄◄ ✅ هذه هي القناة الهدف!"
                found_target = True
            elif chat_id == TARGET_CHANNEL_ID:
                marker = " ◄◄◄ ✅ هذه هي القناة الهدف! (معرف قصير)"
                found_target = True
        except:
            pass

        print(f"{count:3}. {type_str}")
        print(f"     📛 الاسم: {title}")
        print(f"     🆔 المعرف القصير: {chat_id}")
        print(f"     🆔 المعرف الكامل: {full_id}")
        print(f"     👤 @: {username}{marker}")
        print()

    print("=" * 60)
    if found_target:
        print("✅ تم العثور على القناة الهدف في القائمة!")
        print("البوت يمكنه رؤية القناة. المشكلة في مكان آخر.")
    else:
        print("❌ لم يتم العثور على القناة الهدف!")
        print()
        print("السبب المحتمل:")
        print("1. CHANNEL_ID في accounts.env خاطئ")
        print("2. حساب البوت ليس عضواً في القناة")
        print()
        print("🔍 ابحث في القائمة أعلاه عن قناتك")
        print("   ثم انسخ المعرف الكامل (-100...) وضعه في accounts.env")
    print("=" * 60)

    # محاولة إرسال رسالة اختبار للقناة الهدف
    print()
    print("📨 محاولة إرسال رسالة اختبار للقناة الهدف...")
    try:
        await client.send_message(TARGET_CHANNEL_ID, "🤖 اختبار: البوت يستطيع الإرسال في هذه القناة")
        print("✅ نجح الإرسال! البوت يستطيع الكتابة في القناة.")
    except Exception as e:
        print(f"❌ فشل الإرسال: {e}")
        print("السبب: البوت ليس عضواً أو ليس لديه صلاحية الكتابة")

    await client.disconnect()
    print()
    print("=" * 60)
    print("انتهى التشخيص")

if __name__ == "__main__":
    asyncio.run(main())
