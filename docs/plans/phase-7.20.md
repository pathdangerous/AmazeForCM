# Phase 7.20: click a rule, see its rows (green passes, red fails)

Goal. Clicking a rule shows the data with the rule's columns highlighted; in those columns a cell is green when the row passes
the rule, red when it fails, amber when a waiver took the failure out of the flags. Read only: no model call, no write, no
migration. Branch `phase-7.20` from `main` (3382b91). Out of scope: editing a rule from the panel, a rule's history across
loads, downloading the rows, a new CSV export.

## 1. What exists (read before designing)

- `core/engine/sandbox.py`: the only place rule SQL runs. `run_rule(sql)` returns the sorted `_row` of every row that breaks
  the rule, on the `curated` table, after `lock()`. `execute()` (trusted SQL) works only before `lock()`.
- `core/steps/s09_rules.py::_one_delivery()` is the back-test's recipe for one file: `load_csv`, `match_header` (mapped columns
  found in the file, through the approved transformations), `curated_sql`, the uncast check, `lock`, `run_rule`.
- `core/steps/drafts.py::draft_config()` is the feed's approved configuration plus its pending proposals taken as approved
  (mappings, transformations, element types). `rules_input()` uses it and takes the file from
  `SourceFetchRepository.latest_fetched()`.
- An approved rule is a `rules` row (key, SQL, description, `proposal_id`); its `element_keys` live only in its proposal's
  payload. A load's `standard_records` hold `row_number`, `values` (element key to value), `flags` and `waived` (rule keys).
- Steps return a typed result discriminated on `outcome` (rule 3); `apps/api/transactions.py::read()` ends a read transaction.

## 2. Assumptions (numbered; correct me now or I proceed)

1. One step module, `core/steps/rule_rows.py`, no agent, no model call, no write. `proposal_log` is untouched (a test checks).
2. The id in the URL is a rule proposal id or a `rules` row id. A pending, rejected or superseded rule proposal is source (a):
   its SQL runs. An approved rule proposal, or a `rules` id, is source (b): the stored flags. Any other id, or a proposal of
   another kind, is `rule_not_found`.
3. The result is a 200 with a typed union (rule 3), not an HTTP error: `rows_shown`, `rule_not_found`, `no_file`,
   `sql_refused`. Named, expected negative cases are not 4xx. Wrong role is the usual 401/403 of `backend_user`.
4. Source (a) file: `SourceFetchRepository.latest_fetched()` (what the back-test and `rules_input()` use), read through
   `BlobStore.get()`. No fetched file, a file DuckDB cannot read, a mapped column the file lacks, or a value that does not cast
   (the back-test leaves such a delivery out) is `no_file` with a one-sentence reason. No fourth outcome name is added.
5. Source (a) configuration: `draft_config()`. The columns shown are the curated columns: element keys, in mapping order, with
   each value as the engine writes it (`json_value`), so source (a) and source (b) show the same shape.
6. Source (a) order of work: read (resolve the id, the draft configuration, the file reference), end the transaction, read the
   file, then everything in the sandbox with no database connection held (`pool.checkedout() == 0` is tested while it runs).
   Rows are read before `lock()` (execute is refused after it), the rule runs after; only the requested page is returned.
   shortcut: the curated table of one file is held in memory for one request; files are small in the PoC, upgrade to a
   second locked pass if a file ever needs streaming.
7. SQL that is not a plain SELECT with `_row`, fails, or times out is `sql_refused` with the sandbox's reason.
8. Source (b) load: the feed's newest load that has standard records and a status of loaded, released or held (a go-live test
   load counts). None is `no_file`. Failing is the rule key in `flags`; the rule key in `waived` is its own state. No SQL runs.
9. Source (b) element keys come from the proposal payload (`element_keys`); a rule row whose proposal has none shows no column
   as affected (it still shows states).
10. Response `rows_shown`: `rule` (key, description, SQL, `element_keys`, `source` = `draft` | `load`, `load_id` or null),
    `columns` (`key`, `affected`), the rule's columns first, then the rest in order; `rows` (`row`, `values`, `passes`, `waived`);
    `counts` (`rows`, `failing`, `waived`, `shown` = rows matching the filter); `page`, `size`. `passes` is false for a
    failing and for a waived row; `waived` says which.
11. Query: `failing_only` (default false), `page` from 1 (default 1), `size` (default 50, at most
    `RULE_ROWS_MAX_SIZE`, default 200, in `ports/config.py`; a larger `size` is cut to it and the answer says the size used).
    A page past the end is an empty list, not an error.
12. Auth: any backend role (`backend_user`), like the other reads. The consumer app never calls it.
13. No migration. If one seems needed, stop.
14. UI: one shared `RuleRowsPanel` in `apps/ui-shared/` (the folder both apps already import from), shown as a Mantine `Drawer`
    (focus trap and Esc come with it; focus returns to the opener). Its opener is a button on the rule card in the Rules step,
    on each rules item of the chain review, and on each rule of the feed page (current and pending).
15. Colour: fills with dark text, never text colour alone. New tokens in `SHARED` of `theme.ts` (byte-identical in both apps):
    pass fill, fail fill, waived fill and one text colour for each, with all three pairs in `CONTRAST_PAIRS` at 4.5:1. Each
    coloured cell also carries the words ("passes", "fails", "waived") as visually-hidden text and in `aria-label`, and the
    header shows a small legend with the same three words. Affected column headers get the existing marker fill.
16. The panel does not use `DataGrid` (its sort and filter run on the client over all rows; this table is paged by the server).
17. Demo story: one step in `e2e/tests/poc_story.spec.ts` (and the same in `e2e/screens/screens.spec.ts`): open a rule in the
    Rules step and see the failing count. If the story has no rule with a failing row, the step asserts the count line shown.
18. Test time: the API tests share the existing fixtures for a configured feed; the added time is stated in the report.

## 3. Tasks

- **T1** API: `core/steps/rule_rows.py`, `apps/api/routers/rule_rows.py`, setting `rule_rows_max_size`, tests (draft marks
  exactly `run_rule`'s rows; approved marks the flags; waived; `failing_only` and paging; multi-column rule; the four outcomes;
  no model call; no connection held).
- **T2** UI: `RuleRowsPanel`, theme tokens and pairs, the three openers, component tests (colours and labels, toggle, paging, Esc,
  focus returns), the story step.
- **T3** Docs: `docs/demo.md` row in the Rules step, `docs/ui-polish.md` if any, CLAUDE.md line.

## 4. Done when

`pytest -q -n 6`, both UI suites and the three Playwright stories pass. Push `phase-7.20`; do not merge.
