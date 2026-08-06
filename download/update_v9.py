#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سكريبت تحديث monitor_v9.py من GitHub (للمستودعات الخاصة)
يستخدم GitHub API مع التوكن للوصول
"""

import os
import json
import urllib.request
import ssl
import base64

# إعدادات
TOKEN = "REDACTED_TOKEN"
REPO = "azammaza770309310-boop/whatsapp-link-monitor"
FILE_PATH = "monitor_v9.py"
TARGET_FILE = "monitor_v9.py"

# رابط API
API_URL = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"

def download_v9():
    print("=" * 60)
    print("📥 تحديث monitor_v9.py من GitHub (Private Repo)")
    print("=" * 60)
    print()
    print(f"📂 المستودع: {REPO}")
    print(f"📄 الملف: {FILE_PATH}")
    print(f"💾 الهدف: {os.path.abspath(TARGET_FILE)}")
    print()
    print("⏳ جاري التنزيل عبر API...")

    try:
        # تجاوز شهادة SSL
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # طلب API مع التوكن
        req = urllib.request.Request(
            API_URL,
            headers={
                'Authorization': f'token {TOKEN}',
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'Mozilla/5.0'
            }
        )

        with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
            data = json.loads(response.read().decode())

        # استخراج المحتوى (base64 encoded)
        content_b64 = data['content']
        content = base64.b64decode(content_b64)

        # كتابة الملف
        with open(TARGET_FILE, "wb") as f:
            f.write(content)

        file_size = os.path.getsize(TARGET_FILE)
        print()
        print("=" * 60)
        print("✅ تم تحديث monitor_v9.py بنجاح!")
        print("=" * 60)
        print(f"📂 المسار: {os.path.abspath(TARGET_FILE)}")
        print(f"📊 الحجم: {file_size} بايت")
        print()
        print("📌 الخطوات التالية:")
        print("1. افتح monitor_v9.py")
        print("2. اضغط زر التشغيل ▶")
        print("3. أرسل /start للبوت في تيليجرام")
        print("4. أضف البوت لمجموعتك كـ Admin")

    except urllib.error.HTTPError as e:
        print(f"❌ HTTP Error: {e.code} - {e.reason}")
        if e.code == 401:
            print("⚠️ التوكن غير صالح أو منتهي الصلاحية")
        elif e.code == 404:
            print("⚠️ الملف غير موجود في المستودع")
        print()
        print("💡 الحل: تواصل مع المالك للتحقق")
    except Exception as e:
        print(f"❌ خطأ في التنزيل: {e}")
        print()
        print("💡 حلول بديلة:")
        print("1. تأكد من اتصال الإنترنت")
        print("2. فعّل VPN لو كنت في اليمن")
        print("3. أعد المحاولة")

if __name__ == "__main__":
    download_v9()
