import importlib
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
