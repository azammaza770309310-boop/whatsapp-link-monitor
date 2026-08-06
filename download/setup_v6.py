#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سكريبت إعداد v6 - يكتشف معرف القناة الجديدة "Azam Mm"

ماذا يفعل:
1. يتصل بالبوت (BOT_TOKEN)
2. يفحص كل القنوات/المجموعات التي البوت مشرف فيها
3. يعرض القناة المطلوبة ومعرفها
4. يحدّث ملف accounts.env تلقائياً
"""

import asyncio
import os
import re
from telethon import TelegramClient
from dotenv import load_dotenv

load_dotenv(dotenv_path='accounts.env')

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")


async def main():
    print("=" * 60)
    print("🚀 إعداد البوت v6 - اكتشاف معرف القناة")
    print("=" * 60)
    print()

    if not all([API_ID, API_HASH, BOT_TOKEN]):
        print("❌ متغيرات ناقصة في accounts.env!")
        print(f"   API_ID: {'✅' if API_ID else '❌'}")
        print(f"   API_HASH: {'✅' if API_HASH else '❌'}")
        print(f"   BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
        return

    print(f"API_ID: {API_ID}")
    print(f"BOT_TOKEN: {BOT_TOKEN[:20]}...")
    print()

    client = TelegramClient('sessions/setup_bot', API_ID, API_HASH)
    await client.connect()

    try:
        await client.start(bot_token=BOT_TOKEN)
    except Exception as e:
        print(f"❌ فشل تسجيل دخول البوت: {e}")
        await client.disconnect()
        return

    me = await client.get_me()
    print(f"✅ البوت متصل: @{me.username} ({me.first_name})")
    print()
    print("=" * 60)
    print("📋 القنوات/المجموعات التي البوت مشرف فيها:")
    print("=" * 60)
    print()

    channels = []
    count = 0
    async for d in client.iter_dialogs():
        chat = d.entity
        is_channel = False
        is_admin = False

        try:
            if hasattr(chat, 'broadcast') and chat.broadcast:
                is_channel = True
        except:
            pass

        # التحقق من صلاحية المشرف
        try:
            participant = await client.get_permissions(chat, me.id)
            if participant and (participant.is_admin or participant.is_creator):
                is_admin = True
        except:
            pass

        if is_channel or is_admin:
            count += 1
            cid = d.id
            full_id = f"-100{cid}" if cid > 0 else str(cid)
            title = d.name or "بدون"
            uname = getattr(chat, 'username', None) or "بدون"
            admin_mark = "👑 مشرف" if is_admin else "📺 قناة"
            channels.append((full_id, title, uname, admin_mark))

            print(f"{count}. {admin_mark}")
            print(f"   📛 الاسم: {title}")
            print(f"   🆔 المعرف: {full_id}")
            print(f"   👤 @: {uname}")
            print()

    print("=" * 60)
    if not channels:
        print("❌ لم يجد البوت أي قناة هو مشرف فيها!")
        print()
        print("السبب: البوت ليس مشرفاً في أي قناة بعد.")
        print("الحل:")
        print("1. أنشئ قناة جديدة في تيليجرام باسم 'Azam Mm'")
        print("2. من إعدادات القناة → Administrators → Add Admin")
        print("3. ابحث عن اسم البوت وأضفه كـمشرف")
        print("4. أعِد تشغيل هذا السكريبت")
    else:
        print(f"✅ وجد {len(channels)} قناة/مجموعة")
        print()
        # محاولة العثور على قناة "Azam Mm"
        azam_channel = None
        for cid, title, uname, _ in channels:
            if "azam" in title.lower() or "mm" in title.lower():
                azam_channel = (cid, title, uname)
                break

        if azam_channel:
            print(f"🎯 وجدت قناة 'Azam Mm'!")
            print(f"   المعرف: {azam_channel[0]}")
            print(f"   الاسم: {azam_channel[1]}")
            channel_id = azam_channel[0]
        else:
            print("لم يجد قناة 'Azam Mm' تلقائياً.")
            print("اختر رقم القناة من القائمة أعلاه:")
            try:
                choice = int(input("رقم القناة (1-N): ")) - 1
                if 0 <= choice < len(channels):
                    channel_id = channels[choice][0]
                    print(f"اخترت: {channels[choice][1]} ({channel_id})")
                else:
                    print("❌ اختيار غير صالح")
                    await client.disconnect()
                    return
            except Exception:
                print("❌ إدخال غير صالح")
                await client.disconnect()
                return

        # تحديث ملف accounts.env
        print()
        print("📝 تحديث ملف accounts.env...")
        update_env_file(channel_id)
        print(f"✅ تم تحديث CHANNEL_ID = {channel_id}")
        print()
        print("=" * 60)
        print("🎉 الإعداد مكتمل! الخطوة التالية:")
        print("=" * 60)
        print("1. افتح monitor_v6.py")
        print("2. اضغط زر التشغيل ▶")
        print("3. أرسل /help في قناتك الجديدة للاختبار")

    print("=" * 60)
    await client.disconnect()


def update_env_file(channel_id):
    """تحديث CHANNEL_ID في accounts.env"""
    env_path = 'accounts.env'

    if not os.path.exists(env_path):
        print(f"⚠️ ملف {env_path} غير موجود، سيُنشأ")
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write(f"CHANNEL_ID={channel_id}\n")
        return

    with open(env_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # استبدال أو إضافة CHANNEL_ID
    pattern = r'^CHANNEL_ID\s*=.*$'
    if re.search(pattern, content, re.MULTILINE):
        new_content = re.sub(
            pattern,
            f'CHANNEL_ID={channel_id}',
            content,
            flags=re.MULTILINE
        )
    else:
        new_content = content + f"\nCHANNEL_ID={channel_id}\n"

    with open(env_path, 'w', encoding='utf-8') as f:
        f.write(new_content)


if __name__ == "__main__":
    asyncio.run(main())
