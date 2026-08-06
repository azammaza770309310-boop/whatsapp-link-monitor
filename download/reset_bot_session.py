#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سكريبت إعادة ضبط جلسة البوت
يحذف الجلسة القديمة وينشئ جلسة جديدة
"""

import os
import sys
import time

SESSIONS_DIR = "sessions"
BOT_SESSION = os.path.join(SESSIONS_DIR, "bot.session")
BOT_SESSION_JOURNAL = os.path.join(SESSIONS_DIR, "bot.session-journal")
BOT_SESSION_SHM = os.path.join(SESSIONS_DIR, "bot.session-shm")
BOT_SESSION_WAL = os.path.join(SESSIONS_DIR, "bot.session-wal")

print("=" * 60)
print("🔄 إعادة ضبط جلسة البوت")
print("=" * 60)
print()

# التحقق من وجود المجلد
if not os.path.exists(SESSIONS_DIR):
    print(f"⚠️ مجلد {SESSIONS_DIR} غير موجود")
    print("سيتم إنشاؤه عند تشغيل البوت")
    sys.exit(0)

# قائمة الملفات للحذف
files_to_delete = [
    BOT_SESSION,
    BOT_SESSION_JOURNAL,
    BOT_SESSION_SHM,
    BOT_SESSION_WAL,
]

print("📋 الملفات التي سيتم حذفها:")
deleted_count = 0
for f in files_to_delete:
    if os.path.exists(f):
        size = os.path.getsize(f)
        print(f"   🗑️ {f} ({size} بايت)")
        try:
            os.remove(f)
            print(f"      ✅ تم الحذف")
            deleted_count += 1
        except Exception as e:
            print(f"      ❌ فشل: {e}")
    else:
        print(f"   ℹ️ {f} - غير موجود")

print()
print("=" * 60)
if deleted_count > 0:
    print(f"✅ تم حذف {deleted_count} ملف جلسة")
    print()
    print("🎉 الآن البوت سينشئ جلسة جديدة تماماً عند التشغيل!")
    print()
    print("📌 الخطوات التالية:")
    print("1. أوقف monitor_v10.py (لو يعمل)")
    print("2. شغّل monitor_v10.py مرة أخرى ▶")
    print("3. انتظر 10 ثوانٍ")
    print("4. أرسل /start للبوت في تيليجرام")
    print("5. سيرد البوت بالقائمة التفاعلية! 🎉")
else:
    print("ℹ️ لا توجد ملفات جلسة لحذفها")
    print("   البوت سينشئ جلسة جديدة عند التشغيل")
print("=" * 60)
