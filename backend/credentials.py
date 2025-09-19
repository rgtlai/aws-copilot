"""Credential helpers for retrieving secrets from MongoDB."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Dict, Optional
from urllib.parse import quote_plus
from datetime import datetime

from pymongo import MongoClient
from pymongo.collection import Collection


class MissingCredentialsError(RuntimeError):
    """Raised when required credentials are not available."""


def _get_override() -> Optional[Dict[str, str]]:
    override = os.getenv("AWS_CREDENTIALS_OVERRIDE_JSON")
    if not override:
        return None
    try:
        data = json.loads(override)
    except json.JSONDecodeError as exc:
        raise MissingCredentialsError("Invalid AWS_CREDENTIALS_OVERRIDE_JSON payload") from exc
    if not isinstance(data, dict):
        raise MissingCredentialsError("AWS_CREDENTIALS_OVERRIDE_JSON must decode to an object")
    return {
        "aws_access_key_id": data.get("aws_access_key_id"),
        "aws_secret_access_key": data.get("aws_secret_access_key"),
        "aws_session_token": data.get("aws_session_token"),
    }


@lru_cache(maxsize=1)
def _credentials_collection() -> Collection:
    db_name = os.getenv("MONGODB_DB_NAME", "aws_copilot")
    collection_name = os.getenv("AWS_CREDENTIALS_COLLECTION", "aws_credentials")

    uri = os.getenv("MONGODB_URI")
    if not uri:
        host = os.getenv("MONGO_HOST", "localhost")
        port = os.getenv("MONGO_PORT", "27017")
        username = os.getenv("MONGO_INITDB_ROOT_USERNAME")
        password = os.getenv("MONGO_INITDB_ROOT_PASSWORD")
        auth_source = os.getenv("MONGO_AUTH_SOURCE", "admin")

        if username and password:
            uri = (
                "mongodb://"
                f"{quote_plus(username)}:{quote_plus(password)}@{host}:{port}/{db_name}"
                f"?authSource={quote_plus(auth_source)}"
            )
        else:
            uri = f"mongodb://{host}:{port}/{db_name}"

    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    return client[db_name][collection_name]


def clear_cached_collection() -> None:
    """Clear cached MongoDB client (used in tests)."""

    _credentials_collection.cache_clear()  # type: ignore[attr-defined]


def fetch_aws_credentials() -> Dict[str, Optional[str]]:
    """Fetch AWS credentials from overrides or MongoDB.

    Expected document schema in MongoDB collection:
    ``{"type": "aws", "access_key_id": "...", "secret_access_key": "...", "session_token": "...", "active": true}``
    The newest document with ``active: true`` is used.
    """

    override = _get_override()
    if override:
        if not override.get("aws_access_key_id") or not override.get("aws_secret_access_key"):
            raise MissingCredentialsError("AWS override is missing key or secret")
        return override

    collection = _credentials_collection()
    doc = collection.find_one({"type": "aws", "active": True}, sort=[("updated_at", -1)])
    if not doc:
        raise MissingCredentialsError(
            "AWS credentials not found in MongoDB. Provide them via the secure UI flow or chat prompt."
        )

    access_key = doc.get("access_key_id") or doc.get("aws_access_key_id")
    secret_key = doc.get("secret_access_key") or doc.get("aws_secret_access_key")
    session_token = doc.get("session_token") or doc.get("aws_session_token")

    if not access_key or not secret_key:
        raise MissingCredentialsError("Stored AWS credentials are incomplete. Please update them.")

    return {
        "aws_access_key_id": str(access_key),
        "aws_secret_access_key": str(secret_key),
        "aws_session_token": str(session_token) if session_token else None,
    }


def get_aws_credentials_status() -> Dict[str, Optional[str]]:
    """Return metadata about the currently stored AWS credentials.

    Secrets are never returned; instead we provide presence information along
    with non-sensitive metadata (last updated timestamp and access key suffix).
    """

    collection = _credentials_collection()
    doc = collection.find_one({"type": "aws", "active": True}, sort=[("updated_at", -1)])

    if not doc:
        return {"status": "missing"}

    access_key = doc.get("access_key_id") or doc.get("aws_access_key_id")
    suffix = str(access_key)[-4:] if access_key else None

    updated_at = doc.get("updated_at")
    if isinstance(updated_at, datetime):
        updated_iso = updated_at.isoformat()
    else:
        updated_iso = str(updated_at) if updated_at else None

    return {
        "status": "present",
        "updated_at": updated_iso,
        "access_key_last_four": suffix,
    }


def save_aws_credentials(
    *,
    access_key_id: str,
    secret_access_key: str,
    session_token: Optional[str] = None,
) -> None:
    """Persist AWS credentials in MongoDB, marking them as the active record."""

    if not access_key_id or not secret_access_key:
        raise MissingCredentialsError("AWS credentials require both access_key_id and secret_access_key")

    collection = _credentials_collection()

    now = datetime.utcnow()
    # deactivate previous credentials to keep a single active record
    collection.update_many({"type": "aws"}, {"$set": {"active": False}})
    collection.insert_one(
        {
            "type": "aws",
            "access_key_id": access_key_id,
            "secret_access_key": secret_access_key,
            "session_token": session_token,
            "active": True,
            "updated_at": now,
            "created_at": now,
        }
    )
