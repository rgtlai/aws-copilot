"""FastAPI application serving the built frontend and exposing API endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="AWS Copilot App")

api_router = APIRouter(prefix="/api", tags=["api"])


@api_router.get("/health")
def health_check() -> dict[str, str]:
    """Simple health check endpoint."""
    return {"status": "ok"}


app.include_router(api_router)

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
