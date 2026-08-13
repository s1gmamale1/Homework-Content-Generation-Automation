# tests/services/test_failure_classifier.py
import pytest

from app.services import failure_classifier as fc

# The verbatim production failure (2026-08-13). `google-genai` speaks httpx, so
# a network blip surfaces as httpx's ConnectError text — which matched NOTHING
# in `_TRANSIENT`, so classify() said "hard" and `pipeline._requeue_worthy`
# refused the queue retry: the job went terminal at attempts=1 of 3.
_PROD_HTTPX_CONNECT_ERROR = (
    "practice-jigsaw: phase.run practice-jigsaw: gemini api call failed rc=1: "
    "All connection attempts failed :: All connection attempts failed"
)


def test_transient_server_shed():
    assert fc.classify("claude CLI exited rc=1 :: Server is temporarily limiting requests") == "transient"
    assert fc.classify("gemini CLI exited rc=1 :: socket connection closed unexpectedly") == "transient"


def test_gateway_errors_are_transient():
    # 502/503/504 are the same family of transient upstream-gateway blips —
    # all must retry, not fail the job on 1 hard-retry (real: an api reflection
    # job died on a 502 Bad Gateway from the gemini endpoint).
    assert fc.classify("gemini api call failed rc=1: 502 Bad Gateway") == "transient"
    assert fc.classify("gemini api call failed rc=1: 503 Service Unavailable") == "transient"
    assert fc.classify("gemini api call failed rc=1: 504 Gateway Timeout") == "transient"


def test_production_httpx_connect_error_is_transient():
    """REGRESSION (2026-08-13): the exact string that killed a lesson."""
    assert fc.classify(_PROD_HTTPX_CONNECT_ERROR) == "transient"


@pytest.mark.parametrize("text", [
    "All connection attempts failed",
    "httpx.ConnectError: All connection attempts failed",
    "httpcore.ConnectError('[Errno 61] Connection refused')",
    "httpx.ConnectTimeout: timed out",
    "httpx.ReadTimeout",
    "httpcore.RemoteProtocolError: Server disconnected without sending a response.",
])
def test_httpx_transport_shapes_are_transient(text):
    """httpx/httpcore is the HTTP stack google-genai actually uses; the list
    previously only knew requests/urllib3 and Windows-socket shapes."""
    assert fc.classify(f"gemini api call failed rc=1: {text}") == "transient"


@pytest.mark.parametrize("text", [
    "401 UNAUTHENTICATED",
    "403 PERMISSION_DENIED",
    "MAX_TOKENS exceeded",
    "prompt is too long",
    # Must stay non-transient so the autopause path can claim it.
    "You've hit your session limit · resets 12:50am (America/Chicago)",
])
def test_permanent_failures_are_not_transient(text):
    """No transient-net term may match auth, truncation, or session-limit."""
    assert fc.classify(text) != "transient"


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
