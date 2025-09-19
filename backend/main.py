"""FastAPI application serving the built frontend and exposing API endpoints."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import (
    APIRouter,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.agents.runtime import create_deployment_agent, execute_aws_action
from backend.credentials import MissingCredentialsError, get_aws_credentials_status, save_aws_credentials
from agentproplus.tools import UserInputTool

app = FastAPI(title="AWS Copilot App")

api_router = APIRouter(prefix="/api", tags=["api"])


@api_router.get("/health")
def health_check() -> dict[str, str]:
    """Simple health check endpoint."""
    return {"status": "ok"}


class AWSActionRequest(BaseModel):
    """Schema describing a boto3-backed AWS action request."""

    action: str = Field(..., description="aws_deployer action to run")
    params: dict[str, object] = Field(default_factory=dict, description="Arguments for the action")


class AWSCredentialsPayload(BaseModel):
    access_key_id: str = Field(..., alias="accessKeyId")
    secret_access_key: str = Field(..., alias="secretAccessKey")
    session_token: str | None = Field(default=None, alias="sessionToken")

    class Config:
        allow_population_by_field_name = True


@api_router.post("/aws/action")
def run_aws_action(request: AWSActionRequest) -> JSONResponse:
    """Execute a vetted boto3 action via the AgentPro AWS tool."""

    try:
        raw_response = execute_aws_action(request.action, request.params)
        payload = json.loads(raw_response)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if payload.get("status") == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=payload)

    return JSONResponse(payload)


@api_router.post("/credentials/aws", status_code=status.HTTP_201_CREATED)
def store_aws_credentials(payload: AWSCredentialsPayload) -> JSONResponse:
    """Persist AWS credentials via the secure MongoDB store."""

    try:
        save_aws_credentials(
            access_key_id=payload.access_key_id,
            secret_access_key=payload.secret_access_key,
            session_token=payload.session_token,
        )
    except MissingCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return JSONResponse({"status": "success"}, status_code=status.HTTP_201_CREATED)


@api_router.get("/credentials/aws")
def read_aws_credentials() -> JSONResponse:
    """Return metadata describing whether AWS credentials are stored."""

    status_payload = get_aws_credentials_status()
    return JSONResponse(status_payload)


@api_router.post("/aws/upload", status_code=status.HTTP_201_CREATED)
async def upload_file_to_s3(
    bucket: str = Form(...),
    file: UploadFile = File(...),
    object_key: str | None = Form(None),
    region: str | None = Form(None),
) -> JSONResponse:
    """Upload a user-provided file to S3 via the aws_deployer tool."""

    bucket_name = bucket.strip()
    if not bucket_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bucket name is required")

    filename = (object_key or file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to determine object key for upload")

    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(delete=False) as temp_file:
            data = await file.read()
            temp_file.write(data)
            temp_path = Path(temp_file.name)

        params: dict[str, object] = {
            "bucket_name": bucket_name,
            "file_path": str(temp_path),
            "object_name": filename,
        }
        if region:
            params["region"] = region.strip()

        raw_response = execute_aws_action("upload_s3", params)
        payload = json.loads(raw_response)
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass

    if payload.get("status") == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=payload.get("message", payload))

    return JSONResponse(payload, status_code=status.HTTP_201_CREATED)


app.include_router(api_router)


@app.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint bridging frontend conversations to the deployment agent."""

    await websocket.accept()
    agent = create_deployment_agent()

    while True:
        try:
            raw_message = await websocket.receive_text()
        except WebSocketDisconnect:
            break

        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            await websocket.send_json(
                {"type": "error", "detail": "Invalid JSON payload. Expected {\"message\": \"...\"}."}
            )
            continue

        if payload.get("type") == "ping":
            await websocket.send_json({"type": "pong"})
            continue

        message = payload.get("message")
        if not message:
            await websocket.send_json({"type": "error", "detail": "Missing 'message' field in payload."})
            continue

        loop = asyncio.get_running_loop()
        try:
            agent_response = await loop.run_in_executor(None, agent.run, message)
        except Exception as exc:  # noqa: BLE001
            await websocket.send_json({"type": "error", "detail": f"Agent execution failed: {exc}"})
            continue

        await websocket.send_json(
            {
                "type": "agent_response",
                "final_answer": getattr(agent_response, "final_answer", None),
                "thought_process": [
                    {
                        "thought": getattr(step, "thought", None),
                        "action": getattr(getattr(step, "action", None), "action_type", None),
                        "observation": getattr(getattr(step, "observation", None), "result", None),
                    }
                    for step in getattr(agent_response, "thought_process", [])
                ],
            }
        )


frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if frontend_dist.exists():
    # Serve the built frontend as the root application.
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
else:
    index_route = "Frontend build not found. Run `pnpm --filter frontend build` first."

    @app.get("/", include_in_schema=False)
    async def frontend_missing() -> JSONResponse:
        """Return a helpful response while the frontend build is absent."""
        return JSONResponse({"detail": index_route}, status_code=503)

    @app.get("/favicon.ico", include_in_schema=False)
    async def missing_favicon() -> JSONResponse:
        return JSONResponse({"detail": index_route}, status_code=503)


__all__ = ["app"]
