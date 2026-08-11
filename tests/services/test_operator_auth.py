import pytest

from app.services import operator_auth


STRONG_A = "F7a9Jm2_Rq6cV8xW1sK4nP0dZ5uH3yTbG9eL"
STRONG_B = "mD8vQ2kL7xN4pR1sT6wY9cA3fH5jU0zE-BgC"


@pytest.mark.parametrize(
    "raw",
    [
        "123",
        "password",
        "short-token",
        "a" * 32,
        " ",
        f" {STRONG_A}",
        f"{STRONG_A} ",
        "has whitespace " + "x" * 32,
        f"{STRONG_A},123",
        f"{STRONG_A},",
        f"{STRONG_A},{STRONG_A}",
        "Я" * 32,
    ],
)
def test_startup_rejects_every_weak_or_ambiguous_member(raw):
    with pytest.raises(operator_auth.OperatorAuthConfigurationError) as caught:
        operator_auth.require_startup_auth(raw, allow_insecure_local=False)
    # A whitespace-only credential is necessarily a substring of ordinary
    # prose; all non-whitespace token material must remain undisclosed.
    if raw.strip():
        assert raw not in str(caught.value)
    assert str(caught.value).startswith("AUTH_TOKEN member ")


def test_explicit_local_dev_accepts_only_an_empty_token():
    assert (
        operator_auth.require_startup_auth("", allow_insecure_local=True)
        == "local-dev"
    )
    with pytest.raises(operator_auth.OperatorAuthConfigurationError):
        operator_auth.require_startup_auth("123", allow_insecure_local=True)


def test_multiple_strong_tokens_are_valid_for_future_strong_to_strong_rotation():
    assert operator_auth.parse_strong_tokens(f"{STRONG_A},{STRONG_B}") == (
        STRONG_A,
        STRONG_B,
    )


def test_token_match_uses_every_candidate_without_plain_membership(monkeypatch):
    calls = []
    real = operator_auth.hmac.compare_digest

    def tracked(left, right):
        calls.append((left, right))
        return real(left, right)

    monkeypatch.setattr(operator_auth.hmac, "compare_digest", tracked)
    assert operator_auth.constant_time_token_match(STRONG_B, (STRONG_A, STRONG_B))
    assert calls == [
        (STRONG_B.encode(), STRONG_A.encode()),
        (STRONG_B.encode(), STRONG_B.encode()),
    ]


def test_token_match_does_not_short_circuit_after_first_match(monkeypatch):
    calls = []
    real = operator_auth.hmac.compare_digest

    def tracked(left, right):
        calls.append((left, right))
        return real(left, right)

    monkeypatch.setattr(operator_auth.hmac, "compare_digest", tracked)
    assert operator_auth.constant_time_token_match(STRONG_A, (STRONG_A, STRONG_B))
    assert len(calls) == 2


def test_malformed_unicode_presented_value_is_a_safe_miss():
    assert not operator_auth.constant_time_token_match("\ud800", (STRONG_A,))


def test_settings_defaults_are_fail_closed():
    from app.config import Settings

    assert Settings.model_fields["auth_token"].default == ""
    assert Settings.model_fields["allow_insecure_local_auth"].default is False


def test_settings_construction_does_not_run_startup_auth_validation():
    from app.config import Settings

    assert Settings(auth_token="123").auth_token == "123"
