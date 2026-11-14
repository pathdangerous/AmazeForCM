# Phase 3 plan — Custom data product ingestion (Known source?, S1–S4, DiscoveryAssistant)

**Status:** awaiting approval.
**Date:** 2026-11-14. **Branch:** `phase-3`, created from `main` at `cab1a01`. **Scope:** phase 3 of `BUILD_PLAN.md` and its carried items. Local only; nothing is pushed.

**Phase 3 is too big for one build.** This plan covers all of it, so the design is decided once, and splits the work into **3a** (no agent: known source, fetch, scheduler) and **3b** (agents: scope, source list, pick, DiscoveryAssistant). I stop for your approval between them.

## 1. Goal and "done when"

**Part 3a is done when all five are true:**

1. A requirement with status `defined` is checked against the known sources: `known_source` or `no_known_source`.
2. For a known source, its file is fetched and stored, a candidate feed is linked to the requirement, and the requester sees exactly "Your data is getting onboarded; we will notify you once it is ready." Proved by `test_api_consumer_sourcing.py::test_done_when_a_known_source_is_fetched_and_the_requester_sees_onboarding` and in the browser.
3. A source that does not answer gives `source_down`; the requirement waits and the fetch is tried again every day.
4. The daily fetch still runs for that feed after the API is restarted.
5. `pytest` passes with nothing skipped and no warnings.

**Part 3b is done when all six are true:**

1. Every model call goes through `Agent.run()`: it is logged in `proposal_log` first, and every proposal points at its log row. A subclass that defines `run()` is refused when the class is created.
2. With no known source, the requester gets a scope to confirm (S1), then a list of sources with coverage, licence and approvals needed (S2).
3. **Picking a source produces a fetched file handed to step 02** (S3, S4). This is `BUILD_PLAN.md`'s done-when, proved by `test_api_consumer_sourcing.py::test_done_when_picking_a_source_hands_a_fetched_file_to_step_02` and in the browser. A source that needs an approval gives `approval_missing` until the named role approves.
4. The DiscoveryAssistant suggests glossary elements beside the deterministic search, and changes nothing.
5. Each of the ten outcome names has a named test (section 5.4).
6. `pytest` passes with nothing skipped and no warnings.

## 2. State at the start (checked on 2026-11-14)

| Item | Found |
|---|---|
| Git | `main` = `phase-2` = `cab1a01`. `phase-3` created from it. Working tree clean |
| Tests | 632 collected |
| Database | `alembic current`: `0007 (head)`. The application database holds 4 requirements, all `defined`, 9 requests, no feeds and no proposals |
| `agents/` | Only `__init__.py` and an empty `prompts/`. No `base.py`, no `registry.py` |
| `MockProvider` | Fixtures only, keyed by prompt id and version. It ignores the messages, so every requirement would get the same answer |
| `HttpConnector` | No `close()`. Its `httpx.Client` is never closed |
| `LocalScheduler` | Jobs in memory only; a restart loses them |
| `fixtures/sources/` | Empty |
| PyYAML | 6.0.3 is installed (it comes with `uvicorn[standard]`) but is not pinned in `requirements.txt` |
| Import-boundary test | `core.steps` is inside the boundary: no step may import `agents` or `ports.llm` |
| Plugins | Superpowers, Karpathy, i-have-adhd and Octopus are still not loaded. I follow the same flow by hand |

## 3. What you fixed, and where this plan answers it

**Scope you set:** `BUILD_PLAN.md` phase 3 (S1–S4) and seven carried items.

| # | Carried item | Answered in |
|---|---|---|
| 1 | `Agent.run()` is the only entry point | assumptions 19 to 23 |
| 2 | `HttpConnector` closes its connections | assumption 15 |
| 3 | Scheduler jobs survive a restart | assumptions 17, 18 |
| 4 | "Known source?" from a `defined` requirement | assumptions 6 to 9 |
| 5 | DiscoveryAssistant beside the deterministic search | assumptions 38 to 40 |
| 6 | Agent-backed steps and the import boundary | assumption 22 |
| 7 | New statuses by migration | assumptions 41, 42; section 5.1 |

**Questions you asked me to decide:**

| Question | Answer | Assumption |
|---|---|---|
| Outcome names and their tests | The ten names of `CLAUDE.md`, each with a named test | section 5.4 |
| Rule 1 for S1 and S3 | Proposals and approvals from phase 1, with database guards | 28, 33, 36; section 5.3 |
| Rule 4 for the read-only assistant | **Yes, logged in `proposal_log`** | 39 |
| What `approval_missing` means | Nothing is fetched; status `awaiting_approval`; the named role is asked | 34, 35 |
| S1 and S2: in the request or in the background | In their own requests, started by the requester; never in the background, never inside the raise | 2, 3 |
| Synthetic sources; no internet | A synthetic catalogue; the HTTP connector accepts this machine only | 15, 16, 46 |
| "Handed to step 02" | A candidate feed, a stored file, a link to the requirement | 10 |
| MockProvider and prompts | Deterministic rules per prompt; versioned YAML | 24, 25 |
| Is phase 3 too big | **Yes: 3a and 3b** | 1 |

## 4. Assumptions — veto any by number

**The split**

1. **Two parts, one plan, a stop between.** 3a has no model call. 3b adds the agents and ends with `BUILD_PLAN.md`'s done-when. Approving this plan approves 3a's tasks and 3b's design; I stop after checkpoint 3 and wait for your approval to start 3b.

**How a requirement moves**

2. **One endpoint runs one step; consumer-ui chains them.** Raising a requirement stays exactly as in phase 2: `request_data` does not change. Right after a raise, consumer-ui calls "Find a source" by itself. My requests also shows a "Find a source" button for any `defined` requirement, such as the four already in the database.
3. **S1 and S2 run inside requests the requester starts, not in the background.**
   - Each model call is one request and one transaction: call the model, log it, write the proposals, commit.
   - If the call fails, nothing is written, the status does not change, and the requester can try again.
   - No model call ever runs inside the request that raises the requirement.
   - Why not the background: it needs a job runner and polling, and it is not needed for a mock that answers at once. Risk 1 covers the latency with Azure OpenAI.
