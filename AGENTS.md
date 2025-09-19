# AGENTS · aws-copilot

Production blueprint for orchestrating Codex CLI multi-agent workflows that deploy user applications to AWS through a chatbot experience. The runtime leverages the `agentpro` library to compose, validate, and execute agent graphs, and aligns with the **AGENTS.md Addendum: AWS Deployments with boto3** for boto3-based operations.

---

## 1. Global Settings
| Setting | Value |
| --- | --- |
| **Primary model** | `openai/gpt-4.1-mini` (multi-modal, 128k context) |
| **Fallback model** | `openai/gpt-4o-mini` for cost-sensitive or degraded mode responses |
| **Agentic framework** | `agentpro` 0.1.0 (`rgtlai/AgentProPlus@main`) providing graph-based orchestration |
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
| `aws_deployer` | AWS SDK/CLI (boto3 wrapper) | Provision & deploy infrastructure/app | `invoke(action: str, params: dict)` where `action` ∈ {`launch_ec2`, `stop_ec2`, `terminate_ec2`, `list_ec2_instances`, `create_bucket`, `describe_images`, `upload_s3`, `download_s3`, `list_s3_objects`, `deploy_lambda`, `update_lambda_code`, `invoke_lambda`, `create_cluster`, `register_task_definition`, `create_service`, `update_service`} | Credentials pulled just-in-time from MongoDB (never `.env`); destructive actions require explicit confirmation; dry-run (`--no-execute` or equivalent) before apply when supported |
| `github_deployer` | Local git + aws_deployer | Package GitHub repositories and deploy artifacts | `invoke(action: str, params: dict)` where `action` ∈ {`deploy_lambda_repo`, `deploy_ec2_repo`} | Clones repo into temp dir, zips contents, uploads to S3 if needed, and delegates AWS operations to `aws_deployer` |
| `openai_llm` | OpenAI API | Long-form reasoning, code synthesis | `complete(prompt, model, tokens)` | Temperature defaults: 0.2 planning, 0.5 creative; log prompt hashes only |
| `mongo_store` | MongoDB | Persist long-term memory, deployment history | `save(collection, doc)`, `find(query)` | Encrypt sensitive payloads client-side; TTL=90d for request logs |
| `qdrant_vector` | Qdrant | Semantic retrieval for past conversations, templates | `query(collection, embedding, top_k=5)` | Only store sanitized embeddings (no secrets) |
| `github_api` | GitHub | Clone/retrieve repo metadata, PR diff | `fetch_repo(url)`, `create_pr(data)` | Require user token scope check; read-only unless user approves write |
| `telemetry_bus` | Observability pipeline | Emit metrics/logs | `emit(event_type, payload)` | Payload must be scrubbed of secrets (auto-redaction middleware) |
| `agentpro_runtime` | AgentPro runtime APIs | Instantiate/update multi-agent graphs | `load_graph(name: str)`, `run_graph(graph_id, inputs)` | Immutable graph definitions versioned; runtime enforces role guardrails |

---

## 3. Agent Roster

Agents are implemented as `agentpro` graph nodes. Each node enforces least-privilege tool access using AgentPro's role-based guardrail configuration.

### 3.1 Conversation Orchestrator
- **Role**: Primary interface with user; manages turn-taking and routing.
- **Responsibilities**: Gather intents, validate inputs, delegate tasks, summarize progress, and stream interactions over the `/ws/agent` WebSocket.
- **Allowed tools**: `openai_llm`, `repo_reader` (read-only for context), `telemetry_bus`.
- **Guardrails**: Must confirm high-impact actions (deployments, AWS changes) with user; no shell access.

### 3.2 Context Curator
- **Role**: Produce concise technical briefs from repo/frontend/backend.
- **Responsibilities**: Index relevant files, update Qdrant embeddings, maintain context windows.
- **Allowed tools**: `repo_reader`, `qdrant_vector`, `telemetry_bus`.
- **Guardrails**: Strip secrets before embedding; respect 2k token summary limit.

