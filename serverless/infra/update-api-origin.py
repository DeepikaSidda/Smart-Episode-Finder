"""Repoint the CloudFront 'lambda-api' origin at API Gateway.

Lambda function URLs behind CloudFront OAC require callers to send
x-amz-content-sha256 for POST bodies (Lambda rejects unsigned payloads), which a
plain browser fetch() cannot do. API Gateway HTTP API accepts POST natively and
emits the same payload format 2.0, so the handler is unchanged.
"""
import boto3

DIST_ID = "EPGC4W454TWH0"
ORIGIN_ID = "lambda-api"
NEW_DOMAIN = "xcz68za6vg.execute-api.us-east-1.amazonaws.com"

cf = boto3.client("cloudfront")

resp = cf.get_distribution_config(Id=DIST_ID)
etag = resp["ETag"]
cfg = resp["DistributionConfig"]

found = False
for origin in cfg["Origins"]["Items"]:
    if origin["Id"] != ORIGIN_ID:
        continue
    found = True
    print(f"old domain: {origin['DomainName']}")
    origin["DomainName"] = NEW_DOMAIN
    # OAC is only for S3 / Lambda function URL origins; API Gateway is public.
    origin["OriginAccessControlId"] = ""
    origin["CustomOriginConfig"] = {
        "HTTPPort": 80,
        "HTTPSPort": 443,
        "OriginProtocolPolicy": "https-only",
        "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
        "OriginReadTimeout": 60,
        "OriginKeepaliveTimeout": 5,
    }
    print(f"new domain: {origin['DomainName']}")

if not found:
    raise SystemExit(f"origin '{ORIGIN_ID}' not found")

out = cf.update_distribution(Id=DIST_ID, IfMatch=etag, DistributionConfig=cfg)
print("status:", out["Distribution"]["Status"])