4. **Status changes go through one method.** `RequirementRepository.advance(id, from_statuses, to)` locks the row and raises `RequirementNotInStatus` if the status is not one of the expected ones. The API returns 409. A double click cannot run a step twice. Section 5.1 lists every move.
5. **The raise message does not change:** "Requirement raised; the backend team will look for a source." The result of "Find a source" appears under it.

**Known source? (3a)**

6. **A source catalogue.** `fixtures/sources/catalogue.json` lists the synthetic sources; `ports/connectors/catalogue.py` reads it into `SourceEntry` models. Each entry has: code, name, provider, description, `visibility` (`public` or `private`), `channel` (where it is found), `known`, licence (name, permitted use, redistribution), `approvals_needed` (role and reason), the glossary `elements` it declares, `other_fields` (names that are not in the glossary; a ticked term is covered when it equals one), `connector` and `source_ref`. The path is a setting, `SOURCE_CATALOGUE`.
7. **`known: true` means Amaze already has an agreement and a connection.** People made that decision when the source was marked known, so no approval is asked again. A known source must have no approvals needed; the loader refuses one that has.
8. **The rule.** `known_source` when the requirement has at least one element, no ticked terms, and one known source declares every element. With several, pick the one with the fewest declared elements beyond the request, then by code. Any ticked term gives `no_known_source`: a term is free text, so matching it to a source is a judgement. That belongs in S2, where the requester sees the source and picks it.
9. **What each outcome does.**
   - `known_source`: the source is selected (assumption 10) and the first fetch runs (assumption 13).
   - `no_known_source` in 3a: nothing is written, the status stays `defined`, and the requester sees "No known source has this data yet."
   - `no_known_source` in 3b: S1 runs in the same request (assumption 27).

**Fetch and the hand-off to step 02 (S4, 3a)**

10. **"Handed to step 02" is three things:**
    - a **candidate feed** carrying the source: `source_code`, `connector`, `source_ref`;
    - a **stored file**: the latest `fetched` row in `source_fetches`, with the `BlobStore` key `raw/<feed code>/<date>/<file name>`;
    - a **link to the requirement**: one `source_choices` row (requirement, feed, how it was chosen).

    The requirement becomes `onboarding`, and the backend team gets an in-app notification "File ready to profile: <feed>". Phase 4 starts from candidate feeds that have a fetched file.
11. **One feed per source.** The feed's code is the source's code. A second requirement that selects the same source is linked to the existing feed; no second feed is made.
12. **The fetch** (`core/steps/s4_fetch.py`) reads the feed's connector and reference, fetches, and records one `source_fetches` row.
    - `fetched`: the file is stored, and every requirement linked to the feed with status `source_selected` becomes `onboarding`.
    - `source_down`: the connector could not read the source, or the file is empty. The reason is recorded, the backend team is notified, and the requirement stays `source_selected`.
13. **The first fetch runs right after the selection, in the same request, in its own transaction.** The selection is committed first. If the fetch gives `source_down`, the selection stands and the daily job tries again.
14. **The requester's messages:**
    - `onboarding`: "Your data is getting onboarded; we will notify you once it is ready." (exact)
    - `source_selected` after `source_down`: "A source was found: <name>. It did not answer; Amaze tries again every day."
15. **Connectors.**
    - `HttpConnector` gets `close()` and works in a `with` block. It closes its client, including one it was given. The fetch step opens a connector for each fetch and always closes it. Tests assert the client is closed after a successful fetch and after a failed one.
    - `LocalFileConnector` gets a `close()` that does nothing, so every connector is used the same way.
    - **This machine only.** The PoC's connector factory builds `HttpConnector` with allowed hosts `127.0.0.1` and `localhost` (setting `SOURCE_HTTP_ALLOWED_HOSTS`). Any other host is refused as `source_down` before a connection is opened. The catalogue loader refuses such a reference too.
16. **The committed catalogue has file sources only.** The HTTP connector is tested against a server the test starts on `127.0.0.1`. No extra window or process is needed for the demo.

**The daily job (3a)**

17. **One daily job, registered at every start-up, that reads the feeds table when it runs.** It fetches every feed that has a connector and is not rejected.
    - **Why not a pickled APScheduler job store:** it would be a second source of truth beside `feeds`, in a table outside Alembic, tied to Python function paths, and with no Azure counterpart.
    - **Why not one job per feed, re-registered at start-up:** it works, but each selection must add a job and each rejection must remove one, and a missed call leaves a feed without a job. With one job there is nothing per feed to register, so nothing can drift.
    - **Azure:** a Functions timer trigger that reads `feeds` is the same shape (phase 8).
    - If you veto this, I build one job per feed, re-registered from `feeds` at start-up.
18. **Details.** The job runs at 06:00 UTC, a constant in code. Each feed is fetched in its own transaction, and one feed's failure does not stop the others. A stub scheduler is not started, as today. The job lives in `apps/api/jobs.py` and takes its session from a factory the tests replace.

**Agents (3b)**

19. **`agents/base.Agent`.** A subclass declares `name`, `prompt_id`, `autonomy` (A1 or A2), `approver_role` (or none for a read-only agent) and its input and output schemas. `run()` is defined only on the base class and does, in order:
    1. render the prompt and store the input (assumption 20);
    2. call `LLMProvider.complete()`;
    3. `ProposalLog.record()`;
    4. ask the subclass for proposal drafts (`to_proposals`) and create them, each pointing at that log row;
    5. return the log id, the validated output and the proposals.

    `__init_subclass__` raises `TypeError` for a subclass that defines `run`. A read-only agent that returns drafts is refused. Everything is flushed in the caller's transaction, never committed.
