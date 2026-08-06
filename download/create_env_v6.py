#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سكريبت إنشاء ملف accounts.env للنسخة v6
"""

import os

ENV_CONTENT = """# ============================================
# Telegram WhatsApp Link Monitor v6
# ============================================

# === Telegram API credentials ===
# من https://my.telegram.org → API development tools
API_ID=36421189
API_HASH=1bb7284e39673808269821857ba90e95

# === User account (للمراقبة) ===
# رقم هاتف حسابك الذي سيراقب المجموعات
PHONE=+967770309310

# === Bot token (للإرسال للقناة) ===
# من @BotFather على تيليجرام
BOT_TOKEN=8821033695:AAHhtd1vApaTct0a-IrP4d0g9FyZ3dNyxJM

# === Destination channel ===
# سيتم تحديثه تلقائياً بواسطة setup_v6.py
# أو ضعه يدوياً (يبدأ بـ -100)
CHANNEL_ID=0

# === Owner (اختياري) ===
# معرّفك الرقمي لتقييد الأوامر بك فقط
# اتركه فارغاً للسماح لأي عضو بإرسال الأوامر
# للحصول على معرّفك: أرسل /start لـ @userinfobot
OWNER_ID=

# === Logging ===
LOG_LEVEL=INFO

# === Expired Link Checking ===
CHECK_EXPIRED=true
HTTP_TIMEOUT=6

# === History Scan ===
HISTORY_MAX_PER_CHAT=500
HISTORY_BATCH_SIZE=5
HISTORY_SKIP_CHANNEL_POSTS=false

# === Startup scan ===
# None = لا مسح عند البدء
# 7 = مسح أسبوع عند البدء
# 30 = مسح شهر عند البدء
STARTUP_SCAN_DAYS=None

# === Proxy (اختياري) ===
# لليمن أو الشبكات المحجوبة
# التنسيق: socks5:host:port أو http:host:port
# مثال: socks5:127.0.0.1:9050
PROXY=
"""

# كتابة الملف
with open("accounts.env", "w", encoding="utf-8") as f:
    f.write(ENV_CONTENT)

print("=" * 60)
print("✅ تم إنشاء ملف accounts.env للنسخة v6!")
print("=" * 60)
print()
print("📋 المحتوى:")
print("-" * 60)
print(ENV_CONTENT)
print("-" * 60)
print()
print("📌 الخطوات التالية:")
print("1. شغّل setup_v6.py لاكتشاف معرف القناة تلقائياً")
print("   (سيحدّث CHANNEL_ID في accounts.env)")
print("2. شغّل create_v6.py لإنشاء ملف monitor_v6.py")
print("3. شغّل monitor_v6.py لبدء البوت")
