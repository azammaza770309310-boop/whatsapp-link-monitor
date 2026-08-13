#!/usr/bin/env python3
"""اختبار سريع لفلتر Gulf — يتأكد إنه يقبل المجموعات الخليجية بدون ذكر اسم الجامعة."""

import sys
sys.path.insert(0, '/home/z/my-project')

# استورد الكلاس
import importlib.util
spec = importlib.util.spec_from_file_location("bot_module", "/home/z/my-project/bot.py")
# ما نقدر نشغل bot.py كامل (يحتاج telethon) — فنعرف EducationalFilter يدوياً
# بدلاً من ذلك، ننسخ الكلاس لملف اختبار

# اقرأ bot.py واستخرج EducationalFilter
with open('/home/z/my-project/bot.py', 'r') as f:
    content = f.read()

# ابحث عن بداية ونهاية الكلاس
start = content.find('class EducationalFilter:')
# ابحث عن next class أو def بعد EducationalFilter
end_marker = content.find('\n# -------------------------------------------------------------------', start)
if end_marker == -1:
    end_marker = content.find('\nclass MessageFormatter:', start)

filter_code = content[start:end_marker]

# نفّذ الكود
from typing import Tuple
exec(filter_code)

# الحين EducationalFilter متاح
EF = EducationalFilter

# حالات اختبار
test_cases = [
    # (description, text, username, link, source_group, expected)
    ("مجموعة سعودية واضحة", "انضموا لمجموعة KSU", "ksu_students", "https://t.me/ksu_students", "", True),
    ("مجموعة مستوى بدون جامعة", "طلاب المستوى الأول", "level1_2026", "https://t.me/level1_2026", "", True),
    ("دفعة 1446 بدون جامعة", "دفعة 1446 تجمع", "batch1446", "https://t.me/batch1446", "", True),
    ("سياق أكاديمي - محاضرة", "محاضرة د. أحمد بكرة", "cs_lectures", "https://t.me/cs_lectures", "", True),
    ("مجموعة بيتكوين", "تداول بيتكوين وربح", "crypto_signals", "https://t.me/crypto_signals", "", False),
    ("مجموعة عراقية", "جامعة بغداد كلية الهندسة", "uobaghdad", "https://t.me/uobaghdad", "", False),
    ("مجموعة مصرية", "جامعة القاهرة دفعة 2026", "cu_eg", "https://t.me/cu_eg", "", False),
    ("مجموعة فيها محتوى للكبار", "محتوى 18+ فقط", "adult_content", "https://t.me/adult_content", "", False),
    # الحالة المهمة: مجموعة خليجية بدون ذكر جامعة، لكن جاءت من مصدر خليجي
    ("مجموعة بدون اسم جامعة لكن مصدر خليجي", "انضموا للمجموعة", "students_chat", "https://t.me/students_chat", "جامعة الملك سعود طلاب", True),
    ("مجموعة سياق أكاديمي من مصدر خليجي", "الجميع ينضم", "test_group", "https://t.me/test_group", "KFUPM | جامعة البترول", True),
    # بيتكوين داخل مجموعة خليجية — لازم يرفض (الأهم)
    ("بيتكوين داخل مصدر خليجي", "تداول بيتكوين", "crypto", "https://t.me/crypto", "جامعة الملك سعود", False),
    # مجموعة تجارية
    ("متجر للبيع", "متجر ملابس رخيص", "shop_ksa", "https://t.me/shop_ksa", "", False),
    # مجموعة واتساب خليجية
    ("مجموعة واتساب سعودية", "مجموعة طلاب جامعة الملك فهد", "https://chat.whatsapp.com/abc123", "https://chat.whatsapp.com/abc123", "", True),
]

print("=" * 80)
print("اختبار فلتر Gulf — التأكد من قبول المجموعات الخليجية بدون ذكر اسم الجامعة")
print("=" * 80)

passed = 0
failed = 0

for desc, text, username, link, source_group, expected in test_cases:
    should_join, reason = EF.should_join(text, username, link, source_group, '')
    status = "✅ PASS" if should_join == expected else "❌ FAIL"
    if should_join == expected:
        passed += 1
    else:
        failed += 1
    print(f"\n{status} | {desc}")
    print(f"   النص: {text[:50]}")
    print(f"   username: {username}")
    print(f"   المصدر: {source_group[:40] if source_group else '(فاضي)'}")
    print(f"   متوقع: {'قبول' if expected else 'رفض'} | فعلي: {'قبول' if should_join else 'رفض'} ({reason})")

print("\n" + "=" * 80)
print(f"النتيجة: {passed}/{passed+failed} نجح")
if failed == 0:
    print("🎉 كل الاختبارات نجحت!")
else:
    print(f"⚠️  {failed} اختبار فشل — راجع الكود")
print("=" * 80)
