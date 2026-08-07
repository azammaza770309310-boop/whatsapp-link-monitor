#!/usr/bin/env python3
"""Replace _join_group_safe method in bot.py with new version that includes verification."""
import re
from pathlib import Path

bot_file = Path("/home/z/my-project/bot.py")
new_method = Path("/home/z/my-project/scripts/new_join_method.py").read_text(encoding='utf-8')

content = bot_file.read_text(encoding='utf-8')

# Find the old _join_group_safe method — from "async def _join_group_safe" to the next "async def " at same indent
# Pattern: starts with "    async def _join_group_safe" and ends before next "    async def " or "    def "
pattern = re.compile(
    r'    async def _join_group_safe\(self, client, link_data: dict, phone: str\):.*?(?=\n    async def |\n    def |\nclass |\Z)',
    re.DOTALL
)

match = pattern.search(content)
if not match:
    print("ERROR: Could not find _join_group_safe method")
    exit(1)

old_method = match.group(0)
print(f"Old method length: {len(old_method)} chars, {old_method.count(chr(10))} lines")

# Replace
new_content = content[:match.start()] + new_method.rstrip() + content[match.end():]

# Write
bot_file.write_text(new_content, encoding='utf-8')
print(f"✅ Replaced _join_group_safe method")
print(f"New file size: {len(new_content)} chars")
