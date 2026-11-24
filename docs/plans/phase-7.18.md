# Phase 7.18: a seam for in-house data agents

Goal: one named agent can be answered by an in-house service (Prerna's team) instead of the model provider, through a new port,
with the same logging, locking and transaction pattern. Nothing real is called: the only adapter is a stub, and the tests use a
fake. Branch `phase-7.18` from `main` (bb01305 or later). No migration (see assumption 9).
Out of scope: any network call, any real Hexaware client, changes to which agents exist.

## 1. What exists (read before designing)

- `Agent.run()` (`agents/base.py`) is the only path to the model: render prompt, `LLMProvider.complete()`, check output, store input
  blob, `ProposalLog.record()` in its own short transaction, `before_write`, proposals. Nothing is written if the call fails.
- Ports: a `base.py` Protocol, adapters, an Azure/stub adapter with `is_stub` and `ENV_KEYS`, a factory in the package's
  `__init__.py`, a row in `tests/test_azure_stubs.py`, keys in `.env.example`, a selector in `ports/config.py`.
- `LLMUnavailable` is the model for an expected failure: raised by the port, nothing written, one handler in `apps/api/main.py`
  answers 503 with a reason.
- Agents are built in many places (`apps/api/deps.py`, `apps/api/jobs.py`, `agents/evaluate_agents.py`) as
  `Agent(provider=..., blob_store=...)`.
- `tests/test_import_boundaries.py` keeps `agents` and `ports.llm` out of `core/steps` etc.

## 2. Design

### 2.1 T1: port `ExternalAgent` (`ports/external_agents`)

- `base.py`: Protocol `ExternalAgent`: `name`, and `call(*, task: str, input: dict[str, Any], response_schema: type[BaseModel])
  -> ExternalAgentResult`. `ExternalAgentResult`: `output` (a validated `response_schema` instance), `provider`, `model`,
  `model_version`, `latency_ms`, `raw_ref`: what the proposal log needs, same shape as `LLMResult` without prompt fields.
  `ExternalAgentUnavailable(reason, detail)`, `reason` in `timeout | provider_error | malformed_output | not_implemented`.
  `validated(response_schema, output)`: the one helper that turns a dict or model into the schema or raises
  `ExternalAgentUnavailable("malformed_output")`; adapters and `Agent.run()` both use it.
- `hexaware.py`: `HexawareExternalAgent`, `name = "hexaware"`, `is_stub = True`, `ENV_KEYS = (EXTERNAL_AGENT_BASE_URL,
  EXTERNAL_AGENT_API_KEY, EXTERNAL_AGENT_TIMEOUT_SECONDS)` read in the constructor with `read_env`;
  `call()` raises `NotImplementedError("External agent: implement after API is known")`. No import of an HTTP library.
- `__init__.py`: `get_external_agent(settings) -> ExternalAgent | None`: `None` for `EXTERNAL_AGENT_PROVIDER=none` (the default,
  never called), the Hexaware stub for `hexaware`.
- `ports/config.py`: `external_agent_provider: Literal["none","hexaware"] = "none"`, `external_agent_for: str = ""`.

### 2.2 T2: routing in `Agent.run()`

- `Agent.__init__` gains two optional keyword arguments, `external` and `external_for`, both `None` by default; when `None`
  they come from `get_settings()` when `run()` starts. So no construction site changes and every existing agent test is
  untouched.
- Routed only when `external_for == self.name` and an adapter exists. `EXTERNAL_AGENT_FOR` empty, or the provider `none`:
  exactly today's code path (the model provider).
- Routed call: skip prompt rendering; call `external.call(task=self.name, input=agent_input.model_dump(mode="json"),
  response_schema=self.output_schema)`; the result's output is checked with `validated()`; the stored input blob has
  `"messages": []` and `"external": true`. Then the same steps as today: `_log_call` (provider is the adapter's, `"hexaware"`;
  `prompt_id = "external:<agent name>"`, `prompt_version = "external"`), `before_write`, proposals, discard rows.
- An adapter that raises `ExternalAgentUnavailable`, `NotImplementedError` (the stub), or anything else becomes
  `ExternalAgentUnavailable` (`not_implemented` or `provider_error`) **before** the log or any write: no log row, no proposal.
  A malformed output is the same (`malformed_output`), like `LLMUnavailable`: an expected case, nothing written.
- `apps/api/main.py`: one handler answers 503 `{"outcome": "external_agent_unavailable", "reason": ..., "detail": ...}`.
  Background workers already catch `Exception` and record a failed run, so they get the failure text, not a crash.
- The only file that imports `ports.external_agents` outside the port and its tests is `agents/base.py` (plus the settings route
  in `apps/api`, T3, which reads configuration only and never calls the port). A test enforces both this and that `core/` does not
  import it.

### 2.3 T3: Settings page (read only)

- `GET /api/amaze/settings` (any signed-in dev user): `{external_agent_provider, external_agent_for, external_agent_active,
  agents: [{name, autonomy, approver_roles, provider, routed_external}]}`. `provider` is `mock`, `azure_openai` or `external`
  (the adapter's name when routed, e.g. `hexaware`, shown as external). Built from `agents.registry.AGENT_PROMPTS` names and the
  agent classes. No endpoint, key or secret value is in the response: only the selector names' values and a boolean.
- backend-ui: a `Settings` view and nav entry: a table of agents (name, autonomy, provider) and one sentence on whether
  `EXTERNAL_AGENT_FOR` is set and to what, with a warning fill if it names no known agent or the provider is `none`.

## 3. Assumptions (numbered)

1. The port returns an `ExternalAgentResult` (output plus log metadata), not a bare output: the log needs provider, model and
   latency. This is the one change to "call(...) -> output".
2. `EXTERNAL_AGENT_FOR` holds one agent name (the `Agent.name`, e.g. `MappingAgent`), not a list.
3. Routing needs both `EXTERNAL_AGENT_FOR` naming the agent and a provider other than `none`; with the provider `none` nothing is
   routed and the Settings page says so. (Otherwise a mistyped setting would turn an agent off silently.)
4. The external agent gets the agent's structured input (`input_schema` as JSON), not the rendered prompt; prompts stay ours.
5. `ExternalAgentUnavailable` is a typed exception like `LLMUnavailable`, not a `StepResult` variant: the failure can happen in any
   of the agent-backed steps and nothing is written. Its single mapping is 503 with `outcome: external_agent_unavailable`.
6. The call is logged with `provider = "hexaware"`, taken from the adapter's result, so a second adapter later logs its own name.
7. Prompt hashes and versions are untouched; an external call has no prompt, so its log row uses `external:<agent>`.
8. The stub is reported unavailable by `/api/health` only when selected (`is_stub`), like the other stubs; `none` is not counted.
9. No migration: `proposal_log` already holds provider, model, prompt id and version as text. T2 checks that nothing there
   forbids the values above; if a guard did, I stop and ask before adding `0038`.
10. The agent list on the Settings page is the registry's agent names whose class is importable from `agents/`; the evaluator
    (`MappingEvaluator`) is read only and listed too.
11. No Playwright story changes except one step in the demo story that opens Settings; `docs/demo.md` gets the same step.
12. Dev ports 8000, 5173, 5174 were free at the start (checked).

## 4. Tasks and checks

| Task | Test first (all with a fake adapter, no network) | Commit |
|---|---|---|
| T1 | factory returns `None` for `none`, stub for `hexaware`; stub raises the exact `NotImplementedError` text and reads its keys; `validated()` refuses a bad output; stub row in `test_azure_stubs.py`; `.env.example` has the keys | one |
| T2 | routing on / off; log row with provider `hexaware`, exactly one call row; schema-failing output refused, no row, no proposal; unavailable / `NotImplementedError` / other error give `ExternalAgentUnavailable`, no row; empty `EXTERNAL_AGENT_FOR` leaves the existing agent tests unchanged; boundary tests: no `ports.external_agents` in `core/`, and only `agents/base.py` calls it; API maps it to 503 | one |
| T3 | endpoint shape, no secret value in the body; the view renders agents, providers and the flag (vitest) | one |
| Finish | `pytest -q -n 6`, both UI suites, three Playwright stories, a Settings step in the demo story and `docs/demo.md`, `CLAUDE.md` layout line | one |
