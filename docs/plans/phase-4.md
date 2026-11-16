# Phase 4 plan — Profile, sign-off, register (steps 02–04)

**Status:** written on 2026-11-15; **approved on 2026-11-15** with the open questions answered as recommended (Q7: `duckdb==1.5.6`), the three messages of assumption 26 and phase 3's `NO_SOURCE_LEFT` approved word for word, decision D1 (what approving the summary means, assumption 18), and six plan changes: the lock order (assumption 46), a sixth commit-visibility test (42), a note in the sign-off form (section 10), the router helper move as its own commit (39), the plugin section of `CLAUDE.md` (task 14) and risk 15. Those are applied below.
**Date:** 2026-11-15. **Branch:** `phase-4`, created from `main` at `2b167f2`. **Scope:** phase 4 of `BUILD_PLAN.md` and the phase 4 notes of the phase 3 plan (section 16). Local only; nothing is pushed.

**One part, four checkpoints.** Phase 4 is three steps, one agent and one migration; it is not split. I stop at each checkpoint.

## 1. Goal and "done when"

**Phase 4 is done when all eight are true:**

1. A candidate feed's latest fetched file is profiled by DuckDB: columns, types, null rates, distinct counts, and coverage against each requirement that selects the feed. Outcome `profiled`, or `spec_only_low_confidence` when the file gives no data to profile.
2. The ProfilerAgent summarises the profile as one `profile_summary` proposal. The backend team signs off: `meets`, `meets_with_gaps` or `rejected`.
3. **A rejected candidate keeps its reasons and the requester is told.** This is `BUILD_PLAN.md`'s done-when, proved by `test_api_amaze_onboarding.py::test_done_when_a_rejected_candidate_keeps_its_reasons_and_the_requester_is_told` and in the browser (rows N and O).
4. **An accepted one is registered**, with owner, licence (permitted use, redistribution) and visibility. Proved by `test_api_amaze_onboarding.py::test_done_when_an_accepted_candidate_is_registered` and in the browser (row L). Without an owner the outcome is `no_owner`; a requirement handed to a source whose feed is already registered gets `duplicate_linked`.
5. Each of the eight outcome names has a named test (section 5.4), and each of the four new endpoints that commit has a commit-visibility test (assumption 42).
6. Every rule-1 claim of this phase has a database guard, each with a raw-SQL test shown failing without its guard (section 6).
7. No database connection is held during a profiling run or the ProfilerAgent's call (`test_concurrency.py`).
8. `pytest` passes with nothing skipped and no warnings.

## 2. State at the start (checked on 2026-11-15)

| Item | Found |
|---|---|
| Git | `main` = `2b167f2`. `phase-4` created from it. Working tree clean |
| Tests | 1146 collected |
| Database | `alembic current`: `0013 (head)`. The application database holds 14 requirements (5 `defined`, 5 `onboarding`, 2 `no_source_found`, 1 `source_selected`, 1 `awaiting_scope_confirmation`) and 5 feeds, all `candidate` |
| Files to profile | 3 of the 5 candidate feeds have a fetched file: `syn_exchange_volumes` (2 files), `syn_vendor_eod_prices` (2), `syn_open_esg_scores` (1). The other two never answered |
| DuckDB | **Not installed, not in `requirements.txt`.** `pip` offers `2.0.0` (newest; an `abi3` wheel for Windows x64) and `1.5.6` (a `cp314` wheel) |
| `BlobStore` | `put`, `get`, `exists`, `list_keys`. No path, no stream. `AzureBlobStore` is a stub |
| The sample files | Their columns carry the source's own names (`instr_id`, `trade_date`, `traded_qty`), not glossary keys. The catalogue declares each source's glossary elements, but not which column holds which |
| `feeds` | One mutable row per feed: `stage` (`candidate`, `registered`, `rejected`), `owner_id`, `registered_at`. No licence, no visibility. `FeedRepository.register()` (phase 1) flips the stage with only an owner check; nothing in the database asks for a sign-off |
| Proposals | `profile_summary` is already a proposal kind (phase 1). No row of that kind exists |
| Requirements | 11 statuses, 15 moves. No move leaves `onboarding`. `rejected` exists since phase 1 and is unused |
| `source_choices` | Two release reasons: `source_down_n_days`, `requester_asked` (`ck_source_choices_reason`, `0009`) |
| Rejected feeds | "Known source?", S2 and S3 do not look at a feed's stage. Only the daily job skips a rejected feed |
| The daily job | `DailyFetch.run()` loads the catalogue first: a catalogue that fails to load stops every fetch |
| Amaze API | Read only: glossary and `GET /api/amaze/feeds`. No `POST`, no feed page |
| backend-ui | Three views. Feed registry is a read-only table with filters All, Candidate, Registered |
| Plugins | Superpowers, Karpathy, i-have-adhd and Octopus are still not loaded. I follow the same flow by hand |

## 3. What you fixed, and where this plan answers it

**Scope you set:** `BUILD_PLAN.md` phase 4 (steps 02–04), with the outcome names of `CLAUDE.md`.

**Patterns already set (followed, not reopened):**

| Pattern | Where it applies here |
|---|---|
| `Agent.run()` is the only model path; its call row is committed at once | assumptions 14 to 17 |
| No connection during a model call, a fetch or a profiling run: read, end the transaction, work, lock, check again, write | assumptions 11 and 16; `test_concurrency.py` |
| An ordered `approver_roles` tuple; a subclass overrides only `to_proposals` | assumption 14 |
| Typed outcomes; `LLMUnavailable` 503; conflicts 409; never a bare 500 | assumptions 13, 17, 39; section 9 |
| One commit-visibility test per committing endpoint or job, happy path only | assumption 42 |
| A database guard for every rule-1 claim, with a raw-SQL test shown failing without it | assumptions 27 and 34; section 6 |
| Never edit an applied migration; `op.f()`; nothing newer than PostgreSQL 14; synthetic data only | assumptions 37 and 41 |

**Section 16 notes of the phase 3 plan:**

| Note | Answered in |
|---|---|
| 1: step 02 starts from the latest `fetched` row | assumption 11 |
| 2: a rejected candidate: skip its source, tell the requester, release with a third reason | assumptions 24 and 25 |
| 3: registration copies the licence; `duplicate_linked` and "one feed per source" | assumptions 29 and 32 |
| 4: the review shows the conditions the requester accepted; access is restricted here | assumption 31 |
| 4a: registered feeds are always fetched | assumption 33 |
| 4b: `requirements_selecting()`, never `feeds.requirement_id` | assumption 4 |
| 4c: the third release reason in a new migration | assumption 37 |
| 4d: a catalogue that fails to load | assumption 36 |

**What you asked me to decide:**

| Question | Answer | Assumption |
|---|---|---|
| How DuckDB reads the file through the `BlobStore` port | `BlobStore.get()` gives the bytes; they go to a temporary file; an in-memory DuckDB reads that file. Only `get()` is used, so Azure Blob needs no change | 5 to 7 |
| Which role signs off | `backend_team`. It also profiles and registers. The owner named at registration holds `data_owner` | 3 |
| What the requester sees for each sign-off outcome | `meets`: no change. `meets_with_gaps`: still onboarding, with the gaps. `rejected`: the reasons, and "pick another source" or "ask for a new scope" | 22 to 26 |
| What `duplicate_linked` compares | The source code of the requirement's selected source against the registered feeds (one feed per source) | 32 |
| How the profile is stored | `feed_profiles`: one row per run, versioned per feed, append-only; the highest version is current | 12 |

## 4. Assumptions — veto any by number

**The shape of the phase**

1. **One part, four checkpoints, no split.** Checkpoint 1: DuckDB, the data model and its guards. Checkpoint 2: the three steps and the daily job. Checkpoint 3: the API. Checkpoint 4: the UI, documents and review.
2. **One endpoint runs one step; backend-ui chains them.** Nothing is profiled by itself after a fetch and nothing runs in the background. A person of the backend team presses Profile; backend-ui then asks for the summary, as consumer-ui asks for a source after a raise.
3. **Who acts.**
   - `backend_team` profiles, signs off and registers (`CLAUDE.md`: "ProfilerAgent ... Backend team (03)").
   - Every other backend role reads the feed page and gets 403 on the four actions.
   - The owner named at registration must be a user who holds `data_owner`. They are named, not asked: their own approval is step 11 (phase 6).
4. **The profile and the sign-off belong to the feed; coverage belongs to each requirement.**
   - One feed per source (phase 3, assumption 11), so one profile and one sign-off serve every requirement that selects the feed.
   - Coverage is computed for each requirement that selects the feed now: `SourceChoiceRepository.requirements_selecting(feed_id)`, never `feeds.requirement_id` (note 4b). The same call finds who to tell.

**Step 02: the profile**

5. **DuckDB reads through `BlobStore.get()`.**
   - `get(key)` returns the file's bytes. The step writes them to a file in a temporary folder made for that one run, and an in-memory DuckDB connection reads that file. The connection is closed, then the folder is removed, also after a failure.
   - **Why this works unchanged with Azure Blob:** the step uses one port method, `get()`, which every adapter must already have. Local and Azure run the same code; no path, URL or Azure extension reaches business logic (rule 5).
   - **Not chosen:** a new port method that hands out a local path (the local adapter would take a different route from the Azure one, so local tests would not cover it); DuckDB's own Azure or HTTP reader (business logic would know about Azure, and it downloads an extension).
   - **Cost:** the whole file is in memory once and on disk once. Fine for the PoC's files; risk 1.
6. **DuckDB is pinned, in memory, and reads CSV only.**
   - `duckdb==1.5.6` in `requirements.txt`: it has a `cp314` Windows wheel. `2.0.0` is the newest, a first release of a new major version; open question 7.
   - One in-memory connection per run. No DuckDB database file is kept.
   - The profile uses `read_csv` with a header, with types detected from every row (so the same file always gives the same types), `DESCRIBE` for the column types, and one aggregate query: the row count and, per column, the non-null count and the exact distinct count. No approximate functions. An empty cell is a null. Task 1's tests pin all of this on the pinned version.
   - A file whose name does not end in `.csv` is not profiled (assumption 10).
7. **Column names are data, never SQL.** A header comes from outside. Every column name is used as a quoted identifier with its quotes doubled, and the file path is a bound parameter. Test: `test_profiling.py::test_a_hostile_header_is_only_a_column_name`.
8. **The spec: the catalogue says which column holds which declared field.**
   - Today the catalogue declares each source's glossary `elements` and `other_fields`, but the files use the source's own column names. Without a link, "coverage against the requirement" could say only "the source declares it".
   - Each catalogue entry gains `columns`: file column name to the declared element key or other field it holds (`"traded_qty": "volume"`). The loader refuses a value that the entry does not declare, and a declared field with no column or with two.
   - This is the source's own statement, a fixture. It is not approved configuration: the mapping to the standard model is still step 06 (phase 5).
   - Alternative: match column names to glossary names and aliases. It cannot link `traded_qty` to `volume`. Open question 1.
