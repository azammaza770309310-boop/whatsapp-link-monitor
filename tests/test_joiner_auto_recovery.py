#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Joiner Fleet Auto-Recovery Test — [v4.3.3]
===========================================
[user-request — المهمة 1: «تم شغل الانضمام أشوفه متوقف»]

إنتاج 2026-09-02: المفعّل الوحيد للانضمام (♧F) مقطوع → بوابة الأسطول
(connected_joiners==0) تتخطى كل دورات العامل → انضمام متوقف كليًا رغم
وجود حسابين متصلين (Y/🇸🇦) معطّلَي joiner_enabled.

الإصلاح: AUTO-RECOVERY — الحساب المتصل ذو دور joiner يُفعَّل تلقائيًا
ما لم يكن في تبريد إشباع 24h (joiner_sat_<phone> عند ChannelsTooMuch).

السيناريوهات المُختبَرة:
  T1  سيناريو الإنتاج: متصلان معطّلان بلا سجل إشباع → كلاهما يُفعَّل
      تلقائيًا (كتابة Supabase joiner_enabled=1) ويُضافان للأسطول فورًا.
  T2  المعطّل + المقطوع: لا يُلمس إطلاقًا (طلب المشغّل: «ما يتصلوا
      يعودون للعمل» — يُكتشف تلقائيًا عند إعادة الاتصال).
  T3  إشباع حديث (قبل ساعتين) → لا تفعيل (تبريد 24h).
  T4  إشباع قديم (قبل 25h) → تفعيل + مسح سجل الإشباع.
  T5  سجل إشباع تالف (نص غير تاريخ) → يُعامَل كغير مشبع → تفعيل.
  T6  حارس المدة: محاولة واحدة/ساعة — استدعاء ثانٍ فورًا → لا كتابة
      ثانية حتى لو فشلت الأولى.
  T7  فشل كتابة Supabase (استثناء) → لا إضافة للأسطول، والحارس
      المسجّل (لا عاصفة كتابات).
  T8  سيناريو «يعود للعمل بنفسه»: حساب كان مقطوعًا وقت المحاولة
      الأولى (لم يُلمس) ثم اتصل — الدورة التالية تُفعّله.
  T9  Static: كتلة ACCOUNT_SATURATED تكتب سجل joiner_sat_{phone}.
  T10 Static: حلقة fleet-health تستدعي _maybe_auto_enable_disabled_joiners.
  T11 Static: رد /disable_joiner يشرح التحكم الدائم (/set_role monitor).

