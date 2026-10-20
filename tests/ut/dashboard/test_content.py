import json

import pytest

from miniagent.dashboard.json_content import ContentError, parse_json
from miniagent.dashboard.redaction import redact_json, redact_text


@pytest.mark.parametrize(
    ("value",),
    [
        ({"message": "[" * 65 + "]" * 65, "secret": "hide-me"},),
        ({"message": '"\\' + "[]" * 65, "secret": "hide-me"},),
        ({"{}" * 65 + '"\\': "visible-ok", "secret": "hide-me"},),
    ],
)
def test_json_strings_do_not_increase_depth(value):
    result = redact_json(parse_json(json.dumps(value)))
    assert result == {**value, "secret": "[已脱敏]"}


@pytest.mark.parametrize(("depth",), [(62,), (63,), (64,), (65,), (66,)])
def test_json_limits_and_duplicates(depth):
    text = "[" * depth + "0" + "]" * depth
    if depth <= 64:
        assert parse_json(text) is not None
    else:
        with pytest.raises(ContentError, match="json_depth"):
            parse_json(text)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ('{"a":1,"a":2}', "json_duplicate_key"),
        ('{"a":{"x":1,"x":2}}', "json_duplicate_key"),
        ("NaN", "json_nonfinite"),
        ("Infinity", "json_nonfinite"),
        ("-Infinity", "json_nonfinite"),
        ("1e9999", "json_nonfinite"),
        ("[}", "json_invalid"),
        (b'"\xff"', "json_invalid"),
    ],
)
def test_json_rejects_ambiguous_content(text, code):
    with pytest.raises(ContentError, match=code):
        parse_json(text)


@pytest.mark.parametrize(
    ("key",),
    [
        (key,)
        for key in (
            "token",
            "TOKEN",
            "api_key",
            "API-KEY",
            "ApiKey",
            "password",
            "Secret",
            "authorization",
            "access_token",
            "input_tokens",
            "nested-secret",
            "auth_token",
        )
    ],
)
def test_redaction_preserves_only_usage_counts(key):
    assert redact_json({key: 123456789}) == {key: "[已脱敏]"}
    assert redact_json({"input_tokens": 100}, schema="run_usage") == {
        "input_tokens": 100
    }
    assert redact_json({"input_tokens": "private"}, schema="run_usage") == {
        "input_tokens": "[已脱敏]"
    }
    assert redact_json({"nested": {"input_tokens": 123}}, schema="run_usage") == {
        "nested": {"input_tokens": "[已脱敏]"}
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            {
                "type": "compaction_attempt_recorded",
                "result": {"usage": {"input_tokens": 10}},
            },
            {
                "type": "compaction_attempt_recorded",
                "result": {"usage": {"input_tokens": 10}},
            },
        ),
        (
            {
                "type": "compaction_completed",
                "attempts": [{"usage": {"output_tokens": 2}}],
            },
            {
                "type": "compaction_completed",
                "attempts": [{"usage": {"output_tokens": 2}}],
            },
        ),
        (
            {
                "type": "compaction_failed",
                "attempts": [{"usage": {"total_tokens": 12}}],
            },
            {
                "type": "compaction_failed",
                "attempts": [{"usage": {"total_tokens": 12}}],
            },
        ),
        (
            {"type": "other", "result": {"usage": {"input_tokens": 10}}},
            {"type": "other", "result": {"usage": {"input_tokens": "[已脱敏]"}}},
        ),
    ],
)
def test_event_usage_allowlist_is_path_scoped(value, expected):
    assert redact_json(value, schema="event") == expected


@pytest.mark.parametrize(
    ("secret",),
    [
        (secret,)
        for secret in (
            "sk-" + "a" * 32,
            "sk-proj-" + "b" * 40,
            "ghp_" + "c" * 36,
            "github_pat_" + "d" * 40,
            "AKIA" + "E" * 16,
            "Bearer private-bearer-value",
            "Authorization: Basic private-basic-value",
            'api_key="private-assignment-value"',
            "password=private-password-value",
            "-----BEGIN PRIVATE KEY-----\nPRIVATE-MATERIAL\n-----END PRIVATE KEY-----",
            "-----BEGIN RSA PRIVATE KEY-----\nRSA-MATERIAL\n-----END RSA PRIVATE KEY-----",
        )
    ],
)
def test_text_credentials_are_redacted_before_any_pagination(secret):
    rendered = redact_text("visible-control\n" + secret + "\nvisible-tail")
    assert "visible-control" in rendered
    assert "visible-tail" in rendered
    assert secret not in rendered
    assert "[已脱敏]" in rendered


def test_local_file_uris_are_not_displayed_in_plain_logs() -> None:
    assert (
        redact_text("visible file:///private/host-secret tail")
        == "visible [已脱敏] tail"
    )


@pytest.mark.parametrize(
    "key", ["endpoint", "base_url", "openai_base_url", "connection-string", "dsn"]
)
def test_connection_values_are_hidden_in_json_and_text(key: str) -> None:
    assert "connection-sentinel" not in str(
        redact_json({key: "https://connection-sentinel.invalid/api"})
    )
    assert "connection-sentinel" not in redact_text(
        f"{key}=https://connection-sentinel.invalid/api"
    )