### 3.3 Requirements & Data Intake Agent
- **Role**: Collect deployment parameters (AWS region, env vars, repo paths).
- **Responsibilities**: Validate mandatory fields, request clarifications, map user inputs to schema, and gather AWS credentials securely for persistence in MongoDB (never the `.env`). The frontend surfaces a “Configure AWS Credentials” dialog when the agent requests them.
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
- **Guardrails**: Requires signed-off plan; log change set IDs; automatic rollback on failure; must follow the AGENTS.md Addendum confirmation requirements before destructive boto3 operations.

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
1. **Intake**: Orchestrator logs session (via `/ws/agent` WebSocket), collects high-level intent. Requirements Agent presents dynamic form (env vars, repo URL/local path, AWS region, target service) and securely prompts for AWS credentials, storing them in MongoDB. AgentPro graph node `intake_handoff` enforces completion before advancing.
2. **Context Sync**: Context Curator reads `backend/`, `frontend/`, `pyproject.toml`, `package.json`, updates embeddings. Outputs 1–2 paragraph summary + critical files list.
3. **Preflight Validation**:
   - Planning Agent runs `pnpm --dir frontend build --mode development` (dry-run when possible) and `uv run python -m compileall backend`.
   - Confirms AWS credentials exist in MongoDB and remain encrypted at rest; instructs user to provide them if absent.
4. **Plan Drafting**: Planning Agent drafts step list (package, infrastructure provisioning, deployment command). Compliance Officer reviews for policy violations.
   - When the plan involves a GitHub repository, confirm repository URL, branch, staging bucket (for EC2), and Lambda/EC2 parameters required by `github_deployer` before requesting approval.
5. **Dry Run**: Deployment Executor runs `aws_deployer` in preview mode (`--no-execute-changeset`) capturing outputs. Failures loop back to Step 4.
6. **Execution**: On approval, Deployment Executor performs live deploy via `aws_deployer` boto3 actions defined in the AGENTS.md Addendum, streaming logs to Telemetry Bus.
7. **Validation**: Post-Deployment Validator runs FastAPI health check (`/api/health`), verifies AWS response JSON indicates success. Stores results in Mongo (deployment history) and Qdrant (summaries).
8. **Closure**: Orchestrator summarizes outcome, next steps, and stores final transcript metadata. AgentPro commits run metadata (`graph_run_id`, `completion_state`) to telemetry hooks.

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
- Honor the AGENTS.md Addendum boto3 guardrails: confirm destructive operations, log every tool call, and restrict to supported AWS services/actions unless explicitly extended.
- Credential policy: never surface AWS keys in plaintext; fetch them from MongoDB only when executing deployments and redact after use.

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
- **Testing mandate**: Every new feature must include at least one automated test; the pipeline must enforce `uv run python -m pytest` (and relevant frontend suites) to succeed before deployment.
- **Evaluation cadence**: Weekly automated run; failures generate tickets for review.

---

## 9. Telemetry & Logging
- Emit structured JSON events via `telemetry_bus` with fields: `session_id`, `agent`, `phase`, `status`, `latency_ms`, `error_code` (nullable).
- Store live run logs in Mongo `telemetry` collection with per-field redaction.
- Send critical alerts (deployment failure, compliance block) to configured pager/Slack webhook.
- Provide audit trail by linking telemetry events to plan IDs and AWS change set IDs.
- AgentPro runtime observers forward graph lifecycle events (`graph_started`, `node_completed`, `graph_failed`) into the telemetry bus for unified tracing.
- Capture boto3 tool invocations (action, resource identifiers, confirmation status) to satisfy AGENTS.md Addendum auditing.

---

## 10. Maintenance Notes
- Update tool schemas when new AWS services or agent capabilities are added.
- Review guardrails quarterly against AWS, GitHub, and OpenAI policy changes.
- Keep OpenAI models and cost budgets aligned with organizational quotas.
- Conduct live-fire drills every release to ensure rollback paths remain valid.
- Pin AgentPro graph definitions (`./agentpro/*.yaml`) to semantic versions; run AgentPro regression suite when upgrading the library or graph schemas.
- Reconcile updates to the AGENTS.md Addendum so boto3 tool inventories and guardrails stay in sync.
- Coordinate with the UI team to ensure AWS credential capture flows remain functional and continue storing secrets solely in MongoDB.

