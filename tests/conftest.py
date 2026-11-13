import os
from pathlib import Path

TEST_DB = Path("/tmp/classbrief-tests.sqlite3")
TEST_DB.unlink(missing_ok=True)
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["CRON_SECRET"] = "test-cron-secret"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_client(client):
    response = client.post("/login", data={"password": "classbrief"}, follow_redirects=False)
    assert response.status_code == 303
    return client
