# Crestview Capital Group — AI-Powered Client Onboarding Pipeline

A production-grade Python POC demonstrating an AI-driven compliance onboarding pipeline for a financial services firm. Every AI output is structured, explainable, Pydantic-validated, and reviewed by a human before any decision is made.

## What This POC Demonstrates

- **Four-stage LLM chain**: Stage 0 extracts structured data from documents; Stage 1 produces a risk assessment with live tool calls; Stage 2 generates an actionable onboarding summary; an independent LLM-as-judge evaluates both
- **Tool calling**: The Stage 1 LLM calls external compliance APIs mid-analysis (sanctions checks, PEP screening, jurisdiction lookups) using LiteLLM's OpenAI-compatible function-calling API
- **Strict Pydantic v2 validation**: All LLM outputs are validated against typed schemas before being used downstream — invalid JSON or schema mismatches are caught and logged
- **LLM-as-judge evaluation**: An independent evaluator LLM scores each output against defined rubrics (0–100 scale per dimension) with mandatory justifications
- **Real monday.com integration**: Processed applications are written to a real board with status, priority, assigned team, and full assessment text as item updates
- **Simulated external services**: ClientHub, ComplianceOne, SharePoint, Salesforce, and Outlook are mocked with deterministic responses — swapping in real integrations means replacing the service class, not changing the pipeline

## Architecture

```
 ════════════════════ PHASE 1 — Agent 1: Data Ops ═══════════════════

 CSV Input (15 applications)
         │
         ▼
  Data Loader (src/data_loader.py)
         │ ClientApplication (Pydantic)
         ▼
  ┌──────────────────────────────────────┐
  │  STAGE 0 — Document Extraction       │
  │  src/pipeline.py :: run_stage0()     │
  │                                      │
  │  LiteLLM call (claude-sonnet-4-6)   │
  │  Input: uploaded documents           │
  │  Output: ExtractedClientData         │
  └──────────────────────────────────────┘
         │
         ▼
  Completeness Check (src/completeness_checker.py)
  ├── proceed_to_compliance  ──────────────────────────────────────────┐
  ├── ops_review_required → _apply_ops_corrections() → re-check       │
  └── return_to_client    → _apply_client_corrections() → re-check    │
                                                                       │
         ┌─────────────────────────────────────────────────────────────┘
         ▼
  ┌──────────────────────────────────────┐
  │  STAGE 1 — Risk Assessment           │
  │  src/pipeline.py :: run_stage1()     │
  │                                      │
  │  LiteLLM call (claude-sonnet-4-6)   │
  │  ├── System: compliance analyst role │
  │  ├── User: application + schema      │
  │  └── Tools (if ENABLE_TOOLS=true):  │
  │      ├── check_sanctions_list        │
  │      ├── check_pep_status            │
  │      ├── lookup_jurisdiction_risk    │
  │      ├── store_document              │
  │      └── send_status_notification   │
  │                                      │
  │  Output: RiskAssessment (Pydantic)  │
  │          6 mandatory risk dimensions │
  └──────────────────────────────────────┘
         │
         ▼
  ComplianceOne screening → monday.com board item created
         │
         ▼
  Phase 1 results persisted to DB + outputs/latest/

 ════════════════════ PHASE 2 — Agent 2: Compliance ══════════════════

         ▼  (reads Phase 1 output from persistence layer)
  ┌──────────────────────────────────────┐
  │  STAGE 2 — Onboarding Summary        │
  │  src/pipeline.py :: run_stage2()     │
  │                                      │
  │  LiteLLM call (no tools)            │
  │  Input: application + Stage 1 output │
  │  Output: OnboardingSummary (Pydantic)│
  │          monday board fields         │
  └──────────────────────────────────────┘
         │
         ├──► monday.com board updated (status, priority, next steps)
         ├──► Compliance officer determination recorded
         ├──► ClientHub write
         ├──► Salesforce update
         └──► Outlook notification
         │
         ▼
  ┌──────────────────────────────────────┐
  │  EVALUATOR — LLM-as-Judge            │
  │  src/evaluator.py                    │
  │  (runs if RUN_EVALUATIONS=true)      │
  │                                      │
  │  Independent LLM scores Stage 1 +    │
  │  Stage 2 outputs: 5 dimensions each  │
  │  (0–100 with mandatory justification)│
  └──────────────────────────────────────┘
         │
         └──► monday.com eval board + outputs/latest/
```

