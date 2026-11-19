# Phase 7.9 plan: pause and real delete of a feed

**Status:** plan written before any code (2026-11-20). Branch `phase-7.8` (no new branch was asked for). Local only.
**Scope:** the request of 2026-11-20 (A migration 0027, B steps and API, C UI, D tests). Not in scope: retiring a *requirement*, deleting glossary elements, bulk delete, undelete.
**Method:** CLAUDE.md: tests first with the RED line pasted, then the least code, then the GREEN line. Targeted tests only (the request forbids the full suite and Playwright for this phase).

## 1. Goal and done-when

An operator can **pause** a live feed (the daily job stops fetching it; its product stays, labelled paused) and **resume** it, and can **delete** a feed for real, so a demo can be repeated from the same file under the same code.

Done when:
1. Every allowed and refused pause/resume move is tested; the daily job skips a paused feed; the next run after a resume fetches normally, with no backfill.
2. `amaze_delete_feed()` removes every row of one feed (list in section 5), keeps glossary elements and their proposals, writes `feed_deletions` with per-table counts, and leaves every other feed's rows unchanged (counts before and after).
3. A plain `DELETE` on any append-only table outside the function is still refused (one test per table).
4. Delete is refused while a load is `received` (409).
5. **Acceptance (demo repeatability):** onboard `Synthetic_Security_Master.xlsx` as `syn_security_master` to a released load; delete the feed; onboard the same file under the same code again: no quarantine, no `duplicate_linked`, released. Twice in a row.
6. The API maps every named error to 403 or 409, never 500. The UIs show Pause/Resume, Delete (reason and the typed feed code), "Paused: not updating since <date>", and the deletions on the Status page.

## 2. State at the start (read from the schema `e2e_story` at head 0026, and the code)

| Fact | Found |
|---|---|
| Live stage | `feeds.stage` is `candidate`, `registered` or `rejected`. **`registered` means live**: the daily job fetches registered feeds (`FeedRepository.list_to_fetch`) and a registered feed with a go-live is loaded after its fetch. |
| Stage guard | `amaze_guard_feed_stage` (trigger `trg_feeds_a_stage`) allows only candidate to registered and candidate to rejected. Checks `registered_has_owner` and `registered_at_matches_stage` assume `registered` is the only stage with an owner and a `registered_at`. |
| Append-only guards | `amaze_refuse_update_delete()` on 24 tables (every `trg_*_append_only` and `trg_*_no_truncate`); other DELETE guards: `amaze_guard_glossary_run`, `amaze_guard_onboarding_job`, `amaze_guard_onboarding_session`, `amaze_guard_proposal_change` (proposals). `loads`, `feeds` and `products` have no DELETE guard today. |
| Tables with a `feed_id` FK to `feeds` | feed_go_lives, feed_key_policies, feed_profiles, feed_registrations, feed_signoffs, glossary_runs, load_certifications, loads, mappings, onboarding_sessions, products, proposals, rules, source_choices, source_fetches, standard_records, transformations, upload_sources. Children without `feed_id`: load_stages (load), product_elements and product_grants (product), onboarding_events and onboarding_jobs (session), approvals (proposal), proposal_log (through proposals and glossary_runs), proposal_log_discards (through proposal_log). |
| Cycle | `loads.go_live_id` references `feed_go_lives`, and `feed_go_lives.test_load_id` references `loads`. No real cycle: a scheduled load references a go-live; a go-live references a go-live-test load, which references no go-live. Delete scheduled loads, then go-lives, then test loads. |
| Blobs | `BlobStore` has `put/get/exists/list_keys`, no delete. Uploaded feeds also have a drop folder (`DROPZONE_ROOT/<code>/`), read by the dropzone connector, outside `BlobStore`. |
| Requirement statuses | There is no status `no_known_source` (that is a step outcome). `REQUIREMENT_TRANSITIONS` has no move out of `ready` or `access_restricted`. |
| `source_choices` | Versioned per requirement; the highest version is current. A row names a `feed_id` (FK). |

## 3. Assumptions (each departs from, or fills a gap in, the request; none is silent)

