"""Generate the S3 copy of the UI from static/index.html.

Kept as a build step rather than editing static/index.html in place, so the
original stays valid for the EC2 deployment while it is still around as a
rollback option.

Patches applied:
  1. drop the Temporal UI link  (localhost:8233 — Temporal no longer exists)
  2. drop the API Docs link     (FastAPI Swagger is not served by the Lambda)
  3. swap the Temporal logo for the AWS Lambda logo
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "static" / "index.html"
OUT = ROOT / "serverless" / "site" / "index.html"

TEMPORAL_LINK = '      <a href="http://localhost:8233" target="_blank" class="text-slate-600 hover:text-violet-400 transition">Temporal UI</a>\n'
DOCS_LINK = '      <a href="/docs" target="_blank" class="text-slate-600 hover:text-violet-400 transition">API Docs</a>\n'

# The stale Temporal / Gemini logos were removed from static/index.html at source,
# so they are asserted absent below rather than patched here.
REPLACEMENTS = [
    ("Temporal UI link", TEMPORAL_LINK, ""),
    ("API Docs link", DOCS_LINK, ""),
]


def main() -> int:
    html = SRC.read_text(encoding="utf-8")
    failures = []

    for label, old, new in REPLACEMENTS:
        if old not in html:
            failures.append(label)
            continue
        html = html.replace(old, new)

    if failures:
        print("ERROR: these patches did not match, source may have changed:")
        for f in failures:
            print("  -", f)
        return 1

    # Guard against shipping anything that points back at the old stack.
    for banned in ("localhost:8233", '"/docs"', "simpleicons.org/temporal",
                   "googlegemini"):
        if banned in html:
            print(f"ERROR: '{banned}' still present in output")
            return 1

    # Guard against shipping a page without the agent panel.
    for required in ("Ask the Agent", "/api/agent", "mode-before", "mode-after"):
        if required not in html:
            print(f"ERROR: expected '{required}' in output but it is missing")
            return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
