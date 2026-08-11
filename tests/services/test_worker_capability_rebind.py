import app.services.worker as worker


def test_rebind_flips_gemini_capability(monkeypatch):
    # start keyless: no gemini creds in env
    for k in ("GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    worker.CAPABILITIES = worker._compute_capabilities(__import__("os").environ)
    worker.CAPABILITY_BLOB = worker._capability_blob(__import__("os").environ)
    assert worker.CAPABILITIES["can_gemini_api"] is False
    assert worker.CAPABILITY_BLOB["api"]["gemini"] is False

    # apply Vertex creds live, then rebind
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/abs/active.json")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-x")
    worker._rebind_capabilities()
    assert worker.CAPABILITIES["can_gemini_api"] is True
    assert worker.CAPABILITY_BLOB["api"]["gemini"] is True


def test_rebind_refreshes_runtime_auth_fingerprint(monkeypatch):
    token = "F7a9Jm2_Rq6cV8xW1sK4nP0dZ5uH3yTbG9eL"
    monkeypatch.setenv("AUTH_TOKEN", token)
    monkeypatch.setenv("ALLOW_INSECURE_LOCAL_AUTH", "false")

    worker._rebind_capabilities()

    assert worker.CAPABILITY_BLOB["auth_token_fingerprint"] == (
        "sha256:"
        "436fb47cf46a2a52e3be23fb43cead1ad7f77388bb44e6c6d0b28dd46becf979"
    )
    assert token not in repr(worker.CAPABILITY_BLOB)
