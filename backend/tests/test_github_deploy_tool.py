import json
from pathlib import Path

import pytest

from backend.agents.github_deploy import GitHubDeploymentTool


class DummyAWSTool:
    def __init__(self):
        self.calls = []

    def run(self, payload):
        self.calls.append(payload)
        return json.dumps({"status": "success", "result": {"ok": True}})


@pytest.fixture
def dummy_repo(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "lambda_function.py").write_text("def handler(event, context):\n    return 'ok'\n")

    def fake_clone(repo_url, branch):
        assert repo_url == "https://github.com/example/repo.git"
        return repo_dir

    monkeypatch.setattr("backend.agents.github_deploy._clone_repository", fake_clone)

    def fake_zip(source):
        artifact = tmp_path / "artifact.zip"
        artifact.write_bytes(b"zip")
        return artifact

    monkeypatch.setattr("backend.agents.github_deploy._zip_directory", fake_zip)

    return repo_dir


def test_deploy_lambda_repo(dummy_repo):
    aws_tool = DummyAWSTool()
    tool = GitHubDeploymentTool(aws_tool=aws_tool)

    response = json.loads(
        tool.run(
            {
                "action": "deploy_lambda_repo",
                "params": {
                    "repo_url": "https://github.com/example/repo.git",
                    "function_name": "test-fn",
                    "handler": "lambda_function.handler",
                    "runtime": "python3.12",
                    "role_arn": "arn:aws:iam::123456789012:role/lambda",
                    "region": "us-east-1",
                },
            }
        )
    )

    assert response["status"] == "success"
    assert aws_tool.calls[0]["action"] == "deploy_lambda"
    assert aws_tool.calls[0]["params"]["function_name"] == "test-fn"
    assert "summary" in response["result"]


def test_deploy_ec2_repo_upload_only(dummy_repo):
    aws_tool = DummyAWSTool()
    tool = GitHubDeploymentTool(aws_tool=aws_tool)

    response = json.loads(
        tool.run(
            {
                "action": "deploy_ec2_repo",
                "params": {
                    "repo_url": "https://github.com/example/repo.git",
                    "bucket_name": "artifact-bucket",
                    "region": "us-east-1",
                },
            }
        )
    )

    assert response["status"] == "success"
    assert aws_tool.calls[0]["action"] == "upload_s3"
    assert aws_tool.calls[0]["params"]["bucket_name"] == "artifact-bucket"
    assert len(aws_tool.calls) == 1
    assert "summary" in response["result"]
