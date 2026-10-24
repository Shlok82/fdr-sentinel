from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from app.main import app
from app.core.database import get_db

client = TestClient(app)


def test_ping():
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"ping": "pong"}


def test_healthz_endpoint_with_mocked_db():
    mock_db = MagicMock()
    mock_db.execute.return_value.scalar.return_value = 1
    
    app.dependency_overrides[get_db] = lambda: mock_db
    try:
        response = client.get("/healthz")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"
        assert data["app_name"] == "FDR Sentinel"
    finally:
        app.dependency_overrides.clear()
