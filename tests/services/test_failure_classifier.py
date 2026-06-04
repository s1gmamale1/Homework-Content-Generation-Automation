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
    assert fc.classify("codex CLI exited rc=1 :: ModelNotFoundError") == "hard"


def test_accepts_exception_object():
    assert fc.classify(RuntimeError("temporarily limiting requests")) == "transient"


def test_asyncio_timeout_message_is_empty_falls_to_hard():
    import asyncio
    # str(asyncio.TimeoutError()) == "" → no signal → "hard". Documented, NOT relied
    # on: the failover driver (_run_with_failover) intercepts asyncio.TimeoutError
    # before it ever reaches the classifier (immediate failover). This test pins the
    # fallthrough so the interaction is a conscious choice, not an accident.
    assert fc.classify(asyncio.TimeoutError()) == "hard"
