#!/usr/bin/env python3
"""
E2E Code Verification Script
=============================
يتحقق من سلامة الكود قبل الـ deploy:
1. لا يوجد أي SQL يشير إلى جدول watchers في SQLite
2. كل مراجع role/joiner_enabled تأتي من Supabase
3. الـ Migration method موجود
4. الـ /verify و /sqlite_check commands موجودة
5. الـ Pipeline logging markers موجودة

لا يحتاج Telegram أو Supabase — يفحص الكود فقط.
"""

import re
import sys
from pathlib import Path

BOT_FILE = Path(__file__).parent.parent / "bot.py"


def check_no_sqlite_watchers(content: str) -> bool:
    """تأكد من عدم وجود أي SQL يشير إلى جدول watchers في SQLite."""
    patterns = [
        r"FROM\s+watchers",
        r"INTO\s+watchers",
        r"UPDATE\s+watchers",
        r"DELETE\s+FROM\s+watchers",
        r"CREATE\s+TABLE\s+.*watchers",
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, content, re.IGNORECASE):
            # استبعد التعليقات
            line_start = content.rfind('\n', 0, m.start()) + 1
            line = content[line_start:content.find('\n', m.end())]
            if line.strip().startswith('#'):
                continue
            found.append(f"  Line: {line.strip()[:100]}")
    if found:
        print("❌ FAIL: Found SQLite watchers SQL references:")
        for f in found:
            print(f)
        return False
    print("✅ PASS: No SQLite watchers SQL references found")
    return True


def check_supabase_helpers(content: str) -> bool:
    """تأكد من وجود الـ helper methods الجديدة."""
    required = [
        "_supabase_get_watcher",
        "_supabase_update_watcher",
        "_supabase_count_watchers",
        "_supabase_ensure_schema",
        "_sqlite_list_tables",
    ]
    missing = [m for m in required if f"async def {m}" not in content]
    if missing:
        print(f"❌ FAIL: Missing Supabase helper methods: {missing}")
        return False
    print("✅ PASS: All Supabase helper methods present")
    return True


def check_verify_command(content: str) -> bool:
    """تأكد من وجود /verify و /sqlite_check commands."""
    if 'cmd == "/verify"' not in content:
        print("❌ FAIL: /verify command missing")
        return False
    if 'cmd == "/sqlite_check"' not in content:
        print("❌ FAIL: /sqlite_check command missing")
        return False
    print("✅ PASS: /verify and /sqlite_check commands present")
    return True


def check_pipeline_logging(content: str) -> bool:
    """تأكد من وجود PIPELINE markers في الـ logging."""
    required_markers = [
        "[PIPELINE-1]",
        "[PIPELINE-2]",
        "[PIPELINE-3]",
        "[PIPELINE-4]",
        "[PIPELINE-5]",
        "[PIPELINE-6]",
    ]
    missing = [m for m in required_markers if m not in content]
    if missing:
        print(f"❌ FAIL: Missing PIPELINE markers: {missing}")
        return False
    print("✅ PASS: All PIPELINE logging markers present (1-6)")
    return True


def check_startup_verification(content: str) -> bool:
    """تأكد من وجود Startup Verification مع FATAL handling."""
    checks = [
        "[STARTUP VERIFICATION]",
        "Supabase = SOLE source of truth",
        "sys.exit(1)",
        "has_watchers_table",
        "_supabase_count_watchers",
    ]
    missing = [c for c in checks if c not in content]
    if missing:
        print(f"❌ FAIL: Startup verification missing: {missing}")
        return False
    print("✅ PASS: Startup verification with FATAL handling present")
    return True


def check_migration_call(content: str) -> bool:
    """تأكد من استدعاء _supabase_ensure_schema في main()."""
    if "_supabase_ensure_schema()" not in content:
        print("❌ FAIL: Migration call (_supabase_ensure_schema) not invoked")
        return False
    print("✅ PASS: Migration (_supabase_ensure_schema) called on startup")
    return True


def check_init_db_no_watchers(content: str) -> bool:
    """تأكد أن init_db لا ينشئ جدول watchers."""
    # استخرج دالة init_db
    match = re.search(r"async def init_db\(self\).*?(?=\n    async def |\nclass )", content, re.DOTALL)
    if not match:
        print("❌ FAIL: Could not find init_db method")
        return False
    init_db_body = match.group(0)
    if re.search(r"watchers", init_db_body, re.IGNORECASE) and "CREATE TABLE" in init_db_body:
        # تحقق أكتر — هل في CREATE TABLE watchers؟
        if re.search(r"CREATE\s+TABLE\s+.*watchers", init_db_body, re.IGNORECASE):
            print("❌ FAIL: init_db creates a 'watchers' table in SQLite")
            return False
    print("✅ PASS: init_db does NOT create a 'watchers' table")
    return True


