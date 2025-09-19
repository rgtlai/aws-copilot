"""FastMCP server exposing the same AWS actions as ``AWSDeployerTool``.

The server registers a single ``aws_deployer`` tool that mirrors the
``AWSDeployerTool`` interface. Actions and parameters are forwarded directly to
the existing implementation so behaviour remains consistent across both entry
points (FastAPI endpoint and MCP server).
"""

from __future__ import annotations

import json
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

from .aws import AWSDeployerTool


server = FastMCP(
    name="aws-tools-mcp",
    instructions=(
        "Expose AWS deployment actions via MCP. Use the 'aws_deployer' tool with "
        "an action name and params matching the REST API payloads."
    ),
)

_aws_tool = AWSDeployerTool()


@server.tool(name="aws_deployer", description="Execute approved AWS operations (EC2, S3, Lambda, ECS)")
def aws_deployer(action: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Proxy the request to :class:`AWSDeployerTool` and return JSON result."""

    payload = {"action": action, "params": params or {}}
    raw_response = _aws_tool.run(payload)
    return json.loads(raw_response)


def main() -> None:
    """Entry-point for launching the FastMCP server."""

    server.run()


if __name__ == "__main__":
    main()

