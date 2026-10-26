"""Strip credentials out of text before it is logged or returned to a caller.

Why this exists: requests/httpx put the full request URL into their exception
messages. The YouTube Data API takes its credential as a `key=` query parameter, so
an upstream 429 produced an error string containing the live API key — and the API
handler returned that string to the caller. A public endpoint was handing out the
key to anyone who could trigger an error.
"""
import re

_PATTERNS = [
    # Query-string credentials: key=, api_key=, token=, access_token=, client_secret=
    (re.compile(r"(?i)\b(key|api_key|apikey|token|access_token|client_secret|password)=([^&\s\"']+)"),
     r"\1=[REDACTED]"),
    # Google API key format, in case it appears outside a query string
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{10,}"), "[REDACTED]"),
    # Bearer / Basic authorization headers
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-=/+]{8,}"), r"\1 [REDACTED]"),
    # AWS access key ids
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{8,}"), "[REDACTED]"),
]


def redact(text) -> str:
    """Return `text` with any recognisable credential replaced."""
    out = str(text)
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out
