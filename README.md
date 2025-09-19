# AWS Copilot Full-Stack App

This repository hosts a FastAPI backend and a Vite + React frontend served from the same FastAPI process.

## Project Structure

```
backend/    # FastAPI application code
frontend/   # React app built with Vite, Tailwind CSS, and shadcn/ui
``` 

## Requirements

- Python 3.12 or higher
- [uv](https://docs.astral.sh/uv/latest/) for Python dependency management
- [pnpm](https://pnpm.io/) for JavaScript dependencies
- `git` (for packaging GitHub repositories during deployments)

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

### Local setup with Docker-backed MongoDB

1. Ensure Docker Desktop (or the Docker Engine + compose plugin) and `pnpm` are installed.
2. Copy `.env.example` to `.env` and fill in secrets (OpenAI, MongoDB URI if different, etc.).
3. Start the dev stack:
   ```bash
   pnpm dev:up
   ```
   This starts the MongoDB container defined in `docker-compose.yml` and then launches the backend and frontend dev servers. The backend connects to `mongodb://aws_copilot:change-me@localhost:27017/aws_copilot?authSource=admin` by default; adjust the environment variables if you point at a managed MongoDB instance.
4. When finished, stop services with:
   ```bash
   docker compose down
   ```

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
pnpm dev:up            # start MongoDB (Docker) then launch both backend + frontend
```

## AWS Agent API

The backend exposes an endpoint that executes vetted boto3 actions through the
AgentPro toolchain:

```
POST /api/aws/action
{
  "action": "list_ec2_instances",
  "params": {"region": "us-east-1"}
}
```

Destructive actions such as `terminate_ec2` require `"confirm": true` inside
`params`. Responses follow the schema documented in `AGENTS.md` (status,
action, result/message).

### Deploying GitHub Repositories

The conversational agent can now package a public GitHub repository and deploy it
directly to AWS:

- **Lambda** – provide the repository URL, branch (optional), Lambda function
  settings (`function_name`, `handler`, `runtime`, `role_arn`, `region`), and the
  tool zips the repo before invoking `deploy_lambda`.
- **EC2** – supply the repository URL, target S3 bucket (for staging the
  artifact), and optional EC2 launch parameters. The agent uploads the zipped
  artifact via `upload_s3` and, when requested, can launch an instance using the
  packaged code.
- Need an AMI? Ask the agent to run `describe_images` with the owner/filter
  values you want (e.g., owner `amazon` and a name filter matching an Amazon
  Linux 2 AMI). The response includes the AMI IDs you can feed into `launch_ec2`.
- Need a key pair? Use `describe_key_pairs` to list the key pairs in the target
  region and choose an existing one, or create a new key pair in the AWS console
  first and provide its name here.

During the chat flow the assistant confirms the required AWS parameters and then
runs the `github_deployer` tool to orchestrate the clone → package → deploy
sequence.

### Agent WebSocket

For conversational interactions with the deployment agent, connect to the
websocket endpoint (`ws://localhost:8000/ws/agent` in development) and send
JSON payloads of the form `{"message": "..."}`. The server responds with the
agent's final answer and structured reasoning trace:

```
{
  "type": "agent_response",
  "final_answer": "...",
  "thought_process": [
    {"thought": "...", "action": "aws_deployer", "observation": "..."}
  ]
}
```

### Providing AWS Credentials

AWS keys are never read from `.env`. Instead, supply them via the UI/chat
workflow so they can be encrypted and stored in MongoDB inside the
`aws_credentials` collection. If no active credential set is found, the backend
returns an error prompting you to add them. Use the **Configure AWS Credentials**
button in the chatbot to open the secure form. The assistant will continue to
answer questions even without credentials, but deployment actions remain
disabled until the form is completed.

## Local MongoDB (Docker)

Use the provided `docker-compose.yml` to run MongoDB locally:

```bash
MONGO_INITDB_ROOT_PASSWORD=change-me docker compose up -d mongodb
```

Environment defaults in `.env`/`.env.example` configure the FastAPI app to
connect at `mongodb://aws_copilot:change-me@localhost:27017/aws_copilot?authSource=admin`.
For production, override the same variables to point at your managed MongoDB
deployment and update the credentials accordingly.

To streamline local development, run:

```bash
pnpm dev:up
```

This helper starts MongoDB via Docker Compose (if not already running) and then
launches the combined frontend/backend dev servers.

## Environment Variables

Create a `.env` file in the project root if the backend requires configuration values. The file is ignored by git.