NO Telegram credentials — SIMULATION ONLY (stubs).
شغّل:  python3 tests/test_joiner_auto_recovery.py
"""
import asyncio
import os
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')
os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'testhash')
os.environ.setdefault('SUPABASE_URL', '')
os.environ.setdefault('SUPABASE_KEY', '')
os.environ.setdefault('REQUESTS_TARGET_CHANNEL', '@dhkskwksjskwk')

import logging
logging.disable(logging.CRITICAL)

import bot  # noqa: E402

RESULTS = []


def record(name, passed, detail=""):
    RESULTS.append({'name': name, 'passed': passed, 'detail': detail})
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if detail and not passed:
        print(f"         {detail}")


class FakeClient:
    def __init__(self, connected: bool):
        self._connected = connected

    def is_connected(self):
        return self._connected


class FakeSupabaseDB:
    """يسجّل نداءات _supabase_update_watcher ويمكنه الفشل عند الطلب."""

    def __init__(self, fail_phones=()):
        self.calls = []          # (phone, fields)
        self.fail_phones = set(fail_phones)

    async def _supabase_update_watcher(self, phone, **fields):
        if phone in self.fail_phones:
            raise RuntimeError("supabase 500")
        self.calls.append((phone, dict(fields)))
        return True


class FakeProdDB:
    """settings في الذاكرة: joiner_sat_<phone> = iso timestamp."""

    def __init__(self, sat_map=None):
        self.settings = dict(sat_map or {})
        self.set_calls = []

    async def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    async def set_setting(self, key, value):
        self.settings[key] = value
        self.set_calls.append((key, value))


def make_monitor(clients, supa_db, prod_db):
    fm = types.SimpleNamespace(
        user_clients=clients,
        db=supa_db,
        prod_db=prod_db,
        _joiner_auto_enable_at={},
    )
    method = types.MethodType(
        bot.Monitor._maybe_auto_enable_disabled_joiners, fm)
    return fm, method


async def run_tests():
    now = datetime.now()
    Y = '+967000000030'      # متصل (سيناريو الإنتاج)
    SA = '+967000000074'     # متصل (سيناريو الإنتاج)
    F = '+967000000060'      # مقطوع (♧F — المفعّل الوحيد سابقًا)

    # ---- T1: سيناريو الإنتاج بالضبط ----
    clients = {Y: FakeClient(True), SA: FakeClient(True), F: FakeClient(False)}
    supa = FakeSupabaseDB()
    prod = FakeProdDB()
    fm, m = make_monitor(clients, supa, prod)
    connected = [F]  # ♧F مفعّل لكنه مقطوع (لا يُحسب متصلًا في الواقع —
                     # نحاكي أن السنابشوت وصل فارغًا)
    enabled = await m([Y, SA], connected, now)
    record("T1: متصلان معطّلان بلا إشباع → كلاهما AUTO-ENABLE",
           enabled == [Y, SA] and Y in connected and SA in connected,
           f"enabled={enabled} connected={connected}")
    record("T1b: كتابة Supabase joiner_enabled=1 لكل منهما",
           supa.calls == [(Y, {'joiner_enabled': 1}), (SA, {'joiner_enabled': 1})],
           f"calls={supa.calls}")

    # ---- T2: المعطّل المقطوع لا يُلمس ----
    clients = {F: FakeClient(False)}
    supa = FakeSupabaseDB()
    fm, m = make_monitor(clients, supa, FakeProdDB())
    enabled = await m([F], [], now)
    record("T2: المعطّل+المقطوع → لا كتابة ولا تفعيل (يعود بنفسه)",
           enabled == [] and supa.calls == [],
           f"enabled={enabled} calls={supa.calls}")

    # ---- T3: إشباع حديث (قبل ساعتين) → تبريد ----
    sat_key = f'joiner_sat_{Y}'
    prod = FakeProdDB({sat_key: (now - timedelta(hours=2)).isoformat()})
    supa = FakeSupabaseDB()
    fm, m = make_monitor({Y: FakeClient(True)}, supa, prod)
    enabled = await m([Y], [], now)
    record("T3: إشباع قبل ساعتين → لا تفعيل (تبريد 24h)",
           enabled == [] and supa.calls == [] and prod.settings[sat_key] ==
           (now - timedelta(hours=2)).isoformat(),
           f"enabled={enabled} calls={supa.calls}")

    # ---- T4: إشباع قديم (25h) → تفعيل + مسح السجل ----
    prod = FakeProdDB({sat_key: (now - timedelta(hours=25)).isoformat()})
    supa = FakeSupabaseDB()
    fm, m = make_monitor({Y: FakeClient(True)}, supa, prod)
    enabled = await m([Y], [], now)
    record("T4: إشباع قبل 25h → تفعيل + مسح السجل",
           enabled == [Y] and prod.settings.get(sat_key, '') == '',
           f"enabled={enabled} sat={prod.settings.get(sat_key)!r}")

    # ---- T5: سجل تالف → كغير مشبع ----
    prod = FakeProdDB({sat_key: 'garbage-not-a-date'})
    supa = FakeSupabaseDB()
    fm, m = make_monitor({Y: FakeClient(True)}, supa, prod)
    enabled = await m([Y], [], now)
    record("T5: سجل إشباع تالف → يُفعَّل (معاملة آمنة)",
           enabled == [Y] and len(supa.calls) == 1,
           f"enabled={enabled}")

    # ---- T6: حارس المدة — استدعاء ثانٍ فورًا → لا كتابة ----
    prod = FakeProdDB()
    supa = FakeSupabaseDB()
    fm, m = make_monitor({Y: FakeClient(True)}, supa, prod)
    await m([Y], [], now)
    n_after_first = len(supa.calls)
    enabled2 = await m([Y], [], now + timedelta(minutes=5))
    record("T6: محاولة ثانية بعد 5 دقائق → لا كتابة جديدة (حارس الساعة)",
           n_after_first == 1 and enabled2 == [] and len(supa.calls) == 1,
           f"calls={len(supa.calls)} enabled2={enabled2}")
    enabled3 = await m([Y], [], now + timedelta(minutes=61))
    record("T6b: بعد 61 دقيقة → يسمح بمحاولة جديدة",
           len(supa.calls) == 2 and enabled3 == [Y],
           f"calls={len(supa.calls)} enabled3={enabled3}")

    # ---- T7: فشل Supabase → لا إضافة للأسطول + إعادة سريعة ----
    prod = FakeProdDB()
    supa = FakeSupabaseDB(fail_phones={Y})
    fm, m = make_monitor({Y: FakeClient(True)}, supa, prod)
    connected = []
    enabled = await m([Y], connected, now)
    record("T7: فشل كتابة Supabase → خارج الأسطول، بلا استثناء",
           enabled == [] and Y not in connected)
    enabled2 = await m([Y], connected, now + timedelta(seconds=60))
    record("T7b: بعد 60 ثانية من الفشل → لا كتابة (حارس الدقيقتين)",
           len(supa.calls) == 0 and enabled2 == [],
           f"calls={len(supa.calls)}")
    # الفشل العابر يشفي: Supabase عاد للعمل بعد >2 دقيقة → يُفعّل
    supa.fail_phones = set()
    connected2 = []
    enabled3 = await m([Y], connected2, now + timedelta(seconds=125))
    record("T7c: فشل عابر ثم شفاء بعد دقيقتين → تفعيل فوري (لا ساعة انتظار)",
           enabled3 == [Y] and Y in connected2 and len(supa.calls) == 1,
           f"enabled3={enabled3} calls={len(supa.calls)}")

    # ---- T8: «يعود للعمل بنفسه» — مقطوع ثم اتصل ----
    prod = FakeProdDB()
    supa = FakeSupabaseDB()
    clients = {F: FakeClient(False)}
    fm, m = make_monitor(clients, supa, prod)
    await m([F], [], now)                      # مقطوع → لا يُلمس
    clients[F] = FakeClient(True)              # ♧F عاد للاتصال!
    connected = []
    enabled = await m([F], connected, now + timedelta(seconds=5))
    record("T8: الحساب يعود للعمل تلقائيًا فور اتصاله (دورة تالية)",
           enabled == [F] and F in connected and len(supa.calls) == 1,
           f"enabled={enabled} calls={len(supa.calls)}")

    # ---- T9-T11: Static — الكود المصدري ----
    src = Path(PROJECT_ROOT / 'bot.py').read_text(encoding='utf-8')
    sat_block = src[src.find('ACCOUNT_SATURATED'):src.find('ACCOUNT_SATURATED') + 2000]
    record("T9: كتلة الإشباع تكتب سجل joiner_sat_{phone}",
           "joiner_sat_{phone}" in sat_block, "سجل الإشباع غير موجود في الكتلة")

    fleet_def = src.find('async def _joiner_fleet_health_loop')
    fleet_fn = src[fleet_def:fleet_def + 9000]
    record("T10: حلقة fleet-health تستدعي دالة الاسترداد",
           "_maybe_auto_enable_disabled_joiners" in fleet_fn,
           "الاستدعاء غير موجود في الحلقة")

    dis_block = src[src.find('"/disable_joiner"'):src.find('"/disable_joiner"') + 1800]
    record("T11: رد /disable_joiner يشرح التحكم الدائم (/set_role monitor)",
           "set_role" in dis_block and "AUTO-RECOVERY" in dis_block,
           "الرد لا يذكر set_role/AUTO-RECOVERY")


def main():
    print("=" * 70)
    print("Joiner Fleet Auto-Recovery [v4.3.3] — سيناريو توقف الانضمام")
    print("=" * 70)
    asyncio.run(run_tests())
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = len(RESULTS) - passed
    print("\n" + "=" * 70)
    print(f"JOINER-AUTO-RECOVERY RESULTS: {passed}/{len(RESULTS)} assertions passed")
    if failed:
        print("FAILURES:")
        for r in RESULTS:
            if not r['passed']:
                print(f"  ✗ {r['name']}  {r['detail']}")
    print("=" * 70)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