## Project Structure

```
POC-Task/
├── main.py                          # Entry point — runs full pipeline
├── pyproject.toml                   # Dependencies
├── .env.example                     # All required env vars (copy to .env)
├── crestview_client_applications.csv
├── models/
│   ├── client.py                    # ClientApplication (CSV → Pydantic)
│   ├── extraction.py                # ExtractedClientData, DocumentField
│   ├── risk.py                      # RiskAssessment, RiskDimensionResult
│   ├── onboarding.py                # OnboardingSummary, MondayBoardFields
│   ├── pipeline.py                  # PipelineResult, PipelineMetrics, PipelineResultMetadata
│   └── evaluation.py                # RiskAssessmentEvaluation, OnboardingSummaryEvaluation
├── prompts/
│   ├── system_prompt.txt            # Shared system prompt (both stages)
│   ├── stage1_user_prompt.txt       # Stage 1 prompt template (with worked example)
│   ├── stage2_user_prompt.txt       # Stage 2 prompt template
│   ├── evaluator_system_prompt.txt  # Judge system prompt with scoring calibration
│   └── evaluator_user_prompt.txt    # Judge user prompt template
├── services/
│   ├── client_hub.py                # Simulated — client management system
│   ├── compliance_one.py            # Simulated — KYC/AML screening platform
│   ├── sharepoint.py                # Simulated — document storage
│   ├── salesforce.py                # Simulated — CRM
│   ├── outlook.py                   # Simulated — email notifications
│   └── monday_service.py            # REAL — monday.com GraphQL API v2
├── src/
│   ├── data_loader.py               # CSV ingestion → ClientApplication models
│   ├── document_loader.py           # Document parsing → ExtractedClientData
│   ├── completeness_checker.py      # Deterministic completeness gating (no LLM)
│   ├── pipeline.py                  # Stage 0, Stage 1, Stage 2 LLM calls
│   ├── tools.py                     # Tool schemas + ToolDispatcher + _JURISDICTION_RISK
│   ├── evaluator.py                 # LLM-as-judge evaluator
│   ├── output_writer.py             # compliance_report.md + run_summary.json writer
│   └── utils.py                     # JSON extraction, retry helpers
├── persistence/
│   ├── models.py                    # SQLModel table definitions (4 tables)
│   └── repository.py                # Read/write operations (SQLite)
├── data/
│   └── crestview_pipeline.db        # SQLite audit log (auto-created on first run)
└── outputs/
    ├── latest/                      # Symlink to most recent run output
    │   ├── run_summary.json
    │   └── applications/
    │       └── APP-*/
    │           └── compliance_report.md
    └── evaluation_results.json      # Full structured output written after Phase 2
```

## Setup

### 1. Install dependencies

```bash
pip install -e .
```

### 2. Configure environment

```bash
cp .env.example .env
```