20. **The input reference is real.** `run()` stores the rendered messages and the input in the `BlobStore` under `agent-inputs/<agent>/<id>.json`, and `input_ref` is that key. Rule 4 asks for an input reference; a pointer to data that can change later would not be one.
21. **Schemas live in `core/domain/sourcing.py`**, not in `agents/`: the agents' input and output models and `AgentRun`. Steps and agents both import them from there.
22. **Carried item 6: steps receive the agent as an argument, and `core.steps` stays inside the boundary.**
    - The boundary test stays as strict as today: nothing under `core` can import `agents` or `ports.llm`, so no step can make a model call that `Agent.run()` does not log.
    - Dependencies point one way: `agents` imports `core`; `core` never imports `agents`. No import cycle.
    - Steps are tested with a stand-in agent, without a provider.
    - The API builds the agent and passes it in.
    - The other option, a list of "deterministic" step modules, would let the remaining steps import `agents`, and the list would have to be kept right by hand.
23. **No model call outside `Agent.run()`.** A static test fails if any file under `core/`, `apps/` or `agents/` other than `agents/base.py` calls `.complete(`.
24. **Prompts are versioned YAML.** One file per version, `agents/prompts/<prompt_id>.<version>.yaml`, with a system text and a user template. A new version is a new file; an existing file is not edited. `agents/registry.py` names each agent and the prompt version it uses. `pyyaml==6.0.3` is pinned in `requirements.txt`; nothing new is downloaded.
25. **MockProvider stays deterministic and gains rules.**
    - The user message ends with a JSON block holding the agent's input. This is the same message Azure OpenAI will get.
    - For a prompt id with a rule (`ports/llm/mock_rules.py`), the mock reads that block and computes the output. For any other prompt it loads the fixture file, as today.
    - A rule is a pure function: no clock, no randomness, no file or database access.
26. **A failed model call writes nothing.** The transaction is rolled back and the requirement keeps its status. A stub provider gives 503 through the phase 1 handler. This is an error, not a step outcome.

**S1 scope (3b)**

27. **IntentAgent** (A2, approver: requester). Input: the requirement's text, purpose, elements, terms, the requester's note if any, and the channels in the catalogue. Output: `visibility` (`public` or `private`), `provider` (who provides), `where_to_search` (channels) and a reason. Mock rule: `private` if the text, purpose or note contains one of a fixed list of words (`internal`, `private`, `in-house`, `proprietary`), otherwise `public`.
28. **Rule 1 for S1.** One `scope` proposal per run, linked to the requirement, with `required_approver_roles = ["requester"]`. The requester's answer is one row in `approvals`.
    - Approved: outcome `scoped`. A `requirement_scopes` row is written (versioned, append-only). The database accepts it only for a `scope` proposal approved by the requester role, for the same requirement.
    - Rejected: outcome `scope_not_confirmed`. Nothing else is written.
29. **Only the requirement's own requester decides; approve or reject only.** The requester does not edit the scope. After a rejection they add a note and ask again: S1 runs again with the note, and a new proposal is made.

**S2 source list (3b)**

30. **Code finds, the agent ranks, the catalogue states the facts.**
    - The step takes the catalogue entries that match the confirmed scope (visibility and channel) and cover at least one requested element or term.
    - SourceDiscoveryAgent (A2) ranks them and gives a reason for each.
    - Coverage, licence and approvals needed are copied from the catalogue. The model's text is never used for them, and a source code the model invents is dropped.
31. **One `source_list` proposal per listed source**, all pointing at the same log row. Each proposal's payload names one source, and its `required_approver_roles` are `requester` followed by the roles that source needs. Outcome `sources_listed`.
32. **`no_fit`:** no catalogue source in scope covers any requested item, or the agent lists none. No proposal is made. The requirement becomes `no_source_found`, the backend team is notified, and the requester sees "No source fits this request. The backend team has been told." The requester may add a note and ask for a new scope.

**S3 pick (3b)**

33. **Rule 1 for S3: the pick is the requester's approval of that source's proposal.** If the proposal needs no other role, it is now approved: outcome `source_picked`, the source is selected (assumption 10) and the first fetch runs.
34. **`approval_missing`:** the picked source needs a role that has not approved yet.
    - For the requester: "<source> needs approval before it can be used: <reason>, by the <role>. They have been asked."
    - The requirement becomes `awaiting_approval`.
    - Nothing is fetched, no feed is made and no job runs.
    - Every user with the next needed role gets an in-app notification.
35. **The named role decides in backend-ui** (`/api/amaze/approvals`).
    - Approved, and no role is left: the pick completes in that request. Outcome `source_picked`, then the first fetch.
    - Rejected: outcome `approval_missing` with state `refused` and the approver's note. The requirement goes back to `awaiting_source_choice`, or to `no_source_found` if no source is left to pick. The requester is told to pick another source.
36. **Database guards on `source_choices`.** A requester's pick is accepted only if its proposal is a `source_list` proposal that every required role has approved and nobody has rejected (the phase 1 guard from `0005`), is for the same requirement, and names the same source. A known-source row must have no proposal. Raw-SQL tests prove each refusal.
37. **The proposals of sources that were not picked stay `pending`.** Nobody decided them, so no decision is written for them.

**DiscoveryAssistant (step 17, 3b)**

38. **A second, optional step beside the search.** `GET /api/consumer/search` stays deterministic. A new "Ask the assistant" button sends the query, the unmatched terms and the glossary to the DiscoveryAssistant (A2, read only). It returns glossary elements it thinks the requester means, each with a reason.
    - Code drops any key that is not in the glossary.
    - The suggestions appear as unticked boxes, marked "suggested", with the products that hold them.
    - The assistant writes nothing and decides nothing. Entitlement stays in `core/entitlement`.
39. **Rule 4: yes, a read-only call is logged in `proposal_log`.**
    - `Agent.run()` is the only way to call the model, and it always logs. A second path that skips the log is exactly what carried item 1 forbids.
    - The suggestion is shown to a person and can change what they request, so it must be traceable.
    - `proposal_log` is already "one row per model call" (phase 1, decision 3). The row has no proposal and so no approval.
    - Cost: one row per click (risk 8).
40. **Mock rule:** for each unmatched term, drop a plural ending and look for a glossary phrase; if none, take the closest phrase above a fixed similarity. `closing prices` suggests Close price.

**Data, API, UI, documents**

