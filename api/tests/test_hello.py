from fastapi.testclient import TestClient

from app.main import app


def test_hello():
    assert TestClient(app).get("/").json() == {"message": "hello world"}
