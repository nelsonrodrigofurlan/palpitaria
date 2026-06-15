import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from palpitaria.database import get_db
from palpitaria.main import app
from tests.conftest import _require_supabase_engine


@pytest.fixture(scope="module")
def api_client():
    engine = _require_supabase_engine()
    SessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_read_main(api_client):
    response = api_client.get("/")
    assert response.status_code == 200
    assert "Palpitaria FC" in response.text


def test_health_check(api_client):
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "postgresql"