41. **Two migrations.** `0008_sourcing.py` (3a) reaches the application database at checkpoint 1; `0009_scope_and_pick.py` (3b) at checkpoint 4. An applied migration is never edited. Explicit constraint names use `op.f()`.
42. **Requirement statuses** grow from 5 to 12 (section 5.1). 3a adds `source_selected`; 3b adds `awaiting_scope_confirmation`, `scope_not_confirmed`, `scope_confirmed`, `awaiting_source_choice`, `no_source_found` and `awaiting_approval`.
43. **API.** The consumer endpoints are for the requester and only for their own requirement: another's gives 404, a wrong status gives 409. The approvals endpoints are for backend roles and show a proposal only to a user who holds its next required role.
44. **consumer-ui** gets a third hash view, `#/requirement/<id>`: status, message, source, last fetch, and the next action. My requests links to it. No new npm package.
45. **backend-ui** gets one view in 3b, Approvals: the proposals waiting for one of my roles, with Approve and Reject. The feed registry needs no change; candidate feeds already appear in it.
46. **Synthetic data only.** The catalogue and every sample file pass the real-data checks from `test_glossary_seed.py`. Providers have invented names. Sample identifiers are lower-case with hyphens (`syn-0001`), so they have no real identifier's shape.
47. **Documents** (tasks 9 and 20): `CLAUDE.md` layout lines, `BUILD_PLAN.md` (phase 3 is built as 3a and 3b), `README.md`, `.env.example` (the two new settings).
48. **I check the UI** by building it and by a scripted browser run with screenshots. The committed Playwright test stays in phase 7.

## 5. Steps and typed results

### 5.1 How a requirement moves

| From | Action | Outcome | To | Part |
|---|---|---|---|---|
| `defined` | Find a source (rule) | `known_source` | `source_selected` | 3a |
| `defined` | Find a source (rule) | `no_known_source` | stays `defined` | 3a |
| `defined` | Find a source, then S1 proposes | `no_known_source` | `awaiting_scope_confirmation` | 3b |
| `awaiting_scope_confirmation` | Requester approves | `scoped` | `scope_confirmed` | 3b |
| `awaiting_scope_confirmation` | Requester rejects | `scope_not_confirmed` | `scope_not_confirmed` | 3b |
| `scope_not_confirmed`, `no_source_found` | Requester asks again with a note (S1) | | `awaiting_scope_confirmation` | 3b |
| `scope_confirmed` | List sources (S2) | `sources_listed` | `awaiting_source_choice` | 3b |
| `scope_confirmed` | List sources (S2) | `no_fit` | `no_source_found` | 3b |
| `awaiting_source_choice` | Requester picks; no approval needed | `source_picked` | `source_selected` | 3b |
| `awaiting_source_choice` | Requester picks; approval needed | `approval_missing` | `awaiting_approval` | 3b |
| `awaiting_approval` | Last needed role approves | `source_picked` | `source_selected` | 3b |
| `awaiting_approval` | A needed role rejects | `approval_missing` (refused) | `awaiting_source_choice` or `no_source_found` | 3b |
| `source_selected` | Fetch (first, or daily) | `fetched` | `onboarding` | 3a |
| `source_selected` | Fetch (first, or daily) | `source_down` | stays `source_selected` | 3a |

`ready`, `access_restricted` and `rejected` exist since phase 1 and are not used before phases 4 and 7.

### 5.2 The shapes

```python
# core/steps/known_source.py
class KnownSource(StepResult):         # outcome "known_source"
    source: SourceSummary              # code, name, provider, licence
class NoKnownSource(StepResult):       # outcome "no_known_source"
    reason: Literal["terms_ticked", "no_known_source_covers"]

# core/steps/s4_fetch.py
class Fetched(StepResult):             # outcome "fetched"
    feed_code: str
    blob_ref: str
    size_bytes: int
    requirements_onboarding: list[UUID]
class SourceDown(StepResult):          # outcome "source_down"
    feed_code: str
    detail: str

# core/steps/s1_scope.py
class Scoped(StepResult):              # outcome "scoped"
    scope: RequirementScope
class ScopeNotConfirmed(StepResult):   # outcome "scope_not_confirmed"
    note: str | None

# core/steps/s2_sources.py
class SourcesListed(StepResult):       # outcome "sources_listed"
    candidates: list[SourceCandidate]  # proposal id, source, coverage, licence, approvals needed, reason
class NoFit(StepResult):               # outcome "no_fit"
    reason: Literal["nothing_in_scope", "agent_listed_none"]

# core/steps/s3_pick.py
class SourcePicked(StepResult):        # outcome "source_picked"
    source: SourceSummary
    feed_code: str
class ApprovalMissing(StepResult):     # outcome "approval_missing"
    state: Literal["waiting", "refused"]
    missing: list[ApprovalNeeded]      # role and reason
    note: str | None                   # the approver's note when refused
```

Each step's results form a union discriminated on `outcome`, as in phase 2.

The functions. Steps flush and never commit; the API owns each transaction.

| Module | Function | Writes | Part |
|---|---|---|---|
| `known_source.py` | `check_known_source(requirement, catalogue)` | nothing (pure) | 3a |
| `known_source.py` | `select_source(...)` | the feed (new or existing), the `source_choices` row, status `source_selected` | 3a |
| `s4_fetch.py` | `fetch_source(feed_id, ...)` | the blob, the `source_fetches` row, status `onboarding`, notifications | 3a |
| `s1_scope.py` | `propose_scope(requirement, agent, note)` | through `Agent.run()`: the log row and the proposal; status | 3b |
| `s1_scope.py` | `decide_scope(requirement, user, decision, note)` | the approval; for `scoped`, the `requirement_scopes` row; status | 3b |
| `s2_sources.py` | `list_sources(requirement, agent, catalogue)` | through `Agent.run()`: the log row and one proposal per source; status; for `no_fit`, a notification | 3b |
| `s3_pick.py` | `pick_source(requirement, user, proposal_id)` | the approval; then `select_source`, or status `awaiting_approval` and notifications | 3b |
| `s3_pick.py` | `decide_source_approval(proposal_id, user, decision, note)` | the approval; then `select_source`, or the way back | 3b |
| `s17_discover.py` | `assist(query, agent, ...)` | through `Agent.run()`: the log row only | 3b |

