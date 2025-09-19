"""Tooling to deploy GitHub repositories to AWS targets."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Mapping, MutableMapping

from agentproplus.tools import Tool

from .aws import AWSDeployerTool


class GitHubDeploymentError(RuntimeError):
    """Raised for repository deployment failures."""


class GitHubDeploymentTool(Tool):
    """Tool that packages GitHub repositories and deploys them to AWS targets."""

    name: str = "GitHub Deployment"
    description: str = (
        "Clone GitHub repositories, build deployable artifacts, and optionally "
        "publish them to Lambda or EC2 using aws_deployer actions."
    )
    action_type: str = "github_deployer"
    input_format: str = (
        '{"action": "deploy_lambda_repo", "params": {"repo_url": "...", "function_name": "...", ...}}'
    )

    _SUPPORTED_ACTIONS: ClassVar[Mapping[str, Callable[[MutableMapping[str, Any]], Mapping[str, Any]]]]

    def __init__(self, aws_tool: AWSDeployerTool):
        super().__init__()
        self._aws_tool = aws_tool

    def run(self, input_text: Any) -> str:  # type: ignore[override]
        payload = _coerce_payload(input_text)
        action = payload.get("action")
        params = _coerce_params(payload.get("params"))

        if not action:
            raise ValueError("GitHubDeploymentTool requires an 'action' field")

        handler = self._SUPPORTED_ACTIONS.get(action)
        if not handler:
            raise ValueError(
                f"Unsupported GitHub deployment action '{action}'. Supported actions: "
                f"{sorted(self._SUPPORTED_ACTIONS)}"
            )

        try:
            result = handler(self, params)
            return _format_success(action, result)
        except GitHubDeploymentError as exc:
            return _format_error(action, str(exc))
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else str(exc)
            return _format_error(action, f"Command failed: {message.strip()}")
        except Exception as exc:  # noqa: BLE001
            return _format_error(action, f"Unexpected error: {exc}")

    # Deployment handlers -------------------------------------------------

    def _deploy_lambda_repo(self, params: MutableMapping[str, Any]) -> Mapping[str, Any]:
        repo_url = params.get("repo_url")
        if not repo_url:
            raise GitHubDeploymentError("'repo_url' is required")

        function_name = params.get("function_name")
        handler = params.get("handler")
        runtime = params.get("runtime")
        role_arn = params.get("role_arn")
        region = params.get("region") or "us-east-1"

        if not all([function_name, handler, runtime, role_arn]):
            raise GitHubDeploymentError(
                "'function_name', 'handler', 'runtime', and 'role_arn' are required for Lambda deployments"
            )

        branch = params.get("branch")
        directory_hint = params.get("lambda_subdir")
        environment = params.get("environment")
        description = params.get("description")

        repo_path = _clone_repository(repo_url, branch)
        temp_root = repo_path.parent

        try:
            source_path = repo_path / directory_hint if directory_hint else repo_path
            if not source_path.exists():
                raise GitHubDeploymentError(
                    f"Lambda source directory '{directory_hint}' not found in repository" if directory_hint else "Repository checkout failed"
                )

            artifact_path = _zip_directory(source_path)

            deployment_params: Dict[str, Any] = {
                "function_name": function_name,
                "handler": handler,
                "runtime": runtime,
                "role_arn": role_arn,
                "zip_file": str(artifact_path),
                "region": region,
            }

            if description:
                deployment_params["description"] = description
            if environment:
                deployment_params["environment"] = environment
            if "timeout" in params:
                deployment_params["timeout"] = params["timeout"]
            if "memory_size" in params:
                deployment_params["memory_size"] = params["memory_size"]

            response = json.loads(
                self._aws_tool.run({"action": "deploy_lambda", "params": deployment_params})
            )

            if response.get("status") == "error":
                raise GitHubDeploymentError(response.get("message", "Lambda deployment failed"))

            result_payload = response.get("result", {})
            summary = (
                f"Deployed Lambda function '{function_name}' in {region} using repository {repo_url}."
            )

            return {
                "function_name": function_name,
                "runtime": runtime,
                "repository": repo_url,
                "branch": branch,
                "artifact_size": artifact_path.stat().st_size,
                "aws_response": result_payload,
                "summary": summary,
            }
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
            if 'artifact_path' in locals():
                artifact_path.unlink(missing_ok=True)

    def _deploy_ec2_repo(self, params: MutableMapping[str, Any]) -> Mapping[str, Any]:
        repo_url = params.get("repo_url")
        if not repo_url:
            raise GitHubDeploymentError("'repo_url' is required")

        bucket_name = params.get("bucket_name")
        if not bucket_name:
            raise GitHubDeploymentError("'bucket_name' is required to stage artifacts in S3")

        region = params.get("region") or "us-east-1"
        branch = params.get("branch")
        deployment_subdir = params.get("artifact_subdir")

        repo_path = _clone_repository(repo_url, branch)
        temp_root = repo_path.parent

        try:
            source_path = repo_path / deployment_subdir if deployment_subdir else repo_path
            if not source_path.exists():
                raise GitHubDeploymentError(
                    f"Deployment directory '{deployment_subdir}' not found in repository" if deployment_subdir else "Repository checkout failed"
                )

            artifact_path = _zip_directory(source_path)
            object_name = params.get("object_name") or _generate_artifact_name(source_path.name)

            upload_params = {
                "bucket_name": bucket_name,
                "file_path": str(artifact_path),
                "object_name": object_name,
                "region": region,
            }

            upload_response = json.loads(
                self._aws_tool.run({"action": "upload_s3", "params": upload_params})
            )

            if upload_response.get("status") == "error":
                raise GitHubDeploymentError(upload_response.get("message", "Uploading artifact failed"))

            summary_parts = [
                f"Uploaded artifact to s3://{bucket_name}/{object_name} ({Path(artifact_path).stat().st_size} bytes)."
            ]

            launch_result = None
            if params.get("launch_instance"):
                launch_params = {key: value for key, value in params.items() if key not in {
                    "action", "repo_url", "bucket_name", "object_name", "artifact_subdir", "launch_instance", "branch"
                }}
                launch_params.update({
                    "region": region,
                    "user_data": params.get("user_data"),
                })

                if "ami_id" not in launch_params or "instance_type" not in launch_params or "key_name" not in launch_params:
                    raise GitHubDeploymentError(
                        "'ami_id', 'instance_type', and 'key_name' are required when launch_instance is true"
                    )

                default_user_data = _default_user_data(bucket_name, object_name, params.get("artifact_install_path"))
                launch_params.setdefault("user_data", default_user_data)

                launch_response = json.loads(
                    self._aws_tool.run({"action": "launch_ec2", "params": launch_params})
                )

                if launch_response.get("status") == "error":
                    raise GitHubDeploymentError(launch_response.get("message", "Launching EC2 instance failed"))

                launch_result = launch_response.get("result")
                instance_ids = (launch_result or {}).get("instance_ids")
                if instance_ids:
                    summary_parts.append(
                        f"Launched EC2 instance(s) {', '.join(instance_ids)} in {region}."
                    )

            summary = " ".join(summary_parts)

            return {
                "artifact": {
                    "bucket": bucket_name,
                    "object": object_name,
                    "size_bytes": Path(artifact_path).stat().st_size,
                },
                "repository": repo_url,
                "branch": branch,
                "ec2_launch": launch_result,
                "user_data_hint": params.get("user_data") or _default_user_data(bucket_name, object_name, params.get("artifact_install_path")),
                "summary": summary,
            }
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
            if 'artifact_path' in locals():
                artifact_path.unlink(missing_ok=True)


def _coerce_payload(raw: Any) -> Dict[str, Any]:
    if raw is None:
        raise ValueError("GitHubDeploymentTool payload cannot be None")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            raise ValueError("GitHubDeploymentTool payload string is empty")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("GitHubDeploymentTool payload must be JSON parseable") from exc
    if isinstance(raw, Mapping):
        return dict(raw)
    raise TypeError("GitHubDeploymentTool expects a dict or JSON string payload")


def _coerce_params(raw: Any) -> MutableMapping[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError("GitHubDeploymentTool params must be JSON object") from exc
        if not isinstance(parsed, Mapping):
            raise TypeError("GitHubDeploymentTool params JSON must decode to an object")
        return dict(parsed)
    raise TypeError("GitHubDeploymentTool params must be mapping-compatible")


def _format_success(action: str, result: Mapping[str, Any]) -> str:
    return json.dumps({"status": "success", "action": action, "result": result}, default=str)


def _format_error(action: str, message: str) -> str:
    return json.dumps({"status": "error", "action": action, "message": message})


def _clone_repository(repo_url: str, branch: str | None) -> Path:
    temp_root = Path(tempfile.mkdtemp(prefix="github_repo_"))
    destination = temp_root / "repo"
    clone_cmd = ["git", "clone", "--depth", "1"]
    if branch:
        clone_cmd.extend(["--branch", branch])
    clone_cmd.extend([repo_url, str(destination)])

    completed = subprocess.run(clone_cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="ignore")
        raise GitHubDeploymentError(f"Failed to clone repository: {message.strip()}")

    return destination


def _zip_directory(source_path: Path) -> Path:
    if not source_path.exists():
        raise GitHubDeploymentError(f"Source path '{source_path}' does not exist")

    fd, temp_name = tempfile.mkstemp(prefix="repo_artifact_", suffix=".zip")
    os.close(fd)
    Path(temp_name).unlink(missing_ok=True)
    zip_path = Path(temp_name)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in source_path.rglob("*"):
            if file_path.is_file():
                zip_file.write(file_path, file_path.relative_to(source_path))

    return zip_path


def _generate_artifact_name(base: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    safe_base = base or "artifact"
    return f"{safe_base}-{timestamp}.zip"


def _default_user_data(bucket: str, obj: str, install_path: str | None) -> str:
    destination = install_path or "/opt/app"
    return """#!/bin/bash
set -e
sudo yum update -y
sudo yum install -y unzip awscli
mkdir -p {dest}
aws s3 cp s3://{bucket}/{obj} /tmp/deployment.zip
unzip -o /tmp/deployment.zip -d {dest}
""".format(dest=destination, bucket=bucket, obj=obj)


GitHubDeploymentTool._SUPPORTED_ACTIONS = {
    "deploy_lambda_repo": GitHubDeploymentTool._deploy_lambda_repo,
    "deploy_ec2_repo": GitHubDeploymentTool._deploy_ec2_repo,
}
