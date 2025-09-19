"""AgentPro tool implementations for AWS automation.

This module exposes :class:`AWSDeployerTool`, a single action entry point
that covers the boto3 operations documented in the AGENTS.md Addendum and
``AGENTS.md``. The tool accepts structured JSON input describing the desired
AWS action and marshals it to boto3 while enforcing guardrails such as
confirmation for destructive requests and consistent telemetry logging.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Iterable, Mapping, MutableMapping

import boto3
from agentproplus.tools import Tool
from botocore.exceptions import BotoCoreError, ClientError

from backend.credentials import MissingCredentialsError, fetch_aws_credentials
_LOGGER = logging.getLogger("aws_copilot.aws_deployer")
if not _LOGGER.handlers:
    # Use environment LOG_LEVEL if present, default to INFO.
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    _LOGGER.setLevel(log_level)
    handler = logging.StreamHandler()
    handler.setLevel(log_level)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    _LOGGER.addHandler(handler)


class AWSDeployerTool(Tool):
    """AgentPro tool that proxies core AWS deployment actions via boto3.

    The tool expects JSON input in the form ``{"action": "launch_ec2",
    "params": {...}}``. For destructive actions such as terminating instances it
    requires an explicit ``"confirm": true`` field within ``params`` to satisfy
    the guardrails defined in the AGENTS.md Addendum.
    """

    name: str = "AWS Deployer"
    description: str = (
        "Execute approved AWS operations (EC2, S3, Lambda, ECS Fargate) using "
        "boto3. Actions must be supplied as JSON with an action key and "
        "parameters."
    )
    action_type: str = "aws_deployer"
    input_format: str = (
        '{"action": "<supported_action>", "params": { ... }} — see '
        "AGENTS.md Addendum for supported actions."
    )

    _SUPPORTED_ACTIONS: ClassVar[Mapping[str, Callable[[MutableMapping[str, Any]], Mapping[str, Any]]]]
    _DESTRUCTIVE_ACTIONS: ClassVar[Iterable[str]] = ("terminate_ec2",)

    def run(self, input_text: Any) -> str:
        payload = _coerce_payload(input_text)
        action = payload.get("action")
        params = _coerce_params(payload.get("params", {}))

        if not action:
            raise ValueError("AWS Deployer requires an 'action' field in the payload")

        action_handler = self._SUPPORTED_ACTIONS.get(action)
        if not action_handler:
            raise ValueError(
                f"Unsupported AWS action '{action}'. Supported actions: "
                f"{sorted(self._SUPPORTED_ACTIONS)}"
            )

        if action in self._DESTRUCTIVE_ACTIONS:
            if not params.pop("confirm", False):
                return _format_error(
                    action,
                    "Confirmation required for destructive operation. Set 'confirm' to true.",
                )

        _LOGGER.info("AWSDeployerTool executing action=%s params=%s", action, _sanitize(params))

        try:
            result = action_handler(params)
            summarized = _summarize_result(result)
            return _format_success(action, summarized)
        except ValueError as exc:
            _LOGGER.warning("AWS action %s validation error: %s", action, exc)
            return _format_error(action, str(exc))
        except MissingCredentialsError as exc:
            _LOGGER.warning("AWS credentials missing: %s", exc)
            return _format_error(action, str(exc))
        except (ClientError, BotoCoreError) as exc:
            _LOGGER.error("AWS action %s failed: %s", action, exc, exc_info=True)
            return _format_error(action, f"AWS error: {exc}")
        except FileNotFoundError as exc:
            _LOGGER.error("AWS action %s failed: %s", action, exc, exc_info=True)
            return _format_error(action, str(exc))
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors cleanly
            _LOGGER.error("AWS action %s raised unexpected error", action, exc_info=True)
            return _format_error(action, f"Unexpected error: {exc}")


def get_default_aws_tool() -> AWSDeployerTool:
    """Return a preconfigured :class:`AWSDeployerTool` instance."""

    return AWSDeployerTool()


def _coerce_payload(raw: Any) -> Dict[str, Any]:
    if raw is None:
        raise ValueError("AWS Deployer payload cannot be None")

    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            raise ValueError("AWS Deployer payload string is empty")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "AWS Deployer payload must be JSON parseable"
            ) from exc

    if isinstance(raw, Mapping):
        return dict(raw)

    raise TypeError(
        "AWS Deployer expects a dict or JSON string with 'action' and 'params'"
    )


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
            raise ValueError("AWS Deployer params must be JSON object") from exc
        if not isinstance(parsed, Mapping):
            raise TypeError("AWS Deployer params JSON must decode to an object")
        return dict(parsed)
    raise TypeError("AWS Deployer params must be mapping-compatible")


def _format_success(action: str, result: Mapping[str, Any]) -> str:
    return json.dumps({"status": "success", "action": action, "result": result}, default=str)


def _format_error(action: str, message: str) -> str:
    return json.dumps({"status": "error", "action": action, "message": message})


_SENSITIVE_KEYS = {"key", "secret", "token", "password", "credential"}


def _sanitize(params: Mapping[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    for key, value in params.items():
        lower_key = key.lower()
        masked = any(token in lower_key for token in _SENSITIVE_KEYS)
        if masked:
            sanitized[key] = "***"
        else:
            sanitized[key] = value
    return sanitized


def _truncate_string(value: str, *, limit: int = 6000) -> str:
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def _summarize_result(value: Any, *, max_items: int = 5, max_string: int = 6000) -> Any:
    """Recursively cap large results to keep agent observations small.

    - Truncates long strings to ``max_string`` characters.
    - For mappings, if a value is a list longer than ``max_items``, keeps the
      first ``max_items`` items and injects a sibling ``<key>_summary`` with
      ``{"shown": n, "total": N}``.
    - Recurses into nested structures.
    """

    # Strings
    if isinstance(value, str):
        return _truncate_string(value, limit=max_string)

    # Lists (return trimmed list; parent mapping will add summary if applicable)
    if isinstance(value, list):
        # Recurse per item but hard-cap size to avoid deep traversal cost
        trimmed_items = value[:max_items]
        return [_summarize_result(item, max_items=max_items, max_string=max_string) for item in trimmed_items]

    # Mappings
    if isinstance(value, Mapping):
        summarized: Dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, list):
                total = len(item)
                summarized[key] = _summarize_result(item, max_items=max_items, max_string=max_string)
                if total > max_items:
                    summarized[f"{key}_summary"] = {"shown": len(summarized[key]), "total": total}
            else:
                summarized[key] = _summarize_result(item, max_items=max_items, max_string=max_string)
        return summarized

    # Other primitives
    return value


def _resolve_region(params: Mapping[str, Any]) -> str:
    region = params.get("region") or os.getenv("AWS_DEFAULT_REGION")
    if not region:
        raise ValueError(
            "AWS region is required. Set 'region' in the payload or AWS_DEFAULT_REGION in the environment."
        )
    return str(region)


def _client(service_name: str, params: Mapping[str, Any]):
    region = _resolve_region(params)
    creds = fetch_aws_credentials()
    session = boto3.Session(
        region_name=region,
        aws_access_key_id=creds["aws_access_key_id"],
        aws_secret_access_key=creds["aws_secret_access_key"],
        aws_session_token=creds.get("aws_session_token"),
    )
    return session.client(service_name)


_BUCKET_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61})[a-z0-9]$")


def _ensure_list(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("["):
            return json.loads(stripped)
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return [value]


def _ensure_dict(value: Any) -> Dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("{"):
            return json.loads(stripped)
    raise ValueError("Expected dictionary-compatible value")


def _normalize_filters(value: Any) -> list[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        filters: list[Dict[str, Any]] = []
        for entry in value:
            if isinstance(entry, Mapping):
                name = entry.get("Name") or entry.get("name")
                values = entry.get("Values") or entry.get("values")
                normalized_values = _ensure_list(values) or []
                if name and normalized_values:
                    _validate_ec2_filter(name)
                    filters.append({"Name": name, "Values": normalized_values})
            elif isinstance(entry, str):
                parts = entry.split("=", 1)
                if len(parts) == 2:
                    key, vals = parts
                    normalized_values = [item.strip() for item in vals.split(",") if item.strip()]
                    if key.strip() and normalized_values:
                        _validate_ec2_filter(key.strip())
                        filters.append({"Name": key.strip(), "Values": normalized_values})
        return filters
    if isinstance(value, Mapping):
        return _normalize_filters([value])
    if isinstance(value, str):
        return _normalize_filters([value])
    raise ValueError("Filters must be provided as list, dict, or string format")


_VALID_EC2_FILTER_PREFIXES = {
    "architecture",
    "block-device-mapping",
    "description",
    "image-id",
    "image-type",
    "is-public",
    "name",
    "owner-alias",
    "owner-id",
    "platform",
    "root-device-type",
    "state",
    "tag",
    "virtualization-type",
}


def _validate_ec2_filter(name: str) -> None:
    lower_name = name.lower()
    if lower_name.startswith("tag:"):
        return
    if any(lower_name == prefix or lower_name.startswith(f"{prefix}.") for prefix in _VALID_EC2_FILTER_PREFIXES):
        return
    raise ValueError(
        "Unsupported filter name for describe_images. Reference: "
        "https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeImages.html"
    )


def _load_file_bytes(path_value: Any) -> bytes:
    path = Path(str(path_value))
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_bytes()


def _launch_ec2(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    if not params.get("ami_id"):
        raise ValueError("'ami_id' parameter is required for launch_ec2")
    if not params.get("instance_type"):
        raise ValueError("'instance_type' parameter is required for launch_ec2")

    client = _client("ec2", params)
    security_group_ids = _ensure_list(params.get("security_group_ids"))
    tag_map = _ensure_dict(params.get("tags"))
    iam_profile = params.get("iam_instance_profile")

    run_kwargs: Dict[str, Any] = {
        "ImageId": params["ami_id"],
        "InstanceType": params["instance_type"],
        "MinCount": int(params.get("min_count", 1)),
        "MaxCount": int(params.get("max_count", params.get("min_count", 1))),
    }

    if params.get("key_name"):
        run_kwargs["KeyName"] = params["key_name"]

    subnet_id = params.get("subnet_id")
    if subnet_id:
        run_kwargs["SubnetId"] = subnet_id
        if security_group_ids:
            run_kwargs["SecurityGroupIds"] = security_group_ids
    elif security_group_ids:
        run_kwargs["SecurityGroupIds"] = security_group_ids
    if params.get("user_data"):
        run_kwargs["UserData"] = params["user_data"]
    if iam_profile:
        run_kwargs["IamInstanceProfile"] = {"Name": iam_profile}
    if tag_map:
        run_kwargs["TagSpecifications"] = [
            {
                "ResourceType": "instance",
                "Tags": [{"Key": key, "Value": str(value)} for key, value in tag_map.items()],
            }
        ]

    response = client.run_instances(**run_kwargs)
    instances = [instance["InstanceId"] for instance in response.get("Instances", [])]
    return {"instance_ids": instances, "reservation_id": response.get("ReservationId")}


def _stop_ec2(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("ec2", params)
    response = client.stop_instances(InstanceIds=[params["instance_id"]], Force=bool(params.get("force", False)))
    return _extract_instance_state(response)


def _terminate_ec2(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("ec2", params)
    response = client.terminate_instances(InstanceIds=[params["instance_id"]])
    return _extract_instance_state(response)


def _list_ec2_instances(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("ec2", params)
    paginator = client.get_paginator("describe_instances")
    items = []
    for page in paginator.paginate():
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                items.append(
                    {
                        "instance_id": instance.get("InstanceId"),
                        "state": instance.get("State", {}).get("Name"),
                        "type": instance.get("InstanceType"),
                        "public_ip": instance.get("PublicIpAddress"),
                        "private_ip": instance.get("PrivateIpAddress"),
                        "tags": {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])},
                    }
                )
    return {"instances": items}


def _create_bucket(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    region = _resolve_region(params)
    bucket_name = params.get("bucket_name")
    if not bucket_name:
        raise ValueError("'bucket_name' parameter is required for create_bucket")

    bucket_name = _validate_bucket_name(str(bucket_name))

    client = _client("s3", {"region": region})
    create_args: Dict[str, Any] = {"Bucket": bucket_name}
    if region != "us-east-1":
        create_args["CreateBucketConfiguration"] = {"LocationConstraint": region}
    client.create_bucket(**create_args)
    return {"bucket": bucket_name, "region": region}


def _describe_images(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("ec2", params)
    describe_kwargs: Dict[str, Any] = {}

    owners = _ensure_list(params.get("owners"))
    if owners:
        describe_kwargs["Owners"] = owners

    filters = params.get("filters")
    if filters:
        describe_kwargs["Filters"] = _normalize_filters(filters)

    image_ids = _ensure_list(params.get("image_ids")) if params.get("image_ids") else None
    if image_ids:
        describe_kwargs["ImageIds"] = image_ids

    response = client.describe_images(**describe_kwargs)
    images = []
    for image in response.get("Images", []):
        images.append(
            {
                "image_id": image.get("ImageId"),
                "name": image.get("Name"),
                "description": image.get("Description"),
                "state": image.get("State"),
                "creation_date": image.get("CreationDate"),
                "root_device_type": image.get("RootDeviceType"),
                "virtualization_type": image.get("VirtualizationType"),
            }
        )
    return {"images": images}


def _describe_key_pairs(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("ec2", params)

    describe_kwargs: Dict[str, Any] = {}
    key_names = _ensure_list(params.get("key_names")) or _ensure_list(params.get("KeyNames"))
    if key_names:
        describe_kwargs["KeyNames"] = key_names

    filters = params.get("filters")
    if filters:
        describe_kwargs["Filters"] = _normalize_filters(filters)

    response = client.describe_key_pairs(**describe_kwargs)
    pairs = []
    for item in response.get("KeyPairs", []):
        pairs.append(
            {
                "key_name": item.get("KeyName"),
                "key_pair_id": item.get("KeyPairId"),
                "fingerprint": item.get("KeyFingerprint"),
                "type": item.get("KeyType"),
                "tags": {tag.get("Key"): tag.get("Value") for tag in item.get("Tags", [])},
            }
        )
    return {"key_pairs": pairs}


def _upload_s3(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("s3", params)
    file_path = Path(str(params["file_path"]))
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    object_name = params.get("object_name") or file_path.name
    client.upload_file(str(file_path), params["bucket_name"], object_name)
    return {"bucket": params["bucket_name"], "object": object_name, "size_bytes": file_path.stat().st_size}


def _download_s3(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("s3", params)
    destination = Path(str(params["file_path"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(params["bucket_name"], params["object_name"], str(destination))
    size = destination.stat().st_size
    return {"bucket": params["bucket_name"], "object": params["object_name"], "downloaded_to": str(destination), "size_bytes": size}


def _list_s3_objects(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    bucket_name = params.get("bucket_name") or params.get("bucket") or params.get("Bucket")
    if not bucket_name:
        raise ValueError("'bucket_name' parameter is required for list_s3_objects")

    client = _client("s3", params)
    paginator = client.get_paginator("list_objects_v2")
    listing = []
    pagination_args = {"Bucket": bucket_name}
    prefix = params.get("prefix") or params.get("Prefix")
    if prefix:
        pagination_args["Prefix"] = prefix
    for page in paginator.paginate(**pagination_args):
        for obj in page.get("Contents", []):
            listing.append(
                {
                    "key": obj.get("Key"),
                    "size": obj.get("Size"),
                    "last_modified": obj.get("LastModified"),
                    "storage_class": obj.get("StorageClass"),
                }
            )
    return {"objects": listing}


def _deploy_lambda(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("lambda", params)
    zip_bytes = _load_file_bytes(params["zip_file"])
    environment = _ensure_dict(params.get("environment"))
    payload: Dict[str, Any] = {
        "FunctionName": params["function_name"],
        "Runtime": params["runtime"],
        "Role": params["role_arn"],
        "Handler": params["handler"],
        "Code": {"ZipFile": zip_bytes},
    }
    if params.get("description"):
        payload["Description"] = params["description"]
    if environment:
        payload["Environment"] = {"Variables": {str(k): str(v) for k, v in environment.items()}}
    if params.get("timeout"):
        payload["Timeout"] = int(params["timeout"])
    if params.get("memory_size"):
        payload["MemorySize"] = int(params["memory_size"])
    payload["Publish"] = bool(params.get("publish", True))
    response = client.create_function(**payload)
    return {"function_arn": response.get("FunctionArn"), "state": response.get("State"), "last_modified": response.get("LastModified")}


def _update_lambda_code(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("lambda", params)
    zip_bytes = _load_file_bytes(params["zip_file"])
    response = client.update_function_code(
        FunctionName=params["function_name"],
        ZipFile=zip_bytes,
        Publish=bool(params.get("publish", False)),
    )
    return {"function_arn": response.get("FunctionArn"), "last_modified": response.get("LastModified")}


def _invoke_lambda(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("lambda", params)
    payload = params.get("payload", {})
    if isinstance(payload, (dict, list)):
        payload_bytes = json.dumps(payload).encode("utf-8")
    elif isinstance(payload, str):
        payload_bytes = payload.encode("utf-8")
    else:
        payload_bytes = json.dumps(payload).encode("utf-8")

    invocation_type = params.get("invocation_type", "RequestResponse")
    response = client.invoke(
        FunctionName=params["function_name"],
        Payload=payload_bytes,
        InvocationType=invocation_type,
    )
    raw_payload = response.get("Payload")
    if raw_payload is not None:
        response_payload = raw_payload.read().decode("utf-8")
        try:
            parsed_payload = json.loads(response_payload)
        except json.JSONDecodeError:
            parsed_payload = response_payload
    else:
        parsed_payload = None
    return {
        "status_code": response.get("StatusCode"),
        "executed_version": response.get("ExecutedVersion"),
        "payload": parsed_payload,
    }


def _create_cluster(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("ecs", params)
    response = client.create_cluster(clusterName=params["cluster_name"], tags=_format_tags(params.get("tags")))
    cluster = response.get("cluster", {})
    return {"cluster_arn": cluster.get("clusterArn"), "status": cluster.get("status")}


def _register_task_definition(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("ecs", params)
    container_definitions = _ensure_list(params.get("container_definitions"))
    if not container_definitions:
        raise ValueError("'container_definitions' is required and must be a list")
    container_definitions = [
        _ensure_dict(defn) if not isinstance(defn, dict) else defn for defn in container_definitions
    ]
    requires_compatibilities = _ensure_list(params.get("requires_compatibilities"))
    register_args: Dict[str, Any] = {
        "family": params["family"],
        "containerDefinitions": container_definitions,
        "networkMode": params.get("network_mode", "awsvpc"),
    }
    if requires_compatibilities:
        register_args["requiresCompatibilities"] = requires_compatibilities
    if params.get("cpu"):
        register_args["cpu"] = str(params["cpu"])
    if params.get("memory"):
        register_args["memory"] = str(params["memory"])
    if params.get("execution_role_arn"):
        register_args["executionRoleArn"] = params["execution_role_arn"]
    if params.get("task_role_arn"):
        register_args["taskRoleArn"] = params["task_role_arn"]

    response = client.register_task_definition(**register_args)
    task_def = response.get("taskDefinition", {})
    return {
        "task_definition_arn": task_def.get("taskDefinitionArn"),
        "revision": task_def.get("revision"),
    }


def _create_service(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("ecs", params)
    network_configuration = _build_network_configuration(params)
    service_args: Dict[str, Any] = {
        "cluster": params["cluster"],
        "serviceName": params["service_name"],
        "taskDefinition": params["task_definition"],
        "desiredCount": int(params.get("desired_count", 1)),
        "launchType": params.get("launch_type", "FARGATE"),
    }
    if params.get("platform_version"):
        service_args["platformVersion"] = params["platform_version"]
    if network_configuration:
        service_args["networkConfiguration"] = network_configuration
    if params.get("role"):
        service_args["role"] = params["role"]
    response = client.create_service(**service_args)
    service = response.get("service", {})
    return {"service_arn": service.get("serviceArn"), "status": service.get("status")}


def _update_service(params: MutableMapping[str, Any]) -> Mapping[str, Any]:
    client = _client("ecs", params)
    update_args: Dict[str, Any] = {
        "cluster": params["cluster"],
        "service": params["service_name"],
    }
    if "desired_count" in params:
        update_args["desiredCount"] = int(params["desired_count"])
    if params.get("task_definition"):
        update_args["taskDefinition"] = params["task_definition"]
    if params.get("force_new_deployment") is not None:
        update_args["forceNewDeployment"] = bool(params["force_new_deployment"])
    response = client.update_service(**update_args)
    service = response.get("service", {})
    return {"service_arn": service.get("serviceArn"), "status": service.get("status")}


def _extract_instance_state(response: Mapping[str, Any]) -> Mapping[str, Any]:
    entries = []
    for item in response.get("StoppingInstances", []) + response.get("TerminatingInstances", []):
        entries.append(
            {
                "instance_id": item.get("InstanceId"),
                "previous_state": item.get("PreviousState", {}).get("Name"),
                "current_state": item.get("CurrentState", {}).get("Name"),
            }
        )
    return {"instances": entries}


def _format_tags(raw_tags: Any) -> list[Dict[str, str]] | None:
    parsed = _ensure_dict(raw_tags)
    if not parsed:
        return None
    return [{"key": str(key), "value": str(value)} for key, value in parsed.items()]


def _build_network_configuration(params: Mapping[str, Any]) -> Dict[str, Any] | None:
    subnets = _ensure_list(params.get("subnets"))
    security_groups = _ensure_list(params.get("security_groups"))
    assign_public_ip = params.get("assign_public_ip")

    if not subnets and not security_groups and assign_public_ip is None:
        return None

    awsvpc_conf: Dict[str, Any] = {}
    if subnets:
        awsvpc_conf["subnets"] = subnets
    if security_groups:
        awsvpc_conf["securityGroups"] = security_groups
    if assign_public_ip is not None:
        awsvpc_conf["assignPublicIp"] = "ENABLED" if assign_public_ip else "DISABLED"

    return {"awsvpcConfiguration": awsvpc_conf}


def _validate_bucket_name(name: str) -> str:
    if not name:
        raise ValueError("S3 bucket name is required")
    if len(name) < 3 or len(name) > 63:
        raise ValueError("S3 bucket name must be between 3 and 63 characters")
    if any(ch.isupper() for ch in name):
        raise ValueError("S3 bucket names must use lowercase letters only")
    if "." in name:
        raise ValueError("S3 bucket names should not contain periods when using virtual-hosted style URLs")
    if "_" in name:
        raise ValueError("S3 bucket names cannot include underscores; use hyphens instead")
    if not _BUCKET_PATTERN.fullmatch(name):
        raise ValueError(
            "S3 bucket names may contain only lowercase letters, numbers, and hyphens, and must start and end with a letter or number"
        )
    return name


AWSDeployerTool._SUPPORTED_ACTIONS = {
    "launch_ec2": _launch_ec2,
    "stop_ec2": _stop_ec2,
    "terminate_ec2": _terminate_ec2,
    "list_ec2_instances": _list_ec2_instances,
    "create_bucket": _create_bucket,
    "describe_images": _describe_images,
    "describe_key_pairs": _describe_key_pairs,
    "upload_s3": _upload_s3,
    "download_s3": _download_s3,
    "list_s3_objects": _list_s3_objects,
    "deploy_lambda": _deploy_lambda,
    "update_lambda_code": _update_lambda_code,
    "invoke_lambda": _invoke_lambda,
    "create_cluster": _create_cluster,
    "register_task_definition": _register_task_definition,
    "create_service": _create_service,
    "update_service": _update_service,
}
