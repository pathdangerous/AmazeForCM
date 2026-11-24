# Phase 7.17: the code pipeline (commit, checks, test load, result) and a go-live that needs a pass

Goal: each version of a feed's generated code goes through a pipeline: it is committed (a git repository kept apart from this
one), its SQL is parsed, its rules pass the sandbox's check, a test load runs, and the run ends in a typed result. The data
owner's go-live approval needs a **passed** run of the code it pins. Branch `phase-7.17` from `main`. Migration: **0037**.
Out of scope: an Azure DevOps implementation, remotes and pushes, scheduled runs, a run of anything but the generated code.

## 1. What exists (read before designing)

- 7.12 T3: `core/engine/codegen.py` generates the feed's code (`files`: `staging_ddl.sql`, `load.sql`, `curated.sql`,
  `rules/<key>.sql`, `README.md`) and its `sha256` from the approved configuration, deterministic, no model.
  `core/steps/codegen.py: code_for_feed()` is the read-only preview. `store_code_version()` stores it as the feed's next
  `code_versions` row **at the owner's approval**, in the same transaction as the go-live that pins it (`decide_go_live()`).
- `code_versions` (0030): append-only, `sha256` recomputed by the database; `trg_feed_go_lives_c_code_version` makes a new
  go-live pin a code version of its own feed, approved by the same owner, from the same test load and configuration ids.
- The engine (`core/engine/run.py`): `execute_load(plan, blob_store, rule_timeout)` needs no database; `LoadPlan.load` is read
  only for `file_ref` there. `core/engine/sandbox.py: check_rule_sql()` is the sandbox's own check of a rule.
- Ports (`ports/storage` etc.): a `base.py` Protocol, a `local.py`, an Azure stub with `is_stub`, `ENV_KEYS` read in the
  constructor, a factory in the package's `__init__.py`, one row in `tests/test_azure_stubs.py`, keys in `.env.example`.
- `data/` is git-ignored. `amaze_delete_feed()` (0027, last patched by 0034) deletes every table that has a feed.
- The operator workspace approves a go-live through the same `decide_go_live()` (`core/steps/onboarding.py`).

## 2. Design

### 2.1 T1: port `Pipeline`

`ports/pipeline/base.py`: `Pipeline` Protocol: `name: str` and
`commit(*, feed_code, files, sha256, by) -> CommitResult(commit: str, created: bool)`; named errors `PipelineRefused`
(a path or root that is not allowed) and `PipelineUnavailable` (no git, or git failed). `local.py` `LocalGitPipeline(root)`:
a repository of its own at `root` (created with `git init` on the first commit, the root's own `.git`, never the project's);
one folder per feed code; a commit **replaces** the folder's content with `files` and commits it, the message
`<feed_code>: code <sha256[:12]>` with the full hash in the body. Content the folder already holds in HEAD: no new commit
(`created = False`, the commit that last changed the folder). git runs by subprocess (`git -C <root> -c user.name=... -c
user.email=... -c commit.gpgsign=false -c core.autocrlf=false`), so Windows and Replit behave alike and no global git
identity is needed. `azure_devops.py`: stub, same signature, `NotImplementedError`, keys `AZURE_DEVOPS_ORG_URL`,
`AZURE_DEVOPS_PROJECT`, `AZURE_DEVOPS_REPO` in `.env.example`. Settings: `pipeline_provider: local | azure_devops`,
`pipeline_repo_root: Path = ./data/pipeline-repo` (read from the repository root, like `dropzone_root`).
`get_pipeline(settings)` in `ports/pipeline/__init__.py`. The health route counts the adapter like the others.

### 2.2 T2: `pipeline_runs` (migration 0037) and the gate