# 🔧 AGENTS.md Addendum: AWS Deployments with boto3  

### Purpose  
Extend the agent with **AWS deployment capabilities** using boto3.  
The LLM should never run boto3 directly. Instead, it should emit structured tool calls which the runtime executes with boto3.  

---

## Supported Tools  

### **EC2**
- `launch_ec2(region, instance_type, key_name, ami_id)`  
- `stop_ec2(instance_id, region)`  
- `terminate_ec2(instance_id, region)`  
- `list_ec2_instances(region)`  

### **S3**
- `create_bucket(bucket_name, region)`  
- `upload_s3(bucket_name, file_path, object_name)`  
- `download_s3(bucket_name, object_name, file_path)`  
- `list_s3_objects(bucket_name)`  

### **Lambda**
- `deploy_lambda(function_name, zip_file, role_arn, handler, runtime, region)`  
- `update_lambda_code(function_name, zip_file, region)`  
- `invoke_lambda(function_name, payload, region)`  

### **ECS (Fargate)**
- `create_cluster(cluster_name, region)`  
- `register_task_definition(family, container_definitions, requires_compatibilities, cpu, memory, execution_role_arn)`  
- `create_service(cluster, service_name, task_definition, desired_count, launch_type, region)`  
- `update_service(cluster, service_name, desired_count, region)`  

---

## Workflow  

1. **Interpret user request** → Map to supported boto3 tool.  
2. **Emit tool call** → Structured JSON, e.g.:  
   ```json
   {
     "tool": "launch_ec2",
     "args": {
       "region": "us-east-1",
       "instance_type": "t3.micro",
       "key_name": "dev-key",
       "ami_id": "ami-1234567890abcdef0"
     }
   }
   ```  
3. **Runtime executes** → Calls registered boto3 function.  
4. **Return results** → Summarized by the LLM, possibly chaining into next steps.  

---

## Guardrails  
- Confirm destructive actions (`terminate_ec2`, `delete_bucket`, `update_service`).  
- Enforce IAM least-privilege roles.  
- Never expose AWS credentials.  
- Log all boto3 actions for auditing.  

---

## Example Interactions  

**User:**  
> Launch a t3.micro EC2 instance in `us-east-1` with key `dev-key`.  

**Tool Call:**  
```json
{
  "tool": "launch_ec2",
  "args": {
    "region": "us-east-1",
    "instance_type": "t3.micro",
    "key_name": "dev-key",
    "ami_id": "ami-1234567890abcdef0"
  }
}
```  

**User:**  
> Upload `app.zip` to the `my-app-bucket` in S3.  

**Tool Call:**  
```json
{
  "tool": "upload_s3",
  "args": {
    "bucket_name": "my-app-bucket",
    "file_path": "app.zip",
    "object_name": "app.zip"
  }
}
```  

**User:**  
> Deploy a Lambda named `hello-func` with runtime `python3.12`.  

**Tool Call:**  
```json
{
  "tool": "deploy_lambda",
  "args": {
    "function_name": "hello-func",
    "zip_file": "hello_func.zip",
    "role_arn": "arn:aws:iam::123456789012:role/lambda-exec-role",
    "handler": "hello.handler",
    "runtime": "python3.12",
    "region": "us-east-1"
  }
}
```  

**User:**  
> Create an ECS Fargate service with 2 tasks using `my-task`.  

**Tool Call:**  
```json
{
  "tool": "create_service",
  "args": {
    "cluster": "my-cluster",
    "service_name": "my-service",
    "task_definition": "my-task:1",
    "desired_count": 2,
    "launch_type": "FARGATE",
    "region": "us-east-1"
  }
}
```  
