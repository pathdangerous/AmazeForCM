# Phase 5 plan — Glossary builder and mapping (steps 05–06)

**Status:** written on 2026-11-16; **approved on 2026-11-16** with the decisions under "Open questions" (section 4) and three plan changes (assumptions 36 to 38, section 13, risk 12). Being implemented.
**Date:** 2026-11-16. **Branch:** `phase-5`, created from `main` at `f55eb6c`. **Scope:** phase 5 of `BUILD_PLAN.md`, the phase 5 notes of the phase 4 plan (section 16) and of the phase 3 plan (section 16, notes 5 and 5a), and the seven carried notes of the planning request. Local only; nothing is pushed.

**One part, four checkpoints.** Phase 5 is two steps, two agents (and a read-only evaluator), one background runner and one migration. I stop at each checkpoint.

## 1. Goal and "done when"

**Phase 5 is done when all nine are true:**

1. **Approved mappings are stored as a versioned configuration.** This is `BUILD_PLAN.md`'s done-when, proved by `test_api_amaze_mapping.py::test_done_when_approved_mappings_are_stored_as_versioned_configuration` (the steward approves: version 1; proposes again and approves another candidate: version 2; version 1 is still there) and in the browser (rows Q to S).
2. The GlossaryBuilderAgent runs in the background on a registered feed's new columns only, and its three outcomes exist: `proposed`, `duplicate_alias`, `conflict_to_owner`. The glossary owner decides each proposal. backend-ui shows a progress banner.
3. The MappingAgent proposes, for a registered feed's columns, the standard model element each holds; its three outcomes exist: `mapped`, `tie_to_steward`, `no_element_back_to_05`. The data steward decides.
4. The background runner keeps the model-call pattern (no connection held), survives an API restart and never runs one feed twice at once; each has a named test (section 5.5).
5. No agent is sent the whole glossary: the builder, the mapping agent and the DiscoveryAssistant each get at most 10 candidate elements per column or term.
6. `fixtures/gold/mappings.json` holds 31 cases, hard ones included, and `python -m agents.evaluate_mappings` scores any `LLMProvider` against it. The mock's score is pinned by a test.
7. Each of the six outcome names has a named test (section 5.4), each committing endpoint and the job has a commit-visibility test, and every race test runs through the app with an event, not a sleep.
8. Every rule-1 claim of this phase has a database guard, each with a raw-SQL test shown failing without its guard (section 6). A proposal's status moves only pending → approved or rejected, and TRUNCATE of `proposals` is refused.
9. `pytest` passes with nothing skipped and no warnings.

## 2. State at the start (checked on 2026-11-16)

| Item | Found |
|---|---|
| Git | `main` = `f55eb6c`. `phase-5` created from it. One untracked file, `.env.main`; it is not mine and is not touched |
| Tests | 1396 collected and passing. **Three full runs today, one after the other: `1396 passed in 107.95s`, `113.44s`, `113.38s`** (spread 5.5 s, 5%). The 114 to 173 s swing recorded earlier today (phase 4 plan, section 17) did not come back; no dev server or browser was running during these three. Its cause is not established |
| Schema-per-test cases | 40 test functions use `own_schema` (16 commit-visibility, 24 concurrency). In the three runs each costs a setup of 0.55 to 1.0 s, a call of 0.6 to 2.5 s and a teardown up to 0.6 s: **about 1.5 to 4 s each** |
| Database | `alembic current`: `0015 (head)`. PostgreSQL 18.7 |
| Feeds in the application database | 7: **3 registered** (`syn_exchange_volumes`, `syn_instrument_lifecycle`, `syn_open_esg_scores`), 2 rejected, 2 candidates with no file. Each registered feed has a profiled (not spec-only) latest profile |
| New columns in those three | Only `syn_open_esg_scores` has any: `score_year` (undeclared) and `esg_score` (declared as the other field "esg score"). The other two files hold declared glossary elements only |
| Glossary | 33 elements, all seed, version 1 each. No element has ever come from a proposal |
| `mappings`, `rules` | Empty. `MappingRepository` (phase 1) writes a level 1 mapping (one source field to one element) from any approved `mapping` proposal; it checks the feed only when the proposal names one |
| Proposal kinds | `glossary_element` and `mapping` exist since `0002`. No proposal of either kind exists. 31 proposals of the other kinds |
| Guards on configuration | `0005`: a `mappings`, `rules` or non-seed `glossary_elements` row needs an approved proposal of the matching kind, an approved row for every required role and no rejection. **Nothing checks that the row holds what the proposal proposed, or that the mapping's feed is the proposal's** |
| Guards on `proposals` | `trg_proposals_decision_only` (`0015`): an UPDATE may change `status` and `decided_at` only; DELETE refused. **It does not restrict which status follows which, and TRUNCATE is not refused** (phase 4, section 17, deviation 4 of the last block) |
| Tests that write configuration | 22 `add_version` lines in six test files and 6 raw `INSERT INTO mappings` or `glossary_elements` in three. They use `approved_proposal(kind=...)` with the payload `{"note": "synthetic"}` and no feed |
| DiscoveryAssistant | `assist_input()` sends all 33 elements on every call (`core/steps/s17_discover.py:143`) |
| Background work | None. The Scheduler port has `schedule_daily`, `run_now`, `remove`, `list_jobs`; the one job is the daily fetch at 06:00 UTC. A request does every model call |
| Catalogue hint | Each of the 8 entries has `columns` (file column to declared field). The file columns are the sources' own names (`instr_id`, `traded_qty`, `px_close`) |
| A profile after registration | `profile_plan()` refuses a feed that is not a candidate, so a registered feed's latest profile is the one that was signed off and does not change in phase 5 |
| `fixtures/gold/` | Exists, empty |
| Amaze API | Glossary and feed reads; the four phase 4 actions. No proposal list, no decision endpoint for backend roles |
| backend-ui | Four views (Status, Glossary, Feed registry, Feed). The Glossary view is read only. No banner |
| The deck | Not in the repository; "Glossary mapper" appears nowhere in it. `CLAUDE.md`, `BUILD_PLAN.md` and `core/domain/proposal.py` say MappingAgent |
| Plugins | Not loaded. The method is followed by hand, as `CLAUDE.md` says |

## 3. What you fixed, and where this plan answers it

**Scope you set:** `BUILD_PLAN.md` phase 5, with the outcome names of `CLAUDE.md`.

**Patterns already set (followed, not reopened):**

| Pattern | Where it applies here |
|---|---|
| `Agent.run()` is the only model path; its call row is committed at once | assumptions 13, 22, 37 |
| No connection during a model call: read, end the transaction, call, lock and check again, write | assumptions 10, 12, 25; `test_concurrency.py` |
| Ordered `approver_roles`; a subclass overrides only `to_proposals` | assumptions 13, 21 |
| Typed outcomes; `LLMUnavailable` 503; named conflicts 409; never a bare 500 | assumptions 19, 25, 28, 42 |
| One lock order, with `populate_existing` | assumption 49 |
| One commit-visibility test per committing endpoint or job, through the API, happy path only | assumption 45 |
| Race tests through the app with an event, not a sleep | assumption 46 |
| A database guard for every rule-1 claim, with a raw-SQL test shown failing without it | assumptions 18, 32, 34; section 6 |
| Never edit an applied migration; nothing newer than PostgreSQL 14; synthetic data only | assumptions 39, 44 |

**The seven carried notes:**

| Note | Answered in |
|---|---|
| 1: "new columns only", and how a column is judged covered, in code | assumptions 8 and 9; open question 2 |
| 2: the MappingAgent's input is the latest profile of a registered feed; the spec's `columns` is a hint only | assumptions 4, 22 and 30 |
| 3: the background runner, with options | assumptions 10 to 12 and 19; open question 1 |
| 4: no agent is sent the whole glossary, the DiscoveryAssistant included | assumptions 5 to 7 |
| 5: `proposals` status moves only pending → approved or rejected; TRUNCATE refused | assumption 34 |
| 6: the agent's name | assumption 21; open question 3 |
| 7: the time spread, measured, and the expected added time | section 2; assumption 47 |

**The gold set must be honest:** section 8.1 and assumptions 35 to 38. The prototype got 26 of 31 under the first scoring rule, 25 of 31 under the approved one (assumption 36); the built code is measured again in task 9. Its misses are named, two of them wrong maps.

## 4. Assumptions — veto any by number

**The shape of the phase**

1. **One part, four checkpoints, no split.** Checkpoint 1: the candidate code, the gold set, the data model and its guards. Checkpoint 2: the agents, the two steps, the runner and the evaluation. Checkpoint 3: the API and the commit and race tests. Checkpoint 4: the UI, documents and review.
2. **Step 05 runs in the background; step 06 runs in a request.** `BUILD_PLAN.md` asks for a background run of the glossary builder only. The mapping call stays in the steward's request, as every model call so far (open question 9).
3. **Who acts.**
   - `glossary_owner` decides `glossary_element` proposals. `data_steward` asks for mapping proposals and decides them.
   - A glossary run starts by itself at registration; `glossary_owner` or `backend_team` may also start one for a registered feed.
   - Every other backend role reads, and gets 403 on these actions. The requester sees none of it.
