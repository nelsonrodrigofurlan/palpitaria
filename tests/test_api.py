from fastapi.testclient import TestClient
from palpitaria.main import app
from palpitaria.database import get_db
import pytest

# Mock DB for FastAPI
def override_get_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from palpitaria.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

def test_read_main():
    response = client.get("/")
    assert response.status_code == 200
    assert "Palpitaria FC" in response.text

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
