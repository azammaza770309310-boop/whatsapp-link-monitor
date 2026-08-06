#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for the WhatsApp/Telegram Link Monitor (monitor_v12.py).

These tests cover the pure functions (regex extraction, advertiser detection,
university matching, sender-contact extraction, JSON cleaning) and the
HelpRequestDetector. They DO NOT hit Telegram, the database, or any AI API.

Run:
    cd download
    python -m pytest tests/test_monitor_v12.py -v
or:
    python tests/test_monitor_v12.py
"""
import os
import sys
import unittest
from pathlib import Path

# Make monitor_v12.py importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# Suppress logging noise during tests
import logging
logging.disable(logging.CRITICAL)


class TestLinkExtraction(unittest.TestCase):
    """Tests for extract_whatsapp_telegram_links()."""

    def test_extracts_whatsapp_invite_link(self):
        from monitor_v12 import extract_whatsapp_telegram_links
        text = "انضموا لمجموعتنا https://chat.whatsapp.com/ABC123xyz"
        links = extract_whatsapp_telegram_links(text)
        self.assertEqual(len(links), 1)
        self.assertIn("ABC123xyz", links[0])

    def test_extracts_multiple_links(self):
        from monitor_v12 import extract_whatsapp_telegram_links
        text = ("روابط: https://chat.whatsapp.com/AAA "
                "و https://chat.whatsapp.com/BBB")
        links = extract_whatsapp_telegram_links(text)
        self.assertEqual(len(links), 2)

    def test_extracts_t_me_link(self):
        from monitor_v12 import extract_whatsapp_telegram_links
        text = "قناة جديدة: https://t.me/mychannel"
        links = extract_whatsapp_telegram_links(text)
        self.assertEqual(len(links), 1)
        self.assertIn("mychannel", links[0])

    def test_excludes_wa_me_direct_chat(self):
        # wa.me/PHONE without /message is direct chat — should be filtered out
        # because the regex only matches chat.whatsapp.com
        from monitor_v12 import extract_whatsapp_telegram_links
        text = "راسلني: https://wa.me/96650000000"
        links = extract_whatsapp_telegram_links(text)
        self.assertEqual(len(links), 0, "wa.me direct chat must be excluded")

    def test_excludes_t_me_joinchat(self):
        from monitor_v12 import extract_whatsapp_telegram_links
        text = "انضم: https://t.me/+abc123"
        links = extract_whatsapp_telegram_links(text)
        self.assertEqual(len(links), 0, "joinchat links must be excluded")

    def test_excludes_t_me_message_link(self):
        from monitor_v12 import extract_whatsapp_telegram_links
        text = "شوف: https://t.me/c/123456/78"
        links = extract_whatsapp_telegram_links(text)
        self.assertEqual(len(links), 0, "private message links must be excluded")

    def test_deduplicates_links(self):
        from monitor_v12 import extract_whatsapp_telegram_links
        text = ("https://chat.whatsapp.com/SAME123 "
                "https://chat.whatsapp.com/SAME123")
        links = extract_whatsapp_telegram_links(text)
        self.assertEqual(len(links), 1)

    def test_strips_trailing_punctuation(self):
        from monitor_v12 import extract_whatsapp_telegram_links
        text = "روابط (https://chat.whatsapp.com/ABC123)."
        links = extract_whatsapp_telegram_links(text)
        self.assertEqual(len(links), 1)
        self.assertFalse(links[0].endswith("."))

    def test_empty_text_returns_empty_list(self):
        from monitor_v12 import extract_whatsapp_telegram_links
        self.assertEqual(extract_whatsapp_telegram_links(""), [])
        self.assertEqual(extract_whatsapp_telegram_links(None), [])

    def test_no_link_in_text_returns_empty(self):
        from monitor_v12 import extract_whatsapp_telegram_links
        self.assertEqual(extract_whatsapp_telegram_links("hello world"), [])


class TestAdvertiserDetection(unittest.TestCase):
    """Tests for is_advertiser_message()."""

    def test_pure_greeting_is_not_ad(self):
        from monitor_v12 import is_advertiser_message
        self.assertFalse(is_advertiser_message("السلام عليكم، كيف حالكم؟"))

    def test_phone_number_triggers_ad(self):
        from monitor_v12 import is_advertiser_message
        self.assertTrue(is_advertiser_message("اتصل بي +966500000000"))

    def test_marketing_keyword_triggers_ad(self):
        from monitor_v12 import is_advertiser_message
        self.assertTrue(is_advertiser_message("احجز الآن - عرض محدود!"))

    def test_long_message_triggers_ad(self):
        from monitor_v12 import is_advertiser_message
        # 6+ lines = ad
        text = "\n".join(["سطر"] * 7)
        self.assertTrue(is_advertiser_message(text))

    def test_normal_message_not_ad(self):
        from monitor_v12 import is_advertiser_message
        self.assertFalse(is_advertiser_message("محتاج مساعدة في مادة الرياضيات"))

    def test_empty_text_not_ad(self):
        from monitor_v12 import is_advertiser_message
        self.assertFalse(is_advertiser_message(""))
        self.assertFalse(is_advertiser_message(None))


class TestUniversityDetection(unittest.TestCase):
    """Tests for is_target_university_message()."""

    def test_saudi_ahliya_detected(self):
        from monitor_v12 import is_target_university_message
        self.assertTrue(is_target_university_message("جامعة الأهلية السعودية"))

    def test_kuwait_university_detected(self):
        from monitor_v12 import is_target_university_message
        self.assertTrue(is_target_university_message("Kuwait University"))

    def test_qatar_detected(self):
        from monitor_v12 import is_target_university_message
        self.assertTrue(is_target_university_message("جامعة قطر"))

    def test_bahrain_detected(self):
        from monitor_v12 import is_target_university_message
        self.assertTrue(is_target_university_message("University of Bahrain"))

    def test_uae_detected(self):
        from monitor_v12 import is_target_university_message
        self.assertTrue(is_target_university_message("Khalifa University"))

    def test_non_target_university_not_detected(self):
        from monitor_v12 import is_target_university_message
        self.assertFalse(is_target_university_message("هذه رسالة عادية بدون جامعة"))

    def test_empty_text(self):
        from monitor_v12 import is_target_university_message
        self.assertFalse(is_target_university_message(""))
        self.assertFalse(is_target_university_message(None))


class TestSenderContactExtraction(unittest.TestCase):
    """Tests for extract_sender_contact()."""

    def test_extracts_saudi_phone(self):
        from monitor_v12 import extract_sender_contact
        result = extract_sender_contact("اتصل: +966500000000")
        self.assertIn("+966", result)

    def test_extracts_username(self):
        from monitor_v12 import extract_sender_contact
        result = extract_sender_contact("تواصل: @myuser")
        self.assertIn("@myuser", result)

    def test_no_contact_returns_empty(self):
        from monitor_v12 import extract_sender_contact
        self.assertEqual(extract_sender_contact("hello"), "")

    def test_empty_text_returns_empty(self):
        from monitor_v12 import extract_sender_contact
        self.assertEqual(extract_sender_contact(""), "")
        self.assertEqual(extract_sender_contact(None), "")


class TestJsonCleaning(unittest.TestCase):
    """Tests for _extract_clean_json()."""

    def test_plain_json(self):
        from monitor_v12 import _extract_clean_json
        text = '{"should_save": true, "link": "https://example.com"}'
        result = _extract_clean_json(text)
        self.assertEqual(result, text)

    def test_markdown_code_block(self):
        from monitor_v12 import _extract_clean_json
        text = '```json\n{"should_save": true}\n```'
        result = _extract_clean_json(text)
        self.assertEqual(result, '{"should_save": true}')

    def test_markdown_code_block_no_lang(self):
        from monitor_v12 import _extract_clean_json
        text = '```\n{"should_save": false}\n```'
        result = _extract_clean_json(text)
        self.assertEqual(result, '{"should_save": false}')

    def test_text_before_json(self):
        from monitor_v12 import _extract_clean_json
        text = 'Here is the result: {"should_save": true}'
        result = _extract_clean_json(text)
        self.assertEqual(result, '{"should_save": true}')

    def test_text_after_json(self):
        from monitor_v12 import _extract_clean_json
        text = '{"should_save": true} That is the answer.'
        result = _extract_clean_json(text)
        self.assertEqual(result, '{"should_save": true}')

    def test_empty_text(self):
        from monitor_v12 import _extract_clean_json
        self.assertEqual(_extract_clean_json(""), "")
        self.assertEqual(_extract_clean_json(None), "")

    def test_actual_ai_response_with_arabic(self):
        """Simulate a real Groq response that may have markdown + Arabic."""
        from monitor_v12 import _extract_clean_json
        import json
        text = '''Sure, here is the analysis:
```json
{
    "should_save": true,
    "link": "https://chat.whatsapp.com/ABC123",
    "link_type": "whatsapp",
    "sender_contact": "📱 +966500000000",
    "is_advertisement": false,
    "country": "السعودية",
    "description": "مجموعة جامعية"
}
```
'''
        clean = _extract_clean_json(text)
        parsed = json.loads(clean)  # must parse without error
        self.assertTrue(parsed["should_save"])
        self.assertEqual(parsed["country"], "السعودية")


class TestHelpRequestDetector(unittest.TestCase):
    """Tests for HelpRequestDetector.is_help_request()."""

    def test_short_message_rejected(self):
        from monitor_v12 import HelpRequestDetector
        is_help, kws = HelpRequestDetector.is_help_request("مساعدة", min_length=20)
        self.assertFalse(is_help)

    def test_long_message_rejected(self):
        from monitor_v12 import HelpRequestDetector
        long_text = "مساعدة " + "x" * 5000
        is_help, kws = HelpRequestDetector.is_help_request(long_text, max_length=2000)
        self.assertFalse(is_help)

    def test_help_keyword_detected(self):
        from monitor_v12 import HelpRequestDetector
        text = "محتاج مساعدة في مادة الرياضيات جامعة الملك سعود"
        is_help, kws = HelpRequestDetector.is_help_request(text)
        self.assertTrue(is_help)
        self.assertGreater(len(kws), 0)

    def test_spam_message_rejected(self):
        from monitor_v12 import HelpRequestDetector
        text = ("free bitcoin casino earn money fast! " * 5) + " need help with homework"
        is_help, kws = HelpRequestDetector.is_help_request(text)
        self.assertFalse(is_help, "spam keywords must reject the message")

    def test_empty_text(self):
        from monitor_v12 import HelpRequestDetector
        is_help, kws = HelpRequestDetector.is_help_request("")
        self.assertFalse(is_help)
        self.assertEqual(kws, [])

    def test_none_text(self):
        from monitor_v12 import HelpRequestDetector
        is_help, kws = HelpRequestDetector.is_help_request(None)
        self.assertFalse(is_help)


class TestMessageFormatterSecurity(unittest.TestCase):
    """Tests for HTML injection prevention in MessageFormatter."""

    def test_xss_in_group_name_is_escaped(self):
        from monitor_v12 import MessageFormatter
        from datetime import datetime
        malicious = '<script>alert("xss")</script>'
        html = MessageFormatter.format_link_message(
            group_name=malicious,
            sender_name="user",
            sender_contact="",
            message_date=datetime.now(),
            link="https://chat.whatsapp.com/ABC",
            message_text="text",
            source_phone="+966500000000",
        )
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_href_injection_in_link_blocked(self):
        from monitor_v12 import MessageFormatter
        from datetime import datetime
        # Try to break out of the href attribute
        malicious_link = 'https://evil.com" onclick="alert(1)'
        html = MessageFormatter.format_link_message(
            group_name="g",
            sender_name="s",
            sender_contact="",
            message_date=datetime.now(),
            link=malicious_link,
            message_text="text",
            source_phone="+966500000000",
        )
        # The malicious link must NOT appear as a clean href
        self.assertNotIn('href="https://evil.com"', html)
        # The double-quotes inside the URL must be escaped to &quot;
        # so the HTML parser treats everything as part of the href value,
        # not as a separate onclick= attribute.
        # An unescaped quote-then-attribute pattern would be: " onclick="
        # which is what we look for to detect a successful breakout.
        import re
        # Look for an unescaped double-quote followed by space and an attribute name
        breakout_pattern = re.search(r'(?<!&quot;)"\s+onclick=', html)
        self.assertIsNone(breakout_pattern,
                          "Quotes must be escaped to prevent attribute breakout")

    def test_javascript_scheme_link_blocked(self):
        from monitor_v12 import MessageFormatter
        from datetime import datetime
        malicious_link = 'javascript:alert(1)'
        html = MessageFormatter.format_link_message(
            group_name="g",
            sender_name="s",
            sender_contact="",
            message_date=datetime.now(),
            link=malicious_link,
            message_text="text",
            source_phone="+966500000000",
        )
        # Must NOT have an href with javascript scheme
        self.assertNotIn('href="javascript:', html)

    def test_normal_https_link_works(self):
        from monitor_v12 import MessageFormatter
        from datetime import datetime
        html = MessageFormatter.format_link_message(
            group_name="g",
            sender_name="s",
            sender_contact="",
            message_date=datetime.now(),
            link="https://chat.whatsapp.com/ABC123",
            message_text="text",
            source_phone="+966500000000",
        )
        self.assertIn('href="https://chat.whatsapp.com/ABC123"', html)


class TestConfig(unittest.TestCase):
    """Tests for Config class."""

    def test_missing_required_env_returns_errors(self):
        # The Config class loads from accounts.env via dotenv.
        # We simulate missing vars by temporarily renaming accounts.env
        # and clearing the env vars.
        env_backup = dict(os.environ)
        accounts_env_path = Path("accounts.env")
        backup_path = Path("accounts.env.test_backup")
        renamed = False
        try:
            if accounts_env_path.exists():
                accounts_env_path.rename(backup_path)
                renamed = True
            for k in ("API_ID", "API_HASH", "BOT_TOKEN", "CHANNEL_ID"):
                os.environ.pop(k, None)
            from monitor_v12 import Config
            cfg = Config()
            errors = cfg.validate()
            self.assertGreater(len(errors), 0)
            self.assertIn("API_ID required", errors)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)
            if renamed and backup_path.exists():
                backup_path.rename(accounts_env_path)

    def test_valid_config_no_errors(self):
        env_backup = dict(os.environ)
        try:
            os.environ["API_ID"] = "12345"
            os.environ["API_HASH"] = "abc"
            os.environ["BOT_TOKEN"] = "tok"
            os.environ["CHANNEL_ID"] = "-100123"
            from monitor_v12 import Config
            cfg = Config()
            self.assertEqual(cfg.validate(), [])
            self.assertEqual(cfg.api_id, 12345)
            self.assertEqual(cfg.channel_id, -100123)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    def test_invalid_owner_id_ignored(self):
        env_backup = dict(os.environ)
        try:
            os.environ["OWNER_ID"] = "not-a-number"
            from monitor_v12 import Config
            cfg = Config()
            self.assertIsNone(cfg.owner_id)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)


class TestConstantsDefined(unittest.TestCase):
    """Verify previously-missing constants are now defined (regression test)."""

    def test_spam_keywords_defined(self):
        from monitor_v12 import SPAM_KEYWORDS
        self.assertIsInstance(SPAM_KEYWORDS, list)
        self.assertGreater(len(SPAM_KEYWORDS), 0)

    def test_help_keywords_defined(self):
        from monitor_v12 import HELP_KEYWORDS
        self.assertIsInstance(HELP_KEYWORDS, list)
        self.assertGreater(len(HELP_KEYWORDS), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
