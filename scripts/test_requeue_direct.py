#!/usr/bin/env python3
"""اختبار حقيقي ومباشر للـ requeue logic بدون ProductionDB wrapper.
يختبر الـ SQL queries مباشرة."""

import asyncio
import aiosqlite
import tempfile
import os

DB_PATH = f"{tempfile.gettempdir()}/test_requeue_direct.db"

# إزالة DB القديم لو موجود
try:
    os.unlink(DB_PATH)
except:
    pass


async def setup_db():
    """إنشاء قاعدة بيانات وجدول link_queue."""
    db = await aiosqlite.connect(DB_PATH)
    await db.execute("""CREATE TABLE IF NOT EXISTS link_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_link TEXT NOT NULL,
        normalized_link TEXT NOT NULL,
        link_type TEXT,
        username TEXT,
        invite_hash TEXT,
        msg_id TEXT,
        group_name TEXT,
        sender_name TEXT,
        sender_contact TEXT,
        source_phone TEXT,
        message_text TEXT,
        message_link TEXT,
        status TEXT DEFAULT 'QUEUED',
        enqueued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        processed_at TIMESTAMP,
        attempt_count INTEGER DEFAULT 0,
        last_error TEXT,
        next_retry_at TIMESTAMP,
        member_count INTEGER,
        priority INTEGER DEFAULT 3,
        UNIQUE(normalized_link)
    )""")
    await db.commit()
    return db


async def enqueue_link(db, link_data, allow_requeue=False):
    """نسخة طبق الأصل من ProductionDB.enqueue_link للاختبار."""
    try:
        cursor = await db.execute(
            """INSERT OR IGNORE INTO link_queue
            (raw_link, normalized_link, link_type, username, invite_hash, msg_id,
             group_name, sender_name, sender_contact, source_phone, message_text, message_link, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED')""",
            (link_data['raw'], link_data['normalized'], link_data['link_type'],
             link_data.get('username'), link_data.get('invite_hash'), link_data.get('msg_id'),
             link_data.get('group_name', ''), link_data.get('sender_name', ''),
             link_data.get('sender_contact', ''), link_data.get('source_phone', ''),
             link_data.get('message_text', ''), link_data.get('message_link'))
        )
        await db.commit()
        if cursor.rowcount > 0:
            return True

        if allow_requeue:
            cursor = await db.execute(
                """UPDATE link_queue
                   SET status = 'QUEUED',
                       member_count = NULL,
                       priority = 3,
                       next_retry_at = NULL,
                       attempt_count = 0,
                       last_error = NULL
                   WHERE normalized_link = ?
                   AND status IN ('DONE', 'REJECTED', 'FAILED')""",
                (link_data['normalized'],)
            )
            await db.commit()
            if cursor.rowcount > 0:
                return True

        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


