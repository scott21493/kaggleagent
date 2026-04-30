from __future__ import annotations

from arena.security.scrubber import scrub_text


def test_scrubs_bearer_and_password() -> None:
    text = 'Authorization: Bearer abcdefghijklmnop password=supersecret'
    scrubbed = scrub_text(text)
    assert 'abcdefghijklmnop' not in scrubbed
    assert 'supersecret' not in scrubbed
    assert '<REDACTED_TOKEN>' in scrubbed
