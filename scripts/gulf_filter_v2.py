#!/usr/bin/env python3
"""الكلاس المدمج المحسّن — GulfFilter v2
يجمع أفضل ما في EducationalFilter (الحالي) + GulfFilter (DeepSeek).
يحل محل EducationalFilter في bot.py.
"""

import re
from typing import List, Optional, Tuple


class GulfFilter:
    """فلتر ذكي متعدد الطبقات لمجموعات الخليج الأكاديمية.

    يدمج أفضل ما في:
    - EducationalFilter (السابق): قوائم شاملة + is_educational + is_likely_channel
    - GulfFilter (DeepSeek): تنظيم منطقي + regex قوي + فصل _find_* methods

    ترتيب الفحص (من الأقوى للأضعف):
        1. HARD_BLACKLIST → رفض فوري (حتى لو المصدر خليجي)
        2. GULF_WHITELIST → قبول فوري
        3. مصدر خليجي → قبول (البوت يراقب خليجيين أصلاً)
        4. سياق أكاديمي → قبول (مستوى/ترم/دفعة/1446...)
        5. مصدر أكاديمي → قبول
        6. is_educational عام → قبول
        7. رفض احتياطي (fail-safe)
    """

    # ==================================================================
    # القوائم السوداء — مقسّمة لفئات منطقية (من DeepSeek + توسعات)
    # ==================================================================
    BLACKLIST_CRYPTO_INVEST = [
        # إنجليزي
        'bitcoin', 'btc', 'crypto', 'cryptocurrency', 'blockchain',
        'forex', 'trading', 'stocks', 'stock', 'profit', 'money',
        'earn', 'income', 'passive', 'airdrop', 'nft', 'binance',
        'coinbase', 'coin', 'token', 'tokens', 'defi', 'web3',
        'pump', 'dump', 'signal', 'signals', 'fx', 'cfd', 'leverage',
        'trade', 'investment', 'mining',
        # عربي
        'بيتكوين', 'كريبتو', 'عملة رقمية', 'عملات رقمية',
        'استثمار', 'استثماري', 'تداول', 'فوركس', 'بورصة', 'اسهم', 'سهم',
        'ربح', 'ارباح', 'دولار', 'دولارات', 'ايردروب',
        'بينانس', 'اشارات', 'رافعة', 'هايف', 'هيفي',
    ]

    BLACKLIST_GAMBLING = [
        'casino', 'gambling', 'bet', 'betting', 'lottery',
        'رهان', 'مراهنات', 'يانصيب', 'حظ', 'قمار', 'لعبة قمار',
    ]

    BLACKLIST_IRAQI_UNIS = [
        # مدن عراقية
        'بغداد', 'baghdad', 'المصلا', 'mosul', 'البصرة', 'basra',
        'كربلاء', 'karbala', 'نجف', 'najaf', 'كوفة', 'kufa',
        'ميسان', 'maysan', 'واسط', 'wasit', 'ديالى', 'diyala',
        'الأنبار', 'anbar', 'صلاح الدين', 'salahaddin', 'تكريت',
        'سامراء', 'samaraa', 'الحلة', 'babil', 'بابل',
        'القادسية', 'qadisiyyah', 'ذي قار', 'dhiqar', 'مثنى', 'muthanna',
        'erbil', 'أربيل', 'دهوك', 'dohuk', 'سليمانية', 'sulaymaniyah',
        'kirkuk', 'كركوك',
        # إشارات عامة للعراق
        'iraq', 'iraqi', 'عراقي', 'عراق', 'العراق',
    ]

    BLACKLIST_NON_GULF_COUNTRIES = [
        # مصر
        'cairo', 'القاهرة', 'alexandria', 'اسكندرية',
        'egypt', 'مصري', 'مصر',
        # الأردن
        'jordan', 'أردني', 'الاردن', 'عمّان', 'amman',
        # سوريا
        'syria', 'سوري', 'سوريا', 'damascus', 'دمشق', 'حلب', 'aleppo',
        # لبنان
        'lebanon', 'لبناني', 'لبنان', 'beirut', 'بيروت',
        # السودان
        'sudan', 'سوداني', 'السودان', 'خرطوم', 'khartoum',
        # اليمن
        'yemen', 'يمني', 'اليمن', 'صنعاء', 'sanaa', 'عدن', 'aden',
        # المغرب
        'morocco', 'مغربي', 'المغرب', 'rabat', 'الرباط', 'casablanca',
        # الجزائر
        'algeria', 'جزائري', 'الجزائر', 'algiers',
        # تونس
        'tunisia', 'تونسي', 'تونس',
        # ليبيا
        'libya', 'ليبي', 'ليبيا', 'tripoli', 'طرابلس',
        # فلسطين
        'palestine', 'فلسطيني', 'فلسطين', 'gaza', 'غزة', 'ramallah',
    ]

    BLACKLIST_ADULT = [
        'porn', 'xxx', 'adult', '18+', 'محتوى للكبار', 'nsfw',
        'sex', 'dating', 'تعارف', 'زواج', 'متعارف',
    ]

    BLACKLIST_SOCIAL = [
        'sub4sub', 'follow4follow', 'like4like', 'متابعين', 'لايكات',
        'followers', 'subscribers', 'تيك توك', 'يوتيوب', 'سناب',
        'tiktok', 'youtube', 'snapchat', 'instagram', 'انستقرام',
    ]

    BLACKLIST_SHOPS = [
        'متجر', 'متاجر', 'تسوق', 'شراء', 'بيع', 'سعر', 'خصم', 'عرض خاص',
        'store', 'shop', 'buy', 'sell', 'price', 'discount',
        'متوفر', 'للبيع', 'للإيجار', 'توصيل', 'شحن',
        'خدمات', 'باقات', 'باقة', 'اشتراك', 'مدفوع',
    ]

    # القائمة السوداء الموحدة (للفحص السريع)
    HARD_BLACKLIST = (
        BLACKLIST_CRYPTO_INVEST +
        BLACKLIST_GAMBLING +
        BLACKLIST_IRAQI_UNIS +
        BLACKLIST_NON_GULF_COUNTRIES +
        BLACKLIST_ADULT +
        BLACKLIST_SOCIAL +
        BLACKLIST_SHOPS
    )

    # ==================================================================
    # القائمة البيضاء الخليجية (جامعات سعودية + خليجية)
    # ==================================================================
    GULF_WHITELIST = [
        # === السعودية ===
        'السعودية', 'saudi', 'ksa', 'السعودي',
        'الملك سعود', 'ksu', 'الملك عبدالعزيز', 'kau', 'الملك فيصل', 'kfu',
        'الملك خالد', 'kku', 'الملك فهد', 'kfupm',
        'الملك عبدالله', 'kaust', 'الملك سلمان',
        'أم القرى', 'uqu', 'ام القرى', 'الطائف', 'taibahu',
        'الباحة', 'جازان', 'نجران', 'ngran', 'الجوف',
        'الحدود الشمالية', 'حائل', 'hail', 'تبوك',
        'القصيم', 'qassim',
        'الإمام', 'imamu', 'الإمام محمد',
        'النعيرية', 'شقراء', 'المجمعة', 'رماح', 'الخرج',
        'الدوادمي', 'الأفلاج',
        'sattam', 'prince sattam', 'psau',
        'الإمام عبدالرحمن', 'iau', 'الدمام',
        'جدة', 'uj',
        'دار الحكمة', 'اليمامة', 'ابن رشد',
        'pnu', 'norah', 'nora', 'الأميرة نورة',
        'seu', 'السعودية الإلكترونية',
        'majmaah', 'shaqra',
        'taif', 'طيبة',
        # === الكويت ===
        'الكويت', 'kuwait', 'الكويتي',
        'ku', 'AUM', 'AUK', 'GUST', 'الكندي',
        'PAAET', 'الهيئة',
        # === قطر ===
        'قطر', 'qatar', 'القطري',
        'qu', 'qatar university',
        'Carnegie', 'Georgetown', 'HBKU', 'جامعة حمد بن خليفة',
        # === البحرين ===
        'البحرين', 'bahrain', 'البحريني',
        'Ahlia', 'AMA', 'المنامة', 'university of bahrain', 'uob',
        # === الإمارات ===
        'الإمارات', 'UAE', 'الإماراتي', 'امارات',
        'Khalifa', 'Zayed', 'Sharjah', 'دبي', 'أبوظبي', 'الشارقة',
        'UAEU', 'UOS', 'AUS', 'NYUAD',
    ]

    # ==================================================================
    # السياق الأكاديمي العام (من غير ذكر جامعة)
    # ==================================================================
    ACADEMIC_CONTEXT = [
        # مستويات دراسية
        'مستوى', 'مستوى أول', 'مستوى ثاني', 'مستوى ثالث', 'مستوى رابع',
        'مستوى خامس', 'مستوى سادس', 'مستوى سابع', 'مستوى ثامن',
        'level 1', 'level 2', 'level1', 'level2',
        # فصول وترامس
        'ترم', 'ترم أول', 'ترم ثاني', 'ترم صيفي', 'فصل دراسي', 'فصل أول',
        'semester', 'term', 'fall', 'spring', 'summer',
        # دفعات (السنة الهجرية السعودية)
        'دفعة', 'دفعه', '1444', '1445', '1446', '1447', '1448',
        'cohort', 'batch',
        # أنشطة دراسية
        'محاضرة', 'سكشن', 'واجب', 'واجبات', 'بحث', 'مشروع', 'تقرير', 'عرض',
        'ميدتيرم', 'فاينل', 'اختبار', 'امتحان', 'كويز',
        'quiz', 'midterm', 'final', 'lecture', 'section', 'assignment', 'exam',
        # مواد دراسية
        'مادة', 'مواد', 'منهج', 'كتاب', 'ملخص', 'شرائح', 'slides',
        'course', 'subject', 'curriculum', 'syllabus',
        # أنظمة جامعية
        'تسجيل', 'add drop', 'withdraw', 'معدل', 'gpa', 'credit',
        'blackboard', 'بلاك بورد', 'moodle', 'مودل', 'tudris', 'تودرس',
        'registration', 'enrollment',
        # أقسام وتخصصات
        'تخصص', 'قسم', 'شعبة', 'فرقة', 'major', 'department',
        # تجمعات طلابية
        'طلاب', 'طالبات', 'تجمع', 'طلابي', 'طالبة', 'students', 'student',
        # مستويات جامعية
        'بكالوريوس', 'ماجستير', 'دكتوراه', 'دبلوم',
        'bachelor', 'master', 'phd', 'diploma', 'degree',
        # إرشاد أكاديمي
        'مرشد', 'إرشاد', 'ساعات معتمدة', 'تخصص ثانٍ',
        'academic', 'advisor', 'credit hours',
        # مناسبات جامعية
        'جدول', 'جدول المحاضرات', 'تقويم', 'تقويم جامعي',
        'orientation', 'تعريف', 'يوم تعريفي',
        # رموز سعودية/خليجية
        'سنة تحضيرية', 'سنة تحضيري', 'تحضيري', 'preparatory', 'foundation',
        'انتساب', 'تعليم عن بعد', 'distance learning',
    ]

    # كلمات تشير لمصدر أكاديمي (حتى لو ما فيه اسم جامعة)
    ACADEMIC_SOURCE_KEYWORDS = [
        'جامعة', 'كلية', 'معهد', 'أكاديمية', 'مدرسة',
        'دراسة', 'تعليم', 'محاضرة', 'مستوى', 'ترم', 'دفعة',
        'طلاب', 'طالبات', 'طلابي', 'بنات', 'بنين',
    ]

    # كلمات إيجابية قوية (تعليمية مؤكدة) — من EducationalFilter السابق
    STRONG_POSITIVE = [
        'جامعة', 'كلية', 'معهد', 'روضة', 'مدرسة',
        'university', 'college', 'institute', 'school', 'academy',
    ]

    # مؤشرات أن الرابط لقناة (وليس مجموعة)
    CHANNEL_INDICATORS = [
        'قناة', 'channel', 'telegram channel', 'قناة تيليجرام',
        'اخبار', 'news', 'إعلام', 'broadcast', 'اذاعة',
    ]

    # ==================================================================
    # دوال مساعدة
    # ==================================================================
    @classmethod
    def _normalize(cls, text: str) -> str:
        """ترجيع النص lowercase مع إزالة المسافات الجانبية."""
        return (text or '').lower().strip()

    @classmethod
    def _extract_username(cls, link: str) -> str:
        """استخراج username من رابط Telegram أو WhatsApp.

        يدعم:
          - https://t.me/username
          - https://telegram.me/username
          - @username
          - https://chat.whatsapp.com/ABC123 (يرجع ABC123)

        Returns:
            username المستخرج أو '' لو ما فيه
        """
        if not link:
            return ''

        # t.me/username أو telegram.me/username
        m = re.search(r'(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)',
                      link, re.IGNORECASE)
        if m:
            return m.group(1)

        # @username
        m = re.search(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)', link)
        if m:
            return m.group(1)

        # WhatsApp: chat.whatsapp.com/ABC123 — نرجع الـ token (للفحص)
        m = re.search(r'chat\.whatsapp\.com/([A-Za-z0-9]+)', link, re.IGNORECASE)
        if m:
            return m.group(1)

        return ''

    @classmethod
    def _contains_any_term(cls, text: str, term_list: List[str]) -> Optional[str]:
        """فحص هل يحتوي النص على أي مصطلح من القائمة.

        Returns:
            أول مصطلح مطابق أو None
        """
        text_lower = cls._normalize(text)
        if not text_lower:
            return None

        for term in term_list:
            if term.lower() in text_lower:
                return term
        return None

    # ==================================================================
    # فحوصات مستقلة (كل واحدة ترجع Tuple[bool, reason])
    # ==================================================================
    @classmethod
    def is_blacklisted(cls, text: str, link_username: str = '',
                       link: str = '', source_group_name: str = '') -> Tuple[bool, str]:
        """فحص القائمة السوداء في كل الحقول.

        Returns:
            (True, reason) لو فيه كلمة سلبية
            (False, '') لو نظيف
        """
        combined = ' '.join([
            text or '', link_username or '',
            link or '', source_group_name or ''
        ])
        term = cls._contains_any_term(combined, cls.HARD_BLACKLIST)
        if term:
            return True, f'blacklist_{term}'
        return False, ''

    @classmethod
    def is_gulf_target(cls, text: str, link_username: str = '',
                       link: str = '') -> Tuple[bool, str]:
        """فحص هل فيه إشارة خليجية أكاديمية واضحة."""
        combined = ' '.join([text or '', link_username or '', link or ''])
        term = cls._contains_any_term(combined, cls.GULF_WHITELIST)
        if term:
            return True, f'gulf_{term}'
        return False, ''

    @classmethod
    def is_academic_context(cls, text: str, link_username: str = '',
                            link: str = '') -> Tuple[bool, str]:
        """فحص هل فيه سياق أكاديمي عام (مستوى/ترم/دفعة/1446...)."""
        combined = ' '.join([text or '', link_username or '', link or ''])
        term = cls._contains_any_term(combined, cls.ACADEMIC_CONTEXT)
        if term:
            return True, f'academic_{term}'
        return False, ''

    @classmethod
    def is_educational(cls, text: str, link_username: str = '') -> Tuple[bool, str]:
        """فحص تعليمي عام (من EducationalFilter السابق — للحالات الضعيفة).

        Returns:
            (True, reason) لو تعليمي
            (False, reason) لو غير تعليمي
        """
        if not text and not link_username:
            return False, 'empty_text'

        combined = f'{text or ""} {link_username or ""}'.lower()

        # فحص الجامعات السعودية (مطابقة قوية)
        for uni in cls.GULF_WHITELIST:
            if uni.lower() in combined:
                return True, f'saudi_uni_{uni}'

        # فحص الكلمات الإيجابية القوية
        for pos in cls.STRONG_POSITIVE:
            if pos.lower() in combined:
                return True, f'positive_{pos}'

        return False, 'no_educational_keywords'

    @classmethod
    def is_likely_channel(cls, text: str, link_username: str = '') -> bool:
        """يتحقق هل الرابط غالباً لقناة (وليس مجموعة)."""
        combined = f'{text or ""} {link_username or ""}'.lower()
        for ind in cls.CHANNEL_INDICATORS:
            if ind.lower() in combined:
                return True
        return False

    @classmethod
    def _is_source_academic_gulf(cls, source_group_name: str) -> Tuple[bool, str]:
        """فحص هل المصدر خليجي أو أكاديمي حسب اسمه.

        Returns:
            (True, reason) لو خليجي/أكاديمي
            (False, '') لو غير ذلك
        """
        if not source_group_name:
            return False, ''

        # 1. خليجي صريح
        is_gulf, gulf_reason = cls.is_gulf_target(source_group_name, '', '')
        if is_gulf:
            return True, f'source_{gulf_reason}'

        # 2. سياق أكاديمي
        is_acad, acad_reason = cls.is_academic_context(source_group_name, '', '')
        if is_acad:
            return True, f'source_{acad_reason}'

        # 3. كلمات أكاديمية عامة (جامعة، كلية، معهد...)
        term = cls._contains_any_term(source_group_name, cls.ACADEMIC_SOURCE_KEYWORDS)
        if term:
            return True, f'source_keyword_{term}'

        return False, ''

    # ==================================================================
    # الفحص الرئيسي — يجمع كل الفلاتر
    # ==================================================================
    @classmethod
    def should_join(cls, text: str, link_username: str = '', link: str = '',
                    source_group_name: str = '',
                    source_phone: str = '') -> Tuple[bool, str]:
        """الفحص الشامل قبل الانضمام.

        الترتيب (من الأقوى للأضعف):
            1. HARD_BLACKLIST → رفض فوري (حتى لو المصدر خليجي)
            2. GULF_WHITELIST → قبول فوري
            3. مصدر خليجي/أكاديمي → قبول
            4. سياق أكاديمي → قبول
            5. is_educational عام → قبول
            6. رفض احتياطي (fail-safe)

        Args:
            text: نص الرسالة اللي فيها الرابط
            link_username: username المستخرج من الرابط (مثلاً KFUPM_students)
            link: الرابط الكامل
            source_group_name: اسم المجموعة المصدر
            source_phone: رقم المراقب (احتياطي — غير مستخدم حالياً)

        Returns:
            (True, reason) لو ينضم
            (False, reason) لو يرفض
        """
        # استخراج username تلقائياً لو ما مرر
        if not link_username:
            link_username = cls._extract_username(link)

        # 1. القائمة السوداء (أقوى رفض) — تفوز دائماً
        is_bad, bad_reason = cls.is_blacklisted(text, link_username, link, source_group_name)
        if is_bad:
            return False, bad_reason

        # 2. القائمة البيضاء الخليجية (أقوى قبول)
        is_gulf, gulf_reason = cls.is_gulf_target(text, link_username, link)
        if is_gulf:
            return True, gulf_reason

        # 3. مصدر خليجي/أكاديمي
        is_source_ok, source_reason = cls._is_source_academic_gulf(source_group_name)
        if is_source_ok:
            return True, source_reason

        # 4. سياق أكاديمي
        is_acad, acad_reason = cls.is_academic_context(text, link_username, link)
        if is_acad:
            return True, acad_reason

        # 5. فلتر تعليمي عام
        is_edu, edu_reason = cls.is_educational(text, link_username)
        if is_edu:
            return True, edu_reason

        # 6. احتياطي — لا تنضم لمجهول
        return False, f'not_confirmed_{edu_reason}'