1. **"Live" is `registered`.** `feeds.stage` gains `paused`. Allowed moves: `registered` to `paused` and `paused` to `registered`, nothing else. CHECKs `registered_has_owner` and `registered_at_matches_stage` become "stage in (registered, paused)".
2. **A stage move is recorded.** The stage guard allows `registered` to `paused` only if the newest `feed_status_events` row of the feed is a `pause` written in the same transaction, and `paused` to `registered` only after a `resume` (rule 7: recorded, never silent). A paused feed's owner is fixed, as for a registered one.
3. **"Back to no_known_source" is the requirement status `no_source_found`.** It is the existing status from which S1 can run again (`no_source_found` to `awaiting_scope_confirmation`); it carries the meaning "no known source". Two transitions are added to `REQUIREMENT_TRANSITIONS`: `ready` to `no_source_found` and `access_restricted` to `no_source_found` (`onboarding` and `source_selected` to `no_source_found` already exist). Python only; there is no DB guard on `requirements.status`.
4. **Kept proposals keep their `feed_id` as provenance.** A glossary element's proposal must stay (the request) and a glossary proposal must have a non-null `feed_id` (CHECK `names_a_column`, 0017). So the FK `proposals.feed_id` to `feeds` is dropped (the column and the CHECKs stay); a kept proposal then names a feed that `feed_deletions` records (id, code, name). Every other proposal of the feed is deleted, with its approvals.
5. **`source_choices` rows of the feed are deleted**, not left dangling: a requirement's choice chain loses only the versions about this feed. If the deleted feed was the requirement's current selection, the requirement has no current selection; if it was not, the chain is unchanged (the highest version stays the highest).
6. **`proposal_log` rows are deleted only when no kept proposal references them** (a call may have proposed several items). The function collects those ids into a session temp table (`ON COMMIT DROP`) before deleting proposals; the `proposal_log` and `proposal_log_discards` guards accept a row only if its id is in that table. A discard of a call that never produced a proposal belongs to no feed and stays.
7. **One guard function decides.** `amaze_may_delete(table_name, old_row jsonb) returns boolean`: false unless `current_setting('amaze.deleting_feed', true)` is a feed id and the row's feed (its own `feed_id`, or the feed of its parent row, or the temp table for the log tables) equals it. The five DELETE-guarding functions call it first and return OLD when it is true; otherwise they are unchanged. TRUNCATE guards are unchanged. **Known weakness, stated:** any session that can run SQL can `SET LOCAL amaze.deleting_feed` and delete that feed's rows; the guard limits a delete to one feed and leaves a trace only when `amaze_delete_feed()` is used. It is no weaker than a role that may drop a trigger. Recorded in CLAUDE.md as the one exception to rules 1 and 7.
8. **Who may.** Pause, resume and delete: the feed's registered owner, or a user with the `operations` role (a candidate or rejected feed has no owner: operations only). A reason is required (non-blank, at most 500 characters). Four-eyes does not apply (one person, one reason, recorded).
9. **Pause is refused unless the feed is `registered`** (`FeedNotLive`, 409). Resume is refused unless `paused` (`FeedNotPaused`, 409). A load already `received` at pause time finishes (pause stops the next fetch, it does not abort a load).
10. **The daily job skips paused feeds**: `list_to_fetch` excludes `stage = 'paused'` explicitly (a paused feed may still be selected by a requirement). Resume only changes the stage back: the next daily run fetches normally, and nothing is backfilled.
11. **Delete order inside `amaze_delete_feed()`** (children first): onboarding_events, onboarding_jobs, onboarding_sessions, glossary_runs, product_elements, product_grants, products, then per load: standard_records, load_stages, load_certifications; scheduled loads, feed_go_lives, test loads; feed_registrations, feed_signoffs, mappings, rules, transformations, feed_key_policies, feed_profiles, source_fetches, upload_sources, source_choices, approvals of deleted proposals, proposals (one statement, so the `edited_from` self reference holds), proposal_log_discards and proposal_log (temp-table ids), feed_status_events, the feed. Then `feed_deletions` is written (so its row survives, it is a different table with its own append-only guard and no feed FK). `feeds.requirement_id` points the other way and needs nothing.
12. **Refused while a load is `received`:** `FeedLoadInProgress` (409). The function locks the feed row `FOR NO KEY UPDATE` first, then checks. The step takes the source advisory lock before (phase 4 lock order).
13. **Blobs are deleted after the database commit, best effort.** The step collects the keys first (loads' file refs, source fetches' file refs, the upload source's blob key, the `input_ref` and `raw_ref` of the deleted proposal_log rows), and the drop folder of the feed's code. A blob that cannot be deleted is counted and logged; the delete stands. A crash between commit and blob delete leaves orphan blobs, never a half-deleted feed.
14. **Notices.** Requesters of every requirement whose current choice selected the feed are told (their requirement is back to `no_source_found`); the product's owner and grantees are told when a product existed. In-app notifier, as every other notice.
15. **`BlobStore.delete(key)`** is added to the port: local adapter removes the file (missing key is not an error), Azure stub raises `NotImplementedError("Azure adapter: implement after move")`.
16. **`feed_status_events` and `feed_deletions` are append-only** with the same `amaze_refuse_update_delete()` guard, no TRUNCATE, and no `feed_id` FK (deletions must outlive the feed; status events are deleted with the feed by the function, so they keep the FK and the function deletes them).
17. **Downgrade** restores the five guard functions and the stage guard, drops the two tables, the `paused` stage and the extra CHECKs, and re-creates the `proposals.feed_id` FK (refused, with a clear error, if a dangling kept proposal exists).

## 4. Migration 0027 (A)

- `feed_status_events(id, at, feed_id FK, action pause|resume, reason non-blank, by)`: append-only, no truncate; a trigger requires `by` non-blank and, for `pause`, a registered feed and for `resume` a paused one (the stage guard enforces the move; this trigger enforces the order of events).
- `feeds.stage` CHECK `stage` gains `paused`; the two CHECKs widened; `amaze_guard_feed_stage` replaced (allows registered to paused to registered as in assumption 2; everything else as in 0015).
- `feed_deletions(id, at, feed_id (no FK), code, name, by, reason non-blank, rows_deleted jsonb, blobs jsonb null)`: append-only, no truncate.
- `proposals.feed_id` FK dropped (assumption 4).
- `amaze_may_delete()`; the five guard functions re-created with the early exit (bodies copied from the database, then one added line each).
- `amaze_delete_feed(feed_id uuid, by text, reason text) returns jsonb` (the per-table counts); `SET LOCAL amaze.deleting_feed` inside it; raises `FeedLoadInProgress` as an exception with SQLSTATE `restrict_violation`-class message the repository maps to the named error; also raises for an unknown feed or a blank reason.

## 5. Steps and API (B)

- `core/steps/s_feed_lifecycle.py` (no AI): `pause_feed`, `resume_feed`, `delete_feed`, each returning a typed result (`paused`, `resumed`, `deleted`) or raising a named error: `FeedNotLive`, `FeedNotPaused`, `FeedLoadInProgress`, `NotFeedOwnerOrOperations` (403), `ReasonNeeded` (422), `FeedNotFound` (404). Errors live in `core/repositories/errors.py` and are mapped in `apps/api/transactions.py`.
- Routes under `/api/amaze/feeds/{feed_id}`: `POST /pause`, `POST /resume`, `DELETE` (body: `reason`, `confirm_code`; the code must equal the feed's code, else 422), `GET /api/amaze/feed-deletions` (the Status page).
- `FeedRepository`: `list_to_fetch` skips paused; `set_stage` for the two moves (inside the step, after the event row).
- The consumer API's product carries `paused_since` (the pause event's date) while its feed is paused.

## 6. UI (C)

- backend-ui: the registry list and the feed page show Pause/Resume (a reason box) and Delete (a dialog: reason, and the feed code typed to confirm; Delete stays disabled until both match). A paused feed's stage reads "paused". The Status page lists the deletions (who, when, reason, per-table counts).
- consumer-ui: a paused product shows "Paused: not updating since <date>" next to its freshness; a deleted product is gone from the catalogue, its page answers not found, a requirement that lost its feed reads as before for `no_source_found`.

## 7. Tests (D), RED first, targeted

- `tests/test_migrations.py`: head `0027`; the two tables, the stage, the functions exist; downgrade and upgrade round trip.
- `tests/test_feed_lifecycle.py`: every allowed move (registered to paused to registered) and every refused one (pause a candidate, rejected, paused; resume a registered; a stage jump by raw SQL without its event), the reason rules, the role rules, the events written.
- `tests/test_jobs.py` (existing file, new tests): the daily run skips a paused feed; after resume the next run fetches normally and loads once (no backfill).
- `tests/test_feed_delete.py`: for a fully onboarded feed with a released load and a second untouched feed, rows of the feed are zero in every table of section 2, the other feed's counts equal before and after, glossary elements and their proposals are kept, `feed_deletions` has the counts, blobs are gone, requirements went back and were notified; refused with a received load; a plain DELETE on each append-only table is still refused (parametrised, one per table) both with no setting and with the setting naming another feed.
- `tests/test_api_feed_lifecycle.py`: the routes, 403 and 409 and 422 and 404, never 500; commit-visibility for the committing endpoints (happy path only, per the project memory).
- `tests/test_onboarding.py` side: the acceptance walk, twice in a row.
- vitest: the dialog and the buttons, the paused label, the Status list.

## 8. Risks

| Risk | Handling |
|---|---|
| A table is missed and the delete fails on an FK | the "zero rows in every table" test walks `information_schema` for every column that references `feeds`, so a new table fails the test, not the demo |
| The GUC is settable by any SQL session | stated in assumption 7 and in CLAUDE.md; a test proves it can only delete the named feed's rows |
| Orphan blobs after a crash | accepted, assumption 13; the `blobs` count in `feed_deletions` shows what was removed |
| Dangling `feed_id` on kept proposals | provenance by design; feed pages never load a feed by a proposal's `feed_id` except through the feed's own list (checked by the glossary page test) |
| Requirement history changes | only that feed's choice versions go; the requirement's status is moved by the allowed transitions only |

## 9. Order of work

1. Plan (this file) and the CLAUDE.md exception.
2. M: migration tests RED, then 0027, GREEN.
3. S: `BlobStore.delete`, errors, steps, jobs skip, GREEN.
4. API tests RED, routes, GREEN.
5. UI tests RED, screens, GREEN.
6. The acceptance walk, twice.
