#!/usr/bin/env python3
"""جلب سجلات Render الإنتاجية مع ترقيم صفحات وفلترة."""
import json
import sys
import urllib.request

CREDS = json.load(open('/home/z/.secrets/creds.json'))
KEY = CREDS['render_api_key']
OWNER = "tea-da13gk5bedkc73bv3qb0"
SERVICE = "srv-da15nj3l550s73et80p0"

BASE = "https://api.render.com/v1/logs"


def fetch_page(cursor=None, limit=300):
    url = f"{BASE}?resource={SERVICE}&ownerId={OWNER}&limit={limit}"
    if cursor:
        url += f"&cursor={cursor}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_all(pages=6):
    import time
    logs = []
    end = None
    for _ in range(pages):
        url = (f"{BASE}?resource={SERVICE}&ownerId={OWNER}&limit=300"
               + (f"&endTime={end}" if end else ""))
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {KEY}"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    page = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise
        logs.extend(page.get('logs', []))
        if not page.get('hasMore'):
            break
        end = page.get('nextEndTime')
        if not end:
            break
        time.sleep(1.2)  # احترام حد معدل Render logs API
    return logs


if __name__ == "__main__":
    keyword = sys.argv[1] if len(sys.argv) > 1 else ""
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    logs = fetch_all(pages)
    print(f"fetched {len(logs)} log lines", file=sys.stderr)
    for l in reversed(logs):  # الأقدم أولًا
        msg = str(l.get('message', ''))
        if keyword and keyword not in msg:
            continue
        ts = str(l.get('timestamp', ''))[:19]
        # اقتطاع البادئة الزمنية المكررة من داخل الرسالة
        short = msg[msg.find(' - ') + 3:] if ' - ' in msg else msg
        print(f"{ts} | {short[:200]}")
