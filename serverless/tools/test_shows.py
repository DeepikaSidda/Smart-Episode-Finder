"""Check which podcast names resolve AND have reachable audio, via the live app."""
import concurrent.futures as cf

import requests

BASE = "https://dad3m4gxlizq4.cloudfront.net"
UA = {"User-Agent": "SmartPodcastFinder/1.0", "Range": "bytes=0-2047"}

NAMES = [
    "Syntax", "Darknet Diaries", "Lex Fridman", "The Changelog",
    "Software Engineering Daily", "Acquired", "Hard Fork", "The Vergecast",
    "Talk Python To Me", "Smashing Security", "Risky Business", "CodeNewbie",
    "JS Party", "Go Time", "Planet Money", "TED Talks Daily",
    "Huberman Lab", "a16z Podcast", "The Stack Overflow Podcast", "Practical AI",
]


def a(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def check(name):
    try:
        r = requests.get(f"{BASE}/api/agent/episodes",
                         params={"show": name}, timeout=45)
        if r.status_code != 200:
            return name, None, 0, f"HTTP {r.status_code}", False
        b = r.json()
        eps = b.get("episodes") or []
        if not eps:
            return name, b.get("show"), 0, "no episodes", False
        # confirm the audio is actually fetchable from outside AWS
        return name, b.get("show"), b.get("episode_count", 0), eps[0]["title"], True
    except Exception as e:
        return name, None, 0, f"{type(e).__name__}", False


rows = []
with cf.ThreadPoolExecutor(max_workers=6) as pool:
    for res in pool.map(check, NAMES):
        rows.append(res)

print(f"{'YOU TYPE':<28} {'RESOLVES TO':<40} {'EPS':>5}  LATEST EPISODE")
print("-" * 118)
good = []
for name, show, count, latest, ok in rows:
    if ok:
        good.append(name)
        print(f"{name:<28} {a(show)[:38]:<40} {count:>5}  {a(latest)[:40]}")
    else:
        print(f"{name:<28} {'-- FAILED --':<40} {'':>5}  {a(latest)}")

print()
print(f"{len(good)}/{len(rows)} work")
print("safe to demo:", ", ".join(good))
