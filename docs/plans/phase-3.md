# Phase 3 plan — Custom data product ingestion (Known source?, S1–S4, DiscoveryAssistant)

**Status:** approved on 2026-11-14 with four changes: source approval option A (the requester's acceptance is the only approval), a fetch id in the blob key, notifications only on a change, and one transition table for requirement statuses. Those changes are applied below. **3a is built and approved** (checkpoint 3, 2026-11-14; section 17). **The 3b part was approved on 2026-11-14** (versioned source choices and release, assumptions 49 to 55; the acceptance that names the conditions, 56; commit-visibility tests, 57; the option A re-check, section 5.6), with the open questions answered as recommended except Q4, which changed: registered feeds are always fetched (assumption 54).
**Date:** 2026-11-14. **Branch:** `phase-3`, created from `main` at `cab1a01`. **Scope:** phase 3 of `BUILD_PLAN.md` and its carried items. Local only; nothing is pushed.

**Phase 3 is too big for one build.** This plan covers all of it, so the design is decided once, and splits the work into **3a** (no agent: known source, fetch, scheduler) and **3b** (agents: scope, source list, pick, DiscoveryAssistant). I stop for your approval between them.

## 1. Goal and "done when"

**Part 3a is done when all five are true:**

1. A requirement with status `defined` is checked against the known sources: `known_source` or `no_known_source`.
2. For a known source, its file is fetched and stored, a candidate feed is linked to the requirement, and the requester sees exactly "Your data is getting onboarded; we will notify you once it is ready." Proved by `test_api_consumer_sourcing.py::test_done_when_a_known_source_is_fetched_and_the_requester_sees_onboarding` and in the browser.
3. A source that does not answer gives `source_down`; the requirement waits and the fetch is tried again every day.
4. The daily fetch still runs for that feed after the API is restarted.
5. `pytest` passes with nothing skipped and no warnings.

**Part 3b is done when all seven are true:**

1. Every model call goes through `Agent.run()`: it is logged in `proposal_log` first, and every proposal points at its log row. A subclass that defines `run()` is refused when the class is created.
2. With no known source, the requester gets a scope to confirm (S1), then a list of sources with coverage and the conditions of use: licence, permitted use, redistribution and any external approval (S2).
3. **Picking a source produces a fetched file handed to step 02** (S3, S4). This is `BUILD_PLAN.md`'s done-when, proved by `test_api_consumer_sourcing.py::test_done_when_picking_a_source_hands_a_fetched_file_to_step_02` and in the browser. A pick without an explicit acceptance of the source's stated conditions gives `approval_missing`, and nothing is fetched until the requester accepts.
4. **A selected source that stays down is released**, after N failed days in a row or when the requester asks, and the requester can pick again. Every choice and release is one versioned row; the highest version is current. Proved by `test_release.py::test_picking_again_adds_the_next_version` and in the browser (row J).
5. The DiscoveryAssistant suggests glossary elements beside the deterministic search, and changes nothing.
6. Each of the ten outcome names has a named test (section 5.4), and every 3b endpoint that commits has a commit-visibility test (assumption 57).
7. `pytest` passes with nothing skipped and no warnings.

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

**State at the start of 3b (checked on 2026-11-14):**

| Item | Found |
|---|---|
| Git | `phase-3` = `4986c0e`; `main` = `phase-2` = `cab1a01`. Working tree clean |
| Tests | 781 collected |
| Database | `alembic current`: `0008 (head)`. `source_choices` holds 3 rows, all `known_source`, one per requirement |
| `source_choices` | One row per requirement (`uq_source_choices_requirement_id`); `basis` is `known_source` only. No version, no release |
| A requirement that is `source_selected` | Has no move except to `onboarding`. It waits for good if the source stays down |
| Commit-visibility tests | Two: Find a source, and the daily job. Each builds and drops its own schema; 1.0 to 1.5 s each |

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
| 7 | New statuses by migration | assumptions 4, 41, 42; section 5.1 |

**Your decisions on approval (2026-11-14):**

1. **Source approval, option A: the requester's pick is the only approval.** The backend team reviews the data during onboarding (phase 4) and can restrict access there. No backend role approves a source in phase 3: no backend-ui Approvals view and no `/api/amaze/approvals`.
2. **`approval_missing` means the requester has not accepted the source's stated conditions:** its licence, permitted use, redistribution, and any external approval it needs. The conditions are shown, an explicit accept is required, and nothing is fetched until then.
3. **One plan, a stop after checkpoint 3** (Q1); **one daily job** (Q2); **one proposal per listed source** (Q3); **the read-only assistant's call is logged** (Q4); **any ticked term gives `no_known_source`** (Q6); **the raise message stays** (Q7).
4. **`source_choices` is versioned per requirement; the highest version is current** (checkpoint 2). Releasing a source is a new row with action `released` and a reason, `source_down_n_days` or `requester_asked`. Picking again adds the next version. Planned in assumptions 49 to 55.
5. **A way back from a source that stays down** (checkpoint 1): from `source_selected` to `awaiting_source_choice`, after N failed days in a row (N is a setting) or when the requester asks. Assumptions 51 to 53.
6. **Commit-visibility tests: one per endpoint that commits, happy path only** (after 3a). Negative outcomes stay in the shared-session tests. Assumption 57.

**Questions you asked me to decide:**

| Question | Answer | Assumption |
|---|---|---|
| Outcome names and their tests | The ten names of `CLAUDE.md`, each with a named test | section 5.4 |
| Rule 1 for S1 and S3 | Proposals and approvals from phase 1, with database guards | 28, 33, 36; section 5.3 |
| Rule 4 for the read-only assistant | **Yes, logged in `proposal_log`** | 39 |
| What `approval_missing` means | **Your decision 2:** the conditions are not accepted yet; nothing is written or fetched; the status stays `awaiting_source_choice` | 34, 35 |
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
4. **One transition table in code; every status change goes through it.**
   - `REQUIREMENT_TRANSITIONS` in `core/domain/requirement.py` lists every allowed move (from, to): the moves of section 5.1, nothing else.
   - `RequirementRepository.advance(id, to)` locks the row and raises `RequirementNotInStatus` for any move not in the table. The API returns 409, so a double click cannot run a step twice.
   - Phase 1's `set_status()`, which allowed any move, is removed. Its tests are rewritten against the table.
   - `test_repo_requirements.py::test_every_move_not_in_the_table_is_refused` tries every pair of statuses that the table does not allow and expects `RequirementNotInStatus`.
   - 3a's table has two moves; 3b adds eleven, 13 in all (section 5.1). With 11 statuses, the test then refuses 108 moves.
5. **The raise message does not change:** "Requirement raised; the backend team will look for a source." The result of "Find a source" appears under it.

**Known source? (3a)**

6. **A source catalogue.** `fixtures/sources/catalogue.json` lists the synthetic sources; `ports/connectors/catalogue.py` reads it into `SourceEntry` models. Each entry has: code, name, provider, description, `visibility` (`public` or `private`), `channel` (where it is found), `known`, `conditions` (licence name, permitted use, redistribution, and `external_approvals`: approvals outside Amaze the requester must hold, such as a signed vendor agreement), the glossary `elements` it declares, `other_fields` (names that are not in the glossary; a ticked term is covered when it equals one), `connector` and `source_ref`. The path is a setting, `SOURCE_CATALOGUE`.
7. **`known: true` means Amaze already has an agreement and a connection.** People made that decision when the source was marked known, so nothing is asked again: a known source is selected without an acceptance step. A known source must have no external approvals; the loader refuses one that has. Its licence is still shown on the requirement page.
8. **The rule.** `known_source` when the requirement has at least one element, no ticked terms, and one known source declares every element. With several, pick the one with the fewest declared elements beyond the request, then by code. Any ticked term gives `no_known_source`: a term is free text, so matching it to a source is a judgement. That belongs in S2, where the requester sees the source and picks it.
9. **What each outcome does.**
   - `known_source`: the source is selected (assumption 10) and the first fetch runs (assumption 13).
   - `no_known_source` in 3a: nothing is written, the status stays `defined`, and the requester sees "No known source has this data yet."
   - `no_known_source` in 3b: S1 runs in the same request (assumption 27).

**Fetch and the hand-off to step 02 (S4, 3a)**

10. **"Handed to step 02" is three things:**
    - a **candidate feed** carrying the source: `source_code`, `connector`, `source_ref`;
    - a **stored file**: the latest `fetched` row in `source_fetches`, with the `BlobStore` key `raw/<feed code>/<date>/<fetch id>/<file name>`. The fetch id is the row's id, made before the file is stored, so two fetches on one day keep both files;
    - a **link to the requirement**: one `source_choices` row (requirement, feed, how it was chosen). From 3b, the requirement's current row: the highest version, which must be a selection (assumption 49).

    The requirement becomes `onboarding`. Phase 4 starts from candidate feeds that have a fetched file.
11. **One feed per source.** The feed's code is the source's code. A second requirement that selects the same source is linked to the existing feed; no second feed is made.
12. **The fetch** (`core/steps/s4_fetch.py`) reads the feed's connector and reference, fetches, and records one `source_fetches` row.
    - `fetched`: the file is stored, and every requirement linked to the feed with status `source_selected` becomes `onboarding`.
    - `source_down`: the connector could not read the source, or the file is empty. The reason is recorded and the requirement stays `source_selected`.
    - **Notifications are sent only on a change, not daily** (in-app, to each backend team user):
      - "File ready to profile: <feed>" on the feed's **first** `fetched`, never again;
      - "Source down: <feed>" on the **first** `source_down` after a `fetched`, or on the feed's very first fetch if that one fails. A `source_down` after another `source_down` sends nothing.
    - Tests: `test_s4_fetch.py::test_file_ready_is_sent_on_the_first_fetched_only` (fetched, fetched: one notification) and `test_s4_fetch.py::test_source_down_is_sent_once_per_run_of_failures` (down, down, fetched, down: two notifications).
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
    - SourceDiscoveryAgent (A2) ranks them and gives a reason for each. Mock rule: the most requested items covered first, then the fewest other declared fields, then the code.
    - A source this requirement has released is left out (assumption 53).
    - Coverage and conditions are copied from the catalogue. The model's text is never used for them, and a source code the model invents is dropped.
31. **One `source_list` proposal per listed source**, all pointing at the same log row, with `required_approver_roles = ["requester"]` (your decision 1). Each proposal's payload names one source and holds its conditions as listed. Outcome `sources_listed`.
32. **`no_fit`:** no catalogue source in scope covers any requested item, or the agent lists none. No proposal is made. The requirement becomes `no_source_found`, the backend team is notified, and the requester sees "No source fits this request. The backend team has been told." The requester may add a note and ask for a new scope.

**S3 pick (3b)**

33. **Rule 1 for S3: the requester's pick, with the conditions accepted, is the only approval** (your decision 1). It is one `approved` row in `approvals` for that source's proposal, which makes the proposal `approved`. Then outcome `source_picked`: the source is selected (assumption 10) as the requirement's next `source_choices` version, with the accepted conditions stored on that row, and the first fetch runs. The backend team reviews the data in phase 4 and can restrict access there.
34. **`approval_missing`: the requester has not accepted the source's stated conditions** (your decision 2). **(changed for 3b: the acceptance names the conditions, assumption 56.)**
    - The pick request carries `accepted_conditions`: the conditions the requester was shown. If it is missing, or is not exactly the conditions in the catalogue at that moment, the outcome is `approval_missing` and the result lists the conditions in force: licence, permitted use, redistribution and each external approval.
    - Nothing is written: no approval row, no feed, no fetch, no job. The requirement stays `awaiting_source_choice`.
    - The UI shows the conditions and an explicit "I accept these conditions" box; Pick stays disabled until it is ticked. A request sent without it still gets `approval_missing`.
    - The requester's message: "<source> can be used only under these conditions. Accept them to continue."
35. **Declining a source.** The requester may reject a listed source instead (one `rejected` row in `approvals`; the proposal is `rejected`). It is not shown again. If no source is left, the requirement becomes `no_source_found` and the requester may ask for a new scope.
36. **Database guards on `source_choices`.** **(changed for 3b: the full list is assumption 50.)**
    - A requester's pick is accepted only if its proposal is a `source_list` proposal approved by the requester and rejected by nobody (the phase 1 guard from `0005`), is for the same requirement, and names the same source.
    - A pick must have its accepted conditions and the requester recorded (not null).
    - A known-source row must have no proposal, no accepted conditions and no requester.
    - Raw-SQL tests prove each refusal (`test_db_source_guards.py`).
37. **The proposals of sources that were neither picked nor declined stay `pending`.** Nobody decided them, so no decision is written for them.

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

41. **Two migrations.** `0008_sourcing.py` (3a) reached the application database at checkpoint 1; `0009_scope_and_pick.py` (3b) reaches it at checkpoint 4. `0009` also makes `source_choices` versioned (assumption 49). An applied migration is never edited. Explicit constraint names use `op.f()`.
42. **Requirement statuses** grow from 5 to 11 (section 5.1). 3a adds `source_selected`; 3b adds `awaiting_scope_confirmation`, `scope_not_confirmed`, `scope_confirmed`, `awaiting_source_choice` and `no_source_found`. No `awaiting_approval`: under option A nobody else is waited for. Release adds two moves and no status.
43. **API.** The consumer endpoints are for the requester and only for their own requirement: another's gives 404, a move the transition table does not allow gives 409. 3b adds seven that commit (section 9), release among them. No Amaze endpoint is added in phase 3.
44. **consumer-ui** gets a third hash view, `#/requirement/<id>`: status, message, source, last fetch, and the next action. My requests links to it. No new npm package.
45. **backend-ui does not change in phase 3** (your decision 1). The feed registry already shows candidate feeds, which is where the backend team sees what to profile in phase 4.
46. **Synthetic data only.** The catalogue and every sample file pass the real-data checks from `test_glossary_seed.py`. Providers have invented names. Sample identifiers are lower-case with hyphens (`syn-0001`), so they have no real identifier's shape.
47. **Documents** (tasks 9 and 20): `CLAUDE.md` layout lines, `BUILD_PLAN.md` (phase 3 is built as 3a and 3b), `README.md`, `.env.example` (the two new settings).
48. **I check the UI** by building it and by a scripted browser run with screenshots. The committed Playwright test stays in phase 7.

**Versioned source choices and release (3b; added 2026-11-14)**

49. **`source_choices` is versioned per requirement; the highest version is current** (your decision 4).
    - Each row has a `version` (1, 2, 3, ...) and an `action`: `selected` or `released`.
    - A `selected` row is a known-source selection or a requester's pick, as before.
    - A `released` row names the feed and source it releases, with a `reason`: `source_down_n_days` or `requester_asked`.
    - Picking again adds the next version. So a requirement's history reads: version 1 selected X, version 2 released X, version 3 selected Y.
    - The table stays append-only. 3a's three rows become version 1, `selected`.
50. **What the database enforces on `source_choices`.** Each line has a raw-SQL test that bypasses the repository (`test_db_source_guards.py`).
    1. A new row's version is the requirement's highest version plus one; the first is 1. Two rows cannot share a version.
    2. The first row is a selection. A release follows a selection and names that selection's feed and source. A selection never follows a selection.
    3. A source is selected at most once per requirement, so a released source cannot be picked again for it.
    4. A selection has a basis and no reason; a release has a reason and no basis.
    5. A pick (`requester_pick`) has its proposal, the accepted conditions and the requester; the proposal is a `source_list` proposal approved by the requester and rejected by nobody (the `0005` function), for the same requirement and the same source.
    6. A known-source selection has no proposal, no accepted conditions and no requester.
    7. A `requester_asked` release names the requester; a `source_down_n_days` release names nobody.
    8. UPDATE, DELETE and TRUNCATE are refused, as today.

    **Every check on a nullable column is written so that NULL cannot pass by accident** (`IS NULL`, `IS NOT NULL` or `COALESCE`, never a bare comparison), has a test with the NULL case, and is added to `REVIEWED_NULLABLE_CHECKS` with its reason. That is the lesson of checkpoint 1.
51. **Release by rule: N failed days in a row.**
    - N is the setting `SOURCE_DOWN_RELEASE_DAYS`: a whole number, at least 1, default 3.
    - A failed day is a UTC date on which the feed has a `source_down` fetch. Two failures on one day count as one day.
    - The count is per requirement: the failed days since the later of two moments, the requirement's selection of the source and the feed's last `fetched`. Days before the selection do not count, so a requirement that selects a source that is already down gets its own N days.
    - The rule runs in the fetch step, right after a `source_down` is recorded, in the same transaction. Every requirement that is `source_selected` on the feed and has reached N days is released with reason `source_down_n_days`.
    - The outcome stays `source_down`; the result also lists the requirements it released.
    - A requirement that is `onboarding` is never released by this rule: its file is already with step 02. A feed that stops answering later is a late delivery, which is phase 7.
52. **Release on the requester's request.** `POST .../release`, from `source_selected` only, with reason `requester_asked`. The requester need not wait N days. An `onboarding` requirement cannot be released: 409.
53. **Where a released requirement goes.**
    - `awaiting_source_choice`, if at least one listed source can still be picked (a `pending` `source_list` proposal of this requirement).
    - `no_source_found`, if none is left. This is always the case for a known source, because S1 and S2 never ran. From there the requester adds a note and asks for a scope (S1), as after `no_fit`.
    - A released source is not listed and not offered again for that requirement: S2 leaves it out, and the database refuses a second selection (assumption 50, line 3).
    - "Known source?" is not run again after a release. It runs once, from `defined`.
    - This keeps one rule true: a requirement that is `awaiting_source_choice` always has a source to pick. If you want a released requirement to go to `awaiting_source_choice` even with nothing to pick, see open question 1.
54. **The feed and the daily job after a release.**
    - The feed and its fetch history stay. Nothing is deleted or changed; a release is one new `source_choices` row.
    - **(changed on approval, Q4.)** The daily job always fetches a **registered** feed that has a connector and is not rejected. It fetches a **candidate** feed only while some requirement currently selects it (its highest version is a selection of that feed). A candidate feed that every requirement has released is idle: the job skips it, so it produces no more failures and no more notifications. No feed is registered in phase 3 (registration is phase 4), so in phase 3 the registered case is proved by a test that inserts a registered feed directly.
    - Tests, one per case: `test_jobs.py::test_the_job_always_fetches_a_registered_feed` (a registered feed with no requirement selecting it is fetched) and `test_jobs.py::test_the_job_skips_a_candidate_feed_that_no_requirement_selects`.
    - A new selection of that source wakes the feed: the same feed is linked again, and the first fetch runs as for any selection.
    - A fetch moves to `onboarding` only the requirements that currently select the feed. A requirement that released it and now waits on another source is not touched.
    - The backend team is told once per release, in-app: "Source released: <feed>", with the requirement and the reason.
55. **The requester's messages after a release, exact text:**
    - by the rule, a source left to pick: "<source> did not answer for <N> days in a row. Pick another source."
    - by the rule, none left: "<source> did not answer for <N> days in a row. No other source is listed. Add a note and ask for a new scope."
    - on request, a source left to pick: "You released <source>. Pick another source."
    - on request, none left: "You released <source>. No other source is listed. Add a note and ask for a new scope."

**Option A, re-checked for 3b (added 2026-11-14)**

56. **The acceptance names the conditions.** Section 5.6 lists the re-check; this is the one gap it found.
    - Until now the pick carried a yes or no. That does not say what was accepted, and under option A the requester's acceptance is the only approval.
    - The pick now sends back the conditions the requester was shown. The server compares them with the catalogue's conditions at that moment. Equal: they are stored on the `source_choices` row as accepted. Missing or different: `approval_missing`, with the conditions in force, and nothing is written.
    - So the conditions cannot change between the list and the pick without the requester seeing the new ones and accepting again.
    - Picking again after a release needs a new acceptance, for the newly picked source.

**Commit-visibility tests (added 2026-11-14)**

57. **One per 3b endpoint that commits, happy path only** (your decision 6). Seven new tests in `tests/test_commit_visibility.py`, each using the app's real session factory on `amaze_test`, reading on a separate connection, and shown failing with its commit removed:

    | Endpoint | Test | What the separate connection sees |
    |---|---|---|
    | `POST .../scope` | `test_scope_commits_the_proposal` | the `proposal_log` row, the `scope` proposal, the status |
    | `POST .../scope/decision` | `test_scope_decision_commits_the_scope` | the approval, the `requirement_scopes` row, the status |
    | `POST .../sources` | `test_sources_commits_one_proposal_per_source` | the `proposal_log` row, the `source_list` proposals, the status |
    | `POST .../pick` | `test_pick_commits_the_choice_and_the_fetch` | the approval, the `source_choices` row, the `fetched` row, `onboarding` |
    | `POST .../decline` | `test_decline_commits_the_rejection` | the `rejected` approval and proposal |
    | `POST .../release` | `test_release_commits_the_released_row` | the `released` row and the new status |
    | `POST /api/consumer/search/assist` | `test_assist_commits_the_log_row` | the `proposal_log` row, and no proposal |

    - **Find a source keeps its one test.** In 3b it also commits S1's proposal when there is no known source. The endpoint is written with one commit for its first transaction, whichever branch ran, so removing that line still fails the existing test.
    - **Negative outcomes stay in the shared-session tests:** `approval_missing`, `no_fit`, `scope_not_confirmed`, `source_down`, the release by the N-day rule, 404 and 409.
    - **Expected added test time: about 7 to 10 seconds.** Measured cost is 1.0 to 1.5 s per test, mostly building and dropping the test's own schema. The suite takes about 24 s today, before 3b's other tests.

**Demo data (added 2026-11-14)**

58. **A sixth synthetic source, so release and picking again can be shown in the browser.** `syn_backup_eod_prices`: public, market data vendors, not known, declares `instrument_id`, `price_date`, `open_price` and `close_price`, no external approval, and **no file, on purpose**. A request for `open price` then lists two sources; picking this one gives `source_down`, and the requester can release it and pick the other (row J).

**Open questions on this update (2026-11-14), recommendation first**

1. **Where a released requirement goes when no listed source is left.** Recommended: `no_source_found`, so that `awaiting_source_choice` always has a source to pick (assumption 53). Alternative: always `awaiting_source_choice`, with an empty list and the note box to ask for a new scope. **Answer: as recommended, `no_source_found`.**
2. **N, and how the failed days are counted.** Recommended: default 3, counted per requirement since it selected the source (assumption 51). Alternative: counted per feed, whoever selected it and whenever. **Answer: as recommended; `SOURCE_DOWN_RELEASE_DAYS` defaults to 3.**
3. **Known sources and option A.** Recommended: keep 3a as you approved it: a known source is fetched with no acceptance by the requester, because Amaze already holds the agreement (assumption 7). Alternative: the requester accepts a known source's conditions before its first fetch; that changes 3a's flow and adds a wait. **Answer: as recommended; 3a stays.**
4. **An idle feed.** Recommended: the daily job skips a feed that no requirement selects (assumption 54). Alternative: keep fetching every feed that has a connector. **Answer: changed.** Registered feeds are always fetched; candidate feeds only while a requirement currently selects them (assumption 54).
5. **The commit-visibility tests' schema.** Recommended: one schema per test, as today, about 7 to 10 s more (assumption 57). Alternative: one schema per test file, about 2 to 3 s more, with tests that share committed rows. **Answer: as recommended, one schema per test.**

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
| `awaiting_source_choice` | Requester picks and accepts the conditions | `source_picked` | `source_selected` | 3b |
| `awaiting_source_choice` | Requester picks without accepting | `approval_missing` | stays `awaiting_source_choice` | 3b |
| `awaiting_source_choice` | Requester declines the last remaining source | | `no_source_found` | 3b |
| `source_selected` | Fetch (first, or daily) | `fetched` | `onboarding` | 3a |
| `source_selected` | Fetch (first, or daily) | `source_down` | stays `source_selected` | 3a |
| `source_selected` | Fetch: the Nth failed day in a row (rule) | `source_down` | `awaiting_source_choice`, or `no_source_found` if no listed source is left | 3b |
| `source_selected` | Requester releases the source | | `awaiting_source_choice`, or `no_source_found` if no listed source is left | 3b |

`ready`, `access_restricted` and `rejected` exist since phase 1 and are not used before phases 4 and 7. Release has no outcome name of its own: `CLAUDE.md` names none, so none is invented.

The rows that change the status are exactly `REQUIREMENT_TRANSITIONS` (assumption 4), 13 moves after 3b:

| Part | Moves |
|---|---|
| 3a | `defined → source_selected`; `source_selected → onboarding` |
| 3b, S1 | `defined → awaiting_scope_confirmation`; `awaiting_scope_confirmation → scope_confirmed`; `awaiting_scope_confirmation → scope_not_confirmed`; `scope_not_confirmed → awaiting_scope_confirmation`; `no_source_found → awaiting_scope_confirmation` |
| 3b, S2 | `scope_confirmed → awaiting_source_choice`; `scope_confirmed → no_source_found` |
| 3b, S3 | `awaiting_source_choice → source_selected`; `awaiting_source_choice → no_source_found` |
| 3b, release | `source_selected → awaiting_source_choice`; `source_selected → no_source_found` |

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
    released: list[UUID]               # 3b: requirements the N-day rule released (usually none)

# core/steps/s1_scope.py
class Scoped(StepResult):              # outcome "scoped"
    scope: RequirementScope
class ScopeNotConfirmed(StepResult):   # outcome "scope_not_confirmed"
    note: str | None

# core/steps/s2_sources.py
class SourcesListed(StepResult):       # outcome "sources_listed"
    candidates: list[SourceCandidate]  # proposal id, source, coverage, conditions, reason
class NoFit(StepResult):               # outcome "no_fit"
    reason: Literal["nothing_in_scope", "agent_listed_none"]

# core/steps/s3_pick.py
class SourcePicked(StepResult):        # outcome "source_picked"
    source: SourceSummary
    feed_code: str
class ApprovalMissing(StepResult):     # outcome "approval_missing"
    source: SourceSummary
    conditions: SourceConditions       # the conditions in force: licence, permitted use, redistribution, external approvals
    reason: Literal["not_accepted", "conditions_changed"]

# core/steps/release.py  (no outcome name: a plain model, like phase 2's request result)
class Released(BaseModel):
    source: SourceSummary
    reason: Literal["source_down_n_days", "requester_asked"]
    version: int                       # the released row's version
    status: Literal["awaiting_source_choice", "no_source_found"]
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
| `s3_pick.py` | `pick_source(requirement, user, proposal_id, accepted_conditions)` | with the conditions in force accepted: the approval, then `select_source` with them, as the next version; otherwise nothing | 3b |
| `s3_pick.py` | `decline_source(requirement, user, proposal_id, note)` | the rejection; status `no_source_found` if no source is left | 3b |
| `release.py` | `release_source(requirement, reason, user)` | the `released` row (next version); the status; one notification to the backend team | 3b |
| `s4_fetch.py` | `fetch_source(feed_id, ..., release_after_days)` | as in 3a; after a `source_down`, `release_source` for each requirement that reached N days | 3b |
| `s17_discover.py` | `assist(query, agent, ...)` | through `Agent.run()`: the log row only | 3b |

### 5.3 Rule 1: who decides what

| Step | Who decides | Proposal | Approval rows | What the decision allows, and the database guard |
|---|---|---|---|---|
| Known source? | Nobody at request time. People decided when the source was marked known | none | none | A `source_choices` row with basis `known_source`, which must have no proposal |
| S1 | The requester | one `scope` proposal per run; roles `[requester]` | the requester's approval or rejection | A `requirement_scopes` row: needs a `scope` proposal approved by the requester, for the same requirement |
| S2, S3 | The requester alone: the pick with the conditions accepted (your decision 1). The backend team reviews the data in phase 4 | one `source_list` proposal per source; roles `[requester]` | the pick is the approval; declining is the rejection | A `source_choices` row with basis `requester_pick`: needs a `source_list` proposal approved by the requester and rejected by nobody, for the same requirement and source, with the accepted conditions recorded |
| S4 | Nobody: rules execute | none | none | none |
| Release, on request | The requester | none | none | A `released` row with reason `requester_asked`, naming the requester; it must follow a selection of the same source |
| Release, N failed days | Nobody: a rule over the recorded fetches | none | none | A `released` row with reason `source_down_n_days`, naming nobody |
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
| `source_picked` | S3 | `test_s3_pick.py::test_source_picked_when_the_requester_accepts_the_conditions` | 3b |
| `approval_missing` | S3 | `test_s3_pick.py::test_approval_missing_when_the_conditions_are_not_accepted_and_nothing_is_fetched` | 3b |

### 5.5 Carried items and their tests

| Carried item | Named test |
|---|---|
| 1 `Agent.run()` only | `test_agent_base.py`: `test_a_subclass_that_defines_run_is_refused_at_class_creation`, `test_run_logs_the_call_before_it_creates_proposals`, `test_every_proposal_points_at_the_log_row`, `test_a_failed_model_call_writes_nothing`, `test_a_read_only_agent_cannot_create_proposals`; `test_no_direct_llm_calls.py::test_complete_is_called_only_in_agents_base` |
| 2 connector closes | `test_port_connectors.py`: `test_http_client_is_closed_after_a_fetch`, `test_http_client_is_closed_after_a_failed_fetch`, `test_http_refuses_a_host_that_is_not_this_machine`; `test_s4_fetch.py::test_the_step_closes_the_connector_even_when_the_source_is_down` |
| 3 job survives a restart | `test_jobs.py`: `test_the_daily_job_is_registered_at_every_start`, `test_after_a_restart_the_job_fetches_feeds_read_from_the_database`, `test_one_failing_feed_does_not_stop_the_others` |
| 4 Known source? | section 5.4, and `test_api_consumer_sourcing.py::test_find_source_needs_status_defined` (409) |
| 5 DiscoveryAssistant | `test_agent_discovery_assistant.py::test_suggestions_are_glossary_elements_only`; `test_api_consumer.py`: `test_the_assistant_call_is_logged_without_a_proposal`, `test_search_itself_makes_no_model_call` |
| 6 import boundary | `test_import_boundaries.py` unchanged and passing with the agents in place; `test_s1_scope.py` and `test_s2_sources.py` run with a stand-in agent |
| 7 statuses | `test_migrations.py`: `test_upgrade_downgrade_upgrade` with `HEAD` at `0008`, then `0009`; `test_repo_requirements.py`: `test_every_move_not_in_the_table_is_refused`, `test_every_move_in_the_table_is_allowed`, `test_database_refuses_unknown_status`; `test_domain_models.py::test_requirement_statuses_and_transitions_are_the_plans` |
| Blob key per fetch | `test_s4_fetch.py::test_two_fetches_on_one_day_keep_both_files` |
| Notifications on a change | `test_s4_fetch.py`: `test_file_ready_is_sent_on_the_first_fetched_only`, `test_source_down_is_sent_once_per_run_of_failures` |

**3b additions of 2026-11-14 and their tests:**

| Item | Named test |
|---|---|
| Versioned choices: the migration | `test_migrations.py`: `test_0009_makes_existing_choices_version_1_selected`, `test_0009_downgrade_refuses_when_3b_rows_exist` |
| Versioned choices: the guards (assumption 50, raw SQL) | `test_db_source_guards.py`: `test_a_version_must_be_the_next_one`, `test_the_first_row_must_be_a_selection`, `test_a_release_must_follow_a_selection_of_the_same_source`, `test_a_selection_cannot_follow_a_selection`, `test_a_released_source_cannot_be_selected_again`, `test_a_selection_has_a_basis_and_a_release_has_a_reason`, `test_a_pick_needs_an_approved_source_list_proposal_of_the_same_requirement_and_source`, `test_a_pick_needs_the_accepted_conditions_and_the_requester`, `test_a_known_source_row_has_no_proposal`, `test_a_release_names_the_requester_only_when_asked`, `test_null_never_passes_a_source_choice_check` (one case per nullable column) |
| Versioned choices: the repository | `test_repo_source_choices.py`: `test_current_is_the_highest_version`, `test_history_lists_every_version`, `test_requirements_selecting_a_feed_leaves_out_those_that_released_it` |
| Release on request | `test_release.py`: `test_the_requester_releases_a_source_that_did_not_answer`, `test_release_goes_to_awaiting_source_choice_when_a_listed_source_is_left`, `test_release_goes_to_no_source_found_when_none_is_left`, `test_only_a_source_selected_requirement_can_be_released`, `test_a_released_source_is_not_listed_or_picked_again`, `test_picking_again_adds_the_next_version`, `test_the_backend_team_is_told_once_per_release` |
| Release by rule | `test_s4_fetch.py`: `test_released_after_n_failed_days_in_a_row`, `test_two_failures_on_one_day_count_as_one_day`, `test_a_fetched_day_starts_the_count_again`, `test_days_before_the_selection_do_not_count`, `test_n_is_the_setting`, `test_an_onboarding_requirement_is_never_released` |
| The feed after a release | `test_s4_fetch.py::test_a_fetch_moves_only_requirements_that_currently_select_the_feed`; `test_jobs.py`: `test_the_job_always_fetches_a_registered_feed`, `test_the_job_skips_a_candidate_feed_that_no_requirement_selects`, `test_a_new_selection_wakes_the_feed` |
| The acceptance names the conditions (assumption 56) | `test_s3_pick.py`: `test_approval_missing_when_the_conditions_changed_since_the_list`, `test_the_accepted_conditions_are_stored_on_the_choice`, `test_picking_again_needs_a_new_acceptance` |
| Commit visibility | the seven tests of assumption 57 |
| The setting | `test_settings.py::test_source_down_release_days_is_a_whole_number_of_at_least_one` |

The earlier failed days in the rule's tests are written as `source_fetches` rows with a `created_at` in the past. Inserting is allowed on an append-only table; nothing is updated.

### 5.6 Option A, re-checked for 3b

Option A (section 3, decisions 1 and 2): the requester's acceptance is the only approval, and nothing is fetched before it.

| 3b step | Who approves | Fetched before the requester accepts | Result |
|---|---|---|---|
| S1 scope | the requester | nothing | consistent |
| S2 list | nobody approves a list; each source waits for the requester | nothing: listing reads the catalogue only; no sample is fetched | consistent |
| S3 pick | the requester, by accepting the conditions in force | nothing; the first fetch runs only after the choice is committed | **one gap, fixed:** the acceptance was a yes or no; it now names the conditions (assumption 56) |
| S3 decline | the requester | nothing | consistent |
| Release on request | the requester | not applicable | consistent |
| Release after N failed days | nobody: a rule (rules execute) | not applicable | consistent |
| Picking again after a release | the requester, with a new acceptance for the newly picked source | nothing until then | consistent |
| A second requirement picks a source that already has a feed | that requirement's own requester | the feed's daily fetch may already run for the first requirement; nothing is selected or fetched for the second before its acceptance | consistent |
| DiscoveryAssistant | nobody: read only | nothing | consistent |
| Backend roles | none approves anything; they are only told (`no_fit`, file ready, source down, source released) | | consistent |
| Known source (3a, assumption 7) | nobody at request time: Amaze already holds the agreement | **the first fetch runs with no acceptance by the requester** | outside S3 and unchanged, as approved with 3a. Open question 3 |

No backend role appears in any `required_approver_roles` in 3b, and no Amaze endpoint is added.

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
| `requirements` | status list + the five 3b statuses | as above |
| `requirement_scopes` (new) | requirement_id, version, visibility, provider, where_to_search (text[]), proposal_id, confirmed_by | **Append-only.** Unique (requirement, version). Trigger: an approved `scope` proposal (the `0005` function). Trigger: the proposal is for the same requirement |
| `source_choices` (versioned) | + version, action, reason, proposal_id, accepted_conditions (jsonb), decided_by; basis becomes optional and may also be `requester_pick`; the "one row per requirement" constraint is replaced | see below |

**`source_choices` after `0009`** (assumptions 49 and 50):

| Column | `selected`, known source | `selected`, requester's pick | `released` |
|---|---|---|---|
| requirement_id, version | set; unique together | set | set |
| action | `selected` | `selected` | `released` |
| feed_id, source_code | the source | the source | the source being released |
| basis | `known_source` | `requester_pick` | empty |
| proposal_id | empty | the approved `source_list` proposal | empty |
| accepted_conditions | empty | the conditions the requester accepted | empty |
| reason | empty | empty | `source_down_n_days` or `requester_asked` |
| decided_by | empty | the requester | the requester, for `requester_asked`; empty for the rule |

- **Existing rows:** `version` and `action` are added with defaults 1 and `selected`, then the defaults are dropped. Adding a column does not fire the append-only triggers, and no row is updated by hand.
- **Constraints:** unique (requirement, version) replaces unique (requirement); a partial unique index on (requirement, source) for `selected` rows; the checks of assumption 50, lines 4 to 7.
- **Triggers on insert:**
  - `amaze_guard_source_choice_version()` (new): lines 1 and 2 of assumption 50;
  - the `0005` function with argument `source_list`, only for a pick;
  - `amaze_require_proposal_of_requirement()` (new, shared with `requirement_scopes`): same requirement; for `source_choices` also the same source code.
- **Downgrade:** stops if any `source_choices` row is a release, a pick or a version above 1, or if a requirement has a 3b status. `0008` cannot hold those.

**PostgreSQL 14 or older only:** check constraints, arrays, `jsonb ->>` (9.4), triggers with a `WHEN` clause (9.0), partial unique indexes, `ADD COLUMN` with a default (no table rewrite since 11), plpgsql. Nothing from 15 or later.

## 7. Domain models and repositories

| Item | Change | Part |
|---|---|---|
| `core/domain/requirement.py` | the status list; `REQUIREMENT_TRANSITIONS`; `Requirement` unchanged otherwise | 3a, 3b |
| `core/domain/feed.py` | `source_code`, `connector` | 3a |
| `core/domain/sourcing.py` (new) | `SourceSummary`, `SourceConditions`, `SourceChoice`, `SourceFetch`; in 3b `RequirementScope`, `SourceCandidate`, the agents' input and output models, `AgentRun` | 3a, 3b |
| `ports/connectors/catalogue.py` (new) | `SourceEntry`, `SourceCatalogue` | 3a |
| `RequirementRepository` | `advance(id, to)` checked against the table; `set_status()` removed | 3a |
| `FeedRepository` | `create_candidate` takes the source fields; `list_with_source()` | 3a |
| `SourceChoiceRepository` (new) | `record_known(...)`, `for_requirement(id)`, `requirements_for_feed(feed_id)` | 3a |
| `SourceChoiceRepository` (3b) | every write takes the next version. `record_pick(...)`, `record_release(...)`; `current(requirement_id)` replaces `for_requirement`; `history(requirement_id)`; `requirements_selecting(feed_id)` replaces `requirements_for_feed`; `released_sources(requirement_id)` | 3b |
| `FeedRepository` (3b) | `list_to_fetch()` replaces `list_with_source()` in the daily job: feeds with a connector, not rejected, that are registered or that some requirement currently selects (Q4) | 3b |
| 3a code that changes in 3b | `select_source` (next version), `fetch_source` (current selections only; the N-day rule), `requirement_page` (current choice; the release messages), `DailyFetch` (`list_to_fetch`, the setting) | 3b |
| `ports/config.py` (3b) | `SOURCE_DOWN_RELEASE_DAYS` (whole number, at least 1, default 3) | 3b |
| `SourceFetchRepository` (new) | `record(...)`, `latest(feed_id)`, `latest_fetched(feed_id)` | 3a |
| `RequirementScopeRepository` (new) | `add_version(...)`, `current(requirement_id)` | 3b |
| `ProposalRepository` | `list_for_requirement(id, kind)` | 3b |

New named errors: `RequirementNotInStatus`, `FeedHasNoSource`, `UnknownSource` (3a); `ProposalForAnotherRequirement`, `SourceNotInProposal`, `SourceAlreadyReleased` (a released source picked again), `NothingToRelease` (the current choice is not a selection) (3b). 3a's `RequirementAlreadyHasSource` now means: the current choice is already a selection.

## 8. Synthetic sources and the demo paths

`fixtures/sources/catalogue.json` and `fixtures/sources/files/*.csv` (about ten rows each).

| Code | Visibility, channel | Known | Declares | External approvals in its conditions |
|---|---|---|---|---|
| `syn_exchange_volumes` | public, exchanges | yes | instrument_id, price_date, volume | none |
| `syn_offline_accruals` | public, exchanges | yes | instrument_id, price_date, accrued_interest. **Its file is missing on purpose** | none |
| `syn_vendor_eod_prices` | public, market data vendors | no | instrument_id, price_date, open_price, high_price, low_price, close_price, volume | "A signed data agreement with the vendor, held by your desk" |
| `syn_open_esg_scores` | public, public websites | no | instrument_id; other field `esg score` | none |
| `syn_internal_desk_marks` | private, internal systems | no | instrument_id, price_date, mid_price | none |
| `syn_backup_eod_prices` (3b, assumption 58) | public, market data vendors | no | instrument_id, price_date, open_price, close_price. **Its file is missing on purpose** | none |

Every source states a licence, permitted use and redistribution.

All six are file sources (assumption 16). 3a's tests that name five sources and one missing file are updated in 3b.

Signed in as Riley Requester:

| # | Request | What happens | Part |
|---|---|---|---|
| A | `volume` | raise, then Find a source: `known_source` (`syn_exchange_volumes`), `fetched`, "Your data is getting onboarded; we will notify you once it is ready." | 3a |
| B | `accrued interest` | `known_source` (`syn_offline_accruals`), `source_down`; the requirement waits as `source_selected` | 3a |
| C | `open price` | `no_known_source`: the only source that declares it is not known | 3a |
| D | `esg score`, ticked | `no_known_source`: a term was ticked | 3a |
| E | `open price` | scope `public`; confirm; two sources listed with their conditions; pick `syn_vendor_eod_prices` without accepting (sent by the API test; the UI keeps Pick disabled): `approval_missing`, nothing fetched; tick "I accept these conditions" and pick: `source_picked`, `fetched`, onboarding | 3b |
| F | `esg score`, ticked | scope `public`; confirm; `syn_open_esg_scores` listed; pick: `source_picked`, `fetched`, onboarding | 3b |
| G | `yield to maturity`, purpose "internal desk data" | scope `private`; confirm; `no_fit` | 3b |
| H | any of E to G | reject the scope: `scope_not_confirmed`; add a note and ask again | 3b |
| I | search `closing prices` | no match by the search; "Ask the assistant" suggests Close price, unticked | 3b |
| J | `open price` again | scope `public`; confirm; accept and pick `syn_backup_eod_prices`: `source_picked`, then `source_down`. "Choose another source": released (`requester_asked`), back to `awaiting_source_choice` with `syn_vendor_eod_prices` still listed. Accept and pick it: `source_picked`, `fetched`, onboarding. The requirement's choices: version 1 selected, version 2 released, version 3 selected | 3b |
| K | row B's requirement (`accrued interest`, waiting on `syn_offline_accruals`) | "Choose another source": released; no source is listed, so `no_source_found`. Add a note and ask for a scope; confirm; `no_fit`, because the only source that declares it was released | 3b |

The release after N failed days cannot be shown in the browser in one sitting; its tests are in section 5.5.

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
| `POST .../pick` `{proposal_id, accepted_conditions}` | S3: `source_picked` (then the first fetch) or `approval_missing`. `accepted_conditions` is the conditions object the requester was shown (assumption 56) |
| `POST .../decline` `{proposal_id, note}` | the requester declines a listed source |
| `POST .../release` | the requester releases the selected source (assumption 52); 409 unless the requirement is `source_selected` |
| `POST /api/consumer/search/assist` `{q}` | the DiscoveryAssistant's suggestions; logs the call |

`GET .../requirements/{id}` also returns, in 3b: the proposed scope, the listed sources with their proposals and conditions, the history of choices (version, action, source, reason), and whether the source can be released.

No Amaze endpoint is added (your decision 1). Each of the seven `POST` endpoints above commits and has one commit-visibility test (assumption 57).

- 401 if not signed in; 403 for a backend role; 404 for another requester's requirement; 409 for a wrong status; 422 for refused input.
- The catalogue, the blob store, the connector factory, the agents and the notifier are FastAPI dependencies, so tests replace them.
- A failed model call or a stub adapter is never turned into a step outcome.

## 10. UI

**consumer-ui**

1. **Search** (3a): after a raise, the page calls Find a source and shows its result under the raise message.
2. **My requests** (3a): each raised requirement links to its page and shows its message. A `defined` requirement has a "Find a source" button.
3. **Requirement** (`#/requirement/<id>`):
   - 3a: status, message, elements and terms, the source and its licence, the last fetch.
   - 3b: the proposed scope with Confirm and Reject (and a note); the listed sources, each with its coverage and its conditions in full, an "I accept these conditions" box, Pick (disabled until the box is ticked) and Decline.
   - 3b, release: while the requirement is `source_selected`, a "Choose another source" button, with a confirmation. After a release, the message of assumption 55, then the remaining sources to pick, or the note box to ask for a new scope. A short history lists each choice and release.
4. **Search** (3b): "Ask the assistant" appears when a search leaves unmatched terms. Its suggestions are unticked and marked "suggested".

**backend-ui:** no change in phase 3 (your decision 1).

Checked by `npm run build` in both projects and a scripted browser run. Screenshots in `.playwright-mcp/`: `phase3a-A.png` to `phase3a-D.png` and `phase3a-requirement.png`; `phase3b-E.png` to `phase3b-K.png`. The run also checks the browser console.

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
  config.py                                   edit   SOURCE_CATALOGUE, SOURCE_HTTP_ALLOWED_HOSTS; 3b: SOURCE_DOWN_RELEASE_DAYS
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
               s1_scope.py  s2_sources.py  s3_pick.py  release.py   new    3b
               s17_discover.py                                   edit   3b (assist)
               known_source.py  s4_fetch.py  requirement_page.py edit   3b (versions, release)
apps/api/
  main.py  deps.py  routers/consumer.py       edit
  jobs.py                                     new    3a
apps/consumer-ui/src/
  App.tsx  api.ts  views/SearchView.tsx  views/RequestsView.tsx  edit
  views/RequirementView.tsx                   new
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
  test_s1_scope.py  test_s2_sources.py  test_s3_pick.py  test_release.py   new    3b
  test_commit_visibility.py  test_repo_source_choices.py  test_s4_fetch.py
  test_jobs.py  test_known_source.py  test_source_catalogue.py  test_settings.py
  test_api_consumer_sourcing.py                                            edit   3b
fixtures/sources/catalogue.json               edit   3b: syn_backup_eod_prices
.env.example                                  edit   3b: SOURCE_DOWN_RELEASE_DAYS
```

## 12. Tasks, in order

Each task: tests first and shown failing, then code, then its check, then one commit. Files are edited only with the file tools. I stop at the six checkpoints and continue without stopping between them.

### Part 3a

| # | Task | Tests written first | Check |
|---|---|---|---|
| 1 | Migration `0008`: status, feed source fields, `source_choices`, `source_fetches`; repositories; the transition table and `advance()` | `test_migrations.py`, `test_append_only.py`, `test_domain_models.py`, `test_repo_requirements.py`, `test_repo_feeds.py`, `test_repo_source_choices.py`, `test_repo_source_fetches.py` | pytest |
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
| 12 | Migration `0009`: the five statuses and the 13 moves; `requirement_scopes`; versioned `source_choices` with its guards; repositories; 3a's code moved to "the current choice" | `test_migrations.py`, `test_append_only.py`, `test_repo_requirements.py`, `test_repo_requirement_scopes.py`, `test_repo_source_choices.py`, `test_db_source_guards.py`; 3a's `test_known_source.py`, `test_s4_fetch.py`, `test_jobs.py` still pass | pytest; a trigger proof: with the new guard triggers removed, the guard tests fail |
| 13 | IntentAgent and S1 | `test_agent_intent.py`, `test_s1_scope.py`, `test_step_results.py` | pytest; `alembic upgrade head`; `0009 (head)`. **CHECKPOINT 4:** pytest summary, `alembic current`, the triggers on `source_choices` and `requirement_scopes`, the three existing choices read back as version 1 `selected`, the carried item 1 tests by name |
| 14 | SourceDiscoveryAgent and S2; the sixth synthetic source | `test_agent_source_discovery.py`, `test_s2_sources.py`, `test_source_catalogue.py` | pytest |
| 15 | S3 pick: the acceptance names the conditions; decline | `test_s3_pick.py` | pytest |
| 16 | Release: on request, and by the N-day rule in the fetch; the setting; the daily job skips a feed nobody selects | `test_release.py`, `test_s4_fetch.py`, `test_jobs.py`, `test_settings.py` | pytest |
| 17 | Consumer API: the scope, sources, pick, decline and release flow, with one commit-visibility test for each of the six endpoints | `test_api_consumer_sourcing.py`, `test_commit_visibility.py` | pytest; each commit-visibility test shown failing with its commit removed. **CHECKPOINT 5:** pytest summary and its time, the ten outcome tests by name, the `BUILD_PLAN.md` done-when test's output, the release tests by name |
| 18 | DiscoveryAssistant: agent, step, endpoint, its commit-visibility test | `test_agent_discovery_assistant.py`, `test_api_consumer.py`, `test_commit_visibility.py` | pytest; the commit-visibility test shown failing with its commit removed |
| 19 | consumer-ui: scope, sources, conditions, pick, release and the history, the assistant | none | build; browser run, rows E to K |
| 20 | Documents: `CLAUDE.md`, `BUILD_PLAN.md`, `README.md`, `.env.example` | none | documents read back |
| 21 | Review against `CLAUDE.md`'s seven rules, option A (section 5.6), the PostgreSQL 14 list and this plan | none | findings in section 17. **CHECKPOINT 6:** section 13 results and the screenshots. Phase 4 is not started |

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
- **3b:** rows E to K. For row E, check that Pick stays disabled until "I accept these conditions" is ticked. For row J, check that the history shows version 1 selected, version 2 released and version 3 selected.

## 14. Not in phase 3

- Step 02 profiling, DuckDB, sign-off, registration, licence fields on a feed (phase 4).
- Rejecting a candidate feed, and telling the requester (phase 4).
- The glossary builder and mapping (phase 5); rules and the run engine (phase 6).
- Loads from the daily fetch, arrival and structure checks, "Ready" (phase 7).
- Any real source, any internet access, Azure adapters (phase 8).
- Background model calls and a job queue.
- A notifications view in either UI.
- Editing a proposed scope; an access-request workflow; grant screens.
- Any backend approval of a source, a backend-ui Approvals view, `/api/amaze/approvals` (your decision 1).
- The committed Playwright test (phase 7).

## 15. Risks

1. **Model calls run inside requests.** The mock answers at once; Azure OpenAI may take many seconds, and the request waits. Production needs a queue. Phase 8.
2. **The first fetch also runs inside a request.** Fine for a local file; a slow real source would hold the request.
3. **The file is stored before the database commit.** If the commit fails, a blob is left with no row. It is harmless and is overwritten by the next fetch of that day.
4. **One daily job fetches every feed in turn.** Many feeds or one slow source make the run long. Per-feed times are not possible without a change.
5. **The catalogue is a file, and "known" is a flag in it.** A real source registry, and who may mark a source known, are not designed here.
6. **The mock's rules are simple.** They prove the path, not the quality of an answer. There is no gold set for S1 or S2; phase 5 brings the first one.
7. **Proposals of sources that were neither picked nor declined stay `pending` for good.** A list of pending proposals must be read with the requirement's status.
8. **Every click on "Ask the assistant" adds a `proposal_log` row.** The table is append-only; retention is a production question.
9. **The transition table is enforced in code, not in the database.** Hand-written SQL can still set any listed status. A trigger could enforce the table too; not planned.
10. **Tests start a local HTTP server on a thread.** Warnings are errors, so an unclosed socket fails the run. If this proves unstable, the server moves to a session fixture.
11. **Two transactions in one request** (the selection, then the fetch) on the tests' shared session, as in phase 2 risk 10. **Closed after 3a** by `tests/test_commit_visibility.py` (section 17).
12. **"Find a source" is started by the requester's browser.** If the page is closed first, the requirement stays `defined` until they press the button.
13. **Under option A nobody checks an external approval.** The requester states they hold it by accepting the conditions; Amaze records the acceptance but does not verify it. The backend team's review in phase 4 is the control.
14. **A scope can hide a fitting source.** A `private` scope never lists a public source. The way out is a note and a new scope.
15. **The N-day count reads the dates of the fetch rows.** A day on which the daily job did not run at all (the laptop was off) is not a failed day, so N failed days can take longer than N calendar days.
16. **With N set to 1, a pick of a source that is down is released in the same request** as its first fetch. The requester sees the release message at once. That is what the setting asks for.
17. **A released source is closed to that requirement for good**, even if it answers again later. Another requirement can still select it. Reopening it would need a new rule.
18. **An idle candidate feed is not fetched.** If the backend team wants its data anyway, nothing fetches it until a requirement selects it again or it is registered (phase 4). A registered feed is always fetched (Q4).
19. **"Known source?" is not run again after a release.** A second known source that also declares the data is found only through S1 and S2.
20. **The accepted conditions are compared as a whole.** A change to a source's wording in the catalogue makes every open list of that source ask for acceptance again. That is intended, but it can surprise.
21. **Seven more commit-visibility tests add about 7 to 10 seconds** to every test run. If that hurts, the schema can be built once per test file: about 2 to 3 seconds, at the price of tests that share committed rows. **Measured at checkpoint 5: about 14 s for the six built so far** (section 17).
22. **An agent's input file can outlive a rolled-back transaction** (an orphan with no `proposal_log` row). Harmless in the PoC; production needs a cleanup sweep (section 17, after checkpoint 4).

## 16. Carried forward

Notes only; nothing here is built in phase 3.

**Part 3b: the three notes recorded during 3a are now planned, not carried.**

| Note (2026-11-14) | Now in |
|---|---|
| A selected source that stays `source_down` had no route back (checkpoint 1) | assumptions 51 to 55; section 5.1 |
| `source_choices` becomes versioned per requirement (your decision at checkpoint 2) | assumptions 49 and 50; section 6 |
| Commit-visibility tests, one per endpoint that commits, and their added time (after 3a) | assumption 57; risk 21 |

**Phase 4**

1. Step 02 starts from a candidate feed's latest `fetched` row in `source_fetches`.
2. When a candidate feed is rejected, the known-source check and S2 must skip its source, and the requester must be told. The requirements that select it should be released with a third reason; `0009`'s reason list has only the two of phase 3.
3. Registration copies the licence from the catalogue entry; `duplicate_linked` meets the "one feed per source" rule of assumption 11.
4. The backend team's review of the data is where access is restricted (your decision 1). It should show the conditions the requester accepted (`source_choices.accepted_conditions`, on the requirement's current choice).
4a. **Registered feeds are regular feeds from phase 4 on** (Q4, assumption 54). The daily job already fetches a registered feed whether or not a requirement selects it; registration (step 04) is the moment a candidate feed stops depending on a requirement's selection. Phase 4 must keep `FeedRepository.list_to_fetch()`'s two cases and test registration against it.
**Phase 5**

5. The glossary builder runs in the background. Phase 3 deliberately has no background runner, so phase 5 must choose one.

**Phase 7**

6. A daily fetch of a registered feed becomes a load (step 12). Today it only stores the file and a `source_fetches` row.
7. The Playwright story covers S1 to S4, including the acceptance of a source's conditions.

**Phase 8**

8. Email and Teams notifications through an outbox (phase 2 plan, section 16).
9. Model calls and the first fetch through a queue (risks 1 and 2).
10. The Azure Functions timer runs the same single daily job.
11. `SOURCE_HTTP_ALLOWED_HOSTS` is widened only for approved sources.
12. `AzureOpenAIProvider` uses the same prompt files; compare its answers with the mock's on rows E to K.

## 17. Results and deviations (recorded during implementation)

### Tasks 1 to 3 (checkpoint 1)

Tests: 632 at the start, 738 at checkpoint 1. All pass, none skipped, no warnings.

**A bug the tests caught in `0008` before it was applied.** The first `source_fetches` checks let a `source_down` row with no reason and a `fetched` row with no size through. `length(btrim(NULL)) > 0` and `NULL > 0` are NULL, and a check that evaluates to NULL passes. Both checks now spell out `IS NOT NULL`, and a raw-SQL test covers the NULL size. Phase 2's `btrim` checks were reviewed: all are on NOT NULL columns.

**Two test mistakes, fixed:**
- a row with only a connector breaks two feed checks, and PostgreSQL reports one of them;
- a `rollback()` inside a test undid the test's own setup.

**Deviations from the plan:**

1. **`set_status()` was removed in task 1**, with its tests rewritten against the transition table (change 4). `test_every_move_not_in_the_table_is_refused` runs 34 moves; `test_every_move_in_the_table_is_allowed` runs the 2 moves.
2. **`UnknownSource` lives in `ports/connectors/catalogue.py`**, next to the catalogue, not in `core/repositories/errors.py`: `ports` does not import `core`. `SourceConditions` is there too; `core/domain/sourcing.py` imports it.
3. **Source codes must start with `syn_`.** The loader refuses any other, as the demo seed does with `demo_` (rule 6).
4. **Two named errors beyond the plan:** `DuplicateFeedSource` (one feed per source) and `RequirementAlreadyHasSource` (one choice per requirement).
5. **`find_known_source()` checks for status `defined` itself**, so a `no_known_source` on a requirement that has moved on is refused too, not only a move that `advance()` would refuse.

**Application database:**

```
alembic upgrade head     Running upgrade 0007 -> 0008     exit 0
alembic current          0008 (head)
python -m core.seed      glossary seed: 0 inserted, 33 already present
```

The 4 new triggers are present: `source_choices` and `source_fetches` each have `append_only` and `no_truncate`. `feeds` has its 3 new checks and `uq_feeds_source_code`. The 4 requirements are still `defined`. No constraint name repeats its prefix.

### Tasks 4 to 6 (checkpoint 2)

Tests: 769 pass, none skipped, no warnings; the full suite was run twice before the last test was added.

**Proofs that tests can fail.** In `s4_fetch.py`, both notification conditions were changed to always notify. Then `test_file_ready_is_sent_on_the_first_fetched_only` and `test_source_down_is_sent_once_per_run_of_failures` failed, and the conditions were restored with the Edit tool.

**Deviations from the plan:**

1. **The HTTP connector no longer follows redirects.** A redirect could lead to a host the allow-list refuses, so a 3xx answer is `source_down`. Test first: `test_a_redirect_is_not_followed_and_is_not_a_file`.
2. **`HttpConnector`'s default allowed hosts are this machine**, not "any host". The phase 0 tests on the reserved name `data.example.test` (a mock transport) now pass that host explicitly.
3. **`source_fetches.blob_ref` holds the `BlobStore` key** (`raw/...`), not the adapter's reference (`local://raw/...`). `BlobStore.get()` takes a key, and a key works with any adapter.
4. **An unsafe file name is cleaned into one key segment** (`.hidden:prices?.csv` becomes `hidden_prices_.csv`).
5. **The job's dependencies are one `DailyFetch` object.** The API builds it from the settings at start-up; tests put their own in `app.state.daily_fetch`.
6. **Phase 0's `test_scheduler_runs_with_the_api_and_has_no_jobs`** now expects the one daily job; it is renamed `..._holds_only_the_daily_fetch`.

**The four 3a outcome tests, by name:** `test_known_source_when_one_known_source_declares_every_element`, `test_no_known_source_when_none_covers_or_a_term_was_ticked`, `test_fetched_stores_the_file_and_moves_the_requirement_to_onboarding`, `test_source_down_is_recorded_and_the_requirement_waits`: all pass.

**The restart test:** `test_after_a_restart_the_job_fetches_feeds_read_from_the_database` starts the API, stops it, creates a selected feed while it is down, starts it again, and runs the job. The fetch row is `fetched` and the requirement is `onboarding`.

**Audit: check constraints on nullable columns (migrations 0002 to 0008).** A check that evaluates to NULL passes. Ten checks involve a nullable column:

| Check | Is NULL handled? |
|---|---|
| `consumer_requests.ck_consumer_requests_requirement_matches_outcome` | Yes: `IS NOT NULL` on the nullable column; `outcome` is NOT NULL. Never NULL |
| `feeds.ck_feeds_connector` | NULL passes, **on purpose**: a feed without a source |
| `feeds.ck_feeds_connector_has_source_ref` | Yes: `IS NULL` / `IS NOT NULL` only |
| `feeds.ck_feeds_registered_at_matches_stage` | Yes: `IS NOT NULL`; `stage` is NOT NULL |
| `feeds.ck_feeds_registered_has_owner` | Yes: `IS NOT NULL`; `stage` is NOT NULL |
| `feeds.ck_feeds_source_and_connector_together` | Yes: `IS NULL` on both sides |
| `glossary_elements.ck_glossary_elements_proposal_unless_seed` | Yes: `IS NULL`; `created_by` is NOT NULL |
| `proposals.ck_proposals_decided_at_matches_status` | Yes: `IS NULL`; `status` is NOT NULL |
| `source_fetches.ck_source_fetches_down_has_a_reason` | Yes, since the fix before `0008` was applied (checkpoint 1) |
| `source_fetches.ck_source_fetches_fetched_has_a_file` | Yes, since the same fix |

**No check needs fixing, so no migration was added.** The application database holds the fixed `source_fetches` checks. To keep it that way, `test_every_check_on_a_nullable_column_is_reviewed` lists the ten with their reasons and fails on any new check on a nullable column until it is reviewed. It was shown failing with one entry removed.

### Tasks 7 to 9 (checkpoint 3)

Tests: 779 pass, none skipped, no warnings.

**Task 7, consumer API.** `test_api_consumer_sourcing.py` has 9 tests:
- the 3a done-when test, `test_done_when_a_known_source_is_fetched_and_the_requester_sees_onboarding`: raise `volume`, Find a source, `known_source` and `fetched`, the exact message, the requirement page `onboarding`, and the candidate feed in `/api/amaze/feeds`;
- `source_down` (row B), `no_known_source` both ways (rows C and D);
- 409 on a second click, 404 for another requester's requirement, 403 and 401;
- My requests linking the requirement.

**A test gap I could not close: a missing commit after the fetch.** The API tests share one session (risk 11). If the endpoint forgot `session.commit()` after the fetch, they would still see the rows and pass. The browser run against the application database is the check for it: the fetch rows and the `onboarding` status were there after the requests ended.

**Task 8, consumer-ui.** A Requirement view (`#/requirement/<id>`); after a raise, Search calls Find a source and shows its message; My requests links each requirement and has "Find a source" for a `defined` one. One build error on the way: the new state reused the name `found`, which already held the search results. It is renamed `sourceResult`.

**Browser run** on the application database, as Riley Requester. Screenshots in `.playwright-mcp/` (git-ignored). No console errors or warnings.

| Row | Search, then Submit | Seen under the raise message |
|---|---|---|
| A | `volume` | "Your data is getting onboarded; we will notify you once it is ready." The requirement page (`phase3a-requirement.png`) shows `onboarding`, the source and its licence, and "File received" |
| B | `accrued interest` | "A source was found: Accrued interest feed. It did not answer; Amaze tries again every day." In amber |
| C | `open price` | "No known source has this data yet." |
| D | `esg score`, term ticked | "No known source has this data yet." |

Also on My requests: "Find a source" on the phase 2 requirement `volume` found the known source and reused its feed, so it is `onboarding` too (`phase3a-requests.png`). The feed registry in backend-ui shows the two candidate feeds (`phase3a-feeds.png`). The API was stopped and started again; health was `ok`.

**What the run left in the application database:** 8 requirements (5 `defined`, 2 `onboarding`, 1 `source_selected`), 2 candidate feeds, 3 source choices, 3 fetch rows (2 `fetched`, 1 `source_down`), and two stored files under `data/blob/raw/syn_exchange_volumes/`. All of it is append-only or real data, by design.

**Task 9, documents.**
- `CLAUDE.md`: layout lines for `apps/api/jobs.py`, the phase 3 step modules (and that a step receives its agent as an argument), the catalogue and connector rules, `fixtures/sources/`.
- `BUILD_PLAN.md`: phase 3 is built as 3a and 3b; the requester's acceptance is the only approval of a source.
- `README.md`: the requirement view, what `volume` and `accrued interest` do, `0008 (head)`, the phase 3 append-only tables.
- `.env.example`: `SOURCE_CATALOGUE` and `SOURCE_HTTP_ALLOWED_HOSTS`.

**A bug found while writing `.env.example`, fixed with a test first.** A relative `SOURCE_CATALOGUE` was read from the current folder, not the repository. It is now resolved against the repository, as `LOCAL_BLOB_ROOT` already is (`test_a_relative_catalogue_path_is_read_from_the_repository_not_the_current_folder`).

### Section 13 results for 3a

```
alembic upgrade head        no change (already at head)
python -m core.seed         glossary seed: 0 inserted, 33 already present
python -m core.seed --demo  demo seed: 0 inserted, 3 already present
pytest -v                   779 passed; 0 failed, 0 skipped, 0 warnings
alembic current             0008 (head)
npm ... run build           backend-ui exit 0; consumer-ui exit 0
dev.ps1                     three OK lines; health {"status":"ok", ..., "unavailable":[]}
```

### Review of 3a (task 9)

| Rule | 3a state |
|---|---|
| 1 AI proposes, people decide | No agent and no model call in 3a. A known source is selected by a rule; the decision that a source is known is a person's, recorded in the catalogue, and a known source cannot carry external approvals |
| 2 No model call in engine or entitlement | No model call anywhere in 3a. `core.steps` stays inside the import boundary; the only `ports.llm` imports are the phase 0 and 1 ones outside it. `.complete(` appears nowhere in `core` or `apps` |
| 3 Explicit negative outcomes | Four typed outcomes with named tests; the names are read from `CLAUDE.md`. A wrong status is 409, another's requirement 404; an unexpected error is rolled back, never turned into an outcome |
| 4 Every proposal logged | No proposal in 3a |
| 5 Azure behind interfaces | Connectors, blob store, scheduler and notifier are ports and FastAPI dependencies. The daily job has the shape of a Functions timer. No Azure import |
| 6 Synthetic data only | `syn_` codes enforced; the catalogue and every sample file pass the real-data checks; HTTP reaches this machine only and never follows a redirect |
| 7 Versioned, never edited in place | `source_choices` and `source_fetches` are append-only (UPDATE, DELETE and TRUNCATE refused). Requirement status moves only along the transition table |

**PostgreSQL 14:** `0008` uses `ADD COLUMN`, unique and check constraints, `BIGINT`, indexes and the phase 1 trigger function. Nothing newer.

**3a done-when (section 1):** all five met. Item 1: the known-source tests. Item 2: the done-when API test and the browser. Item 3: the `source_down` tests and row B. Item 4: the restart test. Item 5: 779 pass.

**Nothing from 3b or later phases was built:** no agent, no S1 to S3, no `agents/base.py`, no profiling.

**Findings left open (none blocks 3a):**

1. **The three `dev.ps1` windows are still open.** Close them before running `dev.ps1` again.
2. **The API tests cannot see a missing commit** (above, and risk 11). Closed after approval; see below.
3. **A `source_down` requirement has no way back yet.** Section 16 records your decision for 3b.

### After 3a approval: commit visibility (risk 11 closed)

`tests/test_commit_visibility.py`, 2 tests. Each uses the app's real session factory on `amaze_test` and reads the result on a separate connection, which sees only committed rows:

1. `test_find_source_commits_the_selection_and_the_fetch`: the API with no session override, so each request gets its own session from `get_db_session`. Raise `volume`, then Find a source. Then, on a separate connection: requirement `onboarding`, the `source_choices` row, a `fetched` row, and the "File ready to profile" notification.
2. `test_the_daily_job_commits_each_fetch`: `DailyFetch` with `ports.db.get_sessionmaker` on `amaze_test`. Then, on a separate connection: requirement `onboarding`, one `fetched` row, one notification.

**Each test gets its own schema** in `amaze_test`, migrated from scratch and dropped afterwards. The rows are really committed, and the append-only tables refuse DELETE, so removing them row by row would mean working around the triggers. Dropping the whole schema does not, and the test fixture already does the same to `public` on every run. No schema is left behind.

**Shown failing with the commit removed, then restored:**

| Commit removed | New test | The shared-session tests |
|---|---|---|
| `session.commit()` after the fetch in `find_source` | failed: `[('source_selected',)] == [('onboarding',)]` | all 8 in `test_api_consumer_sourcing.py` still passed |
| `session.commit()` per feed in `DailyFetch.run` | failed: `[('source_selected',)] == [('onboarding',)]` | all 5 in `test_jobs.py` still passed |

The second column is the gap risk 11 described; the new tests close it. Both lines were restored with the Edit tool; `git diff` of `apps/` is empty. Tests: 781 pass, run twice.

### Tasks 10 to 13 (checkpoint 4)

Tests: 781 at the start of 3b, 995 at checkpoint 4. All pass, none skipped, no warnings (about 38 s).

**A bug the tests caught in `0009` before it was applied.** `ck_source_choices_pick_has_its_acceptance` was first written `(pick) = (proposal_id IS NOT NULL AND accepted_conditions IS NOT NULL)`. A known-source row or a release with a proposal but no conditions gives `false = false` and passed. Now each column is tied to "pick" on its own. `test_a_known_source_row_has_no_proposal` and `test_a_release_names_the_requester_only_when_asked` caught it.

**Two test mistakes, fixed:** a row that broke two checks (PostgreSQL names only one), and a JSON list that the test helper did not encode.

**Proofs that guards can fail** (each removed with the Edit tool, the tests run, then restored; `grep PROOF` finds nothing):

| Guard removed | Tests that failed |
|---|---|
| `Agent.__init_subclass__` refusing `run()` | `test_a_subclass_that_defines_run_is_refused_at_class_creation` |
| the four new `source_choices` triggers | 11: every version, order, pick-proposal and requester test in `test_db_source_guards.py`, the `proposal_id` NULL case, and `test_database_refuses_a_second_row_with_the_same_version` |
| the three new `requirement_scopes` triggers | 5: the approved-proposal test, the three "holds exactly what was proposed" cases, the own-requester test |
| `decide_scope`'s own-requester check | `test_only_the_requirements_own_requester_decides` |

The tests that still passed without the `source_choices` triggers are the ones the check constraints and the partial unique index decide, as designed.

**Deviations from the plan:**

1. **The prompt loader is `agents/prompt_loader.py`, not `agents/prompts.py`:** a module named like the `prompts/` folder beside it is confusing to import.
2. **`Agent.run()` stores the input after the model call**, not before: render, call, store, log, propose. A failed call then writes nothing at all, not even a blob (`test_a_failed_model_call_writes_nothing` checks the blob store too).
3. **A subclass returns `AgentProposal`s (kind, payload, links); the base class adds the agent's autonomy and approver role**, so a subclass cannot state them differently from its declaration.
4. **The registry holds a sha256 of every prompt file** (line endings read as LF). An edited prompt file fails `test_every_prompt_file_is_hashed_in_the_registry_and_unchanged` until it is saved as a new version.
5. **Mock rules are keyed by prompt id, for any version.** A new prompt version that changes the input's shape needs its rule changed with it.
6. **Guards beyond assumption 50, all with tests:** a fourth trigger function, `amaze_require_requirement_requester()`, checks that `source_choices.decided_by` and `requirement_scopes.confirmed_by` are the requirement's own requester (assumption 29, in the database too). The scope trigger also checks that the scope row holds exactly the proposal's visibility, provider and channels (the requester confirms, never edits). Two more checks: `ck_source_choices_conditions_are_an_object` and `ck_source_choices_version`.
7. **Trigger names carry a letter** (`trg_source_choices_a_version` ... `_d_requester`): PostgreSQL runs triggers of one event in name order, so the order is fixed and the error messages are predictable.
8. **`propose_scope(..., again=True)`** is the "ask again with a note" path (from `scope_not_confirmed` or `no_source_found`); without it, only from `defined`. The status is checked before the agent is called.
9. **`POST .../scope/decision` needs no proposal id:** `decide_scope` decides the newest pending scope proposal. Only one can be pending, because the status moves on with it.
10. **Two named errors beyond the plan:** `NotTheRequester` (the API will answer 404) and `NothingToDecide` (422).
11. **The 0009 migration tests run in a scratch schema** that is dropped afterwards, because their rows sit in append-only tables and cannot be deleted.
12. **Until task 17, the requirement page shows no source when the current choice is a release.** Task 17 adds the release messages.

**Carried item 1, by name:** `test_a_subclass_that_defines_run_is_refused_at_class_creation`, `test_run_logs_the_call_before_it_creates_proposals`, `test_every_proposal_points_at_the_log_row`, `test_a_failed_model_call_writes_nothing`, `test_a_read_only_agent_cannot_create_proposals`, `test_no_direct_llm_calls.py::test_complete_is_called_only_in_agents_base`: all pass.

**Application database:**

```
alembic current          0008
alembic upgrade head     Running upgrade 0008 -> 0009     exit 0
alembic current          0009 (head)
```

Triggers on `source_choices`: `trg_source_choices_a_version`, `_b_approved_pick`, `_c_proposal_match`, `_d_requester`, `_append_only`, `_no_truncate`. On `requirement_scopes`: `_b_approved_proposal`, `_c_proposal_match`, `_d_requester`, `_append_only`, `_no_truncate`. The three existing choices read back as version 1, `selected`, `known_source`, with no reason, proposal, conditions or decider. Requirements: 5 `defined`, 2 `onboarding`, 1 `source_selected`, as before.

### Tasks 14 to 17 (checkpoint 5)

Tests: 995 at checkpoint 4, 1077 at checkpoint 5. All pass, none skipped, no warnings. Full run: 64 s (47 s before task 17's API and commit-visibility tests).

**Recorded after checkpoint 4 (your note): an orphan input file.** `Agent.run()` stores the input file (`agent-inputs/<agent>/<id>.json`) after the model call and before the `proposal_log` row is flushed. If the caller's transaction later rolls back (a later step fails, or the commit fails), the file stays with no log row pointing at it. Harmless in the PoC: nothing reads an input file without its log row. Production needs a cleanup, for example a sweep that removes `agent-inputs/` keys that no `proposal_log.input_ref` names, after a grace period. Risk 22.

**The ten outcome tests, by name, all pass:** the four of 3a (checkpoint 2) and `test_scoped_when_the_requester_confirms`, `test_scope_not_confirmed_when_the_requester_rejects`, `test_sources_listed_one_proposal_per_source`, `test_no_fit_when_no_source_in_scope_covers_the_request`, `test_source_picked_when_the_requester_accepts_the_conditions`, `test_approval_missing_when_the_conditions_are_not_accepted_and_nothing_is_fetched` (two cases: no acceptance; another source's conditions). `test_all_eight_s1_to_s4_outcomes_have_a_union` checks the eight S1 to S4 names against `CLAUDE.md` in order.

**The done-when test** `test_done_when_picking_a_source_hands_a_fetched_file_to_step_02` (row E): raise `open price`; Find a source proposes a public scope; confirm; two sources listed (backup first, then vendor, with its external approval); pick the vendor without an acceptance: `approval_missing`, the exact message, no feed, status `awaiting_source_choice`; pick with the conditions: `source_picked`, `fetched`, the stored file, "Your data is getting onboarded; we will notify you once it is ready.", status `onboarding`, and the candidate feed in `/api/amaze/feeds`. Passes.

**The Q4 tests:**

| Test | Passes | Made to pass by |
|---|---|---|
| `test_jobs.py::test_the_job_always_fetches_a_registered_feed` | yes | already true of 3a's job, which fetched every feed with a connector; written in task 16 so that `list_to_fetch()` cannot lose the case |
| `test_jobs.py::test_the_job_skips_a_candidate_feed_that_no_requirement_selects` | yes | task 16 (`80a8704`): `FeedRepository.list_to_fetch()` |

Also `test_a_new_selection_wakes_the_feed` and `test_repo_feeds.py::test_list_to_fetch_leaves_out_a_candidate_feed_no_requirement_selects`.

**Proofs that guards can fail** (each removed with the Edit tool, run, restored; `grep PROOF` finds nothing in the source):

| Guard removed | Tests that failed |
|---|---|
| S2: dropping a source the agent invents | `test_a_source_the_agent_invents_is_dropped`, `test_an_invented_or_repeated_source_is_dropped` |
| S3: accepted conditions must equal those in force | the `conditions_changed` case, `test_approval_missing_when_the_conditions_changed_since_the_list`, `test_picking_again_needs_a_new_acceptance` |
| `list_to_fetch()`: "currently selected" | `test_the_job_skips_a_candidate_feed_that_no_requirement_selects`, `test_a_new_selection_wakes_the_feed`, `test_list_to_fetch_leaves_out_a_candidate_feed_no_requirement_selects` |
| N-day rule: only `source_selected` | `test_an_onboarding_requirement_is_never_released` |
| N-day rule: counting from the selection | `test_days_before_the_selection_do_not_count` |

**Commit visibility: each commit removed** (a temporary helper ran `_in_transaction` with `session.commit` made a no-op, for one endpoint at a time):

| Commit removed | Its test | Others that failed | Shared-session API tests |
|---|---|---|---|
| `POST .../scope` | `test_scope_commits_the_proposal` failed | none | all passed |
| `POST .../scope/decision` | `test_scope_decision_commits_the_scope` failed | the 4 whose flows pass through it | all passed |
| `POST .../sources` | `test_sources_commits_one_proposal_per_source` failed | pick, decline | `test_a_wrong_status_is_409` failed, as an effect of the proof: the next request's 409 rolled back the uncommitted list on the shared session |
| `POST .../pick`, its fetch commit | `test_pick_commits_the_choice_and_the_fetch` failed | the 3a Find a source test | all passed |
| `POST .../decline` | `test_decline_commits_the_rejection` failed | none | all passed |
| `POST .../release` | `test_release_commits_the_released_row` failed | none | all passed |
| Find a source, first commit | the S1-branch tests (scope, decision, sources, pick, decline) failed | | all passed |

**A correction to assumption 57.** It said that removing Find a source's first commit "still fails the existing test". It does not: for a known source the fetch's commit commits the selection too, so `test_find_source_commits_the_selection_and_the_fetch` passes without it. The S1-branch tests above catch it. For the same reason a pick's own commit is covered by the fetch's commit; the commit that the pick test protects is the fetch's.

**Added test time: about 14 s for the six new commit-visibility tests** (1.5 to 4.3 s each, mostly building the schema), against the 7 to 10 s planned (risk 21). Q5 kept one schema per test.

**Deviations from the plan:**

1. **Each candidate the S2 agent sees carries its catalogue conditions and code-computed coverage;** the agent copies them from its input into each proposal. The output schema forbids extra fields, so a model cannot set them.
2. **With no source in scope, S2 calls no model** (`no_fit`, `nothing_in_scope`, no log row).
3. **Listed sources show the catalogue's conditions now** (the ones a pick must accept), not the copy in the proposal.
4. **`approval_missing` with reason `conditions_changed` also covers an acceptance of other conditions**, not only a change since the list.
5. **`Released` holds `source_code` and `source_name`** instead of a `SourceSummary`, so it survives a source leaving the catalogue; it also names the requirement.
6. **`fetch_source` takes `release_after_days` and `catalogue`;** None means no rule, so 3a's callers are unchanged. `DailyFetch` gets both from the settings.
7. **`FeedRepository.list_with_source()` is removed** for `list_to_fetch()`. 3a's `test_one_failing_feed_does_not_stop_the_others` now registers its broken feed, since an unselected candidate is no longer fetched.
8. **One `_in_transaction` helper in the consumer router** commits each step or rolls it back. `NotTheRequester`, `ProposalForAnotherRequirement` and `ProposalNotFound` give 404; `RequirementNotInStatus` and `ProposalAlreadyDecided` give 409; `StepInputError` gives 422.
9. **Find a source with no known source now answers "No known source has this data yet. Confirm where Amaze should look for it."** and returns the scope. 3a's test is now `test_no_known_source_proposes_a_scope`.
10. **Messages the plan did not word, now fixed in `requirement_page.py`:** scope to confirm, scope not confirmed, scope confirmed, pick a source, and "No listed source is left. Add a note and ask for a new scope." (every listed source declined).
11. **`test_n_is_the_setting` also checks that `DailyFetch.from_settings` reads the setting**, and a bad value is refused by name (`test_a_bad_source_down_release_days_is_refused_by_name`).

**Application database:** no migration in tasks 14 to 17; `alembic current` is `0009 (head)`. The catalogue gained `syn_backup_eod_prices` (no file, on purpose).
