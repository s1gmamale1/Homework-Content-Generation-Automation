# tests/services/test_failure_classifier.py
from app.services import failure_classifier as fc


def test_transient_server_shed():
    assert fc.classify("claude CLI exited rc=1 :: Server is temporarily limiting requests") == "transient"
    assert fc.classify("gemini CLI exited rc=1 :: socket connection closed unexpectedly") == "transient"


def test_not_your_usage_limit_is_transient_not_wall():
    # 'not your usage limit' contains the 'usage limit' wall substring — transient must win.
    assert fc.classify("Rate limited (not your usage limit)") == "transient"


def test_allocation_wall():
    assert fc.classify("You have reached your weekly usage limit") == "wall"


def test_unknown_defaults_to_hard():
    # A genuinely unrecognized failure (no known signal) -> one same-provider
    # retry. NOTE: model-not-found is now special-cased to "wall" (see below),
    # so it can't be the example here anymore.
    assert fc.classify("codex CLI exited rc=1 :: malformed response envelope") == "hard"


def test_accepts_exception_object():
    assert fc.classify(RuntimeError("temporarily limiting requests")) == "transient"


def test_asyncio_timeout_message_is_empty_falls_to_hard():
    import asyncio
    # str(asyncio.TimeoutError()) == "" → no signal → "hard". Documented, NOT relied
    # on: the failover driver (_run_with_failover) intercepts asyncio.TimeoutError
    # before it ever reaches the classifier (immediate failover). This test pins the
    # fallthrough so the interaction is a conscious choice, not an accident.
    assert fc.classify(asyncio.TimeoutError()) == "hard"


def test_extract_refusal_is_immediate_failover():
    from app.services.failure_classifier import ExtractRefusal, classify
    # ExtractRefusal must classify as "wall" → budget 0 → no same-provider retry.
    assert classify(ExtractRefusal("Gate B: summary too short")) == "wall"


def test_model_not_found_is_immediate_failover():
    # A non-existent model (phantom manifest entry / typo) returns the SAME error
    # on every retry, so retrying the same provider is pure waste → classify as
    # "wall" (0 same-provider retries → immediate failover). Seen live: a job on
    # the phantom "gemini-3.5-flash" → ModelNotFoundError on every gemini phase.
    assert fc.classify(
        "Error talking to Gemini API ... ModelNotFoundError: Requested entity was not found."
    ) == "wall"
    assert fc.classify("requested entity was not found") == "wall"
