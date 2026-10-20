from fastapi.testclient import TestClient

from study_chat.app import app


def test_chatkit_endpoint_requires_owning_controller_session():
    client = TestClient(app)
    session = client.post("/api/sessions").json()["session_id"]
    payload = {"type": "threads.list", "params": {"limit": 20, "order": "desc"}}
    response = client.post("/chatkit", json=payload, headers={"X-Study-Session": session})
    assert response.status_code == 200
    assert response.json()["data"] == []
    assert client.post("/chatkit", json=payload).status_code == 401
