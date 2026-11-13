# Amaze for CM PoC — Build plan

One end-to-end path, built in phases. Each phase ends with something that runs and a test that proves it. Do not start a phase until the previous one passes.

**The PoC story:** a consumer asks for data that does not exist yet. The custom data product finds sources; the requester picks one and sees "being onboarded". The file is profiled with SQL before registration and the backend team signs it off. The glossary builder proposes elements in the background and a person approves them. The load runs on approved SQL rules, is certified and released, and the requester is told it is ready. A later delivery with a changed structure is held and a person is notified.

## Phase 0 — Skeleton and placeholders

- Repository layout as in `CLAUDE.md`. No Docker.
- `scripts/setup_db.ps1` creates the `amaze` database and user in the local PostgreSQL; Alembic runs the first migration.
- Python virtual environment for the API; `npm` projects for both UIs; `scripts/dev.ps1` starts the API and both UIs.
- All ports in `ports/` (renamed from `platform/`, which hides Python's built-in module) with local adapters working and Azure adapters as stubs (signatures, config keys, `NotImplementedError`).
- `LLM_PROVIDER=mock` by default; `MockProvider` returns schema-valid outputs from fixtures.
- `ProposalLog` table and `record()`; seeded dev users for each role.
- Import-boundary test: `core/engine` and `core/entitlement` cannot import `agents` or `ports.llm`.
- **Done when:** `scripts/dev.ps1` starts the API and both UIs, both UIs load in the browser, and tests pass.

## Phase 1 — Domain, repositories and glossary

- Models and tables: Requirement, Feed (candidate and registered), GlossaryElement, Mapping (level 1), Rule, Load, Product, Proposal, Approval, Notification.
- Seed a small synthetic enterprise glossary (security master and pricing elements).
- Feed registry and glossary views in `backend-ui`.
- **Done when:** a person can browse the glossary and the empty feed registry.

## Phase 2 — Consumption request and routing (17, routing checks, 01)

- `consumer-ui`: search (step 17), with "not entitled, shown with route to access".
- Routing checks in order: product exists → reuse; compose (stub); data in domains → create; else raise requirement (step 01).
- Requester status page showing the request's state.
- **Done when:** a search for missing data raises a requirement and the requester sees its status.

## Phase 3 — Custom data product ingestion (S1–S4)

- IntentAgent and SourceDiscoveryAgent (mock) list candidate sources from `fixtures/sources/`, with licence and approvals needed.
- Requester picks a source in `consumer-ui`; sees "Your data is getting onboarded; we will notify you once it is ready."
- Local HTTP or file connector fetches a sample; local scheduler registers a daily job (Azure Functions stub in place).
- Negative outcomes: `no_fit`, `approval_missing`, `source_down`.
- The agent base class logs every proposal itself: `agents/base.Agent.run()` is the only public entry point; it calls the `LLMProvider`, then `ProposalLog.record()`, then creates the proposal rows linked to that log row. A subclass cannot replace `run()`. Tests prove a subclass defining `run()` is refused when the class is created, and that every proposal points at its `proposal_log` row (phase 1 plan, section 18).
- `HttpConnector` closes its connections: it gets `close()` and works in a `with` block; a test asserts the connection is closed after a fetch, including a failed one.
- Local scheduler jobs are kept in memory today: store them in PostgreSQL or re-register them from the database at start-up (phase 0 plan, risk 1).
- DiscoveryAssistant (step 17): search by element, domain or purpose, on `agents/base.Agent.run()`, beside phase 2's deterministic search (moved from phase 2; phase 2 plan, section 17).
- **Done when:** picking a source produces a fetched file handed to step 02.

## Phase 4 — Profile, sign-off, register (02–04)

- DuckDB profiles the file directly: columns, types, null rates, distinct counts, coverage against the requirement.
- ProfilerAgent (mock) summarises; backend team signs off: `meets`, `meets_with_gaps`, `rejected`.
- Register with owner, licence (permitted use, redistribution) and who may see it.
- **Done when:** a rejected candidate keeps its reasons and the requester is told; an accepted one is registered.

## Phase 5 — Glossary builder and mapping (05–06)

- GlossaryBuilderAgent runs in the background on new columns only; progress banner in `backend-ui`; glossary owner approves.
- MappingAgent proposes source → standard model mappings; steward approves; ties go to the steward.
- Gold mappings in `fixtures/gold/` and an evaluation script that scores any `LLMProvider` against them (needed when Azure OpenAI replaces the mock).
- **Done when:** approved mappings are stored as a versioned configuration.

## Phase 6 — Rules, approval and the run engine (09, 11, 13)

- RuleRecommenderAgent proposes SQL rules; back-test on fixture history; steward approves, then checker.
- ReviewSummariserAgent drafts the go-live summary; data owner approves; job scheduled; hypercare flag set.
- Run engine: raw → curated → validated → standard model, each stage tracked; failed file rolled back. No AI.
- Mastering and transformations: minimal pass-through in the PoC, clearly marked.
- **Done when:** an approved feed loads end to end from configuration alone.

## Phase 7 — Every load and release (12, 15) and the consumer loop closing

- Step 12: arrival check and deterministic structure check against the template: `additive_parked`, `breaking_held`, both notify.
- Step 15: certification with a simple trust score; `released` or `held_consumers_warned`.
- On release, the product is created (step 20) and the requester gets "Ready" (or "Access restricted").
- **Done when:** the Playwright test runs the full PoC story, including one held delivery.

## Phase 8 — Azure move (on the office laptop)

- Implement `AzureOpenAIProvider`; run the evaluation script against the gold mappings and compare with the mock.
- Implement Azure Blob, Functions timer, Entra ID, Key Vault and email or Teams adapters.
- Point `DATABASE_URL` at the office laptop's PostgreSQL first, run the migrations and the full test suite, then at Azure Database for PostgreSQL.
- Fill in the Bicep templates; deploy to the company subscription. Add containers here only if the Azure hosting choice needs them.
- Switch adapters by environment variables only. No business logic changes.
- **Done when:** the same Playwright test passes against the Azure deployment.

## Out of scope for the PoC

Composition engine (stub only), full mastering survivorship, value drift agent (step 16), exception investigation agent (step 14), streaming delivery, multiple tenants.
