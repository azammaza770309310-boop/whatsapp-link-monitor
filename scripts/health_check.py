#!/usr/bin/env python3
"""Health check script."""
import sys
import urllib.request
import urllib.error


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:10000/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            print(f"✅ {response.status} - {url}")
            print(response.read().decode())
    except urllib.error.URLError as e:
        print(f"❌ {url} - {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ {url} - {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