9. **What a profile holds.**
   - The file: row count; per column, in file order: name, DuckDB type, null count, null rate, distinct count, and the declared field it holds (or none).
   - Against the spec: columns in the file that the spec does not declare; declared columns missing from the file.
   - Against each requirement: per requested element or ticked term: declared by the source or not, its column, whether the column is in the file, its null rate and distinct count; and the list of what is missing.
   - The spec as it was read, so the row is complete evidence if the catalogue changes later.
10. **`profiled` or `spec_only_low_confidence`.**
    - `profiled`: DuckDB read the file as a table with at least one data row.
    - `spec_only_low_confidence`: it did not. The file has a header and no data rows, is not a CSV table, is not a `.csv`, or is missing from the blob store. The profile then holds the spec, coverage from the spec alone (declared or not), the header's column names if one was read, no statistics, and `detail`: why.
    - Low confidence has one consequence: `meets` cannot be signed on it (assumption 20; open question 4).
11. **No connection is held while DuckDB runs.** Three parts, as for the fetch:
    1. `profile_plan()` reads: the feed is a candidate; its latest `fetched` row (note 1); the requirements that select it, with their elements and terms; the catalogue entry.
    2. The API ends that transaction. `run_profile()` gets the bytes, runs DuckDB and computes coverage. No session.
    3. `record_profile()` locks the feed row (`FOR NO KEY UPDATE`), checks again that it is a candidate, takes the next version and writes.
    - A newer file that arrives during the run does not stop the write: the profile names the fetch it read.
12. **Storage: `feed_profiles`, versioned per feed, append-only.**
    - Every run is a new row with the next version; the highest version is current. Profiling the same file twice gives two versions.
    - A row names its feed, the `source_fetches` row it read, the outcome, who started it and the DuckDB version.
    - The database refuses UPDATE, DELETE and TRUNCATE, a version that is not the next one, and a fetch that is not a `fetched` row of the same feed (section 6).
13. **What is not an outcome.**
    - 409: the feed is not a candidate (`FeedNotACandidate`); it has no fetched file (`FeedHasNoFile`); its source left the catalogue (`UnknownSource`).
    - 404: no such feed. 503: a stub adapter, as today.

**The ProfilerAgent**

14. **`agents/profiler.py`.** `ProfilerAgent`: prompt `profiler`, autonomy A2, `approver_roles = ("backend_team",)`, input `ProfilerInput`, output `ProfilerOutput`; it defines only `to_proposals`. Schemas are in `core/domain/profiling.py`.
    - Input: the feed, the profile (assumption 9) and each requirement's title and requested items.
    - Output (extra fields forbidden): `summary`, `recommended_outcome` (`meets`, `meets_with_gaps`, `rejected`), `gaps`, `reasons`.
    - One `profile_summary` proposal, linked to the feed; its payload names the profile and holds the four output fields.
    - **Code computes, the agent summarises.** Every number shown to the backend team comes from the profile row. The agent's text is shown beside it, marked as the agent's.
15. **Mock rule `profiler`** (a pure function, as the others):
    - a requested item that is not in the file is a gap; a requested item whose column has nulls is a gap ("empty in 2 of 10 rows");
    - no gap: `meets`; gaps: `meets_with_gaps`; no requested item in the file at all: `rejected`, with that reason;
    - a spec-only profile: `meets_with_gaps` with the gap "no data rows were profiled", or `rejected` if the spec declares none of the requested items.
16. **The summary follows the model-call pattern.** `summary_input()` reads and checks; the API ends the transaction; `Agent.run(..., before_write=...)` calls the model; in `before_write` the step locks the feed and checks that it is still a candidate and that the profile is still its latest. A newer profile during the call gives `ProfileSuperseded`: 409, a discard row, no proposal.
    - One summary per profile: asking again for a profile that has one is 409 (`ProfileAlreadySummarised`).
17. **A model that does not answer gives 503 and keeps the profile.** The profile was committed by its own endpoint. backend-ui shows "Summarise" for a latest profile with no summary.

**Step 03: the sign-off**

18. **The sign-off is the backend team's decision on the `profile_summary` proposal (decision D1).**
    - The person chooses the outcome (`meets`, `meets_with_gaps` or `rejected`) and writes the gaps or reasons; nothing is copied from the agent unless the person keeps it. `feed_signoffs` holds the person's outcome.
    - **Approving the proposal means the person agrees with its `recommended_outcome`; rejecting it means the person chose another outcome.** One row in `approvals`, by a named person with the role `backend_team`. So a `rejected` sign-off approves a proposal that recommended `rejected`, and a `meets` sign-off rejects a proposal that recommended `meets_with_gaps`.
    - **No sign-off without a summary** (Q3). The reasons: every sign-off then has its proposal and model call in rule 4's audit trail, and there is one decision path. Not the reason: the database cannot check that the signer holds `backend_team`, because `approvals.role` is text written by code; it checks only that the signer is the person on the `backend_team` approval row. If the model is down, the sign-off waits (risk 3).
19. **`feed_signoffs`: versioned per feed, append-only.** Each row: the profile, the proposal, the outcome, `gaps`, `reasons`, who signed. This is where the reasons are kept.
20. **Input rules** (refused with 422, nothing written):
    - `meets`: no gap and no reason;
    - `meets_with_gaps`: at least one gap;
    - `rejected`: at least one reason;
    - `meets` on a spec-only profile is refused.
21. **Only the latest profile of a candidate feed is signed off.** Otherwise 409: no profile (`FeedNotProfiled`), no summary (`ProfileNotSummarised`), already decided (`ProposalAlreadyDecided`), not a candidate. A new file can be profiled and signed off again while the feed is a candidate; each sign-off is the next version.
22. **`meets`:** the feed stays a candidate, ready to register. The requirement stays `onboarding`. The requester sees no change and gets no notification (notifications only on a change).
23. **`meets_with_gaps`:** as `meets`, and each requester whose requirement selects the feed is told: the page shows the gaps under the unchanged onboarding message, and one in-app notification is written.
24. **`rejected`:** in one transaction:
    - the feed becomes `rejected`. This is final in phase 4 (risk 6);
    - every requirement that selects the feed now, `onboarding` or `source_selected`, gets a `released` row with the new reason `candidate_rejected`, naming nobody: the person is on the sign-off row;
    - it moves to `awaiting_source_choice` if a listed source can still be picked, otherwise to `no_source_found`, exactly as a phase 3 release does. Two new moves: `onboarding → awaiting_source_choice` and `onboarding → no_source_found` (17 moves);
    - its requester is told: the page message with the reasons, and one in-app notification. The backend team gets the usual "Source released" notification.
    - The requirement itself is not rejected: its source is. The status `rejected` stays unused (open question 5).
25. **A rejected source is not offered again, to anyone.**
    - "Known source?" does not count it; S2 does not list it; a listed source that was rejected since is not shown; a pick of it is 409 (`SourceRejected`).
    - A requirement that waits on a list whose only source was rejected is closed by the daily job, as for a source that left the catalogue (phase 3, review fix 7).
    - The sign-off takes the advisory lock on the source code that a selection takes (`feed_for`), so a pick and a rejection of the same source run one after the other.
26. **The requester's messages, exact text (wording to approve; open question 6):**
    - `meets_with_gaps`, under "Your data is getting onboarded; we will notify you once it is ready.": "The backend team accepted <source> with gaps: <gap>; <gap>."
    - `rejected`, a source left to pick: "The backend team rejected <source>: <reason>; <reason>. Pick another source."
    - `rejected`, none left: "The backend team rejected <source>: <reason>; <reason>. No other source is listed. Add a note and ask for a new scope."
27. **What the database enforces on a sign-off** (section 6): its version is the next one; the feed is a candidate; the profile is the feed's latest; the proposal is a `profile_summary` proposal of that feed that names that profile; the proposal is decided; **an approved proposal needs every required role's approval and no rejection (the `0005` rules) and `outcome = payload->>'recommended_outcome'`; a rejected proposal needs a `rejected` approval and an outcome that differs (D1)**; the signer is the person on the proposal's `backend_team` approval row; a spec-only profile cannot get `meets`; gaps and reasons fit the outcome.

**Step 04: register**

28. **Register needs an accepting sign-off on the latest profile.** The feed's latest sign-off is `meets` or `meets_with_gaps`, and no newer profile waits. Otherwise 409 (`FeedNotSignedOff`).
29. **What is registered.** One `feed_registrations` row (versioned, append-only; phase 4 writes version 1): the owner, the licence, permitted use and redistribution **copied from the catalogue entry at that moment** (note 3), the visibility, the sign-off, and who registered. Then the feed row: `registered`, `owner_id`, `registered_at`. The owner is told in-app. The requirements stay `onboarding`.
30. **`no_owner`:** no owner is named and the feed has none, or the named user does not hold `data_owner`. Nothing is written; the feed stays a signed-off candidate.
31. **Who may see it: `open` or `restricted`**, the two words products already use. The feed page shows, for each requirement, the conditions its requester accepted (`source_choices.accepted_conditions`), so the person who registers can restrict access knowingly (note 4). Phase 7 gives the product the registration's visibility; grants stay there (open question 8).
32. **`duplicate_linked` compares the source code with the registered feeds.**
    - One feed per source, so a second feed for a source can never exist. What can happen: a requirement is handed to a source (a known source, or a pick) whose feed is **already registered**.
    - Then step 04 answers `duplicate_linked` at once, by rule: nothing is fetched, profiled, signed off or registered again. The requirement is linked to the registered feed (its `source_choices` row) and moves from `source_selected` to `onboarding`. The backend team is told once. The requester sees the onboarding message.
    - It runs right after the selection is committed, in place of the first fetch, in its own transaction.
    - People decided when the feed was registered, so nobody is asked again, as for a known source.
    - Not compared: two catalogue codes that read the same file. Open question 2.
