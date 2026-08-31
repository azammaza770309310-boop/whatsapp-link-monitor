#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_request_filter.py — سكريبت تحقق مستقل لـ Request Filter v2

يختبر الفلتر المحافظ الجديد (Intent + Academic Service) مقابل:
  1. أمثلة يجب أن تُقبل (ACCEPT) — طلبات حقيقية بشخص + خدمة أكاديمية.
  2. أمثلة يجب أن تُرفض (REJECT) — كلمات منفردة/عامة (سبب الـ15,000 رسالة).
  3. أمثلة يجب أن تُرفض كإعلان (REJECT-AD) — مقدم خدمة.
  4. حالات حاسمة: «عندي مشروع»/«عندي واجب»/«أحتاج مساعدة»/«بحث»/«مشروع» وحدها.
  5. seeker مع رقم هاتف/رابط → يجب أن تُقبل (لا تُرفض بسبب الوسيلة).
  6. provider صريح → يجب أن يُرفض.

لا يحتاج أي Telegram/telethon — يختبر analyze_request مباشرة.
شغّل:  python3 verify_request_filter.py
"""
import sys
from request_filter import analyze_request, FILTER_VERSION, FILTER_MODE

# ============================================================
# 1. ACCEPT — طلبات حقيقية (شخص + خدمة أكاديمية)
# ============================================================
ACCEPT_CASES = [
    "من يسوي لي بحث؟",
    "أبي أحد يحل لي واجب",
    "مين يعرف أحد يسوي بوربوينت؟",
    "عندي مشروع تخرج وأحتاج أحد ينجزه",
    "من يساعدني في واجب Excel؟",
    "أحتاج شخص يكتب لي تقرير جامعي",
    "من يجهز لي عرض تقديمي؟",
    "أحد يعرف شخص يسوي خريطة مفاهيم؟",
    "من يشرح لي المادة ويحل معي أسئلة المقرر؟",
    "محتاج أحد يساعدني في مشروع جامعي",
]

# ============================================================
# 2. REJECT — كلمات/عبارات عامة (سبب الـ15,000 رسالة في الفلتر القديم)
# ============================================================
REJECT_CASES = [
    "عندي مشروع",
    "عندي واجب",
    "بحثي صعب",
    "هذا البحث ممتاز",
    "خلصت الواجب",
    "مشروعي جاهز",
    "أحتاج مساعدة",
    "ممكن أحد يساعدني",
    "Excel برنامج ممتاز",
    "عندي عرض بكرة",
    "اختباري الأسبوع القادم",
]

# ============================================================
# 3. CRITICAL — كلمات منفردة (يجب ألا تمر أبدًا)
# ============================================================
CRITICAL_REJECT = [
    "بحث",
    "مشروع",
    "عندي مشروع",
    "عندي واجب",
    "أحتاج مساعدة",
]

# ============================================================
# 4. REJECT-AD — مقدم خدمة / إعلان (provider_detected)
# ============================================================
REJECT_AD_CASES = [
    "نوفر حل واجبات بأسعار ممتازة للتواصل واتساب",
    "نقدم خدمات بحوث وتقارير ومشاريع",
    "خصم على جميع خدماتنا",
    "متخصصون في إعداد البحوث والمشاريع",
    "أسوي واجبات وسكليفات تواصل خاص",
    "لدينا خدمات PowerPoint وExcel",
    # [MBOT-PORT] حالات من mbot.py — dotted-word obfuscation + multi-line ad
    "مكتبنا يقدم خدمات طلابية ت.قرير و.اجب باسعار مناسبة",  # dotted obfuscation
    "نوفر بحوث ومشاريع\nأسعارنا مناسبة\nتواصل واتساب\nخدمات اكاديمية\nاحجز الآن\nفرصة محدودة",  # 6-line ad
]

# ============================================================
# 5. SEEKER + contact — يجب أن تُقبل (لا تُرفض بسبب الهاتف/الرابط)
# ============================================================
SEEKER_WITH_CONTACT = [
    "أبي أحد يحل لي واجب، هذا رقمي 0551234567",
    "من يسوي لي بحث — تواصل معي t.me/ahmad",
    "محتاج أحد يساعدني في مشروع جامعي واتسابي 0501234567",
]

# ============================================================
# 6. PROVIDER صريح — يجب أن يُرفض
# ============================================================
PROVIDER_CASES = [
    "أسوي بحوث وتقارير",
    "أحل واجبات",
    "نوفر مشاريع تخرج",
    "متخصص في حل الواجبات",
    "للتواصل لحل المشاريع",
]

# ============================================================
# 6b. SEEKER multi-line — يجب أن تُقبل (لا تُرفض بسبب تعدد الأسطر)
# [MBOT-PORT] multi-line is a WEAK signal — must NOT cause false rejection
# ============================================================
SEEKER_MULTILINE_CASES = [
    # 6+ lines genuine request (no provider indicators)
    "السلام عليكم\nمحتاج مساعدة من اخواني\nابي احد يسوي لي بحث\nالبحث جامعي تخرج\nعندي مشروع تخرج قريب\nتكفون تساعدوني",
]

# ============================================================
# 7. REJECT-SPAM-EDGE — سبام متقدّم (التحسين v2.1)
#    حالات إعلانية تستخدم تقنيات التلصّص: هاتف ملتصق بأحرف عربية،
#    رقم+@handle فقط، إشارات تسويقية جديدة. يجب أن تُرفض كلها كـ provider.
# ============================================================
REJECT_SPAM_EDGE = [
    # [v2.1-إصلاح] هاتف ملتصق بأحرف عربية — كان يفوت \b القديم
    "للتواصل0540916687",                      # هاتف ملتصق بـ«للتواصل»
    "رقمي للتواصل 0540916687 @DrMedical",     # رقم+@handle (السبام الأصلي من الجلسة السابقة)
    # [v2.1-تعزيز] تركيبة رقم+@handle كانت على عتبة الرفض (6) بالضبط — الآن فوقها
    "0540916687 @DrMed",                      # رقم+@handle فقط
    "+966540916687 @DrMed",                  # كود دولي+@handle
    # إعلان تقرير طبي مزوّر (الحالة الأصلية التي فحصناها)
    "نقدم خدمة تقرير طبي موثق بأختام حقيقية للتواصل 0540916687 @DrMedical",
    # [v2.1-إشارات جديدة] عبارات تسويقية مضافة
    "ننجز لك بحوث ومشاريع بأسعار تنافسية مجاني للاستفسار",
    "مكتبنا يقدم بحوث جاهزة فريقنا محترف تنافسية",
    "خدمات طلابية 5 نجوم موصى به هديه مجانية",
    # إعلان dotted-obfuscation + هاتف ملتصق
    "ن.وفر ت.قرير طبي للتواصل0540916687 @DrMed",
]

# ============================================================
# 8. PRODUCTION-FALSE-POSITIVES — رسائل فعلية وصلت لقناة الطلبات
#    تحت v2.1 المُنتَجة. v3.0+ يجب أن ترفض كلها. كل رسالة هنا مثال
#    واقعي سُحب بالخطأ. الأسباب في v2.1:
#      - «وين القى» كان في REQUEST_INTENT_PHRASES (intent)
#      - «أبغى شرح» كان intent+service (substring على كلمات منفردة)
#      - «افضل واحد يشرح المادة» → action_plus_service (لم يكن recommendation)
#    Hard Gates v3.0+ ترصد كل واحدة عبر resource/info/recommendation gates.
# ============================================================
PRODUCTION_FP_CASES = [
    "محتوى يشرح Systematic Review ويحتوي كلمة بحث",                # FP-1: long-info content with "بحث"
    "وين القى اختبارات ع التاسيس عشان اراجع الدرس اللي اخذته قبل", # FP-2: resource seeking
    "أبغى شرح إحصاء الترم التحضيري وين ألقى؟",                    # FP-3: info seeking (شرح as noun)
    "وين القى شرح الإصدارات المجانيه",                            # FP-4: resource seeking (شرح مجاني)
    "رسالة نصيحة عادية تحتوي كلمة اختبار",                          # FP-5: recommendation with "اختبار"
    "افضل واحد يشرح المادة مين؟",                                  # FP-6: recommendation seeking
    # [v3.1.0] PRODUCTION-FP — 19 رسائل فعلية وصلت لقناة @dhkskwksjskwk
    # بعد نشر c0b5f9b (v3.0.1) في الإنتاج. كلها قبلها v3.0.1 بالخطأ بسبب
    # 7 أسباب جذرية تم تشخيصها وإصلاحها في v3.1.0:
    #   - "واحد" في PERSON_WORDS يطابق "في يوم واحد" (رقم)
    #   - "ه" في _POSSESSIVE_SUFFIXES_LONGEST_FIRST يحوّل "مدرسه"(school) → "مدرس"(teacher)
    #   - Gate 3 (d) role+ownership وحدهم يقبلون (دون exec/service/outsource)
    #   - "تعرف/تعرفين أحد يشرح" غير مدرج في RECOMMENDATION_SEEKING
    #   - "أحد يعرف" في REQUESTER_PHRASES يقبل أسئلة معلومات التواصل
    #   - لا توجد بوابات رفض لـ contact_info/decision/person_status seeking
    #   - "على"/"علي" في OWNERSHIP_NEED يطابق "على حسب" (مجرّد حرف جر)
    # انظر worklog.md PROD-VERIFY-5 للحصول على التحليل التفصيلي.
    "قال الإمام الباقر (عليه السلام): ( خروج السفياني واليماني والخراساني في سنة واحدة، في شهر واحد، في يوم واحد، نظام كنظام الخرز يتبع بعضه بعضا فيكون البأس من كل وجه، ويل لمن ناواهم، وليس في الرايات راية أهدى من راية اليماني، هي راية هدى، لأنه يدعو إلى صاحبكم، فإذا خرج اليماني حرم بيع السلاح على الناس وكل مسلم، وإذا خرج اليماني فانهض إليه فإن رايته راية هدى، ولا يحل لمسلم أن يلتوي عليه، فمن فعل ذلك فهو من أهل النار، لأنه يدعو إلى الحق وإلى طريق مستقيم )",  # FP-7: religious quote — "واحد"/"يحل" matched but religious context
    "الاضمن يعني اداوم الجامعه و اسحب على الحفل ولا وش العلم الدكتوره ذي ماقد دخلت عندنا ابدا",  # FP-8: decision seeking about attendance
    "على حسب الدكتور فيه اللي ياخذها من هالاسبوع وفيه الاسبوع الثاني",  # FP-9: info sharing about timing
    "تعرفين احد يشرح البرمجه كويس؟",                              # FP-10: recommendation seeking
    "ياجماعة راكان الشهري طالب يدرس بالتمريض احد يعرفه يدلني عليه",  # FP-11: contact info seeking (specific person)
    "بنات بليز احتاج وحده فاهمه باللفضي",                          # FP-12: borderline (no exec verb/service)
    "طيب التقرير النهائي والمرحلي يمديني انا اسويه ولا لازم اخلي احد يسويلي هوه",  # FP-13: decision seeking
    "ابي رقم او ايميل الدكتوره عشتار",                            # FP-14: contact info seeking (phone/email)
    "ابي ميه مو ٩٨ عشان اكون المميزه بالمدرسه",                    # FP-15: expressing aspiration
    "يخوان احد يدرس او تخرج بماجستير طاقة متجددة؟",               # FP-16: person status seeking
    "احنا تو البدايات هذا ٢ اسبوع والمدرسه فوضى مقفلين مدارس وجامعين الطلاب",  # FP-17: complaint about school
    "ووحده اعرفها جابتها وهالشي مو لانه صعب عليك بيكون صعب على غيرك الموضوع أرزاق وكل واحد وحلمه والي يطمح له",  # FP-18: philosophical encouragement
    "السلام عليكم نزلت عندي رقم القاعه بس ما نزل اسم الدكتوره اداوم ولا ؟",  # FP-19: decision seeking about attendance
    "بنات عندي محاضره عملي ومنزلين الدكتوره ف البلاك كيف أداوم ولا !",  # FP-20: decision seeking
    "ثقافه اسلاميه عن بعض والمواد الثانيه بعضها يكون النظري فيها عن بعد على حسب يجيك دكتور ولا دكتوره",  # FP-21: info sharing
    "السلام عليكم انا اول ترم لي مستجد وابي اغير كم دكتور عندي اقدر ؟",  # FP-22: decision seeking (can I change prof?)
    "يخوان مين قد درس عند الدكتور جارح الروقي يجيني بالخاص محتاج اعرف عن طريقة الدكتور وما يخصه",  # FP-23: contact info seeking (doctor info)
    "اذا معي عذر يعني عذر طارئ ارسله للدكتور ع الايميل؟ ولا اكلمه على ارض الواقع",  # FP-24: decision seeking (how to communicate)
    "بنات زوجي يشتغل لاهله بمبلغ شهري زهيد راتب وعاجبه الوضع ويأست ماقدر اغير تفكيره وعندي طفلين اصغر من ٣ سنوات وودي أتوظف وأنفع نفسي وهو بكيفه متى ماصحى على عمره وفكر فينا المشكله ماقدر أسوق ابغا مكان قريب جنبنا مدرسه حكوميه بس واضح مستحيل يقبلوني عشاني اجنبيه في روضه خاصه كمان قريب هل في وظيفه بروضه غير التدريس اقدر أتوظف فيها ؟",  # FP-25: career advice seeking
]

# ============================================================
# 9. PRODUCTION-ACCEPT — رسالة فعلية يجب أن تُقبل (طلب شخص + تنفيذ + خدمة)
#    «ابي حد يسوي بحث» = أبى (requester) + حد (person) + يسوي (exec) + بحث (service).
#    Hard Gate 3 option (a): has_requester AND has_execution ✓
#    Hard Gate 4: services non-empty ✓ → ACCEPT (service_execution_request).
# ============================================================
PRODUCTION_ACCEPT_CASES = [
    "ابي حد يسوي بحث",   # the user's literal ACCEPT case this round
    # [v3.0.1] possessive-suffix regression — كان يُرفض في v3.0 بسبب
    # «مشروعي» لا يطابق SERVICE_TERM «مشروع» (token-equality without suffix strip).
    "محتاج شخص ينجز مشروعي",  # ownership+person+exec+possessive-suffixed service
    "من يحل لي واجبي",         # exec implies service (يحل) — also worked in v3.0
    "أبي أحد يكتب تقريري",     # possessive "تقريري" → now matches "تقرير"
    "محتاجة شخص يجهز عرضي",   # possessive "عرضي" → now matches "عرض"
]


def run_cases(cases, expect_accept, label):
    print(f"\n=== {label} ===")
    passed = 0
    for text in cases:
        r = analyze_request(text)
        ok = (r.is_request == expect_accept)
        mark = "✅" if ok else "❌"
        tag = "ACCEPT" if r.is_request else "REJECT"
        print(f"  {mark} {tag} conf={r.confidence} reason={r.reason} "
              f"seeker={r.seeker_confidence} provider={r.provider_confidence} | {text}")
        if ok:
            passed += 1
    return passed, len(cases)


def main():
    print("=" * 72)
    print(f"Request Filter {FILTER_VERSION} ({FILTER_MODE}) — Verify")
    print("المبدأ: Intent + Academic Service (لا مطابقة substring ساذجة)")
    print("=" * 72)

    total_pass = 0
    total_cases = 0

    for cases, expect, label in [
        (ACCEPT_CASES, True, "ACCEPT — طلبات حقيقية"),
        (REJECT_CASES, False, "REJECT — كلمات عامة"),
        (CRITICAL_REJECT, False, "CRITICAL — كلمات منفردة (يجب ألا تمر)"),
        (REJECT_AD_CASES, False, "REJECT-AD — مقدم خدمة/إعلان"),
        (SEEKER_WITH_CONTACT, True, "SEEKER+contact — يجب أن تُقبل"),
        (PROVIDER_CASES, False, "PROVIDER — يجب أن يُرفض"),
        (SEEKER_MULTILINE_CASES, True, "SEEKER-multi-line — يجب أن تُقبل"),
        (REJECT_SPAM_EDGE, False, "REJECT-SPAM-EDGE — سبام متقدّم (v2.1)"),
        (PRODUCTION_FP_CASES, False, "PRODUCTION-FP — رسائل فعلية سُحبت بالخطأ (v3.0.1 → مُصلَحة v3.1.0)"),
        (PRODUCTION_ACCEPT_CASES, True, "PRODUCTION-ACCEPT — رسائل فعلية يجب أن تُقبل (v3.0.1)"),
    ]:
        p, n = run_cases(cases, expect, label)
        total_pass += p
        total_cases += n

    print("\n" + "=" * 72)
    print(f"RESULTS: {total_pass}/{total_cases} cases matched expected")
    print("=" * 72)
    return 0 if total_pass == total_cases else 1


if __name__ == "__main__":
    sys.exit(main())