| Variable | Required | Default | Notes |
|---|---|---|---|
| `LLM_PROVIDER` | No | `anthropic` | `anthropic` or `azure` — selects the LLM backend |
| `ANTHROPIC_API_KEY` | Yes (Anthropic) | — | console.anthropic.com/settings/keys |
| `AZURE_API_KEY` | Yes (Azure) | — | Azure Portal → your OpenAI resource → Keys and Endpoint |
| `AZURE_API_BASE` | Yes (Azure) | — | `https://<resource>.openai.azure.com/` |
| `AZURE_API_VERSION` | Yes (Azure) | — | e.g. `2024-02-01` |
| `AZURE_DEPLOYMENT_NAME` | Yes (Azure) | `gpt-4o` | Deployment name from Azure AI Foundry |
| `MONDAY_API_KEY` | Yes | — | monday.com → Avatar → Admin → API |
| `MONDAY_BOARD_ID` | Yes | — | Number in the board URL: `/boards/1234567890` |
| `MONDAY_EVAL_BOARD_ID` | Yes | — | Separate board for evaluator scores |
| `LLM_MODEL` | No | `anthropic/claude-sonnet-4-6` | Any LiteLLM model string (e.g. `azure/gpt-4o`) |
| `EVALUATOR_MODEL` | No | `anthropic/claude-sonnet-4-6` | Model used for the judge |
| `ENABLE_TOOLS` | No | `true` | Set to `false` for plain completion (no tool calls) |
| `RUN_EVALUATIONS` | No | `true` | Set to `false` to skip the judge LLM call |
| `DATABASE_PATH` | No | `data/crestview_pipeline.db` | SQLite audit log path |
| `CONFIDENCE_THRESHOLD` | No | `0.7` | Extraction confidence below this triggers ops review |
| `RAW_LLM_RESPONSES` | No | `false` | Store full LLM response text in DB |
| `OUTPUT_DIR` | No | `outputs` | Root directory for per-run file output |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `PIPELINE_VERSION` | No | `0.1.0` | Version tag embedded in all outputs |

### Switching to Azure OpenAI

