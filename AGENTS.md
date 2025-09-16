# AGENTS · aws-copilot

Production blueprint for orchestrating Codex CLI multi-agent workflows that deploy user applications to AWS through a chatbot experience.

---

## 1. Global Settings
| Setting | Value |
| --- | --- |
| **Primary model** | `openai/gpt-4.1-mini` (multi-modal, 128k context) |
| **Fallback model** | `openai/gpt-4o-mini` for cost-sensitive or degraded mode responses |
| **Planning/model budget** | Default 80k input / 40k output tokens per user session; hard ceiling 120k/60k enforced by orchestrator |
| **Tool execution budget** | Max 10 concurrent tool invocations; shell commands capped at 120s runtime unless escalated |
| **Rate limits** | 4 planning cycles per minute per user; AWS/GitHub API calls throttled to 2 RPS |
| **Guardrails (global)** | No destructive shell commands without user confirmation; redact `.env`, AWS credentials, PII before persistence; comply with AWS IAM least privilege |
| **Observability** | Structured telemetry emitted per step (see §9); correlation ID propagated across agents and tools |
| **Escalation policy** | Any deployment failure or compliance block escalated to human operator with full runbook snapshot |

---

## 2. Tool Catalog

| Tool | Access | Purpose | Interface / Schema | Limits & Notes |
| --- | --- | --- | --- | --- |
| `repo_reader` | Filesystem (workspace-read) | Inspect project files, generate diffs | `read_file(path: str) -> str`, `list_dir(path: str, depth: int = 2) -> [PathInfo]` | No writes; refuse outside workspace |
| `shell_runner` | Local shell | Run vetted commands (tests, builds) | `run(command: List[str], timeout: int = 120)` | Requires guardrail approval for state-changing commands; auto-captures stdout/stderr |
| `pnpm_client` | Local pnpm | Manage frontend deps/build | `exec(args: List[str])` with cwd forced to `frontend/` | Deny global installs; enforce `--frozen-lockfile` in CI |
| `uv_client` | Local uv | Sync/install Python deps, run backend scripts | `sync()`, `run(args: List[str])` | Reuses `.venv`; no global Python installs |
| `aws_deployer` | AWS SDK/CLI | Provision & deploy infrastructure/app | `deploy(stack: str, artifacts: dict, config: dict)` | Credentials loaded from redacted `.env`; dry-run required before apply |
| `openai_llm` | OpenAI API | Long-form reasoning, code synthesis | `complete(prompt, model, tokens)` | Temperature defaults: 0.2 planning, 0.5 creative; log prompt hashes only |
| `mongo_store` | MongoDB | Persist long-term memory, deployment history | `save(collection, doc)`, `find(query)` | Encrypt sensitive payloads client-side; TTL=90d for request logs |
| `qdrant_vector` | Qdrant | Semantic retrieval for past conversations, templates | `query(collection, embedding, top_k=5)` | Only store sanitized embeddings (no secrets) |
| `github_api` | GitHub | Clone/retrieve repo metadata, PR diff | `fetch_repo(url)`, `create_pr(data)` | Require user token scope check; read-only unless user approves write |
| `telemetry_bus` | Observability pipeline | Emit metrics/logs | `emit(event_type, payload)` | Payload must be scrubbed of secrets (auto-redaction middleware) |

---

## 3. Agent Roster

### 3.1 Conversation Orchestrator
- **Role**: Primary interface with user; manages turn-taking and routing.
- **Responsibilities**: Gather intents, validate inputs, delegate tasks, summarize progress.
- **Allowed tools**: `openai_llm`, `repo_reader` (read-only for context), `telemetry_bus`.
- **Guardrails**: Must confirm high-impact actions (deployments, AWS changes) with user; no shell access.

### 3.2 Context Curator
- **Role**: Produce concise technical briefs from repo/frontend/backend.
- **Responsibilities**: Index relevant files, update Qdrant embeddings, maintain context windows.
- **Allowed tools**: `repo_reader`, `qdrant_vector`, `telemetry_bus`.
- **Guardrails**: Strip secrets before embedding; respect 2k token summary limit.

### 3.3 Requirements & Data Intake Agent
- **Role**: Collect deployment parameters (AWS region, env vars, repo paths).
- **Responsibilities**: Validate mandatory fields, request clarifications, map user inputs to schema.
- **Allowed tools**: `openai_llm`, `mongo_store` (draft forms), `telemetry_bus`.
- **Guardrails**: Persist only sanitized values; mask secrets when echoing back.

### 3.4 Planning & Validation Agent
- **Role**: Design deployment plan, choose tooling, run dry-run validations.
- **Responsibilities**: Compose step plan, run `pnpm`/`uv` checks, verify prerequisites.
- **Allowed tools**: `shell_runner`, `pnpm_client`, `uv_client`, `telemetry_bus`.
- **Guardrails**: Always simulate (e.g., `npm run build --dry-run` or `aws cloudformation deploy --no-execute-changeset`) before apply; abort on lint/test failures.

### 3.5 Deployment Executor
- **Role**: Apply infrastructure and app deployments to AWS.
- **Responsibilities**: Package artifacts, invoke `aws_deployer`, monitor rollout, capture outputs.
- **Allowed tools**: `aws_deployer`, `shell_runner` (packaging), `telemetry_bus`.
- **Guardrails**: Requires signed-off plan; log change set IDs; automatic rollback on failure.