33. **A registered feed is a regular feed** (note 4a). `FeedRepository.list_to_fetch()` keeps its two cases; a feed registered by step 04 is fetched daily even when every requirement has released it.
34. **What the database enforces on registration** (section 6):
    - a `feed_registrations` row needs the feed's latest sign-off, accepting, on the feed's latest profile;
    - **`feeds.stage` is guarded:** a new feed is a candidate; `candidate → registered` needs a registration row with the same owner; `candidate → rejected` needs the feed's latest sign-off to be `rejected`; no other change of stage is allowed;
    - a selection of a rejected feed is refused; a `candidate_rejected` release needs a rejected feed.
    - Not in the database: that the owner holds `data_owner`, and that the licence equals the catalogue's. Identity and the catalogue are outside it. Code and tests only.
35. **Existing tests that register a feed directly move to one helper.** 16 call sites in 7 test files call `FeedRepository.register()`, most of them on a bare candidate; the new guard refuses that. `tests/onboarded.py::registered_feed()` walks the real chain through the repositories (a fetch, a profile, an approved proposal, a sign-off, a registration). No trigger is bypassed.

**The daily job**

36. **A catalogue that fails to load does not stop registered feeds** (note 4d). On a load error the run fetches registered feeds only, applies no N-day release and closes no source list, and tells the backend team in-app, once per run, with the error. Candidate feeds wait for the next run with a good catalogue.

**Data, API, UI, documents**

37. **One migration, `0014_profile_signoff_register.py`.** It adds the three tables and their guards, the stage guard on `feeds`, and the third release reason: `ck_source_choices_reason` is dropped and created again (note 4c). It is developed against the test database, which is rebuilt on every run, and reaches the application database at checkpoint 2. After that, any fix is `0015`. The five feeds in the application database are candidates and its two releases are `requester_asked`, so nothing existing breaks the new guards.
38. **No new requirement status; two new moves** (assumption 24). `REQUIREMENT_TRANSITIONS` and its two tests change with them.
39. **API** (section 9). Six Amaze endpoints, four of which commit. `backend_team` for the actions; any backend role for the reads. Named conflicts give 409, refused input 422, a missing feed 404. The consumer router's `_read` and `_in_transaction` move to `apps/api/transactions.py` so both routers use one helper. **The move is its own commit, first in task 9: no behaviour change, and the consumer tests are not changed.**
40. **UI** (section 10). backend-ui gets a Feed view; consumer-ui shows gaps and rejections. No new npm package.
41. **Synthetic data only.** Two new sources and files (section 8). They pass the real-data checks; codes start with `syn_`.
42. **Commit-visibility tests: six, happy path only.** One for each new endpoint that commits, and one for each new commit inside Find a source and Pick (the link to a registered feed is its own transaction, which the existing tests do not reach). **Expected added test time: 9 to 26 seconds** (1.5 to 4.3 s each, measured in phase 3).

    | Endpoint | Test | What the separate connection sees |
    |---|---|---|
    | `POST .../profile` | `test_profile_commits_the_profile` | the `feed_profiles` row, version 1 |
    | `POST .../profile/summary` | `test_summary_commits_the_proposal` | the `proposal_log` row and the `profile_summary` proposal |
    | `POST .../signoff` | `test_signoff_commits_the_decision` | the approval and the `feed_signoffs` row |
    | `POST .../register` | `test_register_commits_the_registration` | the `feed_registrations` row and the feed `registered` |
    | `POST .../find-source` (link) | `test_find_source_commits_the_link_to_a_registered_feed` | the requirement `onboarding`, no new fetch row |
    | `POST .../pick` (link) | `test_pick_commits_the_link_to_a_registered_feed` | the approval, the `source_choices` row, the requirement `onboarding`, no new fetch row |
43. **Concurrency tests** (`test_concurrency.py`, each on its own schema, about 2 to 4 s each):
    - `test_no_connection_is_held_during_a_profiling_run` (`pool.checkedout() == 0` inside `BlobStore.get()`);
    - `test_no_connection_is_held_during_any_model_call` gains the ProfilerAgent's call;
    - `test_a_new_profile_during_the_summary_gives_409_and_a_discard_row`;
    - `test_two_signoffs_at_once_give_one_success_and_one_409`;
    - `test_a_pick_and_a_rejection_of_one_source_at_once_leave_nobody_on_a_rejected_feed`;
    - `test_a_rejection_and_the_daily_fetch_of_one_feed_at_once_do_not_deadlock`;
    - `test_a_rejection_and_a_requester_release_of_one_requirement_at_once_do_not_deadlock`.
    - In each of the last three, neither request deadlocks, and each gives one success or a 409.
44. **Documents** (task 14): `CLAUDE.md` (layout lines, the DuckDB pin), `BUILD_PLAN.md` (who signs off, what a rejection does, what `duplicate_linked` compares), `README.md` (the demo rows, `0014 (head)`, the new append-only tables), `requirements.txt`. No new setting, so `.env.example` does not change.
45. **I check the UI** by building it and by a scripted browser run with screenshots. The committed Playwright test stays in phase 7.
46. **One lock order, for every step that takes more than one lock** (plan change 1): the advisory lock on the source code (`pg_advisory_xact_lock(hashtext(code))`, as `feed_for`), then the feed row (`FOR NO KEY UPDATE`), then the requirement rows in id order. A step that needs only some of them takes those in the same order. The sign-off's rejection, a selection, the fetch's write and a release follow it; phase 3's code is changed where it does not (the fetch locks the feed, then its requirements, which already fits; a release locks the requirement only). The tests are the last three of assumption 43.

**Open questions, recommendation first**

1. **How coverage links a file column to a requested element.** Recommended: the catalogue states it (`columns`, assumption 8). Alternative: match column names to glossary names and aliases; no catalogue change, but most columns of the sample files would not match, and it is the mapping agent's job in phase 5.
2. **What `duplicate_linked` compares.** Recommended: the source code only (assumption 32). Alternative: also treat a candidate that reads the same `connector` and `source_ref` as a registered feed as a duplicate, found at Register. It needs a way to move a requirement to another feed (a new basis and reason on `source_choices`, a stage for the unused candidate), and the requester accepted the conditions of the other source.
3. **A sign-off without the agent's summary.** Recommended: no (assumption 18). Alternative: allow a sign-off with no proposal when the model is unavailable. **Answer: as recommended. The reasons are rule 4's audit trail and one decision path; the database cannot check role membership (`approvals.role` is text).**
4. **`meets` on a spec-only profile.** Recommended: refused (assumptions 10 and 20). Alternative: allowed; `spec_only_low_confidence` is then a label only.
5. **What a rejection does to the requirement.** Recommended: the source is released and the requester picks again or asks for a new scope (assumption 24). Alternative: the requirement becomes `rejected`, final; the requester raises a new request.
6. **The three requester messages of assumption 26.** Recommended: as written. **Answer: approved word for word, and so is phase 3 fix 7's "The listed sources are no longer offered. Add a note and ask for a new scope."**
7. **The DuckDB version.** Recommended: `1.5.6`. Alternative: `2.0.0`, the newest. **Answer: `duckdb==1.5.6`.**

Questions 1, 2, 4, 5 and 8: **answered as recommended.**
8. **"Who may see it".** Recommended: `open` or `restricted` at registration; who is granted access is decided with the product in phase 7 (assumption 31). Alternative: name roles or users at registration now; that adds a grants table for feeds in this phase.

## 5. Steps and typed results

### 5.1 How a feed and its requirements move

| Feed | Action | Outcome | Feed after | Requirements that select it |
|---|---|---|---|---|
| candidate, has a fetched file | Profile (backend team; DuckDB) | `profiled` or `spec_only_low_confidence` | candidate; a new profile version | stay `onboarding` |
| candidate, latest profile has no summary | Summarise (ProfilerAgent) | a proposal, no outcome | candidate | stay |
| candidate, summary pending | Sign off | `meets` | candidate, signed off | stay; nothing new is shown |
| | | `meets_with_gaps` | candidate, signed off | stay; the requester is told the gaps |
| | | `rejected` | `rejected`, final | released (`candidate_rejected`): `awaiting_source_choice`, or `no_source_found` if no listed source is left; the requester is told the reasons |
| candidate, latest sign-off accepts | Register | `registered` | `registered` | stay `onboarding` |
| | | `no_owner` | unchanged | stay |
| registered | a requirement is handed to its source (rule) | `duplicate_linked` | unchanged | that requirement: `source_selected → onboarding`, no fetch |

`REQUIREMENT_TRANSITIONS` gains `onboarding → awaiting_source_choice` and `onboarding → no_source_found`: 17 moves. `ready` and `access_restricted` are phase 7.

### 5.2 The shapes

```python
# core/steps/s02_profile.py
class Profiled(StepResult):               # outcome "profiled"
    profile: FeedProfile
class SpecOnlyLowConfidence(StepResult):  # outcome "spec_only_low_confidence"
    profile: FeedProfile                  # the spec and coverage from it; no statistics
    detail: str                           # why the file gave nothing to profile

# core/steps/s03_signoff.py
class Meets(StepResult):                  # outcome "meets"
    signoff: FeedSignoff
class MeetsWithGaps(StepResult):          # outcome "meets_with_gaps"
    signoff: FeedSignoff                  # holds the gaps
    told: list[UUID]                      # requirements whose requester was told
class Rejected(StepResult):               # outcome "rejected"
    signoff: FeedSignoff                  # holds the reasons
    released: list[Released]              # one per requirement that selected the feed

# core/steps/s04_register.py
class Registered(StepResult):             # outcome "registered"
    feed: Feed
    registration: FeedRegistration
class DuplicateLinked(StepResult):        # outcome "duplicate_linked"
    feed_code: str
    requirement_id: UUID
    owner_id: str
    registered_at: datetime
class NoOwner(StepResult):                # outcome "no_owner"
    reason: Literal["none_named", "not_a_data_owner"]
```

Each step's results form a union discriminated on `outcome`. Steps flush and never commit.

| Module | Function | Writes |
|---|---|---|
| `profiling.py` | `profile_csv(path)` | nothing: DuckDB in memory, the file's statistics |
| `profiling.py` | `coverage_of(requirement, spec, file_profile)` | nothing (pure) |
| `s02_profile.py` | `profile_plan(session, feed_id, catalogue)` | nothing (read) |
| `s02_profile.py` | `run_profile(plan, blob_store)` | no database; `BlobStore.get()`, a temporary file, DuckDB |
| `s02_profile.py` | `record_profile(session, attempt, user)` | the `feed_profiles` row, next version |
| `s02_profile.py` | `summary_input(session, feed_id)` | nothing (read) |
| `s02_profile.py` | `record_summary(session, agent, agent_input)` | through `Agent.run()`: the call row and one proposal |
| `s03_signoff.py` | `sign_off(session, feed_id, user, outcome, gaps, reasons, ...)` | the approval and the `feed_signoffs` row; for `rejected`: the feed's stage, the releases, the statuses; notifications |
| `s04_register.py` | `register_feed(session, feed_id, user, owner, visibility, catalogue, ...)` | the `feed_registrations` row; the feed's stage, owner and date; one notification |
| `s04_register.py` | `link_to_registered_feed(session, requirement_id, feed_id, ...)` | the status `onboarding`; one notification |
| `feed_page.py` | `feed_page(session, feed, catalogue)` | nothing (read): what the backend team sees |
| `release.py` | `release_source(..., reason="candidate_rejected")` | as in phase 3, from `onboarding` too |

