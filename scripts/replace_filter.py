#!/usr/bin/env python3
"""سكربت لاستبدال EducationalFilter بـ GulfFilter في bot.py."""

# اقرأ الكلاس الجديد من scripts/gulf_filter_v2.py (بدون __main__)
with open('/home/z/my-project/scripts/gulf_filter_v2.py', 'r') as f:
    new_content = f.read()

# استخرج الكلاس فقط (من "class GulfFilter:" إلى "# =====...=====\n# اختبارات")
start_marker = 'class GulfFilter:'
end_marker = '# ==================================================================\n# اختبارات شاملة'

start_idx = new_content.find(start_marker)
end_idx = new_content.find(end_marker)

if start_idx == -1 or end_idx == -1:
    print("❌ Could not find class boundaries in gulf_filter_v2.py")
    exit(1)

# الكلاس + alias
new_class_code = new_content[start_idx:end_idx].rstrip()
# أضف alias للاسم القديم
new_class_code += '''

# Alias — EducationalFilter هو الاسم القديم المستخدم في باقي الكود
EducationalFilter = GulfFilter
'''

# اقرأ bot.py
with open('/home/z/my-project/bot.py', 'r') as f:
    bot_content = f.read()

# ابحث عن حدود EducationalFilter القديم
old_start_marker = 'class EducationalFilter:'
old_end_marker = '\n# -------------------------------------------------------------------\n# Message Formatter'

old_start_idx = bot_content.find(old_start_marker)
old_end_idx = bot_content.find(old_end_marker, old_start_idx)

if old_start_idx == -1 or old_end_idx == -1:
    print("❌ Could not find EducationalFilter boundaries in bot.py")
    exit(1)

# استبدل
new_bot_content = bot_content[:old_start_idx] + new_class_code + bot_content[old_end_idx:]

# اكتب
with open('/home/z/my-project/bot.py', 'w') as f:
    f.write(new_bot_content)

print(f"✅ Replaced EducationalFilter with GulfFilter")
print(f"   Old class: {old_end_idx - old_start_idx} chars")
print(f"   New class: {len(new_class_code)} chars")
print(f"   bot.py size: {len(new_bot_content)} chars")
