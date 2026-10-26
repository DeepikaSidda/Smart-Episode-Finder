"""Check which YouTube channels work in the first card, via the live app.

The app only keeps videos of 10 minutes or more, so channels that post short
content will come back with few or no results even though the search succeeded.
"""
import time

import requests

BASE = "https://dad3m4gxlizq4.cloudfront.net"

CASES = [
    ("Lex Fridman", "AI, consciousness, programming"),
    ("freeCodeCamp", "Python, machine learning, web development"),
    ("Huberman Lab", "sleep, focus, dopamine"),
    ("ThePrimeagen", "Rust, vim, developer tooling"),
    ("Apna College", "DSA, placements, coding interviews"),
    ("Y Combinator", "startups, fundraising, founders"),
    ("Computerphile", "algorithms, cryptography, compilers"),
]


def a(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def run(channel, interests):
    try:
        r = requests.post(f"{BASE}/api/analyze", json={
            "channel_query": channel, "interests": interests,
            "max_videos": 5, "provider": "youtube"}, timeout=40)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        wf = r.json()["workflow_id"]
        for _ in range(22):
            time.sleep(4)
            s = requests.get(f"{BASE}/api/status/{wf}", timeout=30)
            if s.status_code != 200:
                return None, a(s.json().get("detail", ""))[:60]
            if s.json()["phase"] == "completed":
                res = requests.get(f"{BASE}/api/result/{wf}", timeout=60).json()
                return res, None
        return None, "timed out"
    except Exception as e:
        return None, f"{type(e).__name__}"


print(f"{'CHANNEL':<16} {'RESOLVED':<26} {'KEPT':>5} {'TOP':>4}  BEST MATCH")
print("-" * 108)
working = []
for channel, interests in CASES:
    res, err = run(channel, interests)
    if err:
        print(f"{channel:<16} {'-- ' + err:<26}")
        continue
    recs = res.get("recommendations") or []
    n = res.get("video_count", 0)
    top = recs[0] if recs else None
    print(f"{channel:<16} {a(res.get('channel_name'))[:24]:<26} {n:>5} "
          f"{(top['score'] if top else 0):>4}  {a(top['title'])[:46] if top else '(none)'}")
    if recs:
        working.append(channel)

print()
print(f"{len(working)}/{len(CASES)} returned recommendations")
print("good for demo:", ", ".join(working))
