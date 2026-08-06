#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سكريبت شامل لتحميل كل ملفات البوت من GitHub دفعة واحدة
ينزل: monitor_v6.py, v7, v8, v9, v10 + requirements + README + accounts.env.example
"""

import os
import json
import urllib.request
import ssl
import base64

TOKEN = "REDACTED_TOKEN"
REPO = "azammaza770309310-boop/whatsapp-link-monitor"

# قائمة كل الملفات للتنزيل
FILES_TO_DOWNLOAD = [
    "monitor_v6.py",
    "monitor_v7.py",
    "monitor_v8.py",
    "monitor_v9.py",
    "monitor_v10.py",
    "requirements.txt",
    "accounts.env.example",
    "README.md",
    "add_watcher.py",
    "export_session.py",
]


def download_file(filename):
    """تنزيل ملف واحد من GitHub API"""
    api_url = f"https://api.github.com/repos/{REPO}/contents/{filename}"

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(
            api_url,
            headers={
                'Authorization': f'token {TOKEN}',
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'Mozilla/5.0'
            }
        )

        with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
            data = json.loads(response.read().decode())

        if 'content' not in data:
            return False, f"محتوى غير موجود"

        content_b64 = data['content']
        content = base64.b64decode(content_b64)

        with open(filename, "wb") as f:
            f.write(content)

        file_size = os.path.getsize(filename)
        return True, f"{file_size} بايت"

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, "الملف غير موجود"
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:50]


def main():
    print("=" * 60)
    print("📦 تنزيل جميع ملفات البوت من GitHub")
    print("=" * 60)
    print()
    print(f"📂 المستودع: {REPO}")
    print(f"💾 المجلد الحالي: {os.getcwd()}")
    print()
    print(f"📋 عدد الملفات: {len(FILES_TO_DOWNLOAD)}")
    print("=" * 60)
    print()

    success = 0
    failed = 0

    for i, filename in enumerate(FILES_TO_DOWNLOAD, 1):
        print(f"[{i}/{len(FILES_TO_DOWNLOAD)}] 📥 {filename}...")
        ok, info = download_file(filename)
        if ok:
            print(f"   ✅ نجح - {info}")
            success += 1
        else:
            print(f"   ❌ فشل - {info}")
            failed += 1
        print()

    print("=" * 60)
    print(f"📊 النتائج:")
    print(f"   ✅ نجح: {success} ملف")
    print(f"   ❌ فشل: {failed} ملف")
    print("=" * 60)
    print()
    if success > 0:
        print("🎉 تم تنزيل الملفات بنجاح!")
        print()
        print("📌 الخطوات التالية:")
        print("1. افتح monitor_v10.py (أحدث نسخة)")
        print("2. اضغط زر التشغيل ▶")
        print("3. أرسل /start للبوت في تيليجرام")
        print()
        print("💡 ملاحظة:")
        print("• monitor_v10.py = النسخة الأحدث (واجهة أزرار + سحب واتساب)")
        print("• monitor_v9.py = بدون واجهة أزرار")
        print("• monitor_v8.py = مع تسجيل دخول تفاعلي")
        print("• monitor_v7.py = متعدد المستخدمين")
        print("• monitor_v6.py = النسخة القديمة")
    if failed > 0:
        print()
        print("⚠️ بعض الملفات فشل تنزيلها. تحقق من الإنترنت أو VPN.")


if __name__ == "__main__":
    main()