### 5.3 Rule 1: who decides what

| Step | Who decides | Proposal | Approval rows | What the decision allows, and the database guard |
|---|---|---|---|---|
| 02 profile | Nobody: code computes | none | none | A `feed_profiles` row: next version, a `fetched` row of the same feed |
| 02 summary | Nobody yet | one `profile_summary` proposal per profile; roles `[backend_team]` | none | Nothing: a pending proposal changes nothing |
| 03 sign-off | One person with `backend_team` | that proposal | approved if the person's outcome is the recommended one, rejected if it differs (D1) | A `feed_signoffs` row holding the person's outcome: the list of assumption 27. For outcome `rejected`: the feed's stage, and the releases, which need a rejected feed |
| 04 register | One person with `backend_team`, naming an owner | none of its own: it rests on the sign-off | none | A `feed_registrations` row: needs the latest, accepting sign-off. The stage `registered`: needs that row |
| 04 `duplicate_linked` | Nobody at request time: people decided at registration | none | none | A requirement moves to `onboarding`; nothing is registered |

### 5.4 Every outcome name and its test

`test_step_results.py` reads the lines "02 Profile", "03 Sign off" and "04 Register" from `CLAUDE.md` and compares them with the unions, in order.

| Outcome | Named test |
|---|---|
| `profiled` | `test_s02_profile.py::test_profiled_holds_columns_types_null_rates_distinct_counts_and_coverage` |
| `spec_only_low_confidence` | `test_s02_profile.py::test_spec_only_low_confidence_when_the_file_gives_no_data_rows` (four cases: header only, not a table, not a `.csv`, blob missing) |
| `meets` | `test_s03_signoff.py::test_meets_keeps_the_candidate_and_shows_the_requester_nothing_new` |
| `meets_with_gaps` | `test_s03_signoff.py::test_meets_with_gaps_keeps_the_gaps_and_tells_the_requester` |
| `rejected` | `test_s03_signoff.py::test_rejected_keeps_the_reasons_releases_the_requirement_and_tells_the_requester` |
| `registered` | `test_s04_register.py::test_registered_with_owner_licence_and_visibility` |
| `duplicate_linked` | `test_s04_register.py::test_duplicate_linked_when_the_selected_source_is_already_a_registered_feed` |
| `no_owner` | `test_s04_register.py::test_no_owner_when_none_is_named_or_the_named_user_is_not_a_data_owner` |

### 5.5 Carried notes and other named tests

| Item | Named test |
|---|---|
| Note 1: the latest fetched file | `test_s02_profile.py::test_the_profile_reads_the_feeds_latest_fetched_file` |
| Note 2: a rejected source is skipped | `test_known_source.py::test_a_rejected_source_is_not_a_known_source`; `test_s2_sources.py::test_a_rejected_source_is_not_listed`; `test_s3_pick.py::test_picking_a_rejected_source_is_409` |
| Note 2: both places a released requirement goes | `test_s03_signoff.py`: `test_a_rejection_goes_to_awaiting_source_choice_when_a_listed_source_is_left`, `test_a_rejection_goes_to_no_source_found_when_none_is_left` |
| Note 3: the licence is copied | `test_s04_register.py::test_registration_copies_the_licence_from_the_catalogue` |
| Note 4: accepted conditions are shown | `test_api_amaze_onboarding.py::test_the_feed_page_shows_the_conditions_each_requester_accepted` |
| Note 4a: registered feeds are always fetched | `test_jobs.py::test_a_feed_registered_by_step_04_is_fetched_with_no_selection` |
| Note 4b: current choices, not `feeds.requirement_id` | `test_s03_signoff.py::test_only_requirements_that_select_the_feed_now_are_told_and_released` (the first requirement released the feed; a second selects it) |
| Note 4c: the third reason | `test_migrations.py::test_0014_adds_the_third_release_reason_and_keeps_existing_rows` |
| Note 4d: the catalogue fails to load | `test_jobs.py`: `test_a_catalogue_that_fails_to_load_still_fetches_registered_feeds`, `test_a_catalogue_that_fails_to_load_skips_candidates_and_tells_the_backend_team` |
| DuckDB through the port | `test_s02_profile.py`: `test_the_profile_uses_only_blob_store_get`, `test_the_temporary_file_is_removed_after_a_profile_and_after_a_failure` |
| Low confidence | `test_s03_signoff.py::test_meets_is_refused_on_a_spec_only_profile` |
| The agent | `test_agent_profiler.py`: `test_one_profile_summary_proposal_for_the_backend_team`, `test_the_proposal_names_the_profile_and_carries_no_statistics` |
| The summary | `test_s02_profile.py`: `test_a_newer_profile_during_the_call_writes_no_proposal`, `test_a_profile_is_summarised_once` |
| 503 | `test_api_amaze_onboarding.py::test_a_model_that_does_not_answer_gives_503_and_keeps_the_profile` |
| 409 and 403 | `test_api_amaze_onboarding.py`: `test_a_wrong_stage_or_step_is_409` (eight cases), `test_only_the_backend_team_acts` |
| Guards | `test_db_onboarding_guards.py` (section 6); `test_migrations.py::test_every_check_on_a_nullable_column_is_reviewed` with the new checks |
| D1, raw SQL | `test_db_onboarding_guards.py`: `test_an_approved_summary_needs_the_signoff_to_hold_the_recommended_outcome`, `test_a_rejected_summary_needs_the_signoff_to_hold_another_outcome` (each with the allowed and the refused case) |
| D1, the step | `test_s03_signoff.py`: `test_agreeing_with_the_recommendation_approves_the_summary`, `test_choosing_another_outcome_rejects_the_summary` |
| Lock order | the last three tests of assumption 43 |

## 6. Data model

### Migration `0014_profile_signoff_register.py`

| Table | Change | Rules |
|---|---|---|
| `feed_profiles` (new) | feed_id, version, fetch_id, outcome, row_count, columns (jsonb), coverage (jsonb), spec (jsonb), detail, engine, profiled_by | **Append-only.** Unique (feed, version). `profiled`: a row count of at least 1, at least one column, no detail. `spec_only_low_confidence`: a row count of 0 or none, and a detail |
| `feed_signoffs` (new) | feed_id, version, profile_id, proposal_id, outcome, gaps (text[]), reasons (text[]), signed_off_by | **Append-only.** Unique (feed, version), unique (profile), unique (proposal). `meets`: no gap, no reason. `meets_with_gaps`: at least one gap, no reason. `rejected`: at least one reason |
| `feed_registrations` (new) | feed_id, version, signoff_id, owner_id, licence, permitted_use, redistribution, visibility, registered_by | **Append-only.** Unique (feed, version). visibility is `open` or `restricted`; every text is present |
| `feeds` | no column | a stage guard (below) |
| `source_choices` | the reason list gains `candidate_rejected` | the check is dropped and created again; a new trigger (below) |

**Triggers.** Letters fix the order, as in `0009`. Each line has a raw-SQL test in `test_db_onboarding_guards.py`, and each trigger is shown failing when removed.

| Trigger | Refuses |
|---|---|
| `trg_feed_profiles_a_version` | a version that is not the feed's highest plus one |
| `trg_feed_profiles_b_fetch` | a fetch that is not a `fetched` row of the same feed |
| `trg_feed_signoffs_a_guard` | a version that is not the next; a feed that is not a candidate; a profile of another feed or not the feed's latest; a proposal of another feed or naming another profile; `meets` on a spec-only profile; a signer who is not the `backend_team` approver of the proposal |
| `trg_feed_signoffs_b_decision` (new function `amaze_require_signoff_decision`, D1) | a proposal that is pending; an approved proposal without every required role's approval, with a rejection, or whose `recommended_outcome` is not the sign-off's outcome; a rejected proposal without a `rejected` approval, or whose `recommended_outcome` is the sign-off's outcome. One trigger: a `WHEN` clause cannot read the proposal's status |
| `trg_feed_registrations_a_guard` | a version that is not the next; a sign-off of another feed, not the feed's latest, not accepting, or not on the feed's latest profile |
| `trg_feeds_a_stage` (insert, and update of stage or owner) | a new feed that is not a candidate; `registered` without a registration row of the same owner; `rejected` without a latest sign-off `rejected`; any other change of stage |
| `trg_source_choices_f_feed_stage` | a selection of a rejected feed; a `candidate_rejected` release of a feed that is not rejected |
| `append_only`, `no_truncate` on the three new tables | UPDATE, DELETE, TRUNCATE |

- **Every check on a nullable column spells out NULL** (`IS NULL`, `IS NOT NULL`, `COALESCE`, `IS DISTINCT FROM`), has a NULL case in its test, and is added to `REVIEWED_NULLABLE_CHECKS`.
- **Downgrade:** stops if any profile, sign-off or registration exists, or any `candidate_rejected` release. `0013` cannot hold those.
- **PostgreSQL 14 or older only:** check constraints, `text[]` with `cardinality`, `jsonb` with `jsonb_typeof` and `jsonb_array_length` (9.4), triggers on `UPDATE OF` columns and with `WHEN` (9.0), plpgsql. Nothing from 15 or later.

## 7. Domain models and repositories

| Item | Change |
|---|---|
| `core/domain/profiling.py` (new) | `FileColumn`, `FileProfile`, `RequirementCoverage`, `FeedProfile`, `FeedSignoff`, their drafts, `ProfilerInput`, `ProfilerOutput` |
| `core/domain/feed.py` | `FeedRegistration`, its draft, `FeedVisibility` |
| `core/domain/sourcing.py` | `ReleaseReason` gains `candidate_rejected` |
| `core/domain/requirement.py` | the two new moves |
| `ports/connectors/catalogue.py` | `SourceEntry.columns` and its loader rules |
| `FeedProfileRepository` (new) | `add_version(...)`, `latest(feed_id)`, `get(id)`, `history(feed_id)` |
| `FeedSignoffRepository` (new) | `add_version(...)`, `latest(feed_id)`, `history(feed_id)` |
| `FeedRegistrationRepository` (new) | `add_version(...)`, `current(feed_id)` |
| `FeedRepository` | `reject(feed_id)`; `rejected_source_codes()`; `register()` unchanged, now called only by step 04 |
| `SourceChoiceRepository` | `record_release()` accepts `candidate_rejected` (names nobody) |
| `ProposalRepository` | `list_for_feed(feed_id, kind, status)` |
| `core/steps/known_source.py`, `s2_sources.py`, `s3_pick.py` | skip a rejected source (assumption 25) |
| `core/steps/release.py`, `requirement_page.py` | the third reason; the gaps and the rejection messages |
| `apps/api/jobs.py` | the catalogue load error (assumption 36) |