def check_join_group_safe_uses_supabase(content: str) -> bool:
    """تأكد أن _join_group_safe يقرأ role من Supabase وليس SQLite."""
    match = re.search(r"async def _join_group_safe\(.*?(?=\n    async def |\n    def )", content, re.DOTALL)
    if not match:
        print("❌ FAIL: Could not find _join_group_safe method")
        return False
    body = match.group(0)
    if "FROM watchers" in body or "SELECT role FROM watchers" in body:
        print("❌ FAIL: _join_group_safe still reads role from SQLite watchers")
        return False
    if "_supabase_get_watcher" not in body:
        print("❌ FAIL: _join_group_safe does not use _supabase_get_watcher")
        return False
    print("✅ PASS: _join_group_safe reads role/joiner_enabled from Supabase")
    return True


def check_safety_guard_uses_supabase(content: str) -> bool:
    """تأكد أن _safety_guard يقرأ last_join_timestamp من Supabase."""
    match = re.search(r"async def _safety_guard\(.*?(?=\n    async def |\n    def )", content, re.DOTALL)
    if not match:
        print("❌ FAIL: Could not find _safety_guard method")
        return False
    body = match.group(0)
    if "FROM watchers" in body:
        print("❌ FAIL: _safety_guard still queries SQLite watchers")
        return False
    print("✅ PASS: _safety_guard does not query SQLite watchers")
    return True


def check_get_daily_limit_uses_supabase(content: str) -> bool:
    """تأكد أن _get_daily_limit يقرأ role من Supabase."""
    match = re.search(r"async def _get_daily_limit\(.*?(?=\n    async def |\n    def )", content, re.DOTALL)
    if not match:
        print("❌ FAIL: Could not find _get_daily_limit method")
        return False
    body = match.group(0)
    if "FROM watchers" in body:
        print("❌ FAIL: _get_daily_limit still queries SQLite watchers")
        return False
    if "_supabase_get_watcher" not in body:
        print("❌ FAIL: _get_daily_limit does not use _supabase_get_watcher")
        return False
    print("✅ PASS: _get_daily_limit reads role from Supabase")
    return True


def check_enable_disable_joiner_uses_supabase(content: str) -> bool:
    """تأكد أن /enable_joiner و /disable_joiner يكتبان في Supabase."""
    for cmd in ["/enable_joiner", "/disable_joiner"]:
        # ابحث عن الكتلة الخاصة بالأمر
        idx = content.find(f'cmd == "{cmd}"')
        if idx == -1:
            print(f"❌ FAIL: {cmd} command not found")
            return False
        # خذ 500 حرف بعد الأمر
        block = content[idx:idx+800]
        if "_supabase_update_watcher" not in block:
            print(f"❌ FAIL: {cmd} does not use _supabase_update_watcher")
            return False
        if "UPDATE watchers" in block:
            print(f"❌ FAIL: {cmd} still uses SQLite UPDATE watchers")
            return False
    print("✅ PASS: /enable_joiner and /disable_joiner write to Supabase")
    return True


def check_syntax(content: str) -> bool:
    """تأكد من سلامة بناء الكود."""
    import py_compile
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode='w') as f:
        f.write(content)
        tmp_path = f.name
    try:
        py_compile.compile(tmp_path, doraise=True)
        print("✅ PASS: bot.py compiles without syntax errors")
        return True
    except py_compile.PyCompileError as e:
        print(f"❌ FAIL: Syntax error: {e}")
        return False
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def main():
    print("=" * 60)
    print("  E2E CODE VERIFICATION — Supabase Sole Source of Truth")
    print("=" * 60)
    print()

    if not BOT_FILE.exists():
        print(f"❌ bot.py not found at {BOT_FILE}")
        sys.exit(1)

    content = BOT_FILE.read_text(encoding='utf-8')

    checks = [
        ("Syntax check", lambda: check_syntax(content)),
        ("No SQLite watchers SQL", lambda: check_no_sqlite_watchers(content)),
        ("Supabase helpers present", lambda: check_supabase_helpers(content)),
        ("init_db has no watchers table", lambda: check_init_db_no_watchers(content)),
        ("/verify command present", lambda: check_verify_command(content)),
        ("PIPELINE logging markers", lambda: check_pipeline_logging(content)),
        ("Startup verification + FATAL", lambda: check_startup_verification(content)),
        ("Migration call on startup", lambda: check_migration_call(content)),
        ("_join_group_safe uses Supabase", lambda: check_join_group_safe_uses_supabase(content)),
        ("_safety_guard uses Supabase", lambda: check_safety_guard_uses_supabase(content)),
        ("_get_daily_limit uses Supabase", lambda: check_get_daily_limit_uses_supabase(content)),
        ("/enable_joiner + /disable_joiner use Supabase", lambda: check_enable_disable_joiner_uses_supabase(content)),
    ]

    passed = 0
    failed = 0
    for name, check_fn in checks:
        print(f"\n[{name}]")
        try:
            if check_fn():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ ERROR: {e}")
            failed += 1

    print()
    print("=" * 60)
    print(f"  RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    if failed == 0:
        print("✅ ALL CHECKS PASSED — ready for E2E live test on Render")
        sys.exit(0)
    else:
        print("❌ SOME CHECKS FAILED — fix before deploy")
        sys.exit(1)


if __name__ == "__main__":
    main()
