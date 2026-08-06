#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""سكريبت تحديث monitor_v11.py من GitHub"""

import os
import json
import urllib.request
import ssl
import base64

TOKEN = "REDACTED_TOKEN"
REPO = "azammaza770309310-boop/whatsapp-link-monitor"
FILE_PATH = "monitor_v11.py"
TARGET_FILE = "monitor_v11.py"

API_URL = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"

def download_v11():
    print("=" * 60)
    print("📥 تحديث monitor_v11.py من GitHub (PROFESSIONAL)")
    print("=" * 60)
    print()
    print(f"📂 المستودع: {REPO}")
    print(f"📄 الملف: {FILE_PATH}")
    print(f"💾 الهدف: {os.path.abspath(TARGET_FILE)}")
    print()
    print("⏳ جاري التنزيل عبر API...")

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

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

        content_b64 = data['content']
        content = base64.b64decode(content_b64)

        with open(TARGET_FILE, "wb") as f:
            f.write(content)

        file_size = os.path.getsize(TARGET_FILE)
        print()
        print("=" * 60)
        print("✅ تم تحديث monitor_v11.py بنجاح!")
        print("=" * 60)
        print(f"📂 المسار: {os.path.abspath(TARGET_FILE)}")
        print(f"📊 الحجم: {file_size} بايت")
        print()
        print("📌 الخطوات التالية:")
        print("1. افتح monitor_v11.py")
        print("2. اضغط زر التشغيل ▶")
        print("3. أرسل /start للبوت في تيليجرام")
        print("4. ستظهر القائمة الاحترافية!")
        print()
        print("⚠️ ملاحظة: لو شغّلت v11 على هاتفك،")
        print("   لا تشغّله على Render بنفس الوقت (تعارض جلسات)")

    except urllib.error.HTTPError as e:
        print(f"❌ HTTP Error: {e.code} - {e.reason}")
        if e.code == 401:
            print("⚠️ التوكن غير صالح")
    except Exception as e:
        print(f"❌ خطأ في التنزيل: {e}")

if __name__ == "__main__":
    download_v11()
