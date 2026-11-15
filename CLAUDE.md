# Amaze for CM — Proof of Concept

This file tells Claude Code what this repository is and the rules it must follow. Read it fully before every task.

## What this is

A proof of concept (PoC) of the redesigned Amaze for Capital Markets: an agentic data onboarding and consumption system. It proves one end-to-end path. A bigger team builds the production system after approval, so favour clear structure and replaceable parts over completeness.

The system is three platforms, in the order data moves:

1. **Custom data product (ingestion only).** Activates only when an end user asks for data that Amaze has not produced and no known source has. It finds external sources, lists them to the requester with the approvals needed, and once the requester picks one, a daily scheduler fetches new data into Amaze.
2. **Amaze.** The backend team's platform. Onboards each feed once (steps 01–11), then runs every load (steps 12–16).
3. **Data product consumption.** The only platform end users see. Search first, reuse, compose or create; build new only when needed (steps 17–22).

## Non-negotiable rules

1. **AI proposes; people decide; rules execute.** An agent never changes configuration or data directly. It writes a *proposal*. A named person approves or rejects it. Only approved proposals become versioned configuration.
2. **No model call in the run engine (step 13) or in entitlement decisions.** These are deterministic code only.
3. **Every negative outcome is explicit.** Each step returns a typed result: success or one of its named negative outcomes (see the step table). Never return a bare error for an expected case.
4. **Every AI proposal is logged** with the agent name, provider, model and version, prompt version, input reference, output, and who approved or rejected it.
5. **Everything Azure sits behind an interface** (see Placeholders). Local implementations run on a Windows laptop with no containers; Azure implementations are switched on by configuration. Business logic never imports an Azure SDK directly.
6. **Synthetic data only.** No client files, client names or real vendor data in this repository, tests or fixtures.
7. **Configuration is versioned, never edited in place.** A change creates a new version.

## Tech stack

- Backend: Python 3.14 (3.14.5 on this laptop and the office laptop; `requires-python = ">=3.14,<3.15"`), FastAPI, Pydantic v2, SQLAlchemy 2, Alembic. Create the venv with `py -3.14`.
- Data: PostgreSQL 18 (any 18.x minor) on both laptops (stores and repositories). Use no PostgreSQL feature newer than 14 until the Azure Database for PostgreSQL version is confirmed. DuckDB (profiling and SQL rules on files without staging).
- Frontend: React + TypeScript (Vite). Two apps: `backend-ui` (Amaze, backend team) and `consumer-ui` (consumption, end users).
- Local runtime: no containers. PostgreSQL installed directly on Windows. PowerShell scripts are plain ASCII so Windows PowerShell 5.1 reads them correctly. Files go to a local folder (`STORAGE_PROVIDER=local`). The API runs with uvicorn in a Python virtual environment and the two UIs with `npm run dev`. `scripts/dev.ps1` starts all three; `scripts/setup_db.ps1` creates the database and its user.
- Infrastructure as code: Bicep under `infra/`.
- Tests: pytest; Playwright for one end-to-end test of the PoC path.

## Repository layout

```
apps/
  api/                    FastAPI app, routers per platform; jobs.py: the one daily source fetch, registered
                          at every start-up, reading the feeds table when it runs
  backend-ui/             Amaze workbenches (data owner, steward, glossary owner, operations)
  consumer-ui/            Marketplace portal for end users
core/
  domain/                 Pydantic models: Feed, Requirement, Proposal, Mapping, Rule, Load, Product, ...
  repositories/           Tables (tables.py), named errors (errors.py) and one repository per model.
                          Repositories flush but never commit; the caller owns the transaction.
  proposal_log.py         ProposalLog.record(): one row per model call (rule 4)
  seed.py                 python -m core.seed: loads the synthetic glossary (fixtures/glossary/);
                          --demo loads the demo products (fixtures/demo/, codes start with demo_)
  steps/                  One module per step (s1_scope.py ... s22_monitor.py); each returns a typed StepResult
                          (result.py: a Pydantic union discriminated on `outcome`). Also matching.py (request
                          text to glossary elements), routing.py (routing checks), request_data.py (one
                          "Request this data", end to end) and errors.py (refused step input). Phase 3:
                          known_source.py (Known source?), s1_scope.py, s2_sources.py, s3_pick.py, s4_fetch.py
                          (S1-S4), release.py (release a selected source) and requirement_page.py (what the
                          requester sees, and every requester message). A step that needs an agent receives
                          it as an argument, so core/steps never imports agents
  engine/                 Deterministic run engine (step 13). No AI imports allowed here.
  entitlement/            Rules-based entitlement decisions (decide.py). No AI imports allowed here.
agents/
  base.py                 Agent base class. Agent.run() is the only path to the model: it calls the
                          LLMProvider, stores the input, logs the call, then creates the proposals. A
                          subclass that defines run() is refused when the class is created
  registry.py             Agent registry, current prompt versions and a hash of every prompt file
  prompt_loader.py        Loads and renders prompts/<prompt_id>.<version>.yaml
  <agent_name>.py         One file per agent
  prompts/                Versioned prompt templates (YAML). Never edit one: save a new version
ports/                    Ports (interfaces) and adapters — see Placeholders. Named ports/, not
                          platform/, because a package named platform hides Python's own platform module.
                          ports/__init__.py stays empty; each port's factory lives in its own package.
  db/                     Database engine, session and table base class (selected by DATABASE_URL)
  llm/                    LLMProvider: mock (default), azure_openai (stub)
  storage/                BlobStore: local, azure_blob (stub)
  scheduler/              Scheduler: local (APScheduler), azure_functions (stub)
  identity/               Identity: dev users, entra_id (stub)
  secrets/                Secrets: env file, key_vault (stub)
  notify/                 Notifier: in-app, email/Teams (stub)
  connectors/             Source connectors: local file, HTTP (for the custom data product), and the source
                          catalogue (catalogue.py). HTTP reaches only SOURCE_HTTP_ALLOWED_HOSTS (this
                          machine until the Azure move) and never follows a redirect
migrations/               Alembic migrations (alembic.ini at the repository root)
scripts/                  setup_db.ps1, dev.ps1 (PowerShell, Windows)
infra/                    Bicep templates (placeholders until the Azure move)
fixtures/                 Synthetic feeds, sample files, glossary seed, gold mappings for evaluation;
                          sources/: the synthetic source catalogue and its files (codes start with syn_)
tests/
```