### 3.6 Post-Deployment Validator
- **Role**: Confirm deployment success via health checks and AWS responses.
- **Responsibilities**: Hit health endpoints, read AWS status, record results, notify user.
- **Allowed tools**: `aws_deployer` (status), `openai_llm` (report synthesis), `telemetry_bus`.
- **Guardrails**: No modifications; read-only operations.

### 3.7 Compliance & Safety Officer
- **Role**: Enforce policies, monitor for secret leakage, compliance with AWS/GitHub terms.
- **Responsibilities**: Inspect planned actions, redact outputs, veto risky steps.
- **Allowed tools**: `openai_llm` (policy reasoning), `telemetry_bus`.
- **Guardrails**: Cannot be overridden except by human escalation.

---

## 4. Routing Policies
- **Initial user contact** → Conversation Orchestrator.
- **If request lacks deployment parameters** → Requirements & Data Intake Agent gathers inputs.
- **When technical context required** → Context Curator prepares summary & embeddings for others.
- **Once inputs complete** → Planning & Validation Agent generates deployment plan.
- **Compliance review** → Compliance Officer must approve plan before execution.
- **Plan approved** → Deployment Executor performs dry-run, then live deploy if successful.
- **After deployment** → Post-Deployment Validator confirms success, logs telemetry, notifies user.
- **Any failure** → Conversation Orchestrator explains issue, optionally re-engages Planning Agent for remediation.

---

## 5. Workflow / Graph Orchestration
1. **Intake**: Orchestrator logs session, collects high-level intent. Requirements Agent presents dynamic form (env vars, repo URL/local path, AWS region, target service).
2. **Context Sync**: Context Curator reads `backend/`, `frontend/`, `pyproject.toml`, `package.json`, updates embeddings. Outputs 1–2 paragraph summary + critical files list.
3. **Preflight Validation**:
   - Planning Agent runs `pnpm --dir frontend build --mode development` (dry-run when possible) and `uv run python -m compileall backend`.
   - Ensures `.env` present & contains AWS keys (without exposing values).
4. **Plan Drafting**: Planning Agent drafts step list (package, infrastructure provisioning, deployment command). Compliance Officer reviews for policy violations.
5. **Dry Run**: Deployment Executor runs `aws_deployer` in preview mode (`--no-execute-changeset`) capturing outputs. Failures loop back to Step 4.
6. **Execution**: On approval, Deployment Executor performs live deploy, streaming logs to Telemetry Bus.
7. **Validation**: Post-Deployment Validator runs FastAPI health check (`/api/health`), verifies AWS response JSON indicates success. Stores results in Mongo (deployment history) and Qdrant (summaries).
8. **Closure**: Orchestrator summarizes outcome, next steps, and stores final transcript metadata.

Graph edges enforce sequential order with optional rollback path from Steps 5–7 back to Step 4 upon failure.

---

## 6. Memory & Context Handling
- **Short-term (session)**: Maintained in orchestrator buffer (last 50 turns). Compliance Officer can request redaction of lines containing secrets before broadcasting.
- **Long-term (Mongo)**: Deployment metadata (`deployment_id`, repo URL hash, timestamp, status, sanitized config). TTL 90 days.
- **Vector memory (Qdrant)**: Store embeddings of sanitized architecture summaries, troubleshooting steps. Tag by stack (`fastapi`, `vite`, `aws`) for retrieval.
- **Redaction rules**: Mask tokens matching AWS key patterns, GitHub PATs, `.env` secrets before persistence or telemetry. Replace with `***` placeholder.
- **Context pruning**: Before each LLM call, Context Curator trims to <8k tokens prioritizing requirements, current plan, recent tool outputs.

---

## 7. Safety & Compliance
- Enforce AWS IAM best practices; never request elevated permissions beyond deploy scope.
- Perform change-set preview before modifying cloud resources.
- Prohibit arbitrary file deletion or credential exfiltration.
- When interacting with GitHub, default to read-only operations unless user explicitly authorizes write.
- Use OpenAI policy filters (abusive content, PII). Abort conversation if user requests malicious activity.
- Log all compliance decisions with timestamp, agent ID, reason code.

---

## 8. Evaluation Hooks
- **Offline tasks**:
  1. Deploy sample FastAPI + Vite app to mocked AWS (localstack) ensuring plan completeness.
  2. Handle missing env variable scenario and confirm agent prompts user to supply it.
  3. Simulate AWS failure response to verify rollback and user-facing explanation.
- **Metrics**:
  - Deployment success rate (target ≥95%).
  - Average planning tokens (<30k per deployment).
  - Dry-run coverage (100% of deployments precede live run).
  - Mean time to resolution after failure (<2 iterations).
- **Evaluation cadence**: Weekly automated run; failures generate tickets for review.

---

## 9. Telemetry & Logging
- Emit structured JSON events via `telemetry_bus` with fields: `session_id`, `agent`, `phase`, `status`, `latency_ms`, `error_code` (nullable).
- Store live run logs in Mongo `telemetry` collection with per-field redaction.
- Send critical alerts (deployment failure, compliance block) to configured pager/Slack webhook.
- Provide audit trail by linking telemetry events to plan IDs and AWS change set IDs.

---

## 10. Maintenance Notes
- Update tool schemas when new AWS services or agent capabilities are added.
- Review guardrails quarterly against AWS, GitHub, and OpenAI policy changes.
- Keep OpenAI models and cost budgets aligned with organizational quotas.
- Conduct live-fire drills every release to ensure rollback paths remain valid.