async def get_status(db, normalized):
    cursor = await db.execute(
        "SELECT status FROM link_queue WHERE normalized_link = ?",
        (normalized,)
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def get_member_count_priority(db, normalized):
    cursor = await db.execute(
        "SELECT member_count, priority, attempt_count FROM link_queue WHERE normalized_link = ?",
        (normalized,)
    )
    row = await cursor.fetchone()
    return row if row else (None, None, None)


async def run_tests():
    db = await setup_db()
    print("=" * 70)
    print("اختبار مباشر للـ requeue SQL logic")
    print("=" * 70)

    passed = 0
    failed = 0

    # === Test 1: رابط جديد → True ===
    link1 = {
        'raw': 'https://t.me/test_group_1',
        'normalized': 'https://t.me/test_group_1',
        'link_type': 'telegram',
        'username': 'test_group_1',
    }
    result = await enqueue_link(db, link1)
    if result == True:
        print("✅ Test 1 PASSED: New link returns True")
        passed += 1
    else:
        print(f"❌ Test 1 FAILED: Expected True, got {result}")
        failed += 1

    # === Test 2: نفس الرابط بدون requeue → False ===
    result = await enqueue_link(db, link1, allow_requeue=False)
    if result == False:
        print("✅ Test 2 PASSED: Duplicate link returns False (no requeue)")
        passed += 1
    else:
        print(f"❌ Test 2 FAILED: Expected False, got {result}")
        failed += 1

    # === Test 3: رابط DONE مع requeue=True → True + status يصير QUEUED ===
    await db.execute(
        "UPDATE link_queue SET status = 'DONE' WHERE normalized_link = ?",
        (link1['normalized'],)
    )
    await db.commit()
    status_before = await get_status(db, link1['normalized'])
    if status_before != 'DONE':
        print(f"❌ Test 3 FAILED: Setup failed, status={status_before}")
        failed += 1
    else:
        result = await enqueue_link(db, link1, allow_requeue=True)
        status_after = await get_status(db, link1['normalized'])
        if result == True and status_after == 'QUEUED':
            print("✅ Test 3 PASSED: DONE link → requeued to QUEUED")
            passed += 1
        else:
            print(f"❌ Test 3 FAILED: result={result}, status_before={status_before}, status_after={status_after}")
            failed += 1

    # === Test 4: رابط QUEUED مع requeue=True → False (ما يحتاج) ===
    result = await enqueue_link(db, link1, allow_requeue=True)
    if result == False:
        print("✅ Test 4 PASSED: QUEUED link with requeue returns False (no change needed)")
        passed += 1
    else:
        print(f"❌ Test 4 FAILED: Expected False, got {result}")
        failed += 1

    # === Test 5: رابط BANNED مع requeue → False (مو ضمن القائمة) ===
    link2 = {
        'raw': 'https://t.me/banned_group',
        'normalized': 'https://t.me/banned_group',
        'link_type': 'telegram',
        'username': 'banned_group',
    }
    await enqueue_link(db, link2)
    await db.execute(
        "UPDATE link_queue SET status = 'BANNED' WHERE normalized_link = ?",
        (link2['normalized'],)
    )
    await db.commit()
    result = await enqueue_link(db, link2, allow_requeue=True)
    status_after = await get_status(db, link2['normalized'])
    if result == False and status_after == 'BANNED':
        print("✅ Test 5 PASSED: BANNED link stays BANNED (not requeued)")
        passed += 1
    else:
        print(f"❌ Test 5 FAILED: result={result}, status={status_after}")
        failed += 1

    # === Test 6: رابط REJECTED مع requeue → True ===
    link3 = {
        'raw': 'https://t.me/rejected_group',
        'normalized': 'https://t.me/rejected_group',
        'link_type': 'telegram',
        'username': 'rejected_group',
    }
    await enqueue_link(db, link3)
    await db.execute(
        "UPDATE link_queue SET status = 'REJECTED' WHERE normalized_link = ?",
        (link3['normalized'],)
    )
    await db.commit()
    result = await enqueue_link(db, link3, allow_requeue=True)
    status_after = await get_status(db, link3['normalized'])
    if result == True and status_after == 'QUEUED':
        print("✅ Test 6 PASSED: REJECTED link → requeued to QUEUED")
        passed += 1
    else:
        print(f"❌ Test 6 FAILED: result={result}, status={status_after}")
        failed += 1

    # === Test 7: requeue يمسح member_count و priority و attempt_count ===
    # حط قيم
    await db.execute(
        "UPDATE link_queue SET member_count = 5000, priority = 2, attempt_count = 3 WHERE normalized_link = ?",
        (link3['normalized'],)
    )
    await db.commit()
    # requeue (رابط QUEUED بالفعل، فلن requeue)
    # لازم نخليه DONE الأول
    await db.execute(
        "UPDATE link_queue SET status = 'DONE' WHERE normalized_link = ?",
        (link3['normalized'],)
    )
    await db.commit()
    await enqueue_link(db, link3, allow_requeue=True)
    # تحقق
    mc, pri, att = await get_member_count_priority(db, link3['normalized'])
    if mc is None and pri == 3 and att == 0:
        print("✅ Test 7 PASSED: requeue cleared member_count (was 5000→None), priority (2→3), attempts (3→0)")
        passed += 1
    else:
        print(f"❌ Test 7 FAILED: mc={mc}, pri={pri}, attempts={att}")
        failed += 1

    print("\n" + "=" * 70)
    print(f"النتيجة: {passed}/{passed + failed} نجح")
    if failed == 0:
        print("🎉 كل الاختبارات نجحت!")
    else:
        print(f"⚠️  {failed} اختبار فشل")
    print("=" * 70)

    await db.close()
    try:
        os.unlink(DB_PATH)
    except:
        pass


asyncio.run(run_tests())
