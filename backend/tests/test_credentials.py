import json

import pytest

from backend import credentials


class FakeCollection:
    def __init__(self, document=None):
        self._document = document or {
            "type": "aws",
            "active": True,
            "access_key_id": "AKIALOCAL",
            "secret_access_key": "secret",
            "session_token": None,
        }

    def find_one(self, *args, **kwargs):
        return self._document


class FakeDB:
    def __getitem__(self, name):
        return FakeCollection()


@pytest.fixture(autouse=True)
def clear_cache():
    credentials.clear_cached_collection()
    yield
    credentials.clear_cached_collection()


def test_builds_uri_from_components(monkeypatch):
    recorded = {}

    def fake_client(uri, serverSelectionTimeoutMS=None):
        recorded["uri"] = uri

        class _Client:
            def __getitem__(self, name):
                return FakeDB()

        return _Client()

    monkeypatch.setattr(credentials, "MongoClient", fake_client)

    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setenv("MONGO_HOST", "localhost")
    monkeypatch.setenv("MONGO_PORT", "27017")
    monkeypatch.setenv("MONGO_INITDB_ROOT_USERNAME", "aws_copilot")
    monkeypatch.setenv("MONGO_INITDB_ROOT_PASSWORD", "change-me")
    monkeypatch.setenv("MONGO_AUTH_SOURCE", "admin")

    collection = credentials._credentials_collection()
    assert isinstance(collection, FakeCollection)
    assert recorded["uri"].startswith("mongodb://aws_copilot:change-me@localhost:27017/"), recorded["uri"]
    assert "authSource=admin" in recorded["uri"]


def test_fetch_aws_credentials_override(monkeypatch):
    override = {
        "aws_access_key_id": "override",
        "aws_secret_access_key": "override-secret",
        "aws_session_token": "token",
    }

    monkeypatch.setenv("AWS_CREDENTIALS_OVERRIDE_JSON", json.dumps(override))
    creds = credentials.fetch_aws_credentials()
    assert creds == override


def test_fetch_aws_credentials_from_mongo(monkeypatch):
    def fake_client(uri, serverSelectionTimeoutMS=None):

        class _Client:
            def __getitem__(self, name):
                return FakeDB()

        return _Client()

    monkeypatch.setattr(credentials, "MongoClient", fake_client)
    monkeypatch.delenv("AWS_CREDENTIALS_OVERRIDE_JSON", raising=False)
    monkeypatch.setenv("MONGO_INITDB_ROOT_USERNAME", "user")
    monkeypatch.setenv("MONGO_INITDB_ROOT_PASSWORD", "pass")
    monkeypatch.setenv("MONGO_HOST", "db")

    creds = credentials.fetch_aws_credentials()
    assert creds["aws_access_key_id"] == "AKIALOCAL"
    assert creds["aws_secret_access_key"] == "secret"