### 5.3 Rule 1: who decides what

| Step | Who decides | Proposal | Approval rows | What the decision allows, and the database guard |
|---|---|---|---|---|
| Known source? | Nobody at request time. People decided when the source was marked known | none | none | A `source_choices` row with basis `known_source`, which must have no proposal |
| S1 | The requester | one `scope` proposal per run; roles `[requester]` | the requester's approval or rejection | A `requirement_scopes` row: needs a `scope` proposal approved by the requester, for the same requirement |
| S2, S3 | The requester picks; each needed role approves | one `source_list` proposal per source; roles `[requester, ...needed]` | the pick is step 1; each needed role is a later step | A `source_choices` row with basis `requester_pick`: needs a `source_list` proposal approved by every role and rejected by none, for the same requirement and source |
| S4 | Nobody: rules execute | none | none | none |
| Step 17 assistant | The requester ticks or ignores each suggestion | none (read only) | none | Nothing is written except the log row |

Phase 1's rules still apply: roles decide in order, one person cannot decide two steps, and the first rejection ends the proposal.

### 5.4 Every outcome name and its test

The names are `CLAUDE.md`'s, letter for letter. `test_step_results.py` reads the lines "Known source?" and "S1–S4" from `CLAUDE.md` and compares them with the unions, as it does for phase 2.

| Outcome | Step | Named test | Part |
|---|---|---|---|
| `known_source` | Known source? | `test_known_source.py::test_known_source_when_one_known_source_declares_every_element` | 3a |
| `no_known_source` | Known source? | `test_known_source.py::test_no_known_source_when_none_covers_or_a_term_was_ticked` | 3a |
| `fetched` | S4 | `test_s4_fetch.py::test_fetched_stores_the_file_and_moves_the_requirement_to_onboarding` | 3a |
| `source_down` | S4 | `test_s4_fetch.py::test_source_down_is_recorded_and_the_requirement_waits` | 3a |
| `scoped` | S1 | `test_s1_scope.py::test_scoped_when_the_requester_confirms` | 3b |
| `scope_not_confirmed` | S1 | `test_s1_scope.py::test_scope_not_confirmed_when_the_requester_rejects` | 3b |
| `sources_listed` | S2 | `test_s2_sources.py::test_sources_listed_one_proposal_per_source` | 3b |
| `no_fit` | S2 | `test_s2_sources.py::test_no_fit_when_no_source_in_scope_covers_the_request` | 3b |
| `source_picked` | S3 | `test_s3_pick.py::test_source_picked_when_no_approval_is_needed` | 3b |
| `approval_missing` | S3 | `test_s3_pick.py::test_approval_missing_until_every_needed_role_approves` | 3b |

### 5.5 Carried items and their tests

| Carried item | Named test |
|---|---|
| 1 `Agent.run()` only | `test_agent_base.py`: `test_a_subclass_that_defines_run_is_refused_at_class_creation`, `test_run_logs_the_call_before_it_creates_proposals`, `test_every_proposal_points_at_the_log_row`, `test_a_failed_model_call_writes_nothing`, `test_a_read_only_agent_cannot_create_proposals`; `test_no_direct_llm_calls.py::test_complete_is_called_only_in_agents_base` |
| 2 connector closes | `test_port_connectors.py`: `test_http_client_is_closed_after_a_fetch`, `test_http_client_is_closed_after_a_failed_fetch`, `test_http_refuses_a_host_that_is_not_this_machine`; `test_s4_fetch.py::test_the_step_closes_the_connector_even_when_the_source_is_down` |
| 3 job survives a restart | `test_jobs.py`: `test_the_daily_job_is_registered_at_every_start`, `test_after_a_restart_the_job_fetches_feeds_read_from_the_database`, `test_one_failing_feed_does_not_stop_the_others` |
| 4 Known source? | section 5.4, and `test_api_consumer_sourcing.py::test_find_source_needs_status_defined` (409) |
| 5 DiscoveryAssistant | `test_agent_discovery_assistant.py::test_suggestions_are_glossary_elements_only`; `test_api_consumer.py`: `test_the_assistant_call_is_logged_without_a_proposal`, `test_search_itself_makes_no_model_call` |
| 6 import boundary | `test_import_boundaries.py` unchanged and passing with the agents in place; `test_s1_scope.py` and `test_s2_sources.py` run with a stand-in agent |
| 7 statuses | `test_migrations.py`: `test_upgrade_downgrade_upgrade` with `HEAD` at `0008`, then `0009`; `test_repo_requirements.py`: `test_advance_refuses_an_unexpected_status`, `test_database_refuses_unknown_status`; `test_domain_models.py::test_requirement_statuses_are_the_twelve_in_the_plan` |

## 6. Data model

### Migration `0008_sourcing.py` (3a)

| Table | Change | Rules |
|---|---|---|
| `requirements` | status list + `source_selected` | the check constraint is dropped and created again |
| `feeds` | + `source_code`, `connector` (both optional) | connector is `local_file` or `http`; source code and connector are set together; a feed with a connector has a `source_ref` |
| `source_choices` (new) | requirement_id, feed_id, source_code, basis | **Append-only.** One row per requirement. basis is `known_source` |
| `source_fetches` (new) | feed_id, outcome, blob_ref, size_bytes, detail | **Append-only.** outcome is `fetched` or `source_down`; a fetched row has a blob and a size above zero; a source-down row has a reason |

### Migration `0009_scope_and_pick.py` (3b)

| Table | Change | Rules |
|---|---|---|
| `requirements` | status list + the six 3b statuses | as above |
| `requirement_scopes` (new) | requirement_id, version, visibility, provider, where_to_search (text[]), proposal_id, confirmed_by | **Append-only.** Unique (requirement, version). Trigger: an approved `scope` proposal (the `0005` function). Trigger: the proposal is for the same requirement |
| `source_choices` | + proposal_id, chosen_by; basis may also be `requester_pick` | a known-source row has neither; a pick has both. Trigger for a pick: an approved `source_list` proposal (the `0005` function). Trigger: same requirement and same source code as the proposal |