New named errors: `FeedHasNoFile`, `FeedNotProfiled`, `ProfileNotSummarised`, `ProfileAlreadySummarised`, `ProfileSuperseded`, `FeedNotSignedOff`, `SourceRejected` (all 409). `FeedNotACandidate` (409) and `FeedNotFound` (404) exist.

## 8. Synthetic sources and the demo paths

**`columns` is added to the six existing entries** from their files' headers. `syn_open_esg_scores` keeps one column the spec does not declare (`score_year`), so an undeclared column can be seen.

**Two new sources**, each for an outcome the existing files cannot show:

| Code | Visibility, channel | Declares | File |
|---|---|---|---|
| `syn_instrument_lifecycle` | public, market data vendors | instrument_id, issue_date, instrument_status | ten rows; `issued_on` is empty in two: a null rate to show, and a gap |
| `syn_listing_details` | public, exchanges | instrument_id, exchange_mic, lot_size | **a header and no rows, on purpose**: `spec_only_low_confidence` |

Neither is known; neither needs an external approval. Phase 3's tests that count six sources are updated.

Signed in as Blake Backend in backend-ui, and Riley Requester in consumer-ui:

| # | What | What happens |
|---|---|---|
| L | Feed `syn_exchange_volumes` (it has files already) | Profile: `profiled`, 3 columns, 10 rows, `volume` covered. The agent recommends `meets`. Sign off `meets`. Register with no owner: `no_owner`. Register with Devon Owner, open: `registered`. Riley's page is unchanged |
| M | Riley requests `issue date`, picks `syn_instrument_lifecycle` | Profile: `issued_on` 20% null. The agent recommends `meets_with_gaps`. Sign off with that gap. Riley's page shows the gap under the onboarding message. Register, restricted |
| N | Riley requests `lot size`, picks `syn_listing_details` | Profile: `spec_only_low_confidence`; `meets` is not offered. Sign off `rejected`, "No data to check". Riley's page: the reason, and "No other source is listed. Add a note and ask for a new scope." |
| O | Feed `syn_vendor_eod_prices` (two requirements select it) | Sign off `rejected`. The requirement that still has `syn_backup_eod_prices` listed reads "... Pick another source."; the one that released the backup reads "... No other source is listed ...". `open price` no longer lists the vendor |
| P | Riley requests `volume` again, after row L | Find a source: `known_source`, then `duplicate_linked`. The onboarding message; no new fetch; the feed page lists the new requirement |

## 9. API

**Amaze** (`apps/api/routers/amaze.py`):

| Endpoint | Who | Does |
|---|---|---|
| `GET /api/amaze/feeds/{id}` | any backend role | the feed page: source and conditions, the requirements and what each requester accepted, the last fetch, the latest profile and its history, the summary, the sign-offs, the registration, the next action |
| `GET /api/amaze/owners` | any backend role | the users who hold `data_owner`, from the Identity port |
| `POST /api/amaze/feeds/{id}/profile` | `backend_team` | step 02: `profiled` or `spec_only_low_confidence`. Commits |
| `POST /api/amaze/feeds/{id}/profile/summary` | `backend_team` | the ProfilerAgent's proposal for the latest profile. Commits |
| `POST /api/amaze/feeds/{id}/signoff` `{outcome, gaps, reasons}` | `backend_team` | step 03: `meets`, `meets_with_gaps` or `rejected`. Commits |
| `POST /api/amaze/feeds/{id}/register` `{owner_id, visibility}` | `backend_team` | step 04: `registered` or `no_owner`. Commits |

**Consumer:** `find-source` and `pick` return `registration` (`duplicate_linked`, or none) and run no first fetch when the feed is registered. `GET .../requirements/{id}` also returns the gaps of an accepted sign-off and the reasons of a rejection.

- 401 if not signed in; 403 for the requester, and for a backend role without `backend_team` on the four actions; 404 for an unknown feed; 409 for a named conflict; 422 for refused input; 503 for `LLMUnavailable` or a stub adapter.
- The ProfilerAgent, the blob store, the catalogue and the notifier are FastAPI dependencies, so tests replace them.

## 10. UI

**backend-ui**

1. **Feed registry:** each row links to the feed; the filter gains Rejected.
2. **Feed** (`#/feed/<id>`), in the order of the work:
   - the source, its conditions, and each requirement with the conditions its requester accepted;
   - the latest file, and Profile;
   - the profile: one row per column (name, type, null rate, distinct count, declared as), undeclared and missing columns, coverage per requirement; a clear "low confidence: spec only" notice with its reason;
   - the agent's summary and recommended outcome, marked as the agent's; Summarise when there is none;
   - Sign off: the three outcomes (`meets` disabled on a spec-only profile), one line per gap or reason, and next to the inputs: "Gaps and reasons are shown to the requester.";
   - Register: the owner (a list of data owners), open or restricted; the licence shown, not typed;
   - the history of profiles and sign-offs.
   - The four actions show only for `backend_team`.

**consumer-ui, Requirement:** the gaps under the onboarding message; the rejection message with its reasons; the history labels a `candidate_rejected` release "(rejected by the backend team)".

Checked by `npm run build` in both projects and a scripted browser run, rows L to P. Screenshots in `.playwright-mcp/`: `phase4-L.png` to `phase4-P.png`.

## 11. Files

```
docs/plans/phase-4.md                         this plan
docs/ui-polish.md                             edit   three items now; more at task 15
BUILD_PLAN.md  CLAUDE.md  README.md           edit   task 14
requirements.txt                              edit   pin duckdb
migrations/versions/0014_profile_signoff_register.py   new
fixtures/sources/catalogue.json               edit   columns; two sources
fixtures/sources/files/instrument_lifecycle.csv  listing_details.csv   new
agents/
  profiler.py  prompts/profiler.v1.yaml       new
  registry.py                                 edit
ports/
  connectors/catalogue.py                     edit   columns
  llm/mock_rules.py                           edit   profiler
core/
  domain/      profiling.py                   new
               feed.py  sourcing.py  requirement.py              edit
  repositories/ feed_profiles.py  feed_signoffs.py  feed_registrations.py   new
                tables.py  errors.py  feeds.py  source_choices.py  proposals.py   edit
  steps/       profiling.py  s02_profile.py  s03_signoff.py  s04_register.py  feed_page.py   new
               known_source.py  s2_sources.py  s3_pick.py  release.py  requirement_page.py   edit
apps/api/
  transactions.py                             new    the shared _read and _in_transaction
  deps.py  jobs.py  routers/amaze.py  routers/consumer.py        edit
apps/backend-ui/src/
  App.tsx  api.ts  views/FeedRegistryView.tsx                    edit
  views/FeedView.tsx                          new
apps/consumer-ui/src/
  api.ts  views/RequirementView.tsx           edit
tests/
  onboarded.py                                new    registered_feed() helper
  test_profiling.py  test_s02_profile.py  test_s03_signoff.py  test_s04_register.py
  test_agent_profiler.py  test_repo_feed_profiles.py  test_repo_feed_signoffs.py
  test_repo_feed_registrations.py  test_db_onboarding_guards.py
  test_api_amaze_onboarding.py                new
  step_chains.py  stand_in_agents.py  test_migrations.py  test_append_only.py
  test_domain_models.py  test_step_results.py  test_source_catalogue.py  test_mock_rules.py
  test_prompts.py  test_repo_feeds.py  test_repo_source_choices.py  test_repo_requirements.py
  test_repo_loads.py  test_coverage.py  test_routing.py  test_request_data.py
  test_known_source.py  test_s2_sources.py  test_s3_pick.py  test_release.py  test_jobs.py
  test_api_consumer.py  test_api_consumer_sourcing.py  test_commit_visibility.py
  test_concurrency.py                         edit
```

## 12. Tasks, in order

Each task: tests first and shown failing, then code, then its check, then one commit. Files are edited only with the file tools. I stop at the four checkpoints and continue without stopping between them.

**Method for every task (your instruction on approval; the plugins do not load, so it is followed by hand):**
- Before code: list any assumption the plan does not state; if it changes scope, stop and ask. Touch only the files in section 11; any other file is a deviation recorded in section 17. The simplest code that passes.
- RED: the tests written and run, the failing summary line pasted. GREEN: the least code that passes, the summary line pasted. Refactor only with tests green.
- An unexpected failure: reproduce, find the root cause, write it down, then fix. No guess-fixes; no weakened test.
- Every claim in a report is backed by pasted output. Guards and commits are shown failing when removed and restored with the Edit tool; `grep PROOF` finds nothing.
- A checkpoint report also has `git --no-optional-locks diff --stat <last checkpoint>..HEAD`, the files changed outside section 11 (or "none"), the test count and time, and the deviations.

