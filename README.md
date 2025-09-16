# AWS Copilot Full-Stack App

This repository hosts a FastAPI backend and a Vite + React frontend served from the same FastAPI process.

## Project Structure

```
backend/    # FastAPI application code
frontend/   # React app built with Vite, Tailwind CSS, and shadcn/ui
``` 

## Requirements

- [uv](https://docs.astral.sh/uv/latest/) for Python dependency management
- [pnpm](https://pnpm.io/) for JavaScript dependencies

## Installation

```bash
pnpm install      # install frontend + tooling dependencies
uv sync           # create the virtual environment and install backend deps
```

## Development

```bash
pnpm dev
```

The command runs both the FastAPI backend (on port 8000) and the Vite dev server (on port 8080).

## Building for Production

```bash
pnpm build        # builds the React frontend into frontend/dist
pnpm start        # serves the built assets through FastAPI on port 8000
```

The FastAPI application serves the contents of `frontend/dist` at the root path (`/`).

## Useful Commands

```bash
pnpm frontend:dev      # run only the Vite dev server
pnpm frontend:lint     # lint the frontend code
pnpm backend:dev       # run only the FastAPI backend with auto-reload
pnpm backend:start     # run only the FastAPI backend in production mode
```

## Environment Variables

Create a `.env` file in the project root if the backend requires configuration values. The file is ignored by git.
