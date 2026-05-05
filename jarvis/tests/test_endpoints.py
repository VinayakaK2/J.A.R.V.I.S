from fastapi.testclient import TestClient
from main import app

def test_health_endpoint():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "version": "7.0.0"}

def test_auth_and_chat_endpoints():
    with TestClient(app) as client:
        # Register a user
        res = client.post("/register", json={
            "username": "testuser",
            "password": "password",
            "email": "test@test.com",
            "full_name": "Test User"
        })
        assert res.status_code in (200, 400) # 400 if already registered

        # Login
        res = client.post("/login", json={"username": "testuser", "password": "password"})
        assert res.status_code == 200
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Access protected logs
        res = client.get("/logs", headers=headers)
        assert res.status_code == 200

        # Access protected sessions
        res = client.get("/sessions", headers=headers)
        assert res.status_code == 200

        # Access chat endpoint
        res = client.post("/chat", json={"message": "hello", "channel": "web", "session_id": "test_session", "tone": "professional"}, headers=headers)
        assert res.status_code == 200