# ==================================================================
# اختبارات شاملة
# ==================================================================
if __name__ == '__main__':
    test_cases = [
        # (description, text, username, link, source_group, expected)
        ('مجموعة سعودية واضحة', 'انضموا لمجموعة KSU', 'ksu_students',
         'https://t.me/ksu_students', '', True),
        ('مجموعة مستوى بدون جامعة', 'طلاب المستوى الأول', 'level1_2026',
         'https://t.me/level1_2026', '', True),
        ('دفعة 1446 بدون جامعة', 'دفعة 1446 تجمع', 'batch1446',
         'https://t.me/batch1446', '', True),
        ('سياق أكاديمي - محاضرة', 'محاضرة د. أحمد بكرة', 'cs_lectures',
         'https://t.me/cs_lectures', '', True),
        ('مجموعة بيتكوين', 'تداول بيتكوين وربح', 'crypto_signals',
         'https://t.me/crypto_signals', '', False),
        ('بيتكوين بالعربي', 'ربح من بيتكوين', 'btc_arab',
         'https://t.me/btc_arab', '', False),
        ('مجموعة عراقية', 'جامعة بغداد كلية الهندسة', 'uobaghdad',
         'https://t.me/uobaghdad', '', False),
        ('مجموعة مصرية', 'جامعة القاهرة دفعة 2026', 'cu_eg',
         'https://t.me/cu_eg', '', False),
        ('محتوى للكبار', 'محتوى 18+ فقط', 'adult_content',
         'https://t.me/adult_content', '', False),
        ('مجموعة بدون اسم جامعة لكن مصدر خليجي', 'انضموا للمجموعة', 'students_chat',
         'https://t.me/students_chat', 'جامعة الملك سعود طلاب', True),
        ('مجموعة سياق أكاديمي من مصدر خليجي', 'الجميع ينضم', 'test_group',
         'https://t.me/test_group', 'KFUPM | جامعة البترول', True),
        ('بيتكوين داخل مصدر خليجي', 'تداول بيتكوين', 'crypto',
         'https://t.me/crypto', 'جامعة الملك سعود', False),
        ('متجر للبيع', 'متجر ملابس رخيص', 'shop_ksa',
         'https://t.me/shop_ksa', '', False),
        ('مجموعة واتساب سعودية', 'مجموعة طلاب جامعة الملك فهد',
         'https://chat.whatsapp.com/abc123', 'https://chat.whatsapp.com/abc123', '', True),
        ('telegram.me بدلاً من t.me', 'قناة طلابية', 'students',
         'https://telegram.me/students', '', True),
        ('@username مباشر', 'مجموعة طلاب', '@ksu_students',
         '@ksu_students', '', True),
        ('casino night', 'casino night event', 'casino_group',
         'https://t.me/casino_group', '', False),
        ('تجمع طلاب كلية العلوم', 'تجمع طلاب كلية العلوم', 'science_group',
         'https://t.me/science_group', '', True),
        ('hello فقط', 'hello', '', '', 'random group', False),
        ('سنة تحضيرية', 'طلاب السنة التحضيرية', 'prep_year',
         'https://t.me/prep_year', '', True),
        ('مجموعة لبنانية', 'جامعة بيروت', 'aub_leb',
         'https://t.me/aub_leb', '', False),
        ('blackboard نظام سعودي', 'مناقشات blackboard', 'bb_chat',
         'https://t.me/bb_chat', '', True),
        ('مجموعة متابعين', 'تبادل متابعين تيك توك', 'follow4follow',
         'https://t.me/follow4follow', '', False),
        ('مجموعة أردنية', 'جامعة عمّان الأهلية', 'amman_uni',
         'https://t.me/amman_uni', '', False),
    ]

    print('=' * 80)
    print('اختبار GulfFilter v2 — الكلاس المدمج المحسّن')
    print('=' * 80)

    passed = 0
    failed = 0
    failed_cases = []

    for desc, text, username, link, source, expected in test_cases:
        should_join, reason = GulfFilter.should_join(
            text, username, link, source
        )
        status = '✅ PASS' if should_join == expected else '❌ FAIL'
        if should_join == expected:
            passed += 1
        else:
            failed += 1
            failed_cases.append((desc, text, expected, should_join, reason))
        print(f'\n{status} | {desc}')
        print(f'   النص: {text[:50]}')
        print(f'   username: {username[:30]}')
        print(f'   المصدر: {source[:40] if source else "(فاضي)"}')
        print(f'   متوقع: {"قبول" if expected else "رفض"} | '
              f'فعلي: {"قبول" if should_join else "رفض"} ({reason})')

    print('\n' + '=' * 80)
    print(f'النتيجة: {passed}/{passed + failed} نجح')
    if failed == 0:
        print('🎉 كل الاختبارات نجحت!')
    else:
        print(f'⚠️  {failed} اختبار فشل:')
        for desc, text, exp, got, reason in failed_cases:
            print(f'   - {desc}: متوقع={exp}, فعلي={got} ({reason})')
    print('=' * 80)