| # | Task | Tests written first | Check |
|---|---|---|---|
| 0 | This plan updated with the approval's decisions and changes | none | one commit |
| 1 | DuckDB: the pin, and `profile_csv()` in `core/steps/profiling.py` | `test_profiling.py`: file order, types, null count and rate, distinct count, an empty cell is a null, the same file twice gives the same profile, a hostile header, a header only, not a table | `pip install --only-binary=:all:`; pytest |
| 2 | The spec: catalogue `columns`, the two new sources and files, `coverage_of()` | `test_source_catalogue.py`, `test_profiling.py` | pytest |
| 3 | Migration `0014`; tables, domain models, repositories; the two moves; the third reason; `tests/onboarded.py` and the 16 call sites | `test_migrations.py`, `test_append_only.py`, `test_domain_models.py`, the three `test_repo_feed_*.py`, `test_repo_feeds.py`, `test_repo_source_choices.py`, `test_repo_requirements.py`, `test_db_onboarding_guards.py` | pytest; each new trigger removed in turn and its tests shown failing. **CHECKPOINT 1:** pytest summary, the guard proofs, the profile of one committed sample file as JSON |
| 4 | Step 02: `profile_plan`, `run_profile`, `record_profile` | `test_s02_profile.py`, `test_step_results.py` | pytest |
| 5 | ProfilerAgent, its prompt and hash, the mock rule; `summary_input`, `record_summary` | `test_agent_profiler.py`, `test_mock_rules.py`, `test_prompts.py`, `test_s02_profile.py` | pytest |
| 6 | Step 03: the sign-off, the rejection's releases and messages; rejected sources skipped | `test_s03_signoff.py`, `test_release.py`, `test_known_source.py`, `test_s2_sources.py`, `test_s3_pick.py` | pytest |
| 7 | Step 04: `register_feed`, `no_owner`, `link_to_registered_feed` | `test_s04_register.py` | pytest |
| 8 | The daily job: the catalogue load error; a feed registered by step 04 | `test_jobs.py`, `test_repo_feeds.py` | pytest; `alembic upgrade head`; `0014 (head)`. **CHECKPOINT 2:** pytest summary, the eight outcome tests by name, `alembic current`, the new triggers listed, the five feeds still candidates |
| 9 | Amaze API: the feed page, owners, the four actions; `apps/api/transactions.py`; the role check | `test_api_amaze_onboarding.py` | pytest |
| 10 | Consumer API: the link after a selection; gaps and rejection on the requirement page | `test_api_consumer_sourcing.py`, `test_api_consumer.py` | pytest |
| 11 | Commit visibility (five) and concurrency (five) | `test_commit_visibility.py`, `test_concurrency.py` | pytest; each commit removed in turn and its test shown failing. **CHECKPOINT 3:** pytest summary and its time, the two done-when tests' output, the added test time |
| 12 | backend-ui: the Feed view, the registry link and filter | none | build; browser run, rows L to P |
| 13 | consumer-ui: gaps, rejection, the history label | none | build; browser run, Riley's side of rows L to P |
| 14 | Documents: `CLAUDE.md`, `BUILD_PLAN.md`, `README.md`. `CLAUDE.md`'s plugin section says the plugins do not load in this setup, and that the Superpowers and Karpathy method is followed by hand | none | documents read back |
| 15 | UI polish items found, appended to `docs/ui-polish.md` (not fixed) | none | the file read back |
| 16 | Review against `CLAUDE.md`'s seven rules, the PostgreSQL 14 list and this plan. Octopus is not loaded; I review by hand unless you load it | none | findings in section 17. **CHECKPOINT 4:** section 13 results and the screenshots. Phase 5 is not started |

## 13. Commands that prove it is done

Run from `F:\amaze-cm-poc` in Windows PowerShell.

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m core.seed
.\.venv\Scripts\python -m pytest -v
.\.venv\Scripts\python -m alembic current
npm --prefix apps\backend-ui run build
npm --prefix apps\consumer-ui run build
.\scripts\dev.ps1
```

| Command | Expected |
|---|---|
| `pytest -v` | all pass; 0 failed, 0 skipped, 0 warnings |
| `alembic current` | `0014 (head)` |
| both builds | exit code 0 |
| `dev.ps1` | three `OK` lines and health `ok` |

Then in the browser: rows L to P of section 8. For row N, check that `meets` cannot be chosen. For row O, check both rejection messages. For row P, check that no new file appears under `data/blob/raw/syn_exchange_volumes/`.

## 14. Not in phase 4

- The glossary builder and mapping (phase 5). The catalogue's `columns` is not a mapping.
- Rules, go-live approval by the data owner, the run engine (phase 6).
- Loads, arrival and structure checks, certification, the product, "Ready" and "Access restricted" (phase 7).
- Grants on a feed, an access-request workflow, changing a registration (a second version), reopening a rejected feed.
- Profiling in the background or automatically after a fetch; a job queue.
- Parquet, JSON or Excel files; any file but CSV.
- A notifications view in either UI; email or Teams.
- Any real source, any internet access, Azure adapters (phase 8).
- The committed Playwright test (phase 7).

## 15. Risks

1. **The file is read into memory and copied to disk for each profile.** Fine for the PoC. A large file needs a streaming read on the port, and probably a size limit before that. Phase 8.
2. **Profiling and the model call run inside requests.** The mock answers at once; a large file or Azure OpenAI makes the request wait. Production needs a queue (as phase 3, risks 1 and 2).
3. **No sign-off without the agent's summary.** If the model is down, a feed waits. Open question 3.
4. **The profile is one file.** A source whose first file is unusual gets a sign-off on that file. A new file can be profiled again, but nothing asks for it.
5. **One sign-off serves every requirement on the feed.** Gaps are told to every requester who selects it, also to one whose own items are all covered.
6. **A rejected feed is final.** If the source improves, nothing reopens it; that needs a new rule. A rejection also empties the lists of other requirements that had only that source; they are closed by the daily job, not at once (`docs/ui-polish.md` has the same limit for a source that left the catalogue).
7. **The stage guard changes how tests make a registered feed.** 16 call sites move to a helper that writes five rows. Tests get slightly slower and the helper must be kept right.
8. **The catalogue's `columns` is a statement by the fixture's author.** A wrong entry gives a wrong coverage, shown as "missing from the file". Phase 5's approved mapping is the real link.
9. **Type detection is DuckDB's.** A column of numbers with one text value is read as text. The profile shows what DuckDB found, not what the source intended.
10. **The licence is copied at registration and not checked again.** A later change in the catalogue does not reach a registered feed; that needs a second registration version, which phase 4 does not write.
11. **`duplicate_linked` skips the first fetch.** If the registered feed's source is down that day, the requirement is still `onboarding`: its data is the registered feed's, whenever it next arrives.
12. **A catalogue that stays broken tells the backend team every day.** Once per run, on purpose, until it is fixed.
13. **Five commit-visibility and five concurrency tests add about 20 to 40 seconds** to every run, each on its own schema.
14. **DuckDB is a new binary dependency.** Its wheel must be available on the office laptop too; the pin and `--only-binary` check make a missing wheel fail at install, not at run time.
15. **A rejection on a spec-only profile is final and global** (row N). A header-only file gets `rejected`, the feed is rejected for every requirement, and nothing reopens it, though the source's next file might have data (risk 6).

## 16. Carried forward

Notes only; nothing here is built in phase 4.

**Phase 5**

1. The glossary builder runs on "new columns only": the profile's undeclared columns, and declared `other_fields`, are that list.
2. The mapping agent's input is the latest profile of a registered feed: column names, types and the spec's `columns` as a hint. Its approved mappings replace the spec as the link.

**Phase 6**

3. SQL rules proposed by an agent run in DuckDB. Before that, the DuckDB connection must be locked down (no file or network access beyond the one file); phase 4's SQL is fixed code, so it does not need it yet.
4. The data owner's approval at go-live is the first time the named owner decides anything.

**Phase 7**

5. The product takes the registration's visibility; grants and "Access restricted" are decided there.
6. A registered feed's daily fetch becomes a load; its structure check compares the file with the signed-off profile.

**Phase 8**

7. `AzureBlobStore.get()` is all step 02 needs. A streaming read and a size limit come with it (risk 1).
8. Profiling and the summary move to the queue with the other model calls.

## 17. Results and deviations (recorded during implementation)

### Tasks 0 to 3 (checkpoint 1)

Tests: 1146 at the start, 1240 at checkpoint 1. All pass, none skipped, no warnings (`1240 passed in 91.71s`).

| Task | RED | GREEN | Commit |
|---|---|---|---|
| 0 | | | `76653a0` |
| 1 | `1 error in 0.36s` (no module) | `9 passed in 0.50s` | `ab580e0` |
| 2 | `14 failed, 20 passed in 2.06s` (catalogue); `test_profiling.py` failed to collect | `50 passed in 2.29s` | `25c0689` |
| 3 | `7 errors in 1.28s` (no repositories, no helper) | `1240 passed in 87.52s` (full suite) | `d3b7af5` |

**Proofs that guards can fail** (each trigger commented out with the Edit tool, marked `# PROOF`, the tests run, then restored; `grep -rn PROOF --include=*.py` finds nothing; full suite after: `1240 passed in 91.71s`):

| Trigger removed | Tests that failed |
|---|---|
| `trg_*_a_version` (the three tables) | `test_a_profile_version_must_be_the_next_one`, `test_a_signoff_version_must_be_the_next_one`, `test_a_registration_version_and_visibility` |
| `trg_feed_profiles_b_fetch` | `test_a_profile_reads_a_fetched_file_of_its_own_feed` |
| `trg_feed_signoffs_b_guard` | 5: candidate only, latest profile, the profile's summary, the signer, spec-only cannot meet. The pending-summary test still passed: `c_decision` refuses it on its own |
| `trg_feed_signoffs_c_decision` (D1) | `test_an_approved_summary_needs_the_signoff_to_hold_the_recommended_outcome`, `test_a_rejected_summary_needs_the_signoff_to_hold_another_outcome`, `test_a_pending_summary_or_one_without_a_recommendation_cannot_be_signed_off` |
| `trg_feed_registrations_b_guard` | `test_a_registration_needs_the_latest_accepting_signoff_on_the_latest_profile`, `test_a_rejecting_signoff_does_not_register` |
| `trg_feeds_a_stage` | 4 in `test_db_onboarding_guards.py` (new feed, registered, rejected, other changes) and `test_repo_feeds.py::test_the_database_refuses_a_rejection_without_a_rejected_signoff` |
| `trg_source_choices_f_feed_stage` | `test_a_rejected_feed_cannot_be_selected`, `test_a_candidate_rejected_release_needs_a_rejected_feed` |
| `append_only` and `no_truncate` on the three tables | 9 in `test_append_only.py` (update, truncate, delete × 3) |

**Two unexpected failures, with their root causes:**
1. `test_a_pending_summary_...` expected `c_decision`'s message; `b_guard` answered first. Root cause: triggers of one event run in name order, and a pending summary has no approval row, so `b_guard`'s signer check refuses it before `c_decision` runs. Both refuse; the test now accepts either message, and the `b_guard` proof above shows `c_decision` alone refusing it.
2. `test_0014_adds_the_third_release_reason_...` got `MultipleResultsFound`. Root cause: the query read `ck_source_choices_reason` from every schema, and the scratch schema and `public` both have it. The query now filters by `current_schema()`.
3. A syntax error of my own in a test (removing a no-op `.replace()` also removed a closing parenthesis), found by `py_compile` and fixed.