## Placeholders (fill these after the move to the office laptop)

Every adapter below is selected by an environment variable. The default is the local one. Azure adapters exist as stubs: correct class, correct method signatures, configuration keys read from the environment, and `NotImplementedError("Azure adapter: implement after move")` in the body. Tests run against the local adapters only.

| Port | Local (now) | Azure (after the move) | Selector |
|---|---|---|---|
| `LLMProvider` | `MockProvider`: deterministic, rule- and fixture-based proposals | `AzureOpenAIProvider` | `LLM_PROVIDER=mock\|azure_openai` |
| `BlobStore` | Local folder | Azure Blob / Data Lake Storage Gen2 | `STORAGE_PROVIDER=local\|azure_blob` |
| `Scheduler` | APScheduler inside the API | Azure Functions timer trigger | `SCHEDULER_PROVIDER=local\|azure_functions` |
| `Identity` | Seeded dev users with roles | Microsoft Entra ID | `IDENTITY_PROVIDER=dev\|entra_id` |
| `Secrets` | `.env` file | Azure Key Vault | `SECRETS_PROVIDER=env\|key_vault` |
| `Notifier` | In-app notifications table | Email or Teams | `NOTIFY_PROVIDER=inapp\|email` |
| Database | Local PostgreSQL (Windows install) | Azure Database for PostgreSQL | `DATABASE_URL` |

### LLMProvider contract

```python
class LLMProvider(Protocol):
    name: str            # "mock" | "azure_openai"
    def complete(self, *, prompt_id: str, prompt_version: str, messages: list[Message],
                 response_schema: type[BaseModel], temperature: float = 0.0) -> LLMResult: ...

class LLMResult(BaseModel):
    output: BaseModel          # validated against response_schema
    provider: str
    model: str                 # deployment name for Azure OpenAI
    model_version: str
    prompt_id: str
    prompt_version: str
    latency_ms: int
    raw_ref: str | None        # pointer to the stored raw response
```

- `MockProvider` must return outputs that validate against the same `response_schema`, built from fixtures and simple heuristics (for example, name similarity for glossary mapping). It must be deterministic so tests and demos are repeatable.
- `AzureOpenAIProvider` reads `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`, and either `AZURE_OPENAI_API_KEY` or Entra ID through `DefaultAzureCredential` when `AZURE_OPENAI_AUTH=entra`. It uses structured outputs against `response_schema`. Stub only until the move.
- Agents call only `LLMProvider`. No agent imports an SDK.
- Every call goes through `ProposalLog.record(...)` before the proposal is shown to a person.

## Agents in the PoC

Each agent extends `agents/base.Agent`, declares `autonomy` (A1 assist or A2 recommend; A3 is not used in the PoC) and `approver_roles` (an ordered tuple, each role once; `()` for a read-only agent), and returns a `Proposal`.

| Step | Agent | Autonomy | Approver |
|---|---|---|---|
| S1 | IntentAgent: scope (public or private, who provides, where to search) | A2 | Requester |
| S2 | SourceDiscoveryAgent: candidate sources with coverage, licence, approvals needed | A2 | Requester picks (S3) |
| 02 | ProfilerAgent: summarises the DuckDB profile against the requirement | A2 | Backend team (03) |
| 05 | GlossaryBuilderAgent: new elements only, runs in the background | A2 | Glossary owner |
| 06 | MappingAgent: source fields to the standard model | A2 | Data steward |
| 09 | RuleRecommenderAgent: SQL rules, back-tested on past loads | A2 | Steward, then checker |
| 11 | ReviewSummariserAgent: summary for go-live | A1 | Data owner |
| 17 | DiscoveryAssistant: search by element, domain or purpose | A2 | — (read only) |