4. **Both steps work on a registered feed and its latest profile** (note 2). That profile is the signed-off one and is fixed (section 2). A feed that is not registered gives 409 (`FeedNotRegistered`); a profile with no columns gives 409 (`NothingToMap`). On a spec-only profile the column types are not known, so no candidate is dropped for its type.

**Shared code: candidates, never the whole glossary (note 4)**

5. **One pure module, `core/steps/candidates.py`.** No model, no database, deterministic.
   - **Words:** `matching.words()` of phase 2: lower case; every separator cut; `px_close` is ("px", "close").
   - **Score of a name against an element:** 1.0 when its words equal the words of the element's key, name or an alias. Otherwise the best, over those phrases, of the word overlap (shared words divided by all words) and the character similarity (`difflib` ratio of the two phrases), the second counted only at 0.8 or more.
   - **Type fit:** the DuckDB type's family (string, integer, decimal, date, datetime, boolean) against the element's `data_type`: equal, or an integer column into a decimal element. Anything else does not fit.
6. **A candidate list is small.** Elements scoring 0.3 or more, best first, then by key: the first 5, plus any that tie with the fifth, **never more than 10**; the spec's hinted element is added if absent. Each candidate carries key, name, data type, definition, aliases, score, `from_hint` and `type_fits`. Tests: `test_candidates.py::test_a_list_is_never_longer_than_ten`, and `test_no_agent_input_holds_the_whole_glossary` (a 200-element synthetic glossary; every agent input checked).
7. **The DiscoveryAssistant uses the same lists** (phase 3 note 5a). `assist_input()` sends the candidates of each unmatched term (the term and its singular form), not the glossary. The input keeps its shape and the prompt file `discovery_assistant.v1.yaml` is not touched. Every existing assistant test must pass unchanged; `test_s17_discover.py::test_the_assistant_gets_candidates_not_the_whole_glossary` is new.

**Step 05: the glossary builder**

8. **"New columns only" (note 1)** are, from the feed's latest profile:
   - the file's columns the spec does not declare (`score_year`);
   - the file's columns the spec declares as one of its `other_fields` (`esg_score`, declared "esg score");
   - and, at any time, a column step 06 sent back (`no_element_back_to_05`).
   - A column the spec declares as a glossary element is never new: the source names its element, and step 06 takes that as a hint.
9. **A new column is already covered, and skipped, when one of these holds** (`candidates.covered_by()`; open question 2):
   - **by name:** its words equal the words of the key, the name or an alias of exactly one current element; for a declared other field, the field's name counts too. A phrase that two elements share does not cover. The type plays no part;
   - **by a person:** the column has a current mapping;
   - **already asked:** a `glossary_element` proposal exists for this feed and column, pending or decided. The owner's answer stands; nothing is asked twice.
   - A column sent back by step 06 skips the name rule (its name matched and its type did not), not the other two.
10. **The runner (note 3): a `glossary_runs` table, claimed with a lease.** Options in open question 1; this is the recommended one.
    - A run is one row: the feed, its profile, why it started, `queued`, `running`, `done` or `failed`.
    - **Never one feed twice at once:** a partial unique index allows one `queued` or `running` row per feed. Asking again while one is active returns that one.
    - **Claim:** one short transaction takes the oldest `queued` row, or a `running` row whose lease has passed, with `FOR UPDATE SKIP LOCKED`, sets `running`, adds 1 to `attempt`, sets `lease_until` (now plus `GLOSSARY_RUN_LEASE_SECONDS`, default 300) and commits. No lock is held afterwards.
    - **The model-call pattern:** read the input, end the transaction, `Agent.run()`, and in `before_write` lock the feed row, then the run row, and check that the run is still `running` on this attempt. Then the proposals, the run's results and `done`, in one transaction.
    - **Survives a restart:** the rows are in PostgreSQL. A run left `running` by a process that died is claimed again when its lease has passed; a fourth attempt is not made (`failed`, "gave up after 3 attempts").
    - **A late answer** (the lease passed and another worker took the run): `RunSuperseded` in `before_write`, a discard row, nothing written.
11. **What starts the worker.** The Scheduler port gains one method, `run_soon(job_id, func)`: run once, in the background, as soon as possible; calls may overlap, so `func` must be safe to run twice at once (the claim makes it so).
    - The API calls it after a commit that queued a run, and once at every start-up (so rows left by the last process are taken).
    - No polling tick. A run that waits with no worker (the process died between the commit and the kick) is taken at the next start-up or the next kick.
    - `AzureFunctionsScheduler.run_soon` is a stub; in phase 8 it is a queue-triggered function calling the same `GlossaryRuns.drain()`.
12. **One run, one feed, at most one model call.** The call covers every new column of the feed. No new column: `done`, no call, no log row (as S2 with nothing in scope). Progress is per run, not per column.
13. **`agents/glossary_builder.py`.** `GlossaryBuilderAgent`: prompt `glossary_builder`, A2, `approver_roles = ("glossary_owner",)`; it defines only `to_proposals`.
    - Input: the feed and source name, the domain names, and per new column: name, DuckDB type, null rate, distinct count, the declared other-field name if any, and its candidate elements.
    - Output (extra fields forbidden), per column, one of: a new element (key, name, domain, data type, definition); `same_as` a candidate's key; or nothing (several candidates may fit: leave it to step 06). A `same_as` that is not a candidate is dropped by code.
14. **Code names the outcome, not the agent** (`candidates.classify_element()`, pure, called from `to_proposals`). With "hits" = the column's candidates that the agent named, or whose key, name or an alias equals the new element's or the column's:
    - **`proposed`:** no hit. One option: the new element.
    - **`duplicate_alias`:** one hit, and the type fits. The "new" element is that element under another name. One option: add the column's name to it as an alias (open question 6).
    - **`conflict_to_owner`:** one hit whose type differs, or two or more hits. The options are an alias on each hit whose type fits, and the new element if its key and name are free; there may be none. Nothing is recommended.
    - Nothing answered: no proposal; the column is left to step 06 (not an outcome).
15. **Each outcome is one pending `glossary_element` proposal**, linked to the feed, with the column, the outcome, its options and the recommended one (none for a conflict). One pending proposal per feed and column: a partial unique index (section 6).
16. **Mock rule `glossary_builder`** (pure): one candidate scoring 0.5 or more whose type fits: `same_as` it. Two or more: nothing. None: a new element keyed from the column's words, named from them, typed from the DuckDB family, in the domain most of the source's declared elements belong to, with the definition "<Name>: column <column> of <source>. To be confirmed by the glossary owner."
17. **The glossary owner's decision.** Approve one option (the recommended one unless another is chosen; a conflict needs a choice) or reject with a note. No editing: an element is stored as proposed, or not at all.
    - Approve a new element: version 1 of its key. Approve an alias: the element's next version, equal to the last but for the added alias.
    - Under one advisory lock on the glossary, code checks that no other current element owns any phrase of the new version; otherwise 409 (`PhraseTaken`), nothing written.
    - A conflict with no option can only be rejected: the owner has seen it, and the column stays unmapped.
18. **What the database enforces on a glossary row** (section 6): it comes from an approved `glossary_element` proposal (`0005`); it is one of that proposal's options; a new element is version 1, an alias the next version and otherwise equal to the last; `created_by` is the proposal's `glossary_owner` approver; one row per proposal.
19. **A run that fails.** `LLMUnavailable` or any other error: the run is `failed` with the reason, nothing else is written, and the banner offers "Run again". Not retried by itself.
20. **Who is told, in-app.** The glossary owners: once per run that leaves proposals. The data stewards: when an element or alias from a feed's column is approved ("can now be mapped").

**Step 06: mapping**

21. **The agent is `MappingAgent`** (note 6; open question 3): prompt `mapping`, A2, `approver_roles = ("data_steward",)`. It is the name in `CLAUDE.md`, `BUILD_PLAN.md`, the proposal kind and the table; "Glossary mapper" reads as part of step 05; and the name is written to the append-only `proposal_log`, so it cannot be changed later.
22. **Input (note 2):** the feed's latest profile: per column to propose, name, DuckDB type, null rate, distinct count, its candidates (assumption 6) and, where the spec's `columns` names an element, that element as a candidate marked `from_hint`. The hint is one candidate among others; a hint whose type does not fit is dropped like any other by the mock, and the gold set has that case.
    - Columns to propose: every profile column with no current mapping and no pending mapping proposal; or one named column ("Propose again").
    - One model call for the feed's columns; one `mapping` proposal per column that gets an element.
23. **Output, and what code keeps.** Per column: zero, one or several element keys, best first, and a reason. Code drops any key that was not a candidate (the agent never invents an element), any element the steward already rejected for this column, and anything beyond the first 10.
    - no key left: **`no_element_back_to_05`**, no proposal;
    - one: a pending proposal that recommends it (`MappingProposed`, not an outcome: nobody has decided);
    - several: **`tie_to_steward`**, a pending proposal with no recommendation.