One new trigger function, `amaze_require_proposal_of_requirement()`, serves both tables.

**PostgreSQL 14 or older only:** check constraints, arrays, `jsonb ->>` (9.4), triggers with a `WHEN` clause (9.0), plpgsql. Nothing from 15 or later.

## 7. Domain models and repositories

| Item | Change | Part |
|---|---|---|
| `core/domain/requirement.py` | the status list; `Requirement` unchanged otherwise | 3a, 3b |
| `core/domain/feed.py` | `source_code`, `connector` | 3a |
| `core/domain/sourcing.py` (new) | `SourceSummary`, `SourceChoice`, `SourceFetch`; in 3b `RequirementScope`, `SourceCandidate`, `ApprovalNeeded`, the agents' input and output models, `AgentRun` | 3a, 3b |
| `ports/connectors/catalogue.py` (new) | `SourceEntry`, `SourceCatalogue` | 3a |
| `RequirementRepository` | `advance(id, from_statuses, to)` | 3a |
| `FeedRepository` | `create_candidate` takes the source fields; `list_with_source()` | 3a |
| `SourceChoiceRepository` (new) | `record_known(...)`, `for_requirement(id)`, `requirements_for_feed(feed_id)`; in 3b `record_pick(...)` | 3a, 3b |
| `SourceFetchRepository` (new) | `record(...)`, `latest(feed_id)`, `latest_fetched(feed_id)` | 3a |
| `RequirementScopeRepository` (new) | `add_version(...)`, `current(requirement_id)` | 3b |
| `ProposalRepository` | `list_for_requirement(id, kind)`, `waiting_for_roles(roles)` | 3b |

New named errors: `RequirementNotInStatus`, `FeedHasNoSource`, `UnknownSource` (3a); `ProposalForAnotherRequirement`, `SourceNotInProposal` (3b).

## 8. Synthetic sources and the demo paths

`fixtures/sources/catalogue.json` and `fixtures/sources/files/*.csv` (about ten rows each).

| Code | Visibility, channel | Known | Declares | Approvals needed |
|---|---|---|---|---|
| `syn_exchange_volumes` | public, exchanges | yes | instrument_id, price_date, volume | none |
| `syn_offline_accruals` | public, exchanges | yes | instrument_id, price_date, accrued_interest. **Its file is missing on purpose** | none |
| `syn_vendor_eod_prices` | public, market data vendors | no | instrument_id, price_date, open_price, high_price, low_price, close_price, volume | `data_owner`: the licence must be accepted |
| `syn_open_esg_scores` | public, public websites | no | instrument_id; other field `esg score` | none |
| `syn_internal_desk_marks` | private, internal systems | no | instrument_id, price_date, mid_price | none |

All five are file sources (assumption 16).

Signed in as Riley Requester:

| # | Request | What happens | Part |
|---|---|---|---|
| A | `volume` | raise, then Find a source: `known_source` (`syn_exchange_volumes`), `fetched`, "Your data is getting onboarded; we will notify you once it is ready." | 3a |
| B | `accrued interest` | `known_source` (`syn_offline_accruals`), `source_down`; the requirement waits as `source_selected` | 3a |
| C | `open price` | `no_known_source`: the only source that declares it is not known | 3a |
| D | `esg score`, ticked | `no_known_source`: a term was ticked | 3a |
| E | `open price` | scope `public`; confirm; `syn_vendor_eod_prices` listed; pick: `approval_missing`; Devon Owner approves in backend-ui: `source_picked`, `fetched`, onboarding | 3b |
| F | `esg score`, ticked | scope `public`; confirm; `syn_open_esg_scores` listed; pick: `source_picked`, `fetched`, onboarding | 3b |
| G | `yield to maturity`, purpose "internal desk data" | scope `private`; confirm; `no_fit` | 3b |
| H | any of E to G | reject the scope: `scope_not_confirmed`; add a note and ask again | 3b |
| I | search `closing prices` | no match by the search; "Ask the assistant" suggests Close price, unticked | 3b |

## 9. API

**Consumer, 3a** (`apps/api/routers/consumer.py`):

| Endpoint | Does |
|---|---|
| `GET /api/consumer/requirements/{id}` | status, the requester's message, elements, terms, the selected source, the last fetch, the next action |
| `POST /api/consumer/requirements/{id}/find-source` | "Known source?"; for a known source, the selection and the first fetch. Returns both typed results |

**Consumer, 3b:**

| Endpoint | Does |
|---|---|
| `POST .../find-source` | as above; with no known source, S1 proposes a scope |
| `POST .../scope` `{note}` | S1 again, after `scope_not_confirmed` or `no_source_found` |
| `POST .../scope/decision` `{decision, note}` | `scoped` or `scope_not_confirmed` |
| `POST .../sources` | S2: `sources_listed` or `no_fit` |
| `POST .../pick` `{proposal_id}` | S3: `source_picked` (then the first fetch) or `approval_missing` |
| `POST /api/consumer/search/assist` `{q}` | the DiscoveryAssistant's suggestions; logs the call |

**Amaze, 3b** (`apps/api/routers/amaze.py`):

| Endpoint | Does |
|---|---|
| `GET /api/amaze/approvals` | source proposals waiting for one of the caller's roles |
| `POST /api/amaze/approvals/{proposal_id}/decision` `{decision, note}` | the role's decision; on the last approval the pick completes and the first fetch runs |

- 401 if not signed in; 403 for the wrong side (requester or backend); 404 for another requester's requirement; 409 for a wrong status; 422 for refused input.
- The catalogue, the blob store, the connector factory, the agents and the notifier are FastAPI dependencies, so tests replace them.
- A failed model call or a stub adapter is never turned into a step outcome.

## 10. UI

**consumer-ui**

1. **Search** (3a): after a raise, the page calls Find a source and shows its result under the raise message.
2. **My requests** (3a): each raised requirement links to its page and shows its message. A `defined` requirement has a "Find a source" button.
3. **Requirement** (`#/requirement/<id>`):
   - 3a: status, message, elements and terms, the source and its licence, the last fetch.
   - 3b: the proposed scope with Confirm and Reject (and a note); the listed sources with coverage, licence and approvals needed, each with Pick; the wait for an approval; after a refusal, the remaining sources.