**Deviations from the plan:**
1. **Task 3 was not test-first for the migration and domain models.** I wrote `0014`, `tables.py` and the domain models before their tests, then wrote every test before the repositories and the helper. So task 3's RED is a collection error, not a behaviour. The behaviour is proved by the guard removals above. Tasks 4 on are test-first.
2. **`trg_feeds_a_stage` is an AFTER trigger.** A BEFORE trigger answers before the check constraints, and phase 1's `test_database_refuses_a_registered_feed_without_owner` asserts `ck_feeds_registered_has_owner` by name. AFTER keeps both guards, and the test is unchanged.
3. **Trigger names and functions.** One shared function, `amaze_guard_feed_version()`, is `trg_<table>_a_version` on all three tables; the sign-off has `b_guard` and `c_decision` (planned: `a_guard`, `b_decision`); the registration has `a_version` and `b_guard`.
4. **`FeedRepository.reject()` arrived in task 3**, not task 6: `test_list_to_fetch_skips_..._rejected_feeds` (phase 3) made a rejected feed with a raw `UPDATE`, which the stage guard now refuses.
5. **`columns` is required on every catalogue entry.** The two catalogues written inside tests (`test_known_source.py`, `test_source_catalogue.py`) gained it.
6. **A column carries no `declared_as`.** The profile stores the spec (with its `columns`), and each coverage item names its column. Assumption 9 said each column shows its declared field; the UI joins the two.
7. **Models and functions beyond section 5.2:** `SourceSpec`, `ItemCoverage`, `spec_of()`, `spec_check()`. `coverage_of()` takes the catalogue `SourceEntry`; `NotATable` is the named error for a file DuckDB cannot read.
8. **A `rejected` sign-off has no gaps**, only reasons (the plan said "at least one reason" only). The draft and the check constraint agree.
9. **`registered_feed()` replaced 12 call sites; 4 calls stay.** The 4 are in `test_repo_feeds.py`, which tests `register()` itself, and now write the registration row first with `registration()`. The phase 3 count of 16 holds.
10. **The `instrument_lifecycle.csv` fixture was adjusted after its test was written** (one date changed, so the issue dates have 7 distinct values, as the test states).

**Files changed outside section 11:** none.

**The profile of one committed sample file** (`syn_instrument_lifecycle`, a requirement for `issue_date`):

```json
{"file": {"row_count": 10, "engine": "duckdb 1.5.6", "columns": [
   {"name": "instr_id", "type": "VARCHAR", "null_count": 0, "null_rate": 0.0, "distinct_count": 10},
   {"name": "issued_on", "type": "DATE", "null_count": 2, "null_rate": 0.2, "distinct_count": 7},
   {"name": "state", "type": "VARCHAR", "null_count": 0, "null_rate": 0.0, "distinct_count": 3}]},
 "spec_check": [[], []],
 "coverage": {"items": [{"item": "issue_date", "kind": "element", "declared": true, "column": "issued_on",
                         "in_file": true, "null_rate": 0.2, "distinct_count": 7}], "missing": []}}
```

**Application database:** not migrated yet; `alembic current` is `0013`. `0014` reaches it at checkpoint 2, as planned.

### Fixes after checkpoint 1 (your review, 2026-11-15)

`0014` was not applied anywhere but the test database, so it was edited, not followed by `0015`.

| # | Fix | RED | GREEN | Commit |
|---|---|---|---|---|
| 1 | The pending-summary test split, so no test accepts two messages: `test_a_pending_summary_has_no_signer` (b_guard's message), `test_a_pending_summary_is_refused_by_the_decision_guard` (a raw `approvals` row by the signer while the proposal stays pending, so b_guard passes; c_decision's message), `test_a_summary_without_a_recommendation_cannot_be_signed_off` | a test change: with `c_decision` removed, `2 failed, 1 passed`; with `b_guard` removed, `1 failed, 2 passed` (each new test fails without its own guard) | `37 passed in 1.93s` | `750d132` |
| 2 | `amaze_guard_feed_registration` refuses a feed that is not a candidate | `test_a_registered_feed_gets_no_second_registration`: `1 failed` | `1243 passed in 91.91s` | `a59eb2d` |

### Tasks 4 to 8 (checkpoint 2)

Tests: 1243 after the fixes, 1331 at checkpoint 2. All pass, none skipped, no warnings (`1331 passed in 114.14s`). Every file was test-first.

| Task | RED | GREEN | Commit |
|---|---|---|---|
| 4 step 02 | `1 error in 0.31s` (no module) | `35 passed in 5.36s`; full `1259 passed in 98.21s` | `6de217d` |
| 5 ProfilerAgent, summary | `2 errors in 0.44s`; `9 failed, 35 passed` (mock rule, registry) | `68 passed in 3.20s`; full `1277 passed in 98.69s` | `fe59f06` |
| 6 step 03, rejected sources | `6 failed, 69 passed`; the sign-off file `1 error` | `94 passed in 10.07s`; full `1302 passed in 97.02s` | `4451eaa` |
| 7 step 04 | `1 error in 0.27s`; `2 failed, 21 passed` | `35 passed in 3.89s`; full `1316 passed in 106.98s` | `d7d958a` |
| 8 the daily job | `2 failed, 12 passed` | `14 passed`; full `1319 passed in 106.92s` | `e8e6376` |
| check-constraint tests | (tests for existing checks) | `50 passed in 2.21s`; full `1331 passed in 114.14s` | `d743d1a` |

**The feed row lock before the next version is read, and its proof** (lock replaced by a plain read with the Edit tool, run three times, restored; `grep PROOF` finds nothing):

| Step | The line | Test | Without the lock |
|---|---|---|---|
| `record_profile` | `s02_profile.py:126` `feed = FeedRepository(session).lock(plan.feed_id)` | `test_two_profiles_of_one_feed_at_once_take_the_next_versions` | 3 of 3 failed: `UniqueViolation ... uq_feed_profiles_feed_id_version`. Two profiles are both legitimate, so the result is versions 1 and 2, not a 409 |
| `sign_off` | `s03_signoff.py:91` `feed = feeds.lock(feed_id)` (after `lock_source`) | `test_two_signoffs_at_once_give_one_success_and_one_409` | 3 of 3 **still passed**, with the source and feed locks both removed and the profile read slowed by 0.3 s: `record_decision` locks the summary's proposal row `FOR UPDATE` and the second then finds it decided (`ProposalAlreadyDecided`). The feed lock is there for the lock order and the stage change, not for this race |
| `register_feed` | `s04_register.py:68` `feed = feeds.lock(feed_id)` | `test_two_registrations_at_once_give_one_success_and_one_409` | 3 of 3 failed with `IntegrityError` once the sign-off read was slowed by 0.3 s. Without the slowdown it passed 3 of 3: one request finished before the other read. Both concurrency tests now slow one read |

**Lock order as built** (assumption 46: source code, feed row, requirements in id order):

| Step | Order |
|---|---|
| Known source? (`find_known_source`) | the check unlocked → `lock_source(code)` → requirement → `feed_for` (its `lock_source` again is a no-op) → feed insert or read. A source rejected in between: `SourceRejected` (409). **Changed from phase 3**, which locked the requirement first |
| S3 pick | the proposal read unlocked → `lock_source(code)` → requirement → the proposal row (`record_decision`) → `feed_for`. **Changed from phase 3** the same way |
| S4 `record_fetch` | feed (`FOR NO KEY UPDATE`) → each requirement that selects it, in selection order (unchanged; the feed lock is first, so two such steps never hold requirements out of order) |
| Release on request | requirement only (its `source_choices` insert takes `FOR KEY SHARE` on the feed, which does not wait for `FOR NO KEY UPDATE`) |
| 02 `record_profile`, summary `before_write` | feed only |
| 03 `sign_off` | `lock_source(code)` → feed → the summary's proposal row → the requirements in id order, each re-checked: one its requester released meanwhile is skipped |
| 04 `register_feed` | feed only. `link_to_registered_feed`: requirement only |
| The daily job's close of empty lists | requirement only |

The two lock-order tests of plan change 1 (a rejection with the daily fetch; a rejection with a requester release) are task 11 and are not built yet.

**Every check constraint of `0014` and the raw-SQL test that covers it** (`test_db_onboarding_guards.py`):

| Check | Test | NULL case |
|---|---|---|
| `ck_feed_profiles_profiled_has_rows` | `test_null_never_passes_a_profile_check` (4 cases) | `row_count` NULL |
| `ck_feed_profiles_spec_only_has_a_reason` | the same (3 cases) | `detail` NULL |
| `ck_feed_profiles_outcome`, `_json_shapes`, `_text_present` | `test_the_other_profile_checks` (5 cases) | NOT NULL columns |
| `ck_feed_signoffs_gaps_and_reasons_fit` | `test_gaps_and_reasons_fit_the_outcome` (4 cases) | NOT NULL columns |
| `ck_feed_registrations_visibility` | `test_a_registration_version_and_visibility` | NOT NULL |
| `ck_feed_registrations_text_present` | `test_a_registration_needs_every_text` (5 cases) | NOT NULL columns |
| `ck_feed_profiles_version`, `ck_feed_signoffs_version`, `ck_feed_registrations_version` | **behind the version trigger**: `test_version_zero_is_refused_by_the_version_trigger_before_the_check` shows version 0 refused by `trg_*_a_version` (a BEFORE trigger answers first) | NOT NULL |
| `ck_feed_signoffs_outcome`, `ck_feed_signoffs_signed_off_by` | **behind `b_guard` and `c_decision`**: `test_a_signoffs_outcome_and_signer_are_refused_by_its_triggers_before_the_checks` | NOT NULL |

The nullable columns of `0014` are `feed_profiles.row_count` and `detail` only; both are in `REVIEWED_NULLABLE_CHECKS`. The new check tests were not shown failing without their checks: `0014` is applied now and is not edited, even for a proof. Each test matches the refusing constraint by name, so it can pass only if that check refuses the row.

**Unexpected failures, with their root causes:**
1. `test_a_new_selection_wakes_the_feed` (phase 3) failed after task 8's first change. Root cause: I had added a `session.rollback()` after listing the feeds, and on the tests' shared session it undid the test's uncommitted setup. Nothing needed it; removed.
2. `test_version_zero_...` did not raise. Root cause: the test helpers chose the version with `version or next_version`, and 0 is falsy. Now `is None`.
3. The first register concurrency proof passed without the lock. Root cause: no pause between the read and the write, so one request finished first. Fixed in the test (a 0.3 s read), not by weakening it.

