import json

import pytest

from backend.agents.aws import AWSDeployerTool
from backend.agents.runtime import AWSActionProxy
from backend.credentials import MissingCredentialsError


@pytest.fixture(autouse=True)
def stub_credentials(monkeypatch):
    monkeypatch.setattr(
        "backend.agents.aws.fetch_aws_credentials",
        lambda: {
            "aws_access_key_id": "test",
            "aws_secret_access_key": "secret",
            "aws_session_token": None,
        },
    )
    yield


def test_terminate_requires_confirmation():
    tool = AWSDeployerTool()

    response = json.loads(
        tool.run({"action": "terminate_ec2", "params": {"instance_id": "i-123", "region": "us-east-1"}})
    )

    assert response["status"] == "error"
    assert "Confirmation required" in response["message"]


def test_supported_action_uses_handler(monkeypatch):
    tool = AWSDeployerTool()
    called_with = {}

    def stub_handler(params):
        called_with.update(params)
        return {"reservation_id": "r-1"}

    monkeypatch.setitem(AWSDeployerTool._SUPPORTED_ACTIONS, "launch_ec2", stub_handler)

    payload = {
        "action": "launch_ec2",
        "params": json.dumps({"ami_id": "ami-1", "instance_type": "t3.micro", "key_name": "dev", "region": "us-east-1"}),
    }

    response = json.loads(tool.run(payload))

    assert response == {"status": "success", "action": "launch_ec2", "result": {"reservation_id": "r-1"}}
    assert called_with["ami_id"] == "ami-1"
    assert called_with["instance_type"] == "t3.micro"


def test_unknown_action_raises():
    tool = AWSDeployerTool()

    with pytest.raises(ValueError):
        tool.run({"action": "unknown", "params": {}})


def test_missing_credentials_error(monkeypatch):
    tool = AWSDeployerTool()

    def raise_missing():
        raise MissingCredentialsError("Provide credentials via UI")

    monkeypatch.setattr("backend.agents.aws.fetch_aws_credentials", raise_missing)

    response = json.loads(tool.run({"action": "list_ec2_instances", "params": {"region": "us-east-1"}}))

    assert response["status"] == "error"
    assert "Provide credentials" in response["message"]


def test_action_proxy_wraps_deployer(monkeypatch):
    captured = {}

    def fake_run(self, payload):
        captured.update(payload)
        return "ok"

    monkeypatch.setattr(AWSDeployerTool, "run", fake_run)

    deployer = AWSDeployerTool()

    proxy = AWSActionProxy(action="list_s3_objects", deployer=deployer)

    result = proxy.run({"bucket_name": "demo", "region": "us-east-1"})

    assert result == "ok"
    assert captured["action"] == "list_s3_objects"
    assert captured["params"] == {"bucket_name": "demo", "region": "us-east-1"}


def test_create_bucket_missing_name_returns_error():
    tool = AWSDeployerTool()

    response = json.loads(tool.run({"action": "create_bucket", "params": {"region": "us-east-1"}}))

    assert response["status"] == "error"
    assert "'bucket_name' parameter is required" in response["message"]


def test_create_bucket_rejects_invalid_name():
    tool = AWSDeployerTool()

    response = json.loads(
        tool.run({"action": "create_bucket", "params": {"bucket_name": "testing_bucket", "region": "us-east-1"}})
    )

    assert response["status"] == "error"
    assert "cannot include underscores" in response["message"]


def test_list_s3_objects_accepts_bucket(monkeypatch):
    tool = AWSDeployerTool()

    captured_args = {}

    class DummyPaginator:
        def paginate(self, **kwargs):
            captured_args.update(kwargs)
            yield {"Contents": [{"Key": "foo.txt", "Size": 10, "StorageClass": "STANDARD"}]}

    class DummyClient:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return DummyPaginator()

    monkeypatch.setattr("backend.agents.aws._client", lambda service, params: DummyClient())

    response = json.loads(
        tool.run({"action": "list_s3_objects", "params": {"bucket": "demo-bucket", "region": "us-west-2"}})
    )

    assert response["status"] == "success"
    assert captured_args["Bucket"] == "demo-bucket"


def test_launch_ec2_missing_ami_returns_error():
    tool = AWSDeployerTool()

    response = json.loads(
        tool.run({
            "action": "launch_ec2",
            "params": {
                "instance_type": "t3.small",
                "region": "us-east-1",
            },
        })
    )

    assert response["status"] == "error"
    assert "ami_id" in response["message"]


def test_launch_ec2_with_subnet_requires_group_ids(monkeypatch):
    tool = AWSDeployerTool()

    def fake_client(service, params):
        class DummyClient:
            def run_instances(self, **kwargs):
                assert kwargs["SubnetId"] == "subnet-123"
                assert kwargs.get("SecurityGroupIds") == ["sg-123"]
                return {"Instances": [{"InstanceId": "i-123"}]}

        return DummyClient()

    monkeypatch.setattr("backend.agents.aws._client", fake_client)

    response = json.loads(
        tool.run(
            {
                "action": "launch_ec2",
                "params": {
                    "ami_id": "ami-123",
                    "instance_type": "t3.micro",
                    "region": "us-east-1",
                    "subnet_id": "subnet-123",
                    "security_group_ids": ["sg-123"],
                },
            }
        )
    )

    assert response["status"] == "success"


def test_describe_images(monkeypatch):
    tool = AWSDeployerTool()

    def fake_client(service, params):
        class DummyClient:
            def describe_images(self, **kwargs):
                return {
                    "Images": [
                        {
                            "ImageId": "ami-123",
                            "Name": "amzn2-ami",
                            "Description": "Amazon Linux 2",
                            "State": "available",
                            "CreationDate": "2025-01-01T00:00:00.000Z",
                            "RootDeviceType": "ebs",
                            "VirtualizationType": "hvm",
                        }
                    ]
                }

        return DummyClient()

    monkeypatch.setattr("backend.agents.aws._client", fake_client)

    response = json.loads(
        tool.run(
            {
                "action": "describe_images",
                "params": {
                    "region": "us-east-1",
                    "owners": ["amazon"],
                    "filters": [{"Name": "name", "Values": ["amzn2-ami-hvm-*-x86_64-gp2"]}],
                },
            }
        )
    )

    assert response["status"] == "success"
    assert response["result"]["images"][0]["image_id"] == "ami-123"


def test_describe_images_rejects_bad_filter():
    tool = AWSDeployerTool()

    response = json.loads(
        tool.run(
            {
                "action": "describe_images",
                "params": {"filters": [{"Name": "instance-type", "Values": ["t2.micro"]}]},
            }
        )
    )

    assert response["status"] == "error"
    assert "Unsupported filter" in response["message"]


def test_describe_key_pairs(monkeypatch):
    tool = AWSDeployerTool()

    def fake_client(service, params):
        class DummyClient:
            def describe_key_pairs(self, **kwargs):
                return {
                    "KeyPairs": [
                        {
                            "KeyName": "my-key",
                            "KeyPairId": "key-123",
                            "KeyFingerprint": "aa:bb",
                            "KeyType": "rsa",
                            "Tags": [{"Key": "env", "Value": "dev"}],
                        }
                    ]
                }

        return DummyClient()

    monkeypatch.setattr("backend.agents.aws._client", fake_client)

    response = json.loads(
        tool.run(
            {
                "action": "describe_key_pairs",
                "params": {"region": "us-west-2"},
            }
        )
    )

    assert response["status"] == "success"
    assert response["result"]["key_pairs"][0]["key_name"] == "my-key"
