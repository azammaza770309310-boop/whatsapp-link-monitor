#!/usr/bin/env python3
"""اختبار حقيقي للـ enqueue_link مع allow_requeue.

هذا الاختبار:
1. ينشئ قاعدة بيانات SQLite مؤقتة
2. ينشئ جدول link_queue
3. يختبر scenarios:
   - إدخال رابط جديد → True
   - إدخال نفس الرابط → False (مكرر)
   - requeue رابط DONE → True
   - requeue رابط QUEUED → False (ما يحتاج)
   - requeue رابط BANNED → False (ما ينحط QUEUED تاني)
"""

import asyncio
import sys
import os
import tempfile
import sqlite3

# استخرج ProductionDB من link_system.py
sys.path.insert(0, '/home/z/my-project')

# اقرأ link_system.py واستخرج الكلاسات
with open('/home/z/my-project/link_system.py', 'r') as f:
    content = f.read()

# بدّل DB_FILE لمسار مؤقت
import re
content_modified = re.sub(
    r'DB_FILE\s*=\s*["\'][^"\']*["\']',
    f'DB_FILE = "{tempfile.gettempdir()}/test_requeue.db"',
    content
)

# نفّذ
exec(content_modified)

# الحين ProductionDB متاح
async def run_tests():
    # إنشاء قاعدة البيانات
    db = ProductionDB()
    await init_production_tables(db)

    print("=" * 70)
    print("اختبار حقيقي لـ enqueue_link مع allow_requeue")
    print("=" * 70)

    passed = 0
    failed = 0

    # === Test 1: رابط جديد ===
    link_data = {
        'raw': 'https://t.me/test_group_1',
        'normalized': 'https://t.me/test_group_1',
        'link_type': 'telegram',
        'username': 'test_group_1',
        'group_name': 'Test Group 1',
    }
    result = await db.enqueue_link(link_data)
    if result == True:
        print("✅ Test 1 PASSED: New link returns True")
        passed += 1
    else:
        print(f"❌ Test 1 FAILED: Expected True, got {result}")
        failed += 1

    # === Test 2: نفس الرابط بدون requeue → False ===
    result = await db.enqueue_link(link_data, allow_requeue=False)
    if result == False:
        print("✅ Test 2 PASSED: Duplicate link returns False")
        passed += 1
    else:
        print(f"❌ Test 2 FAILED: Expected False, got {result}")
        failed += 1

    # === Test 3: رابط DONE مع requeue=True → True ===
    # أولاً، حط الرابط في حالة DONE
    conn = await db._conn()
    await conn.execute(
        "UPDATE link_queue SET status = 'DONE' WHERE normalized_link = ?",
        (link_data['normalized'],)
    )
    await conn.commit()

    # تأكد إنه DONE
    status = await db.get_link_status(link_data['normalized'])
    if status != 'DONE':
        print(f"❌ Test 3 FAILED: Cannot set status to DONE (got {status})")
        failed += 1
    else:
        # جرب requeue
        result = await db.enqueue_link(link_data, allow_requeue=True)
        if result == True:
            # تأكد إنه رجع QUEUED
            new_status = await db.get_link_status(link_data['normalized'])
            if new_status == 'QUEUED':
                print("✅ Test 3 PASSED: DONE link re-queued to QUEUED")
                passed += 1
            else:
                print(f"❌ Test 3 FAILED: Status is {new_status}, expected QUEUED")
                failed += 1
        else:
            print(f"❌ Test 3 FAILED: Expected True, got {result}")
            failed += 1

    # === Test 4: رابط QUEUED مع requeue=True → False ===
    # (ما يحتاج requeue لأنه QUEUED)
    result = await db.enqueue_link(link_data, allow_requeue=True)
    if result == False:
        print("✅ Test 4 PASSED: QUEUED link with requeue returns False (no change needed)")
        passed += 1
    else:
        print(f"❌ Test 4 FAILED: Expected False, got {result}")
        failed += 1

    # === Test 5: رابط BANNED مع requeue=True → False ===
    # BANNED ما ينحط في requeue (مو ضمن DONE/REJECTED/FAILED)
    link_data2 = {
        'raw': 'https://t.me/banned_group',
        'normalized': 'https://t.me/banned_group',
        'link_type': 'telegram',
        'username': 'banned_group',
    }
    await db.enqueue_link(link_data2)
    await conn.execute(
        "UPDATE link_queue SET status = 'BANNED' WHERE normalized_link = ?",
        (link_data2['normalized'],)
    )
    await conn.commit()

    result = await db.enqueue_link(link_data2, allow_requeue=True)
    if result == False:
        print("✅ Test 5 PASSED: BANNED link NOT re-queued (stays BANNED)")
        passed += 1
    else:
        print(f"❌ Test 5 FAILED: Expected False, got {result}")
        failed += 1

    # === Test 6: رابط REJECTED مع requeue=True → True ===
    link_data3 = {
        'raw': 'https://t.me/rejected_group',
        'normalized': 'https://t.me/rejected_group',
        'link_type': 'telegram',
        'username': 'rejected_group',
    }
    await db.enqueue_link(link_data3)
    await conn.execute(
        "UPDATE link_queue SET status = 'REJECTED' WHERE normalized_link = ?",
        (link_data3['normalized'],)
    )
    await conn.commit()

    result = await db.enqueue_link(link_data3, allow_requeue=True)
    if result == True:
        new_status = await db.get_link_status(link_data3['normalized'])
        if new_status == 'QUEUED':
            print("✅ Test 6 PASSED: REJECTED link re-queued to QUEUED")
            passed += 1
        else:
            print(f"❌ Test 6 FAILED: Status is {new_status}, expected QUEUED")
            failed += 1
    else:
        print(f"❌ Test 6 FAILED: Expected True, got {result}")
        failed += 1

    # === Test 7: requeue يمسح member_count + priority ===
    # حط member_count وpriority
    await conn.execute(
        "UPDATE link_queue SET member_count = 5000, priority = 2 WHERE normalized_link = ?",
        (link_data3['normalized'],)
    )
    await conn.commit()
    # requeue
    await db.enqueue_link(link_data3, allow_requeue=True)
    # تحقق
    cursor = await conn.execute(
        "SELECT member_count, priority, attempt_count FROM link_queue WHERE normalized_link = ?",
        (link_data3['normalized'],)
    )
    row = await cursor.fetchone()
    if row and row[0] is None and row[1] == 3 and row[2] == 0:
        print("✅ Test 7 PASSED: requeue cleared member_count, priority, attempts")
        passed += 1
    else:
        print(f"❌ Test 7 FAILED: Got mc={row[0]}, pri={row[1]}, attempts={row[2]}")
        failed += 1

    print("\n" + "=" * 70)
    print(f"النتيجة: {passed}/{passed + failed} نجح")
    if failed == 0:
        print("🎉 كل الاختبارات نجحت!")
    else:
        print(f"⚠️  {failed} اختبار فشل")
    print("=" * 70)

    # نظف
    try:
        os.unlink(f"{tempfile.gettempdir()}/test_requeue.db")
    except:
        pass

asyncio.run(run_tests())
