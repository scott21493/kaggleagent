from __future__ import annotations

import re

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'(?i)("username"\s*:\s*")[^"]+("\s*,\s*"key"\s*:\s*")[^"]+(" )?'), '<REDACTED_KAGGLE_JSON>'),
    (re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*'), r'\1<REDACTED_TOKEN>'),
    (re.compile(r'(?i)(api[_-]?key\s*[=:]\s*)[A-Za-z0-9._~+/-]{12,}'), r'\1<REDACTED_API_KEY>'),
    (re.compile(r'(?i)(password\s*[=:]\s*)\S+'), r'\1<REDACTED_PASSWORD>'),
]


def scrub_text(text: str) -> str:
    scrubbed = text
    for pattern, replacement in SECRET_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed
