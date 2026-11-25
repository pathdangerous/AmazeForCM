# Phase 7.19: glossary upload and Add element; a column added after the first release joins the product

Goal. Part A: the glossary owner can bring many elements in at once (a CSV or XLSX file) or add one by hand; the glossary is the
base for auto-mapping. Every row is a proposal the glossary owner approves (rule 1); an approved row is a new element or the
next version of an existing one, never an edit in place (rule 7). No model call anywhere in part A (rule 4 logs model calls;
there is none). Part B: a column that joins the feed after the first release (drift, then code version 2) joins the feed's
product too. Branch `phase-7.19` from `main` (2ce0737). Migration 0038. Out of scope: deleting an element, removing an alias,
a new domain through a file, a model reading the file, changing which element a mapping uses.

## 1. What exists (read before designing)

- `proposals` (0002 and later): kinds are a check list (`ck_proposals_kind`, last set by 0034); `origin` agent | manual | rule
  (0031) with `ck_proposals_origin_matches_source`: a manual item has an author and no logged call. `ck_proposals_manual_kinds`
  (0034): a manual item is one of the listed kinds and always has a feed. `ck_proposals_names_a_column` (0017): a
  glossary_element or mapping proposal has a feed and names a column.
- `glossary_elements` (append-only by the 0005 trigger `amaze_require_approved_proposal('glossary_element')` and 0016/0025
  `amaze_require_glossary_option()`): a non-seed row needs an approved proposal **of kind glossary_element**, written by the
  glossary_owner who approved it, and equal to one of that proposal's `options` (a new element at version 1, or an alias as the
  next version). A whole-element new version (a changed definition, say) has no path today.
- `decide_element()` (`core/steps/s05_glossary.py`) is the glossary owner's decision for the agent's proposal, with the
  glossary advisory lock and `phrases_taken()` (phrases by words: key, name and aliases of every other current element).
- `proposals` have an append-only `approvals` row per decision; `ProposalRepository.record_decision()` checks the role and the
  step; one step here (glossary_owner), so four-eyes never bites.
- The 7.15 audit view (`core/steps/audit.py`) lists each `approvals` row joined to its proposal (feed optional, origin shown).
- `product_elements` is append-only (insert allowed; no trigger limits inserts). Step 20 (`core/steps/s20_create_product.py`)
  creates the product at the first release with the current mappings' elements and never touches it again. The consumer's
  page, dictionary, preview and downloads all read `product_page._columns()`, which already orders by the latest profile
  (fix-column-order).

## 2. Assumptions (numbered; correct me now or I proceed)

1. A glossary_import proposal is **one element row** (one proposal per row), origin `manual`, author = the uploader, no feed, no
   requirement, approver roles exactly `['glossary_owner']`, autonomy A1. Not an agent proposal: no `proposal_log` row.
2. A new proposal kind `glossary_import` rather than a loosened `glossary_element`. Reason: `glossary_element` is the agent's
   kind (options, candidates, a column); keeping the two apart means every guard written for the agent's kind stays exactly as
   strict as it is. Only the guards that must know the new kind get a new version (section 4).
3. Payload: `{upload_id, row, label ('new'|'update'), key, element {key,name,domain,data_type,definition,aliases,unit}, base_version
   (null for new), used_by_mappings, summary}`. The element's `unit` is NULL for a new element and the current version's unit for
   an update (the file has no unit column).
4. The file's columns are `key, name, domain, data_type, definition, aliases` (header names exact, case-insensitive, any order;
   extra columns are ignored; a missing one refuses the file, naming it). `aliases` split on `;`, trimmed, blanks dropped.
   CSV is decoded UTF-8 (a BOM is accepted); XLSX is the first sheet, read with openpyxl read-only, values as text. Both are read
   from bytes in memory with no database connection held. The limit is `UPLOAD_MAX_MB` (the existing setting). More than
   `GLOSSARY_UPLOAD_MAX_ROWS` (2000, a constant) data rows is refused as `too_many_rows`, so one request cannot queue unbounded
   work.
5. Labels, decided in this order for each row (the first that applies): `invalid(reason)` (missing field; bad key (not
   `^[a-z][a-z0-9_]*$`); bad alias (same rule, or equal to the key, or repeated); unknown data_type; unknown domain (not a
   domain of any current element: a file cannot invent a domain); already pending (an earlier upload's proposal for this key is
   still pending)) > `duplicate_in_file` (the key already appeared in an earlier row of this file, whatever that row's label; the
   later row is the duplicate) > `alias_conflict(<element key>)` (`phrases_taken()` finds another current element owning the
   element's key, name or alias, or an earlier row of this file that made a proposal does) > `unchanged` (key exists and name,
   domain, data_type, definition equal the current version's and the alias sets are equal) > `update` (key exists, a field differs)
   > `new`. `alias_conflict` covers a name or key phrase too, because `phrases_taken()` is the existing rule and a second, weaker
   one would let through what the owner screen refuses. The reasons are plain text.
6. Proposals are written only for `new` and `update` rows, in one transaction with the upload record, under the glossary
   advisory lock (so the labels cannot go stale between reading the glossary and writing). The upload record
   (`glossary_uploads`, append-only) keeps every row of the file with its label and, for new/update, its proposal id, so the page
   can filter by label and show rows that have no proposal.
7. A decision is per proposal: the glossary owner who clicks writes one `approvals` row. "Approve selected" / "Reject selected" is
   one request carrying the proposal ids; each item runs in its own savepoint, so one refused item (an alias conflict, a stale
   base version) does not stop the others and the answer lists every item's typed outcome: `approved`, `rejected` or
   `refused` with a named reason. A rejection needs a note (existing rule `needs_a_note`); the page asks once for the whole batch.
8. Approving writes `glossary_elements` through `GlossaryRepository.add_import()`: version 1 of a new key, or the next version of
   the key. Python checks first (under the glossary lock): the key is still new (new) or its latest version is still
   `base_version` (update; else refused `base_changed`) and `phrases_taken()` is empty (else refused `alias_conflict`). The database
   re-checks (section 4) so a path that skips Python is refused too.
9. An update of an element that mappings use makes a new version and changes nothing else (a mapping names an element key, not
   a version; the engine reads the current element). `used_by_mappings` = the number of current mappings (latest version of each
   feed column) naming the key, counted when the file is read, and shown on the row: "used by N mappings".
10. The "Add element" form is a one-row upload through the same path: the page builds a one-row CSV and posts it to the same
    endpoint, so labels, guards and the upload record are identical (the file name is `add-element`).
11. Who may upload and decide: the `glossary_owner` role (the dev user `dev-glossary-owner`). Reading the uploads is open to any
    backend user. The page is visible to everyone who sees the glossary today; the buttons are enabled only for the owner, as
    the decision buttons are today.
12. The audit view and CSV gain `upload` rows (who, when, file, counts by label) beside the decision rows, which already include
    a glossary_import decision (feed empty, `by` = manual). Filters by person work for both; the feed filter hides uploads (they
    have no feed), as it hides a decision of a feed-less item.
13. Part B, "consumers" of a product: the requesters of the requirements that selected the feed (`requirements_selecting`),
    once each, plus the requester of the requirement the product was created for. A person with only a grant is not told (they
    never asked for this feed); noted as a limit, not built.
14. Part B, which elements join: every element of the current mappings of the feed that the product does not hold, at each
    released load, inside step 20's savepoint. The engine refuses a load whose mappings differ from the go-live's, so "current
    mappings" is the go-live's set. Written as new `product_elements` rows; nothing is deleted. One notice per consumer per load
    that added at least one element, listing each added column ("<product> gained column <element name>" per column; several
    in one load make several notices in one batch, one per column). A second release adds nothing and sends nothing.
15. Part B, hiding: a product that has a feed shows only the elements its feed's current mappings name (the page, dictionary,
    preview, CSV and XLSX all read `_columns()`, so one filter there covers all five). An element no longer mapped stays in
    `product_elements`. A product with no feed keeps every element, as today. Old loads' records have no value for a new
    element: the cell is empty (`values.get`).
16. No new test infrastructure: tests use `db_session`, `tests/configured.py`, the dev users and the API test client in use today.
    Each committing endpoint or job gets one happy-path commit-visibility test (memory: commit-visibility tests), and the added
    test time is stated at the end.

## 3. Tasks, one commit each

- T0 plan (this file, BUILD_PLAN.md).
- T1 migration 0038 + `GlossaryRepository.add_import()` + the DB tests.
- T2 `core/steps/glossary_upload.py` (read, label, write, decide), `POST /api/amaze/glossary/uploads`, `GET .../uploads`,
  `GET .../uploads/{id}`, `GET .../template`, `POST .../decisions`; tests for every label, CSV and XLSX, bulk decisions, no model
  call.
- T3 backend-ui Glossary page: "Upload glossary file", "Download template", the upload's rows with labels and a filter,
  Approve selected / Reject selected, "Add element".
- T4 audit view and CSV list uploads and decisions; the "used by N mappings" note is already on the row (T2).
- T5 part B: step 20 adds new elements; `_columns()` hides unmapped ones; notices.
- T6 finish: `fixtures/glossary/sample_upload.csv`, the demo story (`e2e`), `docs/demo.md`, BUILD_PLAN, `graphify update .`.

## 4. Every trigger, function or constraint that gets a new version (migration 0038), and why

Each is rewritten from the text of the migration that last defined it, with every replacement asserted (a changed source line
stops the migration), and each downgrade restores the previous text. Nothing is loosened except for the new kind.

| # | What | Last defined | New version | Why |
|---|---|---|---|---|
| 1 | `ck_proposals_kind` | 0034 | + `glossary_import` | the new kind exists |
| 2 | `ck_proposals_manual_kinds` | 0034 | + `OR (kind = 'glossary_import' AND feed_id IS NULL)` | a feed-less manual item, for this kind only; every other manual kind still needs a feed |
| 3 | `amaze_require_approved_proposal()` (trigger `trg_glossary_elements_approved_proposal`) | 0005 | the kind test accepts `glossary_import` when the table is `glossary_elements` | an approved glossary_import proposal may write a glossary element; no other table or kind changes |
| 4 | `amaze_require_glossary_option()` (trigger `trg_glossary_elements_b_option`) | 0025 | a branch for a glossary_import proposal: the row equals its element, version 1 of a new key, or the next version with `base_version` the latest; no alias new to the element is held by another element | rule 7 and "an alias held by another element cannot be approved" |
| 5 | `ck_proposals_glossary_import_shape` (new) | - | a glossary_import proposal is manual, has no feed or requirement, roles exactly `{glossary_owner}`, autonomy A1 and a payload that names its upload, label, key and element | the new kind is only this shape |
| 6 | `amaze_guard_glossary_import_proposal()` + trigger on `proposals` INSERT (new) | - | the payload's upload exists, the author is the upload's uploader, the label is new or update, the key equals the element's key, a `new` key does not exist, an `update` key exists | a proposal is tied to a real upload |
| 7 | `uq_proposals_one_pending_glossary_import_per_key` (new index) | - | one pending glossary_import per key | two uploads cannot race to the same key |
| 8 | `glossary_uploads` (new table) + `trg_glossary_uploads_append_only` (existing `amaze_refuse_update_delete`) + a TRUNCATE refusal (existing `amaze_refuse_truncate`) | - | who, when, file name, SHA-256, counts by label, every row with its label | the record of an upload is evidence |

Guards deliberately **not** changed: `ck_proposals_names_a_column` (a glossary_element or mapping proposal without a feed is still
refused), the 0016 mapping guard, `amaze_guard_proposal_change`, the edit guard (a glossary_import proposal is not an
editable kind: a wrong row is rejected and uploaded again), `proposal_audit` (it already LEFT JOINs the log). No guard is loosened
for anything but the new kind; if one had to be, work stops and is reported.

## 5. Checks

- RED/GREEN per task. `pytest -q -n 6`, both UI suites (`npm --prefix apps\backend-ui test`, `npm --prefix apps\consumer-ui test`)
  and the three Playwright stories (`cd e2e; npx playwright test`; the screens and workspace configs are not run).
- Tell the user to run `alembic upgrade head` on dev and on Replit (0038).
