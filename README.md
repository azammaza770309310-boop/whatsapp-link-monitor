# WhatsApp Link Monitor v6 - Production Edition

بوت تيليجرام احترافي لسحب روابط واتساب من المجموعات والقنوات تلقائياً.

## المميزات

- ✅ معمارية مزدوجة (User + Bot)
- ✅ سحب جميع أنواع روابط واتساب
- ✅ فلترة الروابط المنتهية تلقائياً
- ✅ مسح تاريخي متزايد ذكي
- ✅ أوامر تحكم كاملة عبر تيليجرام
- ✅ إعادة اتصال تلقائي
- ✅ جاهز للنشر على Render/Railway

## النشر على Render

### Build Command
```
pip install -r requirements.txt
```

### Start Command
```
python monitor_v6.py
```

### Service Type
**Background Worker** (not Web Service)

### Environment Variables
انسخ قيم accounts.env.example إلى Environment Variables في Render.

## ملاحظة تسجيل الدخول

عند أول نشر، ستحتاج لتسجيل الدخول محلياً ورفع ملف الجلسة.
