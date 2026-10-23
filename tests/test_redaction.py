from vision_app.redaction import redact_text, redact_value


def test_redacts_hf_and_bearer():
    text = "HF_TOKEN=hf_1234567890abcdefghijklmnop Authorization: Bearer abc123"
    out = redact_text(text)
    assert "hf_123" not in out
    assert "abc123" not in out
    assert "***" in out


def test_redacts_sensitive_keys_recursively():
    out = redact_value({"api_key": "secret", "nested": [{"token": "secret"}]})
    assert out == {"api_key": "***", "nested": [{"token": "***"}]}
