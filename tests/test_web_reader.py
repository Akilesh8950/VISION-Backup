import pytest

from vision_app.tools.web_reader import extract_readable, validate_public_url


def test_extract_readable_skips_scripts():
    title, text = extract_readable("<html><head><title>Hi</title><script>bad()</script></head><body><p>One</p><p>Two</p></body></html>")
    assert title == "Hi"
    assert "One" in text and "bad" not in text


def test_blocks_loopback_and_credentials():
    with pytest.raises(PermissionError):
        validate_public_url("http://127.0.0.1/admin")
    with pytest.raises(PermissionError):
        validate_public_url("https://user:pass@example.com")
