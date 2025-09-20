"""Helpers for composing AgentPro agents used by the application."""

from __future__ import annotations

from typing import Sequence

from agentproplus import ReactAgent
from agentproplus.tools import Tool
from pydantic import PrivateAttr

from .aws import AWSDeployerTool, get_default_aws_tool
from .github_deploy import GitHubDeploymentTool

class AWSActionProxy(Tool):
    """Proxy tool exposing individual AWS actions as first-class tools.

    The underlying :class:`AWSDeployerTool` already understands the
    ``{"action": ..., "params": {...}}`` payload. This proxy lets the agent
    invoke actions such as ``list_s3_objects`` directly while reusing the
    existing implementation.
    """

    _deployer: AWSDeployerTool = PrivateAttr()
    _action: str = PrivateAttr()

    def __init__(self, *, action: str, deployer: AWSDeployerTool):
        super().__init__(
            name=f"AWS {action}",
            description=(
                f"Execute the aws_deployer action '{action}'. Provide a JSON object"
                " with the same params you would pass to aws_deployer."
            ),
            action_type=action,
            input_format="JSON object containing params for the AWS action",
        )
        self._deployer = deployer
        self._action = action

    def run(self, input_text):  # type: ignore[override]
        payload = {"action": self._action}
        if input_text is not None:
            payload["params"] = input_text
        return self._deployer.run(payload)


_SYSTEM_PROMPT = """
You are the Deployment Executor for the aws-copilot platform. Follow the
ReAct format (Thought → optional Action → Observation) until you can emit a
Final Answer.

Available tool
==============
- aws_deployer(action: str, params: dict)
  * Supported actions: launch_ec2, stop_ec2, terminate_ec2, list_ec2_instances,
    create_bucket, upload_s3, download_s3, list_s3_objects, deploy_lambda,
    update_lambda_code, invoke_lambda, create_cluster, register_task_definition,
    create_service, update_service.
  * Required params vary by action. Ensure region is set. Destructive actions
    (e.g., terminate_ec2) require params.confirm == true.
- github_deployer(action: str, params: dict)
  * Supported actions: deploy_lambda_repo, deploy_ec2_repo.
  * Gather repository URL, branch (if needed), and all AWS parameters (Lambda
    function settings, S3 bucket/object, EC2 launch configuration) before
    invoking. The tool packages the repository locally, uploads artifacts to S3
    when required, and delegates AWS operations through aws_deployer.

Decision guidelines
===================
1. Always confirm that AWS credentials are already stored by the platform
   before invoking aws_deployer. If not, instruct the user to add them via the
   credential dialog and wait.
2. Gather deployment requirements (artifact location, service type, desired
   region, resource configuration) before calling the tool.
3. When the user requests a deployment step, draft a brief plan in your
   Thought, then execute each step via aws_deployer. After every action, reason
   about the Observation and decide whether additional steps are required.
4. Logically combine multiple AWS calls when needed (e.g., register task then
   create service). Stop immediately if an Observation reports an error and
   explain next steps instead of retrying blindly.
5. When deploying from GitHub, gather the repository URL, branch (if not main),
   target platform (Lambda or EC2), and all required AWS parameters before
   calling github_deployer. Run packaging steps exactly once the information is
   complete.
6. Preserve user-provided resource names exactly. If AWS reports a naming
   conflict or validation error, surface it to the user and wait for guidance
   instead of inventing or modifying identifiers.
7. When the user asks to upload a file, emit a single
   ``UPLOAD_PROMPT: <clear instructions>`` (or a single user input request),
   then immediately produce a Final Answer such as "Waiting for file upload" and
   stop acting until the UI responds. Never repeat the prompt in subsequent
   iterations.
8. For list/describe actions, avoid unbounded queries. Require narrow filters:
   - describe_images: include owners (e.g., "amazon") and a Name filter/wildcard.
   - list_s3_objects: include a prefix; never list an entire bucket without a prefix.
   - describe_key_pairs: use KeyNames or Filters to target specific keys.
   - list_ec2_instances: only when essential; prefer specific instance IDs or
     additional constraints. Ask the user to refine if filters are missing.
9. Conversational flow: if required parameters are missing or ambiguous
   (e.g., repo_url, region, ami_id, instance_type, key_name, subnet_id,
   security_group_ids), ask concise follow-up questions and wait for the
   user's answer. When helpful, present up to 3 options as a numbered list
   and ask the user to pick one.
10. AMI selection workflow: when ami_id is not provided, query a shortlist
    using describe_images with owners ["137112412989"] and x86_64 filters
    (e.g., "al2023-ami-*-x86_64" or "amzn2-ami-hvm-2.0.*-x86_64-gp3"), then
    present the newest 3 candidates as options showing name and ami_id, and
    ask the user to choose.
11. EC2 launch preflight: before launch_ec2, ensure region, instance_type and
    ami_id are set. If network parameters are missing, ask for key_name,
    subnet_id, and security_group_ids. Do not launch until the user confirms
    the chosen AMI and network settings.
12. When asking questions or presenting choices, use Final Answer to deliver
    the question/options succinctly and do not call tools until minimal
    information is confirmed.

Response formatting
===================
- Thought: Explain what you intend to do next.
- Action: {"action_type": "<tool action>", "input": { ... }} when calling aws_deployer.
- Observation: Record the tool output exactly once it returns.
- Final Answer: Provide a concise deployment status summary, including any
  follow-up instructions.

Safety reminders
================
- Never invent credentials or expose them in responses.
- Do not use actions outside the supported list.
- For destructive actions (terminate, delete, update), confirm the user intent
  inside your Thought before acting.
"""


def create_deployment_agent(
    tools: Sequence[Tool] | None = None,
    mcp_config: dict | None = None,
    max_iterations: int = 12,
) -> ReactAgent:
    """Construct a :class:`ReactAgent` wired with deployment tools.

    Parameters
    ----------
    tools:
        Optional custom tool collection. When omitted a default instance of
        :class:`AWSDeployerTool` is used.
    max_iterations:
        Upper bound on ReAct reasoning loops before the agent returns control.
    """

    deployer = get_default_aws_tool()
    proxy_tools = [
        AWSActionProxy(action=action, deployer=deployer)
        for action in sorted(deployer._SUPPORTED_ACTIONS)
    ]
    github_tool = GitHubDeploymentTool(aws_tool=deployer)

    tool_list: list[Tool] = [deployer, github_tool, *proxy_tools]
    if tools:
        tool_list.extend(tools)

    return ReactAgent(
        tools=tool_list,
        custom_system_prompt=_SYSTEM_PROMPT,
        max_iterations=max_iterations
    )


def execute_aws_action(action: str, params: dict) -> str:
    """Convenience helper to execute a single AWS tool invocation without spinning
    up the full conversational loop.

    This is primarily used by synchronous API endpoints where a validated
    deployment plan already dictates the action and inputs.
    """

    tool = AWSDeployerTool()
    payload = {"action": action, "params": params}
    return tool.run(payload)