4. **Search** (3b): "Ask the assistant" appears when a search leaves unmatched terms. Its suggestions are unticked and marked "suggested".

**backend-ui** (3b): Approvals view. A table of what waits for my roles: source, requirement, requester, reason. Approve and Reject, with a note.

Checked by `npm run build` in both projects and a scripted browser run. Screenshots in `.playwright-mcp/`: `phase3a-A.png` to `phase3a-D.png` and `phase3a-requirement.png`; `phase3b-E.png` to `phase3b-I.png` and `phase3b-approvals.png`. The run also checks the browser console.

## 11. Files

```
docs/plans/phase-3.md                         this plan
BUILD_PLAN.md  CLAUDE.md  README.md  .env.example            edit   tasks 9 and 20
requirements.txt                              edit   pin pyyaml (3b)
migrations/versions/0008_sourcing.py          new    3a
migrations/versions/0009_scope_and_pick.py    new    3b
fixtures/sources/catalogue.json  files/*.csv  new    3a
agents/
  base.py  registry.py  prompts.py                               new    3b
  intent.py  source_discovery.py  discovery_assistant.py         new    3b
  prompts/intent.v1.yaml  source_discovery.v1.yaml  discovery_assistant.v1.yaml   new    3b
ports/
  config.py                                   edit   SOURCE_CATALOGUE, SOURCE_HTTP_ALLOWED_HOSTS
  connectors/ base.py  http.py  local_file.py  __init__.py       edit   close(), allowed hosts, factory
  connectors/catalogue.py                     new    3a
  llm/mock.py                                 edit   3b
  llm/mock_rules.py                           new    3b
core/
  domain/      requirement.py  feed.py                           edit
               sourcing.py                                       new
  repositories/ tables.py  errors.py  requirements.py  feeds.py  proposals.py   edit
                source_choices.py  source_fetches.py             new    3a
                requirement_scopes.py                            new    3b
  steps/       known_source.py  s4_fetch.py                      new    3a
               s1_scope.py  s2_sources.py  s3_pick.py            new    3b
               s17_discover.py                                   edit   3b (assist)
apps/api/
  main.py  deps.py  routers/consumer.py       edit
  routers/amaze.py                            edit   3b
  jobs.py                                     new    3a
apps/consumer-ui/src/
  App.tsx  api.ts  views/SearchView.tsx  views/RequestsView.tsx  edit
  views/RequirementView.tsx                   new
apps/backend-ui/src/
  App.tsx  api.ts                             edit   3b
  views/ApprovalsView.tsx                     new    3b
tests/
  conftest.py  test_migrations.py  test_append_only.py  test_domain_models.py
  test_repo_requirements.py  test_repo_feeds.py  test_port_connectors.py
  test_port_llm.py  test_step_results.py  test_api_consumer.py             edit
  test_repo_source_choices.py  test_repo_source_fetches.py  test_source_catalogue.py
  test_known_source.py  test_s4_fetch.py  test_jobs.py
  test_api_consumer_sourcing.py                                            new    3a
  test_agent_base.py  test_prompts.py  test_no_direct_llm_calls.py  test_mock_rules.py
  test_agent_intent.py  test_agent_source_discovery.py  test_agent_discovery_assistant.py
  test_repo_requirement_scopes.py  test_db_source_guards.py
  test_s1_scope.py  test_s2_sources.py  test_s3_pick.py  test_api_amaze_approvals.py   new    3b
```

## 12. Tasks, in order

Each task: tests first and shown failing, then code, then its check, then one commit. Files are edited only with the file tools. I stop at the six checkpoints and continue without stopping between them.

### Part 3a

| # | Task | Tests written first | Check |
|---|---|---|---|
| 1 | Migration `0008`: status, feed source fields, `source_choices`, `source_fetches`; repositories; `advance()` | `test_migrations.py`, `test_append_only.py`, `test_domain_models.py`, `test_repo_requirements.py`, `test_repo_feeds.py`, `test_repo_source_choices.py`, `test_repo_source_fetches.py` | pytest |
| 2 | Source catalogue, the synthetic sources and files, the two settings | `test_source_catalogue.py` | pytest |
| 3 | "Known source?" check and the selection | `test_known_source.py`, `test_step_results.py` | pytest; `alembic upgrade head`; `0008 (head)`. **CHECKPOINT 1:** pytest summary, `alembic current`, the triggers on the two new tables |
| 4 | Connectors: `close()`, this machine only, the factory; a local test server fixture | `test_port_connectors.py` | pytest |
| 5 | S4 fetch and the hand-off | `test_s4_fetch.py` | pytest |
| 6 | The daily job and the restart | `test_jobs.py` | pytest. **CHECKPOINT 2:** pytest summary, the four 3a outcome tests by name, the restart test's output |
| 7 | Consumer API: the requirement page and Find a source | `test_api_consumer_sourcing.py` | pytest |
| 8 | consumer-ui: Requirement view, My requests, the call after a raise | none | `npm run build`; browser run, rows A to D |
| 9 | Documents; review of 3a against `CLAUDE.md`'s rules and this plan | none | findings in section 17. **CHECKPOINT 3:** section 13 results for 3a and the screenshots. **I stop here; 3b needs your approval** |

### Part 3b

