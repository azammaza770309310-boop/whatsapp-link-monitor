#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""اختبار سريع لـ regex روابط واتساب"""

import re

WHATSAPP_LINK_PATTERN = re.compile(
    r"""
    (?:https?://)?
    (?:
        chat\.whatsapp\.com
      | whatsapp\.com/channel
      | whatsapp\.com/contact
      | wa\.me
      | api\.whatsapp\.com
      | l\.whatsapp\.com
    )
    [^\s<>"'\)\]]*
    """,
    re.IGNORECASE | re.VERBOSE,
)

tests = [
    "شوف هذا الرابط https://chat.whatsapp.com/ABC123XYZ",
    "انضم للمجموعة chat.whatsapp.com/INVITE789",
    "قناة جديدة: https://whatsapp.com/channel/0029VaoPPPKFMlgqOLXNW93a",
    "تواصل معي wa.me/967770309310",
    "http://wa.me/message/ABCDEF123456",
    "أرسل عبر https://api.whatsapp.com/send?phone=967770309310&text=hello",
    "رمز QR: api.whatsapp.com/q?code=XYZ",
    "رابط مختصر: https://l.whatsapp.com/l/1234",
    "نص بدون روابط",
    "روابط متعددة chat.whatsapp.com/A1 و wa.me/967123456789 و whatsapp.com/channel/B2",
    "رابط مع نقطة في النهاية: https://chat.whatsapp.com/Test123.",
    "رابط مع فواصل: (https://wa.me/96777777)",
]

for t in tests:
    matches = WHATSAPP_LINK_PATTERN.findall(t)
    cleaned = [m.rstrip(".,;:!?)]}>\"'") for m in matches]
    print(f"\nالنص: {t}")
    print(f"  → الروابط: {cleaned}")