All LLM calls are routed through [LiteLLM](https://docs.litellm.ai), so switching providers requires only env var changes — no code modifications.

```bash
LLM_PROVIDER=azure
AZURE_API_KEY=<your-key>
AZURE_API_BASE=https://<your-resource>.openai.azure.com/
AZURE_API_VERSION=2024-02-01
AZURE_DEPLOYMENT_NAME=gpt-4o   # must be gpt-4o or gpt-4 to support tool calling
```

> **Note:** Stage 1 tool calling requires a deployment backed by `gpt-4o` or `gpt-4`. Older Azure deployments (e.g. `gpt-35-turbo`) do not support function calling and will error if `ENABLE_TOOLS=true`.

### 3. monday.com Board Setup

You need two boards.

**Main onboarding board** (`MONDAY_BOARD_ID`) — one item per application:

| Column Name | Type | Notes |
|---|---|---|
| Status | Status | Labels: New, Processing, In Compliance, Out Of Compliance, Need Information, Approved, Rejected, Pending Review, Pending Client, Ready to Onboard |
| Priority | Status | Labels: Low, Medium, High, Critical |
| Assigned Team | Text | Free text |

**Eval board** (`MONDAY_EVAL_BOARD_ID`) — one item per application with evaluator scores:

| Column Name | Type |
|---|---|
| Status | Status |
| Overall Risk Score | Number |
| Overall Summary Score | Number |

After creating the boards, run `get_board_columns()` to confirm column IDs match what `monday_service.py` expects.

## Running the Pipeline

The pipeline runs in two phases. Phase 1 and Phase 2 are separate commands — run Phase 1 first, then Phase 2 reads its output.

### Phase 1 — Agent 1: Document extraction + risk assessment
```bash
python main.py --phase=1
```

### Phase 2 — Agent 2: Onboarding summary + compliance determination
```bash
python main.py --phase=2
```

### Full run with all features enabled
```bash
RUN_EVALUATIONS=true ENABLE_TOOLS=true python main.py --phase=1
RUN_EVALUATIONS=true ENABLE_TOOLS=true python main.py --phase=2
```

### Without tools (plain completion mode, no tool calls)
```bash
ENABLE_TOOLS=false python main.py --phase=1
```

### Debug logging
```bash
LOG_LEVEL=DEBUG python main.py --phase=1
```

## What to Expect

### Console output
For each of the 15 applications you will see:
- Risk level, review track, complexity, estimated review time
- Compliance flags (if any)
- Blockers (if any)
- Evaluator scores (if `RUN_EVALUATIONS=true`, Phase 2 only)

After all applications:
- A formatted metrics table (risk distribution, escalation rate, avg scores, latency, token usage)
- Top compliance flags across the run

### File output
Each run writes to `outputs/<run_id>/` with a symlink at `outputs/latest/`:

```
outputs/latest/
├── run_summary.json                  # Aggregate metrics for the run
└── applications/
    └── APP-2026-XXXX/
        └── compliance_report.md      # Human-readable report per application
```

After Phase 2 completes, `outputs/evaluation_results.json` is also written with the full structured output for every application (risk assessment, onboarding summary, evaluator scores, pipeline metadata).

### Audit log
Every run is persisted to `data/crestview_pipeline.db` (SQLite):
- `pipeline_runs` — one row per phase run
- `application_results` — full risk assessment + onboarding summary JSON per application
- `llm_calls` — model, tokens, latency, tool calls made for every LLM invocation
- `evaluation_scores` — all 10 evaluator dimension scores with reasoning JSON

### monday.com boards
**Main board** — one item per application, updated across both phases:
- Status column set to compliance determination (`In Compliance` / `Out Of Compliance` / `Need Information`)
- Priority and assigned team set from the onboarding summary
- Item updates: completeness routing notes, risk assessment body, onboarding summary, next steps, officer determination

**Eval board** — one item per application with evaluator scores (Phase 2, `RUN_EVALUATIONS=true` only)

A "Pipeline Run Summary" item with aggregate metrics is posted at the end of each phase.

## What Is Real vs Simulated

| Component | Status | Notes |
|---|---|---|
| LLM calls (Stage 0, Stage 1, Stage 2, Evaluator) | **Real** | Calls Anthropic API via LiteLLM |
| monday.com board writes | **Real** | Calls monday.com GraphQL API v2 |
| ClientHub | Simulated | In-memory dict; replace with REST API calls |
| ComplianceOne | Simulated | Deterministic responses based on known risk signals in the test data |
| SharePoint | Simulated | Logs URL generation; replace with Microsoft Graph API |
| Salesforce | Simulated | Hardcoded RM assignments; replace with Salesforce REST API |
| Outlook | Simulated | Logs email sends; replace with Microsoft Graph /sendMail |

## Tool Calling

Stage 1 exposes five tools to the LLM:

| Tool | Service | What it checks |
|---|---|---|
| `check_sanctions_list` | ComplianceOne | OFAC SDN, UN, EU, FinCEN databases |
| `check_pep_status` | ComplianceOne | Politically Exposed Person database |
| `lookup_jurisdiction_risk` | Internal DB | FATF grey list / black list classification |
| `store_document` | SharePoint | Stores received documents |
| `send_status_notification` | Outlook | Notifies stakeholders |

The LLM decides autonomously when to call these tools based on the application content. For example, it will call `check_sanctions_list` for a client with an opaque ownership structure and `lookup_jurisdiction_risk` for an offshore-registered entity.

Toggle tools: `ENABLE_TOOLS=true/false`

In production, the simulated `ComplianceOneService` would be replaced with real API calls to ComplianceOne's screening endpoints. The tool interface (name, parameters, return shape) stays the same — only the implementation changes.

## Evaluation Methodology

After each application is processed, an independent LLM call (the "judge") scores the outputs:

### RiskAssessment scored on:
- **Reasoning quality** (0–100): Are findings grounded in specific application data, not boilerplate?
- **Reasoning completeness** (0–100): Were all six dimensions addressed? Signals missed?
- **Risk level appropriateness** (0–100): Is the assigned level defensible?
- **Compliance flags accuracy** (0–100): Specific and actionable? False positives / negatives?
- **Overall assessment quality** (0–100)

### OnboardingSummary scored on:
- **Actionability** (0–100): Can an ops team member act on this?
- **Risk grounding** (0–100): Does complexity/track logically follow from the risk assessment?
- **Next steps quality** (0–100): Specific, ordered, correctly assigned?
- **Completeness** (0–100): Missing blockers, gaps?
- **Overall summary quality** (0–100)

### Calibration
- **81–100**: Excellent — a senior compliance officer would have no material corrections
- **61–80**: Good — solid analysis with minor gaps
- **41–60**: Acceptable but incomplete — significant room for improvement
- **21–40**: Major gaps — key signals missed or reasoning too vague
- **0–20**: Fundamentally flawed — a compliance officer would reject this outright

The judge is instructed to be critical and not default to high scores. Every score requires a specific justification citing evidence from the output.