**Deviations from the plan:**
1. **Known source? and S3 pick were reordered** to take the source lock before the requirement (lock order above). Assumption 46 allowed changing phase 3 code; no phase 3 test changed.
2. **`requirement_page.py` changed in task 6**, not 10: the page showed the N-day message after a `candidate_rejected` release. `RequirementPage` gained `signoff_note`.
3. **`SignoffDoesNotFit`** (422, a `StepInputError`) lives in `s03_signoff.py`; `core/steps/errors.py` is not in section 11.
4. **`ProfileSummary`, `summary_of()`** and the plan types `ProfilePlan`, `ProfileAttempt` are beyond section 5.2. `ProfilerInput` carries `requirement_titles`.
5. **The sign-off's approval note** is "Signed off as <outcome>".
6. **`test_a_feed_registered_by_step_04_is_fetched_with_no_selection` passed before task 8's change**: `list_to_fetch()` already had the registered case. It stays as a regression test.
7. **The requester's notification subjects** (not worded in the plan): "Accepted with gaps: <source>", "Source rejected: <source>"; the backend team: "Linked to a registered feed: <feed>", "Source catalogue could not be read"; the owner: "Feed registered: <feed>".

**Files changed outside section 11:** none.

**Application database:**

```
alembic current          0013
alembic upgrade head     Running upgrade 0013 -> 0014, profile, sign-off, register: steps 02 to 04
alembic current          0014 (head)
```

Triggers present: `trg_feed_profiles_a_version`, `_b_fetch`, `_append_only`; `trg_feed_signoffs_a_version`, `_b_guard`, `_c_decision`, `_append_only`; `trg_feed_registrations_a_version`, `_b_guard`, `_append_only`; `trg_feeds_a_stage`; `trg_source_choices_f_feed_stage` (the `no_truncate` statement triggers are not listed by `information_schema.triggers`). The five feeds are still `candidate`; the three new tables are empty; `ck_source_choices_reason` holds the three reasons.

### Tasks 9 to 11 (checkpoint 3)

Tests: 1331 at checkpoint 2, 1359 at checkpoint 3. `1359 passed in 100.92s`, none skipped, no warnings.

| Task | RED | GREEN | Commit |
|---|---|---|---|
| 9, part 1: `apps/api/transactions.py` | a pure move: no test changed (`git diff --stat -- tests` empty) | `85 passed` (the consumer API, commit-visibility and concurrency tests); full `1331 passed in 103.39s` | `ef6e6a1` |
| 9, part 2: Amaze API | `12 failed in 4.14s` | `31 passed in 6.62s`; full `1343 passed in 109.03s` | `5764c44` |
| 10 consumer API | `2 failed, 2 passed` (the gaps note and a rejected source's 409 already worked through tasks 6 and 9) | full `1347 passed in 83.60s` | `aa658d2` |
| 11 commit visibility and concurrency | written after the code: their RED is each proof below | `15 passed`; `21 passed in 30.32s`; full `1359 passed in 100.92s` | `3a4a2b1` |

**The two done-when tests** (`test_api_amaze_onboarding.py`, `pytest -v`):

```
test_done_when_a_rejected_candidate_keeps_its_reasons_and_the_requester_is_told PASSED [ 50%]
test_done_when_an_accepted_candidate_is_registered PASSED [100%]
2 passed in 2.44s
```

**Commit visibility: each commit removed** (its `in_transaction(...)` given `keep=False`, a rollback, with the Edit tool; restored; `git diff --stat -- apps` empty after):

| Commit removed | Its test | Others that failed |
|---|---|---|
| `POST .../profile` | `test_profile_commits_the_profile` failed | the 5 later flows (they need a profile) |
| `POST .../profile/summary` | `test_summary_commits_the_proposal` failed | the 4 later flows |
| `POST .../signoff` | `test_signoff_commits_the_decision` failed | the 3 later flows |
| `POST .../register` | `test_register_commits_the_registration` failed | the 2 link tests |
| the link to a registered feed (`_after_selection`, shared by find-source and pick) | `test_find_source_commits_the_link_to_a_registered_feed` and `test_pick_commits_the_link_to_a_registered_feed` both failed | none |

Six tests, five commits: find-source and pick share one link commit. The pick's own selection commit is phase 3's, covered by `test_pick_commits_the_choice_and_the_fetch`.

**Concurrency: each test and what it was shown failing against** (each change made with the Edit tool, run three times where timing matters, restored; `grep PROOF` finds nothing; `git diff --stat -- core apps` empty):

| Test | Shown failing against | Result |
|---|---|---|
| `test_no_connection_is_held_during_a_profiling_run` (checkpoint 2) | not shown failing: it checks `pool.checkedout() == 0` inside `BlobStore.get()`, and the step holds no session there by design | |
| `test_two_profiles_of_one_feed_at_once_take_the_next_versions` (checkpoint 2) | `record_profile`'s feed lock replaced by `get` | 3/3 `UniqueViolation` |
| `test_two_signoffs_at_once_give_one_success_and_one_409` (checkpoint 2) | the source and feed locks removed | **3/3 still passed**: the proposal row lock refuses the second |
| `test_two_registrations_at_once_give_one_success_and_one_409` (checkpoint 2) | `register_feed`'s feed lock replaced by `get` | 3/3 `IntegrityError` |
| `test_no_connection_is_held_during_any_model_call` (+ the ProfilerAgent's call) | the summary endpoint without `read()` | `SessionInTransaction` from `Agent.run()` |
| `test_a_new_profile_during_the_summary_gives_409_and_a_discard_row` | `ProfileSuperseded` removed from `before_write` | failed (200, a proposal of a superseded profile); `test_s02_profile.py::test_a_newer_profile_during_the_call_writes_no_proposal` failed too |
| `test_a_pick_and_a_rejection_of_one_source_at_once_leave_nobody_on_a_rejected_feed` | the pick's `lock_source` removed | 3/3 failed: `IntegrityError` from `trg_source_choices_f_feed_stage` ("is rejected: it cannot be selected"), a 500, not the 409 |
| `test_a_rejection_and_the_daily_fetch_of_one_feed_at_once_do_not_deadlock` | `sign_off` locking the requirements before the feed (the order reversed) | 3/3 `DeadlockDetected` |
| `test_a_rejection_and_a_requester_release_of_one_requirement_at_once_do_not_deadlock` | `sign_off`'s re-check of a requirement released meanwhile removed | 3/3 failed: the rejection gives `RequirementNotInStatus`, is rolled back, and the feed stays a candidate |
| `test_a_profile_during_a_signoff_gives_a_success_or_a_409_never_a_500` (your 1) | `sign_off`'s `feeds.lock()` replaced by `feeds.get()` | 3/3 `IntegrityError` ("profile ... is not the latest profile"), a 500. Restored: 3/3 passed |
| `test_a_pick_and_a_fetch_of_the_same_feed_at_once_both_succeed` (your 2) | the feed lock as `FOR UPDATE` instead of `FOR NO KEY UPDATE` | 3/3 failed, **not by deadlock**: the pick's `source_choices` insert waited for the whole fetch, so the fetch did not hand the file to the new requirement (`source_selected`, not `onboarding`) |

**Unexpected failures, with their root causes:**
1. Your proof 1 first failed with `SignoffDoesNotFit`, not a 500. Root cause: `_slow` slept before the read, so the profile committed first and the sign-off read the newer (spec-only) profile. `_slow` gained `after=True` (sleep after the read); then 3/3 failed with the 500 and 3/3 passed with the lock.
2. Two expected statuses in the new concurrency tests were wrong, not the code: a rejection leaves a requirement `awaiting_source_choice` when another source is listed; a pick that commits while the fetch waits gets the file (`onboarding`).

**Deviations from the plan:**
1. **The concurrency tests run the steps on threads**, each on its own session, after a setup through the API; the plan's `test_concurrency.py` tests of phase 3 ran requests through the app. Each new test starts one request and the other 0.1 s later, with one read slowed, so the interleaving is known.
2. **`FindSourceResponse` and `PickResponse` gained `registration`** (`DuplicateLinked` or none).
3. **`transactions.py` gained `NOT_FOUND`** (with `FeedNotFound`) and the phase 4 conflicts in task 9 part 2, after the pure move.
4. **`core/steps/feed_page.py`** names the undeclared and missing columns `undeclared_columns`, `missing_columns`, and the latest fetch `last_file`.

**Files changed outside section 11:** none.

**After checkpoint 3 (your review): the profiling-run proof.** `run_profile(...)` was moved inside the `read(...)` call of `POST .../profile` in `apps/api/routers/amaze.py` with the Edit tool. `test_no_connection_is_held_during_a_profiling_run` **still passed** (`1 passed in 2.04s`): it calls the step functions itself, never the endpoint, so it cannot see the change. A new endpoint-level test was written with the change still in place, `test_no_connection_is_held_during_the_profile_endpoint` (the API's real session factory, a blob store that records `pool.checkedout()` on `get()`), and failed: `assert [1] == [0]`. The endpoint was restored with the Edit tool; `git diff --stat -- apps` is empty; both tests pass (`2 passed`).

**`pytest --durations=15`** (the test setup unchanged):

```
2.18s call     tests/test_port_connectors.py::test_http_client_is_closed_after_a_failed_fetch[None]
2.11s call     tests/test_concurrency.py::test_two_concurrent_picks_give_one_success_and_one_409
2.01s call     tests/test_api.py::test_health_reports_unreachable_database_with_503
1.78s call     tests/test_concurrency.py::test_two_requirements_selecting_the_same_new_source_at_once_make_one_feed
1.25s call     tests/test_concurrency.py::test_a_pick_and_a_fetch_of_the_same_feed_at_once_both_succeed
1.17s call     tests/test_concurrency.py::test_a_pick_and_a_rejection_of_one_source_at_once_leave_nobody_on_a_rejected_feed
1.03s call     tests/test_concurrency.py::test_a_rejection_and_the_daily_fetch_of_one_feed_at_once_do_not_deadlock
0.95s call     tests/test_concurrency.py::test_a_rejection_and_a_requester_release_of_one_requirement_at_once_do_not_deadlock
0.89s call     tests/test_migrations.py::test_upgrade_downgrade_upgrade
0.89s call     tests/test_migrations.py::test_0002_refuses_to_drop_a_recorded_decision
0.80s call     tests/test_concurrency.py::test_two_signoffs_at_once_give_one_success_and_one_409
0.74s call     tests/test_migrations.py::test_0009_downgrade_restores_0008_when_there_are_no_3b_rows
0.72s setup    tests/test_concurrency.py::test_two_registrations_at_once_give_one_success_and_one_409
0.72s call     tests/test_migrations.py::test_0012_moves_a_0011_discard_into_its_own_table
0.67s setup    tests/test_concurrency.py::test_a_status_change_during_the_model_call_gives_409_and_logs_it_as_discarded[find-source]
1359 passed in 100.92s (0:01:40)
```
