import json

from fastapi.testclient import TestClient

from backend.credentials import MissingCredentialsError
from backend.main import app

client = TestClient(app)


def test_run_aws_action_success(monkeypatch):
    def fake_execute(action, params):
        assert action == "list_ec2_instances"
        return json.dumps({"status": "success", "action": action, "result": {"instances": []}})

    monkeypatch.setattr("backend.main.execute_aws_action", fake_execute)

    response = client.post("/api/aws/action", json={"action": "list_ec2_instances", "params": {"region": "us-east-1"}})

    assert response.status_code == 200
    assert response.json()["result"] == {"instances": []}


def test_run_aws_action_error(monkeypatch):
    def fake_execute(action, params):
        return json.dumps({"status": "error", "action": action, "message": "boom"})

    monkeypatch.setattr("backend.main.execute_aws_action", fake_execute)

    response = client.post("/api/aws/action", json={"action": "terminate_ec2", "params": {"confirm": False}})

    assert response.status_code == 400
    assert response.json()["detail"]["message"] == "boom"


def test_run_aws_action_invalid(monkeypatch):
    def fake_execute(action, params):
        raise ValueError("bad request")

    monkeypatch.setattr("backend.main.execute_aws_action", fake_execute)

    response = client.post("/api/aws/action", json={"action": "oops", "params": {}})

    assert response.status_code == 400
    assert response.json()["detail"] == "bad request"


def test_agent_websocket(monkeypatch):
    class DummyStep:
        def __init__(self):
            self.thought = "thinking"
            self.action = type("A", (), {"action_type": "test_action"})()
            self.observation = type("O", (), {"result": "observed"})()

    class DummyResponse:
        def __init__(self):
            self.final_answer = "hi"
            self.thought_process = [DummyStep()]

    class DummyAgent:
        def run(self, query):
            assert query == "hello"
            return DummyResponse()

    monkeypatch.setattr("backend.main.create_deployment_agent", lambda: DummyAgent())

    with client.websocket_connect("/ws/agent") as websocket:
        websocket.send_json({"message": "hello"})
        data = websocket.receive_json()

    assert data["type"] == "agent_response"
    assert data["final_answer"] == "hi"
    assert data["thought_process"][0]["action"] == "test_action"


def test_store_credentials(monkeypatch):
    recorded = {}

    def fake_save(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr("backend.main.save_aws_credentials", fake_save)

    response = client.post(
        "/api/credentials/aws",
        json={"accessKeyId": "abc", "secretAccessKey": "def", "sessionToken": "ghi"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "success"
    assert recorded == {
        "access_key_id": "abc",
        "secret_access_key": "def",
        "session_token": "ghi",
    }


def test_store_credentials_error(monkeypatch):
    def fake_save(**kwargs):
        raise MissingCredentialsError("missing")

    monkeypatch.setattr("backend.main.save_aws_credentials", fake_save)

    response = client.post(
        "/api/credentials/aws",
        json={"accessKeyId": "abc", "secretAccessKey": "def"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "missing"


def test_get_credentials_present(monkeypatch):
    def fake_get():
        return {"status": "present", "updated_at": "2024-09-17T12:00:00Z", "access_key_last_four": "ABCD"}

    monkeypatch.setattr("backend.main.get_aws_credentials_status", fake_get)

    response = client.get("/api/credentials/aws")

    assert response.status_code == 200
    assert response.json()["status"] == "present"
    assert response.json()["access_key_last_four"] == "ABCD"


def test_get_credentials_missing(monkeypatch):
    monkeypatch.setattr("backend.main.get_aws_credentials_status", lambda: {"status": "missing"})

    response = client.get("/api/credentials/aws")

    assert response.status_code == 200
    assert response.json()["status"] == "missing"


def test_upload_to_s3_success(monkeypatch):
    def fake_execute(action, params):
        assert action == "upload_s3"
        assert params["bucket_name"] == "demo-bucket"
        assert params["object_name"] == "foo.txt"
        return json.dumps(
            {
                "status": "success",
                "action": "upload_s3",
                "result": {"bucket": "demo-bucket", "object": "foo.txt", "size_bytes": 4},
            }
        )

    monkeypatch.setattr("backend.main.execute_aws_action", fake_execute)

    response = client.post(
        "/api/aws/upload",
        data={"bucket": "demo-bucket", "objectKey": "foo.txt", "region": "us-east-1"},
        files={"file": ("foo.txt", b"data", "text/plain")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["result"]["bucket"] == "demo-bucket"


def test_upload_to_s3_error(monkeypatch):
    def fake_execute(action, params):
        return json.dumps({"status": "error", "action": action, "message": "upload failed"})

    monkeypatch.setattr("backend.main.execute_aws_action", fake_execute)

    response = client.post(
        "/api/aws/upload",
        data={"bucket": "demo-bucket"},
        files={"file": ("foo.txt", b"data", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "upload failed"
