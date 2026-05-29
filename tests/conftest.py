"""Shared pytest fixtures for the test suite.

Install test dependencies before running:
    uv add --dev pytest pytest-asyncio httpx

Run with:
    pytest tests/ -v
"""
import os
import pytest

# Set minimal env so Settings() can construct without a real DB / API key.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("GEMINI_API_KEY", "fake-key")
os.environ.setdefault("AUTH_TOKEN", "test-token")


@pytest.fixture
def auth_headers() -> dict:
    return {"Authorization": "Bearer test-token"}