Step 12's structure check is deterministic (no agent). Step 13 and entitlement have no agent.

## Steps and negative outcomes in the PoC

Implement these as typed results in `core/steps/`. Names in `snake_case`.

- **17 Discover**: `found`, `not_entitled_visible` (shown, with the route to access)
- **Routing checks**: `reuse`, `compose` (stub for PoC), `create_product`, `raise_requirement`
- **01 Define requirement**: `defined`, `element_not_in_glossary`
- **Known source?**: `known_source` → 02, `no_known_source` → S1
- **S1–S4**: `scoped`, `scope_not_confirmed`; `sources_listed`, `no_fit`; `source_picked`, `approval_missing`; `fetched`, `source_down`
- **02 Profile**: `profiled`, `spec_only_low_confidence`
- **03 Sign off**: `meets`, `meets_with_gaps`, `rejected` (reasons kept, requester told)
- **04 Register**: `registered`, `duplicate_linked`, `no_owner`
- **05 Glossary**: `proposed`, `duplicate_alias`, `conflict_to_owner`
- **06 Map**: `mapped`, `tie_to_steward`, `no_element_back_to_05`
- **09 Validation**: `rules_proposed`, `flags_most_history_returned`
- **11 Approve**: `approved_scheduled`, `rejected_back_to_step`, `test_failed_held`
- **12 Arrival and structure**: `on_time_complete`, `late_alert`, `resent_quarantined`, `additive_parked`, `breaking_held`
- **13 Run**: `stage_completed` per stage, `failed_rolled_back`
- **15 Certify**: `released`, `held_consumers_warned`
- **20 Create product**: `created`, `data_not_in_domains`
- Requester messages: "Your data is getting onboarded; we will notify you once it is ready.", then "Ready" or "Access restricted".

## Plugins used in this project, and which one wins

Installed in Claude Code: Superpowers, Karpathy guidelines, Claude Octopus, i-have-adhd.

Order of precedence when they disagree:

1. **This file's non-negotiable rules** override every plugin.
2. **Superpowers** is the working method: brainstorm, write the plan, test first, execute, review. Use it instead of ad-hoc planning. Plans go in `docs/plans/`, one per phase of `BUILD_PLAN.md`.
3. **Karpathy guidelines** apply to every change: surface assumptions first, keep it simple, change only what the task needs, and define how the result will be checked.
4. **i-have-adhd** shapes replies to the developer: answer first, numbered steps, no filler. It changes how replies read, never what gets checked or reported. Problems, risks and failed tests are always stated.
5. **Claude Octopus** is off by default. Turn it on for a review at the end of each phase, then off again. Use the Claude provider only. Do not configure other AI providers (Codex, Gemini or others) in this repository unless the project sponsor has approved sending code to them.

## How to work in this repo

- Follow the Superpowers flow for each phase: brainstorm → plan in `docs/plans/` → tests for the step's negative outcomes first → implement → review.
- Keep `core/engine/`, `core/entitlement/`, `core/domain/`, `core/repositories/` and `core/steps/` free of `agents` and `ports.llm` imports; a test enforces this. An agent-backed step receives its agent as an argument; the API builds the agent (phase 3 plan, assumption 22). No file outside `agents/base.py` calls `.complete(`; a test enforces this too.
- Every step that changes a requirement (or any row others may change at the same time) locks that row first (`RequirementRepository.lock()`), and the API maps each named conflict to 409, never 500.
- No database connection is held during a model call. Every agent-backed step follows one pattern: (1) a read-only `*_input()` builds the agent's input and checks the status; (2) the API ends that read transaction; (3) `Agent.run(..., before_write=...)` calls the model; (4) in `before_write` the step locks the row and checks its status again; (5) only then are the log row, the proposals and the status change written, in one transaction. `Agent.run()` commits the call's `proposal_log` row in its own short transaction right after the call, before the request writes anything; a change during the call gives 409 and a second, append-only row in `proposal_log_discards` pointing at it, with no proposal. Every answered model call has exactly one call row. `tests/test_concurrency.py` checks `pool.checkedout() == 0` during every call. The source fetch (S4) follows the same read / end transaction / call / lock / write pattern.
- Rules 1 and 7 are also enforced by the database: append-only tables refuse UPDATE, DELETE and TRUNCATE, and `mappings`, `rules` and non-seed `glossary_elements` rows need an approved proposal of the matching kind. Never work around these triggers; add a new version instead.
- At the end of each phase, append any UI polish items found to `docs/ui-polish.md` (one line each: screen, issue, proposed fix). Do not fix them unless asked; phase 7.5 does.
- Edit files only with the file tools (Read, Edit, Write). Never rewrite or patch files through PowerShell, sed or other shell commands.
- When an Azure detail is needed, add it to the stub and to `.env.example`, never to business logic.
- Do not add features beyond the current phase in `BUILD_PLAN.md`.