24. **`mapped` means stored.** The steward's approval writes the mapping and returns `mapped`; a tie the steward settles ends as `mapped` too. A pending recommendation is not called mapped. A rejection is a recorded decision, not an outcome name.
25. **The model-call pattern.** `mapping_input()` reads; the API ends the transaction; `Agent.run(..., before_write=...)`; in `before_write` the step locks the feed row and checks that it is still registered and that no input column got a pending proposal meanwhile, otherwise `MappingAlreadyProposed`: 409, a discard row, nothing written. `LLMUnavailable`: 503, nothing written.
26. **Back to 05.** For each `no_element_back_to_05` column that the glossary builder has not been asked about, a glossary run is queued for the feed (reason `sent_back`), in the same transaction. A column the owner already answered is not sent again.
27. **A rejected element is not proposed again** for that column of that feed. When every candidate was rejected the column is `no_element_back_to_05`.
28. **The steward's decision.** Approve one candidate (the recommended one unless another is chosen; a tie needs a choice) or reject with a note.
    - Refused, nothing written: a choice that is not a candidate (422); an element another column of the feed is currently mapped to (409, `ElementAlreadyMapped`); a decided proposal (409); a user without the role (403).
    - Written in one transaction, after the feed row is locked: the approval and the mapping, next version for that column.
29. **Versioning.** "Propose again" on a mapped column asks the agent for that one column. Approving the new proposal writes version 2; rejecting it keeps version 1. A proposal that would change nothing (its only candidate is the current element) is not created (open question 7).
30. **Approved mappings replace the hint as the link.**
    - `MappingRepository.current_for_feed()` is the only link later phases read; `DomainCoverage` (phase 1) already reads only mappings.
    - The feed page shows, per column, "Mapped to" from `mappings`, and the spec's statement beside it, labelled "the source says".
    - Profiles keep the spec as evidence of what was signed off; step 02's coverage of a candidate feed still uses it. Nothing in steps 05 or 06 treats it as true.
    - Test: `test_s06_map.py::test_an_approved_mapping_wins_over_the_specs_hint`.
31. **A mapping never names an element that is only proposed** (open question 5). It names a current glossary element; `trg_mappings_glossary_key` (`0004`) already refuses another. A column whose element waits for the glossary owner is `no_element_back_to_05` until the element is approved and the steward proposes again.
32. **What the database enforces on a mapping row** (section 6): an approved `mapping` proposal (`0005`); of the same feed; naming the same source field; the element is one of the proposal's candidates; the version is the column's next; the feed is registered; one row per proposal.
33. **Mock rule `mapping`** (pure), over the candidates whose type fits: one with score 1.0: it; else the hinted one; else every candidate at 0.5 or more (none, one, or a tie).

**The `proposals` guards (note 5)**

34. **`0016` replaces the body of `amaze_guard_proposal_change()` and adds a TRUNCATE trigger.**
    - `status` changes only from `pending`, and only to `approved` or `rejected`; a decided proposal never changes again.
    - `trg_proposals_no_truncate` refuses TRUNCATE.
    - Not added: a check, at the status change, that the approvals agree. `0005` and the sign-off guard make that check where it matters, when the configuration row is written, and `record_decision` updates the proposal before it inserts the approval.
    - Existing tests that set a status by hand keep working where they move pending → approved or rejected. Two tests in `test_db_onboarding_guards.py` (lines 306 and 381) forge approved → rejected to reach a sign-off guard behind it. That move is now refused, so each asserts the refusal instead; the sign-off guard keeps its other tests.

**The gold set and the evaluation**

35. **`fixtures/gold/mappings.json`: 31 cases in four synthetic sources** (`syn_gold_...`), against the seed glossary. Each case: column, DuckDB type, the spec's hint if any, a category, and what a careful person would decide, with the source's description as context. Section 8.1 lists them.
    - The expected answers were written by judgement before the mock rule was run, and none was changed after its score was seen. Cases the mock cannot get right were put in on purpose.
