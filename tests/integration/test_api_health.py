"""Integration tests for GET /api/v1/health."""
import pytest
from unittest.mock import patch, AsyncMock
from sqlalchemy.exc import OperationalError


pytestmark = pytest.mark.asyncio


class TestHealthEndpoint:
    async def test_returns_200_with_ok_status(self, client):
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"

    async def test_db_ok_when_db_reachable(self, client):
        response = await client.get("/api/v1/health")
        body = response.json()
        assert body["db"] == "ok"

    async def test_db_error_string_when_db_unreachable(self, client):
        """When the DB session.execute raises, health returns error string (not 500)."""
        from app.db import get_session
        from main import app

        async def broken_session():
            from unittest.mock import MagicMock, AsyncMock
            sess = MagicMock()
            sess.execute = AsyncMock(side_effect=OperationalError("conn failed", None, None))
            yield sess

        app.dependency_overrides[get_session] = broken_session
        try:
            response = await client.get("/api/v1/health")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "ok"
            assert "error" in body["db"]
        finally:
            app.dependency_overrides.pop(get_session, None)

    async def test_no_auth_required(self, client):
        """Health endpoint must be reachable without auth headers."""
        response = await client.get("/api/v1/health")
        assert response.status_code != 401