| # | Task | Tests written first | Check |
|---|---|---|---|
| 10 | `Agent` base, `AgentRun`, registry, prompt files and loader; the two static tests | `test_agent_base.py`, `test_prompts.py`, `test_no_direct_llm_calls.py` | pytest |
| 11 | MockProvider rules | `test_port_llm.py`, `test_mock_rules.py` | pytest |
| 12 | Migration `0009`: statuses, `requirement_scopes`, the pick columns and guards; repositories | `test_migrations.py`, `test_append_only.py`, `test_repo_requirement_scopes.py`, `test_repo_source_choices.py`, `test_db_source_guards.py` | pytest |
| 13 | IntentAgent and S1 | `test_agent_intent.py`, `test_s1_scope.py`, `test_step_results.py` | pytest; `alembic upgrade head`; `0009 (head)`. **CHECKPOINT 4:** pytest summary, `alembic current`, the carried item 1 tests by name |
| 14 | SourceDiscoveryAgent and S2 | `test_agent_source_discovery.py`, `test_s2_sources.py` | pytest |
| 15 | S3 pick and the approvals | `test_s3_pick.py` | pytest |
| 16 | APIs: the consumer flow and the Amaze approvals | `test_api_consumer_sourcing.py`, `test_api_amaze_approvals.py` | pytest. **CHECKPOINT 5:** pytest summary, the ten outcome tests by name, the `BUILD_PLAN.md` done-when test's output |
| 17 | DiscoveryAssistant: agent, step, endpoint | `test_agent_discovery_assistant.py`, `test_api_consumer.py` | pytest |
| 18 | consumer-ui: scope, sources, pick, the assistant | none | build; browser run, rows E to I |
| 19 | backend-ui: Approvals view | none | build; browser run |
| 20 | Documents | none | documents read back |
| 21 | Review against `CLAUDE.md`'s seven rules, the PostgreSQL 14 list and this plan | none | findings in section 17. **CHECKPOINT 6:** section 13 results and the screenshots. Phase 4 is not started |

## 13. Commands that prove each part is done

Run from `F:\amaze-cm-poc` in Windows PowerShell.

```powershell
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m core.seed
.\.venv\Scripts\python -m core.seed --demo
.\.venv\Scripts\python -m pytest -v
.\.venv\Scripts\python -m alembic current
npm --prefix apps\backend-ui run build
npm --prefix apps\consumer-ui run build
.\scripts\dev.ps1
```

| Command | Expected, 3a | Expected, 3b |
|---|---|---|
| `pytest -v` | all pass; 0 failed, 0 skipped, 0 warnings | the same |
| `alembic current` | `0008 (head)` | `0009 (head)` |
| both builds | exit code 0 | exit code 0 |
| `dev.ps1` | three `OK` lines and health `ok` | the same |

Then in the browser, signed in as Riley Requester:

- **3a:** rows A to D of section 8. For row A: the requirement page shows "Your data is getting onboarded; we will notify you once it is ready.", and `http://localhost:5173/#/feeds` shows the candidate feed `syn_exchange_volumes`. Then close the API window, start it again, and check that the health line is `ok`; the restart test in `test_jobs.py` proves the fetch itself.
- **3b:** rows E to I. For row E, sign in to backend-ui as Devon Owner and approve in the Approvals view.

## 14. Not in phase 3

- Step 02 profiling, DuckDB, sign-off, registration, licence fields on a feed (phase 4).
- Rejecting a candidate feed, and telling the requester (phase 4).
- The glossary builder and mapping (phase 5); rules and the run engine (phase 6).
- Loads from the daily fetch, arrival and structure checks, "Ready" (phase 7).
- Any real source, any internet access, Azure adapters (phase 8).
- Background model calls and a job queue.
- A notifications view in either UI.
- Editing a proposed scope; an access-request workflow; grant screens.
- A general approvals screen for other proposal kinds.
- The committed Playwright test (phase 7).

## 15. Risks

1. **Model calls run inside requests.** The mock answers at once; Azure OpenAI may take many seconds, and the request waits. Production needs a queue. Phase 8.
2. **The first fetch also runs inside a request.** Fine for a local file; a slow real source would hold the request.
3. **The file is stored before the database commit.** If the commit fails, a blob is left with no row. It is harmless and is overwritten by the next fetch of that day.
4. **One daily job fetches every feed in turn.** Many feeds or one slow source make the run long. Per-feed times are not possible without a change.
5. **The catalogue is a file, and "known" is a flag in it.** A real source registry, and who may mark a source known, are not designed here.
6. **The mock's rules are simple.** They prove the path, not the quality of an answer. There is no gold set for S1 or S2; phase 5 brings the first one.
7. **Proposals of sources that were not picked stay `pending` for good.** A list of pending proposals must be read with the requirement's status.
8. **Every click on "Ask the assistant" adds a `proposal_log` row.** The table is append-only; retention is a production question.
9. **Twelve requirement statuses.** Steps use `advance()`, but phase 1's `set_status()` still allows any move. Removing it is a later clean-up.
10. **Tests start a local HTTP server on a thread.** Warnings are errors, so an unclosed socket fails the run. If this proves unstable, the server moves to a session fixture.
11. **Two transactions in one request** (the selection, then the fetch) on the tests' shared session, as in phase 2 risk 10.
12. **"Find a source" is started by the requester's browser.** If the page is closed first, the requirement stays `defined` until they press the button.
13. **A needed approval is by role.** Any user with the role can give it; the dev users have one per role.
14. **A scope can hide a fitting source.** A `private` scope never lists a public source. The way out is a note and a new scope.

## 16. Carried forward

Notes only; nothing here is built in phase 3.

**Phase 4**

1. Step 02 starts from a candidate feed's latest `fetched` row in `source_fetches`.
2. When a candidate feed is rejected, the known-source check and S2 must skip its source, and the requester must be told.
3. Registration copies the licence from the catalogue entry; `duplicate_linked` meets the "one feed per source" rule of assumption 11.

**Phase 5**

4. The glossary builder runs in the background. Phase 3 deliberately has no background runner, so phase 5 must choose one.

**Phase 7**

5. A daily fetch of a registered feed becomes a load (step 12). Today it only stores the file and a `source_fetches` row.
6. The Playwright story covers S1 to S4 and the approval.

**Phase 8**

7. Email and Teams notifications through an outbox (phase 2 plan, section 16).
8. Model calls and the first fetch through a queue (risks 1 and 2).
9. The Azure Functions timer runs the same single daily job.
10. `SOURCE_HTTP_ALLOWED_HOSTS` is widened only for approved sources.
11. `AzureOpenAIProvider` uses the same prompt files; compare its answers with the mock's on rows E to I.

## 17. Results and deviations (recorded during implementation)

Nothing yet.
