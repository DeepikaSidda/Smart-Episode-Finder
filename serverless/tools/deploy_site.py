"""Upload site/index.html to S3, invalidate CloudFront, wait for it to go live."""
import re
import time
import uuid

import boto3
import requests

BUCKET = "spf-site-414691912352"
DIST = "EPGC4W454TWH0"
URL = "https://dad3m4gxlizq4.cloudfront.net/"
LOCAL = r"c:\Users\sidda\Downloads\Smart-Podcast-Finder\serverless\site\index.html"

body = open(LOCAL, "rb").read()
boto3.client("s3").put_object(
    Bucket=BUCKET, Key="index.html", Body=body,
    ContentType="text/html; charset=utf-8", CacheControl="public, max-age=60",
)
boto3.client("cloudfront").create_invalidation(
    DistributionId=DIST,
    InvalidationBatch={
        "Paths": {"Quantity": 2, "Items": ["/index.html", "/"]},
        "CallerReference": "site-" + uuid.uuid4().hex[:10],
    },
)
print(f"uploaded {len(body)} bytes and invalidated")

BADGE_RE = re.compile(r'text-xs font-medium text-[a-z]+-\d+')
for i in range(24):
    h = requests.get(URL, timeout=30).text
    if not BADGE_RE.search(h):
        print(f"live after ~{i * 10}s | {len(h)} bytes | header badges gone")
        print("agent panel present:", "Ask the Agent" in h)
        print("mode switch present:", "mode-before" in h)
        break
    time.sleep(10)
else:
    print("still propagating - try a hard refresh in a minute")