`pipeline_runs`: `id, created_at, feed_id (FK), code_sha256, commit_hash (null when the commit failed), commit_created,
stages (jsonb), outcome, run_by`. Append-only (UPDATE, DELETE, TRUNCATE refused; a DELETE only for the feed being deleted by
`amaze_delete_feed()`, which 0037 patches to delete the feed's runs). `stages` is exactly four objects, in order `commit`,
`sql_parse`, `rule_check`, `test_load`, each `{stage, status, log: [text]}` with status `passed`, `failed` or `skipped`.
`trg_pipeline_runs_guard` recomputes the result from them: the stages before the first failed one are `passed`, the ones after
it `skipped`; `outcome = failed_at_<stage>` for the first failed stage, else `passed`; a `passed` run has a commit hash
(40 to 64 hex) and a `code_sha256` that is 64 hex. A run is evidence, not configuration: it never changes a mapping, rule or
version, so it is no exception to rules 1 and 7.

`trg_feed_go_lives_d_pipeline` (BEFORE INSERT, after `c_code_version`): a new go-live needs a `passed` run of its own feed whose
`code_sha256` is the `sha256` of the code version it pins; else `check_violation`
"go-live: code version N has no passed pipeline run". Earlier go-lives are not touched.

`core/steps/pipeline.py`, one pattern like step 02 (no connection held while git, DuckDB or the file work):
1. `pipeline_plan(session, feed_id)` (read only): the feed is registered (else `FeedNotRegistered`), `code_for_feed()`, the
   configuration, the latest fetched file's `blob_ref` (may be none).
2. the API ends the transaction.
3. `run_pipeline(plan, *, pipeline, blobs, rule_timeout_seconds)` (no database): the four stages, stopping at the first
   failure (the rest are `skipped`):
   - `commit`: `pipeline.commit(...)`; `PipelineRefused` or `PipelineUnavailable` is `failed`, its text the log.
   - `sql_parse`: every `*.sql` file parses with DuckDB (`duckdb.extract_statements`); the log lists each file and its
     statement count.
   - `rule_check`: every `rules/*.sql` passes `check_rule_sql()`; the log lists each rule. No rules is `passed` ("no rules").
   - `test_load`: a **dry** load: `execute_load()` of the plan on the latest fetched file with the configuration the code was
     generated from, nothing written (no `loads` row, no record, no notice). Passed when the four engine stages complete; a
     failure logs the stage and its reason. No fetched file is `failed`.
4. `record_pipeline_run(session, attempt, by)`: lock the feed row (first lock of the lock order), check the code's hash is
   still the configuration's (else `ConfigurationChangedMeanwhile`, 409, nothing written), add one row.

`decide_go_live()` asks first (before it writes anything): a passed run for `code_for_feed(...).sha256`, else
`PipelineNotPassed` (a `RepositoryError`, 409, text: "Run the pipeline on the Code view first: it has no passed run for the
code of this configuration"). The database trigger is the second line. No model call anywhere in `pipeline.py`.

### 2.3 T3: endpoints and the Code view

`POST /api/amaze/feeds/{id}/pipeline-runs` (any backend role; runs and returns the run); `GET .../pipeline-runs` (read only:
every run, newest first, with `code_version` = the version whose `sha256` it has, or null for code not yet approved).
Code view: "Run pipeline" runs it for the configuration now; each version and "The configuration now" list their runs: the
result in words, the stages with their logs, and the commit hash (first 10 characters, the full one on hover). Reused in the
go-live summary of the operator workspace so the one-click path can run it too. The button carries `disabled` while a run is
in flight. Results are words plus a mark, never colour alone.

## 3. Tests (first; the RED line is pasted before the code)

- T1 `tests/test_port_pipeline.py` (tmp_path): the first commit makes a repository of its own with one commit and a 40-hex hash;
  the same files again: `created=False`, same hash; changed files: a new commit, the other files of the feed's folder
  replaced, another feed's folder untouched; a path with `..`, an absolute path or a root that is the project root:
  `PipelineRefused`; git not found: `PipelineUnavailable`; the project's `git status --porcelain` is the same before and after.
  Stub row in `tests/test_azure_stubs.py`.
- T2 `tests/test_db_pipeline_guards.py`, `tests/test_pipeline.py`: a passed run is stored; a failed stage must leave later ones
  skipped; an outcome that is not the stages' is refused; a passed run without a commit is refused; the run row is append-only;
  deleting the feed deletes its runs; **a go-live with no passed run is refused by the database and, through the step, is
  `PipelineNotPassed`**; a run that failed at each of the four stages blocks go-live (one test per stage); a passed run for
  other code (another hash) does not count; the commit hash is recorded and equals the repository's HEAD; the config changing
  during the run is `ConfigurationChangedMeanwhile`; no connection is held during the run; the whole run leaves the project's
  `git status` unchanged. `tests/test_migrations.py`: head 0037, up/down/up, downgrade refused while a run exists.
- T3 `tests/test_api_pipeline.py` (+ one commit-visibility test for the POST, about 1 to 1.5 s added) and vitest for the panel.
- Full: `pytest -q -n 6`, `npm --prefix apps\backend-ui test`, `npm --prefix apps\consumer-ui test`, all Playwright stories
  (`e2e/`: story, operator story, demo story, screens and workspace screens configs).

## 4. Assumptions (stop and ask if one is wrong)

1. A code version is stored at the owner's approval (0030), so a run cannot name a `code_versions` row that does not exist yet.
   A run is keyed by **feed and the code's SHA-256**, the same hash the version will carry; the go-live trigger joins the two.
   0030 is not changed. The Code view shows a run under the version with that hash, or under "The configuration now".
2. The pipeline runs on the generated code of the feed's current configuration (`code_for_feed()`), never on hand-edited text.
3. The local pipeline repository is `PIPELINE_REPO_ROOT` (default `./data/pipeline-repo`, git-ignored because `data/` is), a
   repository of its own with its own `.git`; the adapter refuses the project root and any root that contains it. Tests use
   `tmp_path`; the Playwright API uses its own folder under `data/`.
4. The git command line must be installed. Missing or failing git is the typed result `failed_at_commit` (a log line), never a
   500. Whether Replit has git is not checked from here; if it does not, the pipeline reports `failed_at_commit` there.
5. "One commit per code version" means one commit per distinct content of a feed's folder: running the same code twice does not
   add a commit and returns the commit that holds it (`commit_created` says so). Code that returns to an earlier content is a
   new commit (the folder changed).
6. The test load is a dry in-memory run of the engine on the feed's latest fetched file: it writes no `loads` row and no record
   and notifies no one. The go-live's own recorded test load (step 11) is unchanged and is still pinned by the go-live.
7. "SQL parse" is DuckDB parsing every generated `.sql` file; "sandbox rule check" is `check_rule_sql()` on each rule file.
   Rule SQL still runs only in the sandbox (inside the test load).
8. A run is by a person (`run_by`), any backend-team role, as the go-live summary is. The pipeline is not an agent: no
   `proposal_log` row, no proposal.
9. Only a registered feed has a pipeline run (`FeedNotRegistered`, 409).
10. A passed run stays valid for any later go-live of the same hash, even months later: the hash is the code's identity, and a
    changed configuration or profile changes the hash. The file the test load read is not part of the identity.
11. A failed run keeps its commit (the commit stage passed): the repository holds every code version that was tried.
12. Deleting a feed deletes its `pipeline_runs` (as for every table with a feed, 7.9) and leaves its folder in the pipeline
    repository (as 7.16 left a profile's file in the blob store).
13. Every test helper that writes a go-live (`tests/configured.py` and the raw inserts) first records a passed run through the
    repository, with no git: those tests are about other rules.
14. `core.seed --demo` and `--demo-history` write no go-live (they need one onboarded by a person); the demo story and every
    Playwright story click "Run pipeline" before "Approve go-live".
15. The operator workspace shows the same pipeline panel in the go-live summary, because its one-click approval would otherwise
    be refused with no way to run the pipeline on that page.
16. The Azure DevOps adapter stays a stub; its keys go to `.env.example` only.