36. **The score** (`agents/evaluation.py::score()`, pure). A case is right when:
    - expected `mapped`: the agent names exactly one element, and it is the expected one;
    - expected `tie_to_steward`: the agent names **at least two and at most 3** elements, and every element the case says must be offered is among them (approved change 1: a tie of seven asks the steward to do the agent's work);
    - expected `no_element_back_to_05`: the agent names none.
    - **Score = right cases / all cases.** Reported with it: the count per category; the **mean tie size** (the mean number of elements named, over the cases where the agent named two or more); the **candidate recall** (below); and the **wrong maps** (the agent names one element and the gold set does not accept it), listed, because a confident wrong mapping costs more than a tie.
    - **Candidate recall** (approved change 2): per case, whether each expected or must-offer element is in the candidate list that code built for that column (assumption 6), reported as "candidate recall n of m" over all those elements, with the cases that miss one listed. A miss whose element was never a candidate is the filter's; one whose element was a candidate is the model's. `score()` takes the candidate lists with the answers.
37. **The evaluation goes through `Agent.run()`** (open question 8, decided with a change). `MappingEvaluator` is a read-only agent (`approver_roles = ()`) on the same prompt, input builder and output schema as `MappingAgent`. It creates no proposal; every call is logged in `proposal_log` under its own name. One call per gold source.
    - `python -m agents.evaluate_mappings` uses the provider selected by `LLM_PROVIDER` and **logs to a separate database named by `EVAL_DATABASE_URL`** (in `.env.example`), never to the application's. It **refuses to run**, with a message and exit code 2, when `EVAL_DATABASE_URL` is not set or equals `DATABASE_URL`. It prints the table, the score, the mean tie size, the candidate recall and the wrong maps.
    - `README.md` shows how to create `amaze_eval`, owned by the same user as `amaze`, and bring it to `alembic upgrade head` with `DATABASE_URL` pointed at it for that one command.
38. **The mock's score is measured with the built code in task 9 and pinned** by `test_evaluation.py::test_the_mock_scores_as_measured_and_misses_these` (the number, the names of the misses and the wrong maps). The throwaway prototype of assumptions 5, 6 and 33 (2026-11-16, not repository code) got 26 of 31 under the first scoring rule; under the approved rule its case 14 (a tie of seven) is not right, so it predicts **25 of 31 (80.6%)**, with six misses, two of them wrong maps (section 8.1). If the built code gives another number, that is a deviation in section 17 with its cause. **The gold file is never changed to fit a score.**

**Data, API, UI, tests**

39. **One migration, `0016_glossary_runs_and_configuration_guards.py`**, developed against the test database and applied to the application database at checkpoint 2. After that, any fix is `0017`. The application database holds no mapping, no proposal-made glossary row and no proposal of the two kinds, so nothing existing breaks the new guards.
40. **Existing tests that write configuration move to one helper**, `tests/configured.py`: `approved_mapping()` and `approved_element()` make a proposal of the real shape, decided by the right dev user, on a feed made by `tests/onboarded.py`. No trigger is bypassed. Tests of `0005`'s own refusals keep their raw SQL and name the trigger that answers.
41. **No requirement status, move or message changes.** The requester sees nothing new in phase 5 (open question 4).
42. **API** (section 9): six Amaze endpoints, three of which commit. Named conflicts 409, refused input 422, a missing feed or proposal 404, the wrong role 403.
43. **UI** (section 10): the banner, proposals in the Glossary view, a mapping table in the Feed view. No new npm package. consumer-ui does not change.
44. **Synthetic data only.** One new catalogue source, `syn_bond_terms`, and its file (section 8.2); the gold set. Codes start with `syn_`.
45. **Commit-visibility tests: six, happy path only, through the API.**

    | Endpoint or job | Test | What the separate connection sees |
    |---|---|---|
    | `POST .../register` | `test_register_commits_the_glossary_run` | the `glossary_runs` row, `queued` |
    | the glossary job | `test_the_glossary_job_commits_its_proposals` | the run `done`, the `proposal_log` row, the proposals |
    | `POST .../glossary/run` | `test_glossary_run_commits_the_queued_run` | the `glossary_runs` row |
    | `POST .../mappings/propose` | `test_propose_commits_the_mapping_proposals` | the `proposal_log` row and the proposals |
    | `POST .../proposals/{id}/decision` (glossary) | `test_a_glossary_decision_commits_the_element` | the approval and the element version |
    | `POST .../proposals/{id}/decision` (mapping) | `test_a_mapping_decision_commits_the_mapping` | the approval and the `mappings` row |
46. **Race tests** (`test_concurrency.py`, each on its own schema, the second request started by an event set inside the first):
    - `test_no_connection_is_held_during_any_model_call` gains the mapping call; `test_no_connection_is_held_during_the_glossary_job`;
    - `test_two_workers_at_once_run_a_queued_run_once`;
    - `test_two_requests_for_a_run_of_one_feed_give_one_active_run`;
    - `test_a_late_answer_after_the_lease_writes_nothing_and_is_discarded`;
    - `test_after_a_restart_a_queued_run_and_an_expired_one_are_run`;
    - `test_two_mapping_requests_of_one_feed_at_once_give_200_and_409`;
    - `test_two_decisions_of_one_mapping_at_once_give_200_and_409`;
    - `test_two_alias_approvals_of_one_element_at_once_take_the_next_versions`.
47. **Test time (note 7).** Baseline today: 108 to 113 s (section 2). Phase 5 adds 14 schema-per-test cases (6 and 8): **21 to 56 s at 1.5 to 4 s each, most likely about 30 s**, and about 150 ordinary tests, about 5 s. Expected full run: **about 145 s, between 135 and 175 s**. Checkpoint 3 reports three runs again.
48. **No test starts a worker on the application database.** `running_app` (in `tests/conftest.py`) puts a do-nothing worker in `app.state.glossary_runs` unless the test gives its own; worker tests call `drain()` themselves on the test engine. Test: `test_jobs.py::test_the_app_under_test_runs_no_worker_of_its_own`.
49. **Lock order, extended at its end:** the advisory lock on the source code, the feed row (`FOR NO KEY UPDATE`), the proposal row, the requirement rows in id order, the `glossary_runs` row, the advisory lock on the glossary. A step takes only those it needs, in this order. The next mapping version is read after the feed row is locked; the next element version after the glossary lock.
50. **Documents** (task 13): `CLAUDE.md` (layout, the agent table and step 05's line, the runner and lock order), `BUILD_PLAN.md` (phase 5 as built), `README.md` (the demo rows, `0016 (head)`, `glossary_runs`), `.env.example` (`GLOSSARY_RUN_LEASE_SECONDS`).
51. **I check the UI** by building it and by a scripted browser run with screenshots. The committed Playwright test stays in phase 7.

**Open questions, recommendation first**

**Decided on 2026-11-16:** questions 1 to 7 and 9 as recommended (question 3: `MappingAgent`, prompt id `mapping`). Question 8: through `Agent.run()` as recommended, but logged to a separate database (`EVAL_DATABASE_URL`, assumption 37). Assumptions 17 (no editing) and 24 (mapped means stored) stand. **Limitation recorded:** a rejected glossary proposal ends that column in phase 5; there is no "ask again with a note" (risk 16, section 14). The gold set of section 8.1 is approved as written and **frozen when task 3 commits it**; any later change is a new version of the file, recorded in section 17.

1. **The background runner (note 3).** Recommended: **a `glossary_runs` table claimed with a lease, started by `Scheduler.run_soon()` after a commit and at start-up** (assumptions 10 and 11). It meets all three demands with one table and one port method, and phase 8 swaps the trigger only. Alternatives:
   - the same table with a polling tick every few seconds: no lost kick, but a timer that runs in every test and on an idle laptop;
   - APScheduler's own PostgreSQL job store, one job per feed: survives a restart, but stores pickled jobs, has no Azure equivalent, guards "one feed at a time" inside one process only, and phase 3 chose to keep nothing per feed in the scheduler;
   - FastAPI background tasks or a thread per request: the least code, lost on a restart;
   - a separate worker process on the same table: the cleanest split, but a fourth window in `dev.ps1` to supervise;
   - a real queue: nothing local without containers; phase 8.
2. **The coverage rule (note 1).** Recommended: **covered by name when the column's words equal the key, name or an alias of exactly one element; type plays no part** (assumption 9). Exact, explainable, and a near-miss costs only a `duplicate_alias` proposal. Alternatives: also require the type to fit (a `maturity` text column is then "new" at once, without the trip through step 06); or count a similar name (0.8 or more) as covered (fewer proposals, but a wrongly "covered" column is never proposed).
3. **The agent's name (note 6).** Recommended: **`MappingAgent`**, prompt id `mapping` (assumption 21). Alternative: `GlossaryMapper`, as the deck; `CLAUDE.md`, `BUILD_PLAN.md` and the proposal kind's comment would change with it. The UI label can say "Glossary mapper" either way.
4. **What the requester sees in phase 5.** Recommended: **nothing new**: the requirement stays `onboarding` with the same message; glossary and mapping work is the backend's (assumption 41). Alternative: one line under the onboarding message when every requested item is mapped; it needs a rule for "every item" and tells the requester about a step that still is not "Ready".
5. **May a mapping use an element the builder proposed in the same run?** Recommended: **no**: a mapping names only an element that is in the glossary; the column waits as `no_element_back_to_05` and the steward proposes again after the owner approves (assumption 31). Alternative: a mapping proposal that depends on a pending element and can be approved only after it; it needs an order between two approvers and a guard for it. A smaller variant: keep "no", and start the mapping proposal by itself when the element is approved.
6. **What `duplicate_alias` writes.** Recommended: **a proposal to add the column's name as an alias of the existing element**, decided by the glossary owner (assumption 14): the glossary learns the name and the coverage rule stays "names and aliases". It goes beyond "new elements only" in `CLAUDE.md`, whose line would change. Alternative: nothing is written to the glossary; the column goes to step 06 with that element as a candidate.
7. **How a second mapping version arises.** Recommended: **"Propose again" on one mapped column** (assumption 29), so the done-when is proved through the API. Alternative: no way to re-map in phase 5; versioning is then proved at repository and database level only.
8. **Where evaluation calls are logged.** Recommended: **a read-only `MappingEvaluator` through `Agent.run()`, logged in the database of `DATABASE_URL`** (assumption 37): one model path, and phase 8's comparison keeps its evidence. Alternative: the same agent on a scratch schema of the test database, dropped afterwards: nothing is kept, and nothing append-only grows.
9. **Mapping in a request or in the background.** Recommended: **in the steward's request** (assumption 2), the pattern of every call so far. Alternative: on the runner too; the banner then covers both, and the table and worker carry a second kind of run.

## 5. Steps and typed results

### 5.1 How a column moves

| Column of a registered feed | Action | Outcome | After |
|---|---|---|---|
| new, not covered | the glossary run (by itself at registration, or asked for) | `proposed` | a pending proposal: a new element |
| | | `duplicate_alias` | a pending proposal: an alias on an existing element |
| | | `conflict_to_owner` | a pending proposal with the options to choose from, or none |
| | | none: left to step 06 | no proposal |
| has a pending glossary proposal | the glossary owner decides | approved | the element's new version; the stewards are told |
| | | rejected | nothing; the column is not asked about again |
| no mapping, no pending mapping proposal | Propose mappings (steward; MappingAgent) | a recommendation, no outcome | a pending proposal recommending one element |
| | | `tie_to_steward` | a pending proposal, no recommendation |
| | | `no_element_back_to_05` | no proposal; a glossary run is queued if the builder was never asked about the column |
| has a pending mapping proposal | the steward approves one candidate | `mapped` | `mappings`: the column's next version |
| | the steward rejects | none | nothing; those elements are not proposed again for this column |
| mapped | Propose again (steward) | as above | a new proposal, or none if nothing would change |

Feeds, requirements and their statuses do not change in phase 5.

### 5.2 The shapes

```python
# core/steps/s05_glossary.py: one result per column of a run
class Proposed(StepResult):            # outcome "proposed"
    column: str
    proposal_id: UUID
class DuplicateAlias(StepResult):      # outcome "duplicate_alias"
    column: str
    element_key: str                   # the existing element
    proposal_id: UUID
class ConflictToOwner(StepResult):     # outcome "conflict_to_owner"
    column: str
    clashes: list[str]                 # the existing elements it clashes with
    proposal_id: UUID

# core/steps/s06_map.py
class Mapped(StepResult):              # outcome "mapped": returned by the steward's approval
    mapping: Mapping                   # feed, source field, element, version, proposal
class TieToSteward(StepResult):        # outcome "tie_to_steward"
    source_field: str
    candidates: list[str]
    proposal_id: UUID
class NoElementBackTo05(StepResult):   # outcome "no_element_back_to_05"
    source_field: str
    reason: Literal["no_candidate", "type_does_not_fit", "every_candidate_rejected"]
    glossary_run_id: UUID | None       # the run queued for it, if one was
```

Each step's results form a union discriminated on `outcome`, in `CLAUDE.md`'s order. Not outcomes: `MappingProposed` (a pending recommendation), `ElementStored` (the owner's approval), a rejection. Steps flush and never commit.

| Module | Function | Writes |
|---|---|---|
| `candidates.py` | `score`, `type_fits`, `candidates_for`, `new_columns`, `covered_by`, `classify_element` | nothing (pure) |
| `s05_glossary.py` | `queue_run(session, feed_id, reason, user)` | a `glossary_runs` row, or returns the active one |
| `s05_glossary.py` | `claim_run(session)` | one run: `running`, attempt, lease |
| `s05_glossary.py` | `builder_input(session, run)` | nothing (read) |
| `s05_glossary.py` | `record_run(session, run, agent, agent_input, notifier, owners)` | through `Agent.run()`: the call row and the proposals; the run's results and `done`; one notification per owner |
| `s05_glossary.py` | `fail_run(session, run, error)` | the run: `failed` |
| `s05_glossary.py` | `decide_element(session, proposal_id, user, decision, choice, note, ...)` | the approval; on approval the element's new version and the stewards' notification |
| `s06_map.py` | `mapping_input(session, feed_id, column)` | nothing (read) |
| `s06_map.py` | `record_mappings(session, agent, agent_input, user)` | through `Agent.run()`: the call row and the proposals; a queued glossary run for columns sent back |
| `s06_map.py` | `decide_mapping(session, proposal_id, user, decision, choice, note)` | the approval; on approval the `mappings` row |
| `feed_page.py` | `feed_page(...)` | nothing (read): gains the glossary run and the mapping table |
| `s04_register.py` | `register_feed(...)` | as phase 4, and a queued glossary run |
| `s17_discover.py` | `assist_input(...)` | nothing: candidates instead of the glossary |
| `apps/api/jobs.py` | `GlossaryRuns.drain()` | claims and runs until none is left; each run in its own session |

### 5.3 Rule 1: who decides what

| Step | Who decides | Proposal | Approval rows | What the decision allows, and the database guard |
|---|---|---|---|---|
| 05 run | Nobody: the agent proposes, code names the outcome | one `glossary_element` proposal per column, roles `[glossary_owner]` | none | Nothing: a pending proposal changes nothing. Guard: one pending proposal per feed and column (unique index) |
| 05 decision | One person with `glossary_owner` | that proposal | approved (with the option chosen) or rejected | A `glossary_elements` row: `0005` (approved, every role, no rejection); it is one of the proposal's options; version 1 for a new element, the next and otherwise equal for an alias; `created_by` is the approver; one row per proposal |
| 06 propose | Nobody | one `mapping` proposal per column, roles `[data_steward]` | none | Nothing. Guard: one pending proposal per feed and column |
| 06 decision | One person with `data_steward` | that proposal | approved (with the candidate chosen) or rejected | A `mappings` row: `0005`; the proposal's feed and source field; the element is one of its candidates and is in the glossary; the column's next version; the feed is registered; one row per proposal |
| any | | | | A proposal's status moves only pending → approved or rejected; its other columns never change; no DELETE, no TRUNCATE |

Not in the database, code and tests only: that the approver holds the role (`approvals.role` is text written by code, as in phase 4); that no two elements share a phrase; that no two columns of a feed map to one element.

### 5.4 Every outcome name and its test

`test_step_results.py` reads the lines "05 Glossary" and "06 Map" from `CLAUDE.md` and compares them with the unions, in order.

| Outcome | Named test |
|---|---|
| `proposed` | `test_s05_glossary.py::test_proposed_when_no_element_has_the_columns_name` |
| `duplicate_alias` | `test_s05_glossary.py::test_duplicate_alias_when_the_new_element_is_an_existing_one_under_another_name` |
| `conflict_to_owner` | `test_s05_glossary.py::test_conflict_to_owner_when_the_name_is_taken_by_an_element_of_another_type_or_by_two` |
| `mapped` | `test_s06_map.py::test_mapped_when_the_steward_approves_a_candidate` |
| `tie_to_steward` | `test_s06_map.py::test_tie_to_steward_when_several_elements_fit_and_the_steward_must_choose` |
| `no_element_back_to_05` | `test_s06_map.py::test_no_element_back_to_05_queues_a_glossary_run_for_the_column` (three reasons) |

### 5.5 Carried notes and other named tests

| Item | Named test |
|---|---|
| Note 1: new columns | `test_candidates.py`: `test_new_columns_are_the_undeclared_ones_and_the_declared_other_fields`, `test_a_column_declared_as_an_element_is_never_new` |
| Note 1: covered | `test_candidates.py`: `test_a_name_equal_to_a_key_name_or_alias_of_one_element_covers`, `test_a_phrase_two_elements_share_does_not_cover`, `test_a_mapped_or_already_asked_column_is_covered`; `test_s05_glossary.py::test_a_column_sent_back_skips_the_name_rule` |
| Note 2: the latest profile of a registered feed; the hint | `test_s06_map.py`: `test_only_a_registered_feed_is_mapped`, `test_the_input_is_the_latest_profile_and_the_hint_is_one_candidate`, `test_an_approved_mapping_wins_over_the_specs_hint` |
| Note 3: the pattern | `test_concurrency.py::test_no_connection_is_held_during_the_glossary_job` |
| Note 3: a restart | `test_concurrency.py::test_after_a_restart_a_queued_run_and_an_expired_one_are_run`; `test_port_scheduler.py::test_run_soon_runs_the_function_once_in_the_background` |
| Note 3: never twice | `test_two_workers_at_once_run_a_queued_run_once`, `test_two_requests_for_a_run_of_one_feed_give_one_active_run`, `test_a_late_answer_after_the_lease_writes_nothing_and_is_discarded`; raw SQL in section 6 |
| Note 4 | `test_candidates.py::test_a_list_is_never_longer_than_ten`; `test_no_agent_input_holds_the_whole_glossary`; `test_s17_discover.py::test_the_assistant_gets_candidates_not_the_whole_glossary` |
| Note 5 | `test_db_approval_guard.py`: `test_a_status_moves_only_from_pending_to_approved_or_rejected` (six moves), `test_truncate_of_proposals_is_refused` |
| Note 6 | `test_agent_mapping.py::test_the_log_row_names_mappingagent` |
| The agents | `test_agent_glossary_builder.py`, `test_agent_mapping.py`: one proposal per column for the right role; an invented key is dropped; a rejected element is not offered again |
| The decisions | `test_s05_glossary.py`: `test_approving_a_new_element_stores_version_1`, `test_approving_an_alias_stores_the_next_version`, `test_a_conflict_needs_a_choice`, `test_a_taken_phrase_is_409_and_nothing_is_written`; `test_s06_map.py`: `test_a_tie_needs_a_choice`, `test_a_choice_that_was_not_offered_is_refused`, `test_an_element_another_column_holds_is_409`, `test_propose_again_and_approve_gives_version_2` |
| The gold set | `test_gold_set.py`: `test_every_category_has_a_case`, `test_every_expected_element_is_in_the_seed_glossary`, `test_codes_start_with_syn_gold`; `test_evaluation.py`: `test_the_score_counts_right_cases_and_lists_wrong_maps`, `test_the_mock_scores_26_of_31_and_misses_these_five`, `test_an_evaluation_creates_no_proposal_and_logs_every_call` |
| 503 | `test_api_amaze_mapping.py::test_a_model_that_does_not_answer_gives_503_and_writes_nothing`; `test_jobs.py::test_a_model_that_does_not_answer_fails_the_run_and_keeps_its_reason` |
| 409, 403, 422 | `test_api_amaze_mapping.py`: `test_a_wrong_state_is_409` (cases), `test_only_the_named_roles_act`, `test_refused_input_is_422` |
| No worker on the application database | `test_jobs.py::test_the_app_under_test_runs_no_worker_of_its_own` |

## 6. Data model

### Migration `0016_glossary_runs_and_configuration_guards.py`

| Table | Change | Rules |
|---|---|---|
| `glossary_runs` (new) | feed_id, profile_id, reason (`registered`, `requested`, `sent_back`), columns (text[]: the columns sent back), requested_by, status, attempt, lease_until, started_at, finished_at, proposal_log_id, results (jsonb: one outcome per column), error | Not append-only: operational state, like `feeds.stage`. `status` is `queued`, `running`, `done` or `failed`; `running` has a lease; `failed` has an error; `done` has results. **One `queued` or `running` row per feed** (partial unique index). DELETE and TRUNCATE refused |
| `proposals` | no column | the status guard, the TRUNCATE trigger, one pending proposal per kind, feed and column (below) |
| `glossary_elements` | no column | one row per proposal; the row is an option of its proposal (below) |
| `mappings` | no column | one row per proposal; the row matches its proposal (below) |

**Triggers and indexes.** Each line has a raw-SQL test in `test_db_configuration_guards.py` (or the file named), and each is shown failing when removed.

| Guard | Refuses |
|---|---|
| `amaze_guard_proposal_change()` (body replaced; `trg_proposals_decision_only`) | as `0015`, and a `status` change that does not start at `pending` or does not end at `approved` or `rejected` |
| `trg_proposals_no_truncate` | TRUNCATE of `proposals` |
| `uq_proposals_one_pending_per_feed_column` (unique, partial, on kind, feed and the payload's `source_field`, for pending `glossary_element` and `mapping` proposals) | a second pending proposal of one kind for one column of one feed |
| `uq_glossary_elements_proposal_id` (partial), `uq_mappings_proposal_id` | two configuration rows from one proposal |
| `trg_glossary_elements_b_option` | a non-seed row that is none of its proposal's options: a new element whose key, name, domain, type, definition or aliases differ, or whose version is not 1; an alias row whose version is not the key's highest plus one, or that differs from the last version in anything but the one added alias; a `created_by` who is not the proposal's `glossary_owner` approver |
| `trg_mappings_b_matches_proposal` | a row whose feed is not the proposal's; whose source field is not the proposal's; whose element is not one of the proposal's candidates; whose version is not the column's highest plus one; whose feed is not registered |
| `trg_glossary_runs_a_status` | a new run that is not `queued`; any move but queued → running, running → done, failed or running again with a higher attempt; a change to a `done` or `failed` run; DELETE |
| `trg_glossary_runs_no_truncate` | TRUNCATE |

- The `0005` triggers stay as they are and run first (`trg_..._approved_proposal` sorts before `_b_`).
- **Every check on a nullable column spells out NULL**, has a NULL case in its test and is added to `REVIEWED_NULLABLE_CHECKS` (`lease_until`, `error`, `results`, `proposal_log_id`, `requested_by`).
- **Downgrade:** stops if any `glossary_runs` row exists; restores `0015`'s function body.
- **PostgreSQL 14 or older only:** partial unique indexes on an expression, `jsonb` operators and `jsonb_array_elements` (9.4), `to_jsonb` (9.5), `FOR UPDATE SKIP LOCKED` (9.5, in code), advisory locks, plpgsql, statement-level TRUNCATE triggers. Nothing from 15 or later.

## 7. Domain models and repositories

| Item | Change |
|---|---|
| `core/domain/mapping.py` (new) | `Candidate`, `NewColumn`, `ColumnToMap`, `GlossaryBuilderInput`, `GlossaryBuilderOutput`, `MappingInput`, `MappingOutput`, `ElementOption`, `GlossaryRun`, its draft, `GlossaryRunReason`, `GlossaryRunStatus` |
| `core/domain/sourcing.py` | `AssistantInput.glossary` documented as candidates; no shape change |
| `GlossaryRunRepository` (new) | `queue(...)`, `active(feed_id)`, `claim(lease_seconds)`, `lock(id)`, `finish(...)`, `fail(...)`, `latest(feed_id)`, `summary()` |
| `GlossaryRepository` | `lock()` (the advisory lock); `add_version()` takes the option chosen; `phrases_taken(...)` |
| `MappingRepository` | `add_version()` requires the proposal's feed, field and a candidate; `rejected_elements(feed_id, source_field)` |
| `ProposalRepository` | `pending_for_column(kind, feed_id, source_field)`, `list_for_feed_column(...)`, `list_kind(kind, status)` |
| `FeedRepository` | no change (`lock()` is used) |
| `ports/scheduler/` | `Scheduler.run_soon()`; local: an APScheduler job with no trigger; Azure: stub |
| `ports/config.py` | `glossary_run_lease_seconds` (default 300, at least 1) |
| `ports/llm/mock_rules.py` | `glossary_builder`, `mapping` |
| `apps/api/jobs.py` | `GlossaryRuns` (as `DailyFetch`: built from settings, replaceable in tests) and `kick_glossary_runs()` |

New named errors: `NothingToMap`, `MappingAlreadyProposed`, `ElementAlreadyMapped`, `PhraseTaken`, `RunSuperseded` (all 409); `ChoiceNotOffered`, `ChoiceNeeded` (422, `StepInputError`). `FeedNotRegistered`, `ProposalAlreadyDecided` (409), `ProposalNotFound` (404) and `NotAnApprover` (now mapped to 403) exist.

## 8. Synthetic data and the demo paths

### 8.1 The gold set (`fixtures/gold/mappings.json`)

Four invented sources, 31 columns, against the 33 seed elements. "Mock" is the prototype's answer (assumption 38).

| # | Source | Column, type | Category | A careful person decides | Mock |
|---|---|---|---|---|---|
| 1–4 | `syn_gold_eod_prices` | `instrument_id`, `price_date`, `close_price`, `Open Price` | easy | mapped to the element of that name | right |
| 5–7 | | `day_high`, `day_low`, `traded_volume` | alias only | `high_price`, `low_price`, `volume` | right |
| 8 | | `ccy`, text | tie | a tie: `currency` or `price_currency` (a price file does not say which) | **miss**: maps `currency` |
| 9–10 | `syn_gold_vendor_quotes` | `instr_id`, `px_date` (the spec hints both) | hint | `instrument_id`, `price_date` | right |
| 11–12 | | `bid`, `offer` | alias only | `bid_price`, `ask_price` | right |
| 13 | | `mid_px` | abbreviation | `mid_price` | right |
| 14 | | `price` (the vendor does not say which) | tie | a tie that offers `close_price` and `mid_price` | right (a tie of seven) |
| 15 | | `quote_ccy`, text | misleading name | `price_currency` | **wrong map**: `currency` |
| 16 | `syn_gold_volume_stats` | `instrument`, text | context | `instrument_id` | **miss**: a tie of five |
| 17 | | `trade_date` | context | `price_date` | **miss**: no element |
| 18 | | `volume` | easy | `volume` | right |
| 19 | | `high`, whole number (the day's largest trade) | misleading name | no element: it is a quantity, not `high_price` | **wrong map**: `high_price` |
| 20 | | `open`, text (open for trading, Y or N) | type mismatch | no element | right |
| 21 | `syn_gold_bond_terms` | `isin` | easy | `isin` | right |
| 22 | | `maturity`, text (a tenor such as 5Y) | type mismatch | no element: `maturity_date` is a date | right |
| 23, 27–29 | | `coupon`, `issuer`, `lei`, `ccy` | alias only | `coupon_rate`, `issuer_name`, `issuer_lei`, `currency` | right |
| 24 | | `issue_dt` | abbreviation | `issue_date` | right |
| 25–26 | | `rating`, `esg_score` | no element | no element: back to 05 | right |
| 30 | | `date` (the source does not say which) | tie | a tie that offers `issue_date` and `maturity_date` | right |
| 31 | | `lot`, text, hinted as `lot_size` | type mismatch | no element: the hint is wrong for a text column | right |

- **Score: 26 of 31, 83.9%, under the first scoring rule.** By category: easy 6/6, alias only 9/9, hint 2/2, abbreviation 2/2, tie 2/3, type mismatch 3/3, no element 2/2, context 0/2, misleading name 0/2.
- **Under the approved rule (assumption 36, a tie names at most 3): 25 of 31, 80.6%,** tie 1/3: case 14's tie of seven is no longer right. This is the prototype's prediction; task 9 measures the built code.
- **Wrong maps: 2** (cases 15 and 19). The other three misses are safe: they ask the steward or send the column back.
- What the score does not show: the mock reads names and types only. It cannot use the source's description, which is what cases 8, 16, 17 and 19 turn on. Phase 8 runs the same file against Azure OpenAI.
- Identifier type names (`isin`, `lei`) are allowed; the file holds no values at all.

### 8.2 One new source, and the demo rows

`syn_bond_terms` ("Bond terms", public, market data vendors, not known, no external approval), declaring `instrument_id`, `maturity_date`, `coupon_rate` in columns `instr_id`, `matures_on`, `cpn`. Its file, ten rows, also holds four columns the spec does not declare: `date`, `venue_mic_code`, `rating`, and `maturity` (text, a tenor). Phase 3 and 4 tests that count eight sources are updated.

Glenn Glossary, Sam Steward and Blake Backend in backend-ui; Riley Requester in consumer-ui.

| # | What | What happens |
|---|---|---|
| Q | `syn_open_esg_scores` (registered before phase 5). Glenn: Run the glossary builder | The banner shows the run, then "2 proposals wait". `score_year` and `esg_score`: `proposed`. Glenn approves both: two elements, version 1. Sam: Propose mappings: `instrument` (the source says `instrument_id`), `esg_score`, `score_year`, one recommendation each. Sam approves: three mappings, version 1 |
| R | Riley requests `coupon rate`, picks Bond terms; Blake profiles, signs off `meets`, registers | The run starts by itself. `rating`: `proposed`. `venue_mic_code`: `duplicate_alias` of `exchange_mic`. `date`: left to step 06. `maturity`: covered by name, skipped. Glenn approves both. Sam: Propose mappings: `instr_id`, `matures_on`, `cpn`, `venue_mic_code`, `rating` recommended; `date`: `tie_to_steward` (issue, maturity or price date); `maturity`: `no_element_back_to_05` (its type does not fit), and a run is queued: `conflict_to_owner` with nothing to approve; Glenn rejects it with a note. Sam approves the five and picks `price_date` for `date` |
| S | Sam: Propose again on `date` | A tie again. Choosing `maturity_date`: refused, `matures_on` holds it. Choosing `issue_date`: version 2. The history shows version 1 `price_date`, version 2 `issue_date` |
| T | Riley searches `closing prices`, Ask the assistant | The same suggestion as today. The stored input file holds at most 10 elements, not 33 |

## 9. API

**Amaze** (`apps/api/routers/amaze.py`):

| Endpoint | Who | Does |
|---|---|---|
| `GET /api/amaze/glossary/runs` | any backend role | the banner: active runs, the last finished ones with their counts and errors, and how many glossary proposals wait |
| `POST /api/amaze/feeds/{id}/glossary/run` | `glossary_owner` or `backend_team` | queues a run for a registered feed, or returns the active one. Commits, then kicks the worker |
| `GET /api/amaze/proposals?kind=&status=&feed_id=` | any backend role | `glossary_element` and `mapping` proposals with their feed, column, options or candidates, the agent's reason and the decisions |
| `POST /api/amaze/feeds/{id}/mappings/propose` `{column}` | `data_steward` | step 06 for every unmapped column, or for the one named. Commits |
| `POST /api/amaze/proposals/{id}/decision` `{decision, choice, note}` | the proposal's approver role | step 05's or 06's decision, by the proposal's kind. Commits |
| `GET /api/amaze/feeds/{id}` | any backend role | gains the feed's glossary run and, per column: type, what the source says, the current mapping and its version, the pending proposal, and why a column has none |

- `POST .../register` also queues the run and kicks the worker after its commit. The consumer API's `/search/assist` keeps its contract.
- 401 if not signed in; 403 for the requester and for a backend role without the role named; 404 for an unknown feed or proposal; 409 for a named conflict; 422 for refused input; 503 for `LLMUnavailable` or a stub adapter.
- The two agents, the worker and the kick are FastAPI dependencies, so tests replace them.

## 10. UI

**backend-ui**

1. **Banner**, under the header on every view: "Glossary builder: running for <feed>; 1 waiting", then "<n> proposals wait for the glossary owner" (a link), or "failed: <reason>" with Run again for those who may. It asks the API every 3 seconds while a run is active, otherwise when the view changes.
2. **Glossary:** a Proposals list above the elements. Each: the feed and column, what is proposed (a new element, an alias on <element>, or a conflict with its options), the agent's reason marked as the agent's, and Approve (with the option chosen) or Reject with a note. The actions show only for `glossary_owner`.
3. **Feed** (a registered feed), after the registration:
   - Glossary builder: the last run and its outcomes; Run the glossary builder;
   - Mapping: one row per column: name, type, "the source says", "Mapped to" with its version, or the pending proposal with its candidates (one must be chosen for a tie), Approve and Reject, or why there is none ("with the glossary builder", "rejected by the glossary owner", "its type does not fit"); Propose mappings; Propose again on a mapped column; the versions of a column.
   - The mapping actions show only for `data_steward`.

**consumer-ui:** no change.

Checked by `npm run build` in both projects and a scripted browser run, rows Q to T. Screenshots in `.playwright-mcp/`: `phase5-Q.png` to `phase5-T.png`, and `phase5-banner.png`.

## 11. Files

```
docs/plans/phase-5.md                          this plan
docs/ui-polish.md                              edit   task 14
BUILD_PLAN.md  CLAUDE.md  README.md  .env.example     edit   task 13
migrations/versions/0016_glossary_runs_and_configuration_guards.py   new
fixtures/gold/mappings.json                    new
fixtures/sources/catalogue.json                edit   syn_bond_terms
fixtures/sources/files/bond_terms.csv          new
agents/
  glossary_builder.py  mapping.py  evaluation.py  evaluate_mappings.py   new
  prompts/glossary_builder.v1.yaml  prompts/mapping.v1.yaml              new
  registry.py                                  edit
ports/
  scheduler/base.py  local.py  azure_functions.py   edit   run_soon
  llm/mock_rules.py                            edit   two rules
  config.py                                    edit   the lease setting
core/
  domain/      mapping.py                      new
               sourcing.py                     edit
  repositories/ glossary_runs.py               new
                tables.py  errors.py  glossary.py  configuration.py  proposals.py   edit
  steps/       candidates.py  s05_glossary.py  s06_map.py   new
               s04_register.py  s17_discover.py  feed_page.py   edit
apps/api/
  deps.py  jobs.py  main.py  transactions.py  routers/amaze.py   edit
apps/backend-ui/src/
  App.tsx  api.ts  views/GlossaryView.tsx  views/FeedView.tsx    edit
  views/RunBanner.tsx                          new
tests/
  configured.py                                new    approved_mapping(), approved_element()
  test_candidates.py  test_gold_set.py  test_evaluation.py  test_s05_glossary.py  test_s06_map.py
  test_agent_glossary_builder.py  test_agent_mapping.py  test_repo_glossary_runs.py
  test_db_configuration_guards.py  test_api_amaze_mapping.py     new
  conftest.py  onboarded.py  step_chains.py  stand_in_agents.py
  test_migrations.py  test_append_only.py  test_db_approval_guard.py  test_db_onboarding_guards.py
  test_domain_models.py  test_step_results.py  test_mock_rules.py  test_prompts.py
  test_repo_configuration.py  test_repo_glossary.py  test_repo_proposals.py  test_coverage.py
  test_routing.py  test_request_data.py  test_api_consumer.py  test_api_consumer_sourcing.py
  test_api_amaze_onboarding.py  test_s04_register.py  test_s17_discover.py
  test_agent_discovery_assistant.py  test_source_catalogue.py  test_known_source.py  test_s2_sources.py
  test_port_scheduler.py  test_azure_stubs.py  test_settings.py  test_jobs.py
  test_import_boundaries.py  test_no_agent_input_holds_the_whole_glossary.py (new)
  test_commit_visibility.py  test_concurrency.py                 edit
```

## 12. Tasks, in order

Each task: tests first and shown failing, then code, then its check, then one commit. Files are edited only with the file tools. I stop at the four checkpoints and continue without stopping between them.

**Method for every task (the plugins do not load, so it is followed by hand):**
- Before code: list any assumption the plan does not state; if it changes scope, stop and ask. Touch only the files in section 11; any other file is a deviation recorded in section 17. The simplest code that passes.
- RED: the tests written and run, the failing summary line pasted. GREEN: the least code that passes, the summary line pasted. Refactor only with tests green.
- An unexpected failure: reproduce, find the root cause, write it down, then fix. No guess-fixes; no weakened test.
- Every claim in a report is backed by pasted output. Guards and commits are shown failing when removed and restored with the Edit tool; `git grep PROOF` finds nothing.
- A checkpoint report also has `git --no-optional-locks diff --stat <last checkpoint>..HEAD`, the files changed outside section 11 (or "none"), the test count and time, and the deviations.

| # | Task | Tests written first | Check |
|---|---|---|---|
| 0 | This plan updated with the approval's decisions | none | one commit |
| 1 | `candidates.py`: score, type fit, candidate lists, new columns, covered, classify | `test_candidates.py` | pytest |
| 2 | The DiscoveryAssistant on candidates | `test_s17_discover.py`, `test_agent_discovery_assistant.py`, `test_no_agent_input_holds_the_whole_glossary.py` (the assistant's part) | pytest; every existing assistant test unchanged |
| 3 | The gold set and the pure score. **The gold file is committed before any mock rule exists** | `test_gold_set.py`, `test_evaluation.py` (the score only) | pytest |
| 4 | Migration `0016`; tables, domain models, repositories; `tests/configured.py` and the call sites | `test_migrations.py`, `test_append_only.py`, `test_domain_models.py`, `test_repo_glossary_runs.py`, `test_repo_glossary.py`, `test_repo_configuration.py`, `test_repo_proposals.py`, `test_db_approval_guard.py`, `test_db_configuration_guards.py` | pytest; each new guard removed in turn and its tests shown failing. **CHECKPOINT 1:** pytest summary, the guard proofs, the gold file's categories |
| 5 | The two agents, the evaluator, the prompts and hashes, the two mock rules | `test_agent_glossary_builder.py`, `test_agent_mapping.py`, `test_mock_rules.py`, `test_prompts.py` | pytest |
| 6 | Step 05: queue, claim, input, record, fail, decide; the run queued at registration; the catalogue's new source and file | `test_s05_glossary.py`, `test_s04_register.py`, `test_step_results.py`, `test_source_catalogue.py` | pytest |
| 7 | The runner: `Scheduler.run_soon`, `GlossaryRuns`, the start-up kick, the do-nothing worker in `running_app` | `test_port_scheduler.py`, `test_azure_stubs.py`, `test_jobs.py`, `test_settings.py` | pytest |
| 8 | Step 06: input, record, back to 05, decide, propose again | `test_s06_map.py`, `test_step_results.py` | pytest |
| 9 | The evaluation script and the mock's pinned score; `EVAL_DATABASE_URL` in `ports/config.py`, `.env.example` and `README.md` (brought forward from task 13) | `test_evaluation.py`, `test_settings.py` | pytest; `amaze_eval` created and upgraded; `python -m agents.evaluate_mappings` (and its refusal when the two URLs are equal); `alembic upgrade head`; `0016 (head)`. **CHECKPOINT 2:** pytest summary, the six outcome tests by name, the evaluation's printed table, `alembic current`, the new triggers listed |
| 10 | Amaze API: runs, proposals, propose, decision; the feed page; the role checks | `test_api_amaze_mapping.py`, `test_api_amaze_onboarding.py` | pytest |
| 11 | Commit visibility (six) and races (eight) | `test_commit_visibility.py`, `test_concurrency.py` | pytest; each commit removed in turn and its test shown failing; each race test shown failing against the lock or check it protects. **CHECKPOINT 3:** three full runs and their times, the done-when test's output, the added test time against assumption 47 |
| 12 | backend-ui: the banner, the Glossary proposals, the Feed mapping table | none | build; browser run, rows Q to T |
| 13 | Documents: `CLAUDE.md`, `BUILD_PLAN.md`, `README.md`, `.env.example` | none (`test_step_results.py` reads `CLAUDE.md`) | documents read back |
| 14 | UI polish items found, appended to `docs/ui-polish.md` (not fixed) | none | the file read back |
| 15 | Review against `CLAUDE.md`'s seven rules, the PostgreSQL 14 list and this plan, by hand | none | findings in section 17. **CHECKPOINT 4:** section 13 results and the screenshots. Phase 6 is not started |

## 13. Commands that prove it is done

Run from `F:\amaze-cm-poc` in Windows PowerShell.

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m core.seed
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m alembic current
.\.venv\Scripts\python -m agents.evaluate_mappings      # EVAL_DATABASE_URL names amaze_eval (README)
npm --prefix apps\backend-ui run build
npm --prefix apps\consumer-ui run build
.\scripts\dev.ps1
```

| Command | Expected |
|---|---|
| `pytest -q` | all pass; 0 failed, 0 skipped, 0 warnings |
| `alembic current` | `0016 (head)` |
| `evaluate_mappings` | provider `mock`; logged to `amaze_eval`; the score pinned in task 9 (prototype: `25 of 31 (80.6%)`), the mean tie size, the candidate recall; wrong maps: `quote_ccy`, `high`. With `EVAL_DATABASE_URL` equal to `DATABASE_URL`: refused, exit code 2 |
| both builds | exit code 0 |
| `dev.ps1` | three `OK` lines and health `ok` |

Then in the browser: rows Q to T of section 8.2. For row R, check that the run starts without a click and that the banner shows it. For row S, check that version 1 is still listed. For row T, open the newest file under `data/blob/agent-inputs/DiscoveryAssistant/` and count its glossary entries.

## 14. Not in phase 5

- Rules, the checker, go-live approval, the run engine (phase 6). Nothing loads data through a mapping yet.
- Loads, structure checks, certification, the product, "Ready" and "Access restricted" (phase 7).
- Transformations: a mapping is level 1, one column to one element as it is. A column whose type does not fit is not mapped.
- Editing a proposal before approving it; a glossary change written by a person without a proposal; retiring an element or an alias.
- Asking the glossary builder again, with a note, about a column whose proposal the owner rejected: that column ends there in phase 5 (risk 16).
- A mapping that names a pending element; a mapping proposal started by itself when an element is approved.
- Mapping or the builder on a candidate feed; a new profile of a registered feed.
- Sample values in an agent's input: the agents see names, types and counts only.
- One model call per column; progress inside a run; a retry of a failed run by itself; a polling tick.
- A notifications view in either UI; email or Teams.
- `AzureOpenAIProvider` and its score (phase 8). The committed Playwright test (phase 7).

## 15. Risks

1. **The mock's score says little about a real model.** It reads names and types; 26 of 31 is the floor a real model must beat, and the two wrong maps show what a name-only rule does. Phase 8 measures Azure OpenAI on the same file.
2. **The gold set is one person's judgement** (mine), 31 cases, one glossary. Some expected answers can be argued (case 8: a tie, or `price_currency`). They are fixed before approval so you can change them; after that a change is a new version of the file.
3. **A kick can be lost.** If the process dies between the commit and the kick, the run waits until the next kick or start-up. The banner shows a waiting run, and Run the glossary builder kicks again.
4. **The lease is a guess.** 300 s is long for the mock and may be short for a slow model: a second worker would then repeat the call, and the first answer is discarded (logged, not used). The setting changes it.
5. **Classification sees only the candidates.** A new element whose name clashes with an element that was not among the column's candidates is `proposed`, and the clash is found at approval (`PhraseTaken`, 409). The owner then rejects it.
6. **A conflict with no option is a proposal nobody can approve.** It exists so that a named person closes it. It may read oddly in the audit trail.
7. **Aliases from one source's column names enter the shared glossary** when the owner approves them (`venue_mic_code`). A poor alias makes a later column "covered" by name. The owner decides each; open question 6 has the alternative.
8. **Covered by name ignores the type.** A column whose name matches and whose type does not takes two steps (06 sends it back, then 05). Open question 2.
9. **The new guards change how tests write configuration.** About 28 call sites move to `tests/configured.py`, which writes six rows for one mapping. Tests get slower and the helper must be kept right (as phase 4, risk 7).
10. **The pending-proposal index reads the payload.** A proposal of the two kinds written without `source_field` would escape it; the domain model requires the field, and `0015` already fixes a payload once written.
11. **No two columns of a feed map to one element, in code only.** Two approvals at once are held apart by the feed lock; raw SQL could still write it.
12. **Evaluation calls grow `proposal_log` in the evaluation database** (`EVAL_DATABASE_URL`, `amaze_eval`): four rows per run with the mock. The application database does not grow; the script refuses to run when the two URLs are equal. The evaluation database must be kept at the same migration head, or the script fails on its first write.
13. **The banner is hard to see with the mock**, which answers at once. The scripted browser run holds a run with a slow stand-in provider to take the screenshot; the real path is the same.
14. **Fourteen schema-per-test cases add about 30 s** to every run (assumption 47).
15. **The DiscoveryAssistant's input changes.** A term that shares no word with any element and is not spelled like one gets no candidate, and so no suggestion. The mock's own rule needed the same similarity, so its answers should not change; the existing tests decide.
16. **A rejected glossary proposal ends the column.** Nothing asks again; a column the owner rejected by mistake needs a new rule ("ask again with a note"), which phase 5 does not have.

## 16. Carried forward

Notes only; nothing here is built in phase 5.

**Phase 6**

1. The run engine reads `MappingRepository.current_for_feed()` and nothing else for the link from a file column to an element. An unmapped column is not loaded.
2. A column whose type does not fit its element needs a transformation; phase 6's "minimal pass-through" must say what happens to it.
3. Rules are proposed against mapped elements. `RuleRecommenderAgent` gets candidates and a small input too, never the whole glossary or rule set.
4. The go-live summary (step 11) should list the unmapped columns and the rejected glossary proposals of the feed.
5. `rules` rows need the same "matches its proposal" guard that `0016` gives `mappings`.

**Phase 7**

6. A structure change (step 12, `additive_parked`) is a new column: it is the builder's and the mapping agent's input, on the load's profile, not the signed-off one.
7. The Playwright story covers one glossary approval and one mapping approval.

**Phase 8**

8. `python -m agents.evaluate_mappings` with `LLM_PROVIDER=azure_openai`; compare with the mock's pinned score, mean tie size and candidate recall, and with its two wrong maps.
9. `AzureFunctionsScheduler.run_soon` becomes a queue-triggered function that calls `GlossaryRuns.drain()`; the other model calls can move to the same table.
10. Whether sample values may be sent to the model is a data-governance decision; today no agent sees a value.

## 17. Results and deviations (recorded during implementation)

### Checkpoint 1 (tasks 0 to 4)

**Deviations**

1. `test_agent_discovery_assistant.py:77` asserted `len(sent.glossary) == 33`, the behaviour assumption 7 removes, so "every existing assistant test unchanged" could not hold for that clause. Only it changed, to `<= 10`; every other assistant test is unchanged.
2. Assumption 7: the assistant gets **one list per term**, each element scored on the better of the term and its singular form (`candidate_list(..., also=...)`), so a term never brings more than 10 (done-when 5). Two lists per term could have sent 20.
3. `glossary_runs.requested_by` is **NOT NULL** (every run is queued by a person's action: the registrant, the steward or the requester of the run), so `REVIEWED_NULLABLE_CHECKS` gains 5 entries, not 6. Added beyond section 6: `ck_glossary_runs_finished_at_matches_status`, `ck_glossary_runs_call_only_when_done`, `ck_glossary_runs_requested_by`, and one move in `trg_glossary_runs_a_status`: a queued run's `columns` may grow (a column sent back while the run waits joins it).
4. New named errors beyond section 7: `ProposalForAnotherField`, `NotAProposalOption`, `GlossaryRunNotFound`. `ProposalRepository.list_kind` is not added: the existing `list(status, kind)` does it. `GlossaryRunRepository.summary()` moves to task 10, where the banner's shape is fixed.
5. The status guard changed a third test that forged a decided proposal: `test_db_approval_guard.py::test_refuses_a_proposal_rejected_at_step_two_and_forced_approved` now asserts the refusal of the forgery (0005's "has a rejected decision" keeps `test_refuses_a_fully_approved_proposal_with_an_extra_rejected_row`). Assumption 34 named only the two in `test_db_onboarding_guards.py`.
6. `test_coverage.py::test_a_feed_that_is_not_registered_does_not_count`: a mapping on a candidate feed can no longer be written, so the test asserts `FeedNotRegistered` first, then the coverage as before.
7. Method slips: `0016` was written before its tests in `test_migrations.py`, so no strict RED exists for those (the 15 removal proofs below are the failing evidence); and 17 lines were appended to `tests/test_repo_proposals.py` with a shell heredoc, against `CLAUDE.md`'s file-tool rule (checked: ASCII, LF, as the rest of the file).

**Unexpected failures and their root causes**

1. CHECK names of `glossary_runs` came out as `ck_glossary_runs_ck_glossary_runs_...`: the migration context applies the naming convention unless the name is wrapped in `op.f()` (as `0014` does). Fixed with `op.f()`.
2. `test_domain_models.py`: my new `ELEMENT` constant shadowed the file's own; renamed `NEW_ELEMENT`.
3. `test_gold_set.py`: my expected counts were wrong (by section 8.1: 22 mapped, 6 no element); the gold file was not changed.
4. `test_repo_glossary_runs.py`: my helper expired a lease by UPDATE, which `trg_glossary_runs_a_status` rightly refuses (a new lease needs a new attempt). The tests now claim with a lease of 0 seconds.

**Guard removal proofs** (each removed from `0016` with the Edit tool, only its tests run, restored with the Edit tool; the test database is rebuilt from the migrations every session):

| Guard removed | Its tests |
|---|---|
| status rule in `amaze_guard_proposal_change()` | 8 failed (the 8 passing are pending → approved forgeries, still allowed) |
| `trg_proposals_no_truncate` | 1 failed |
| `uq_proposals_one_pending_per_feed_column` | 1 failed |
| `uq_glossary_elements_proposal_id` | 1 failed |
| `trg_glossary_elements_b_option` | 17 failed |
| `uq_mappings_proposal_id` | 1 failed |
| `trg_mappings_b_matches_proposal` | 11 failed |
| `trg_glossary_runs_a_status` | 16 failed |
| `trg_glossary_runs_no_truncate` | 1 failed |
| `uq_glossary_runs_one_active_per_feed` | 3 failed |
| each of the five run CHECKs | 1 failed each |

**Full suite at checkpoint 1:** `1557 passed in 130.68s`, one run, nothing skipped, no warnings. Baseline 108 to 113 s: about 20 s more before any of the 14 schema-per-test cases. The cause is not isolated (no single new test is above 1.2 s); checkpoint 3 runs three timed runs.

