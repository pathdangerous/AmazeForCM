# Phase 0 plan — Skeleton and placeholders

**Status:** approved on 2026-11-13 with changes (Python 3.14, ASCII scripts, two extra risks, three stop points). Those changes are already applied below.
**Date:** 2026-11-13. **Branch:** `phase-0`. **Scope:** phase 0 of `BUILD_PLAN.md` only.

## 1. Goal and "done when"

Phase 0 is done when all four are true:

1. `scripts/dev.ps1` starts the API and both UIs.
2. Both UIs load in the browser and show live data from the API.
3. `pytest` passes with nothing skipped.
4. Every port has a working local adapter and an Azure stub that raises `NotImplementedError("Azure adapter: implement after move")`.

## 2. Machine state (checked on 2026-11-13)

| Item | Found | Status |
|---|---|---|
| Python | 3.14.5 at `C:\Python314`, reached with `py -3.14` | OK. `py` with no version now selects 3.15, so every command names `-3.14` |
| Node / npm | v24.13.0 / 11.6.2 | OK |
| Git | 2.56.1 | Repository initialised today; baseline commit on `main` |
| PostgreSQL | Server 18.6 (service `postgresql-x64-18`, `C:\Program Files\PostgreSQL\18`), running, `localhost:5432` accepting connections. The `psql` on PATH is the 18.4 client in `F:\PostgreSQL\18` | OK: same major version as the 18.4 target |
| `postgres` superuser | Needs a password; no saved password file | You run `setup_db.ps1` yourself (one prompt) |
| PowerShell | Windows PowerShell 5.1 only; execution policy RemoteSigned | Scripts are written for 5.1 |
| Ports 8000, 5173, 5174 | Free | OK |
| Plugins | Superpowers, Karpathy, i-have-adhd, Octopus not loaded | I follow the same flow by hand |

## 3. Decisions you made

1. **Python 3.14** (3.14.5 here and on the office laptop). `pyproject.toml` sets `requires-python = ">=3.14,<3.15"`, and a test fails on any other interpreter. Python 3.12 is not used.
2. **PostgreSQL 18.4** here and on the office laptop. No feature newer than PostgreSQL 14 is used until the Azure database version is confirmed.
3. **`platform/` is renamed to `ports/`.** A package named `platform` collides with Python's built-in `platform` module. Imports become `ports.llm`, `ports.storage`, and so on.
4. **Git:** one commit per task on the `phase-0` branch. Local only. Nothing is pushed.
5. **Roles:** seven dev users, one per role (section 9).
6. **Scripts are plain ASCII** (`setup_db.ps1`, `dev.ps1`), so Windows PowerShell 5.1 reads them correctly.
7. **Stop points:** after task 1 (you run `setup_db.ps1`), after task 5 (pytest output and `alembic current`), and after task 16 (the full section 14 results).

## 4. Assumptions — veto any by number

1. **Database features.** Phase 0 uses only features present since PostgreSQL 14 (plain tables, `jsonb`, `timestamptz`, check constraints), because the Azure Database for PostgreSQL version is not confirmed yet.
2. **`.env`.** I create `.env` from `.env.example` with a randomly generated password for the `amaze` database user. The file is git-ignored. You never type that password.
3. **Tests use real PostgreSQL**, in a separate `amaze_test` database, not SQLite. Each test runs in a transaction that is rolled back. If the database is unreachable the tests fail; they do not skip.
4. **Two additions to the CLAUDE.md layout:** `ports/db/` (engine, session, table base class: the "Database" row of the Placeholders table) and `migrations/` with `alembic.ini` at the root.
5. **Dev users are a fixture file, not a table.** `fixtures/dev_users.json` is loaded by the dev identity adapter. With Entra ID, users will not live in our database, so log tables store the user id as text.
6. **Dev sign-in** is an `X-Dev-User: <user id>` header, chosen from a dropdown in each UI. No passwords. It works only when `IDENTITY_PROVIDER=dev`.
7. **`ProposalLog` gets `record()` and `record_decision()`.** Rule 4 requires "who approved or rejected it", so the decision columns and the method that fills them are in phase 0. The approval workflow and its screens stay in later phases.
8. **`MockProvider` uses fixtures only in phase 0**, keyed by prompt id and prompt version. Heuristics (name similarity) arrive with the first agent that needs them.
9. **Not created in phase 0:** `agents/base.py`, `agents/registry.py`, prompts, `StepResult`, domain models, and the three per-platform API routers. `BUILD_PLAN.md` first needs them in later phases. Their folders exist as empty packages.
10. **The `notifications` table is created in phase 0**, because the in-app Notifier cannot work without it. Phase 1's Notification model reuses the table.
11. **Connectors:** the interface plus a local-file and an HTTP connector. No Azure stub, because CLAUDE.md defines no Azure counterpart or selector for connectors.
12. **Local scheduler keeps jobs in memory.** No persistent job store in phase 0.
13. **API paths start with `/api`.** The Vite dev servers proxy `/api` to port 8000, so no CORS setup. Ports are fixed: API 8000, backend-ui 5173, consumer-ui 5174. A taken port is an error, not a silent change.
14. **Two independent npm projects.** No workspace, no UI library, no router. A fetch helper of about 20 lines is duplicated in both.
15. **Python dependencies:** plain `venv`, `pip` and `requirements.txt` with exact versions recorded at install time. No DuckDB, Azure SDKs or Playwright until the phase that uses them.
16. **`dev.ps1` opens three titled PowerShell windows**, waits for the three ports and prints OK or FAILED for each. You stop the system by closing the windows.
17. **No linter, formatter or CI** in phase 0.
18. **End-of-phase review:** Octopus is not installed, so I review the result against CLAUDE.md's rules and this plan. You can also run `/code-review`.

## 5. Files

```
.gitattributes                 new    LF line endings in the repository
.gitignore                     edit   add .pytest_cache/
.env.example                   edit   add TEST_DATABASE_URL
.env                           new    generated, git-ignored
CLAUDE.md                      edit   platform/ -> ports/, platform.llm -> ports.llm, layout additions
BUILD_PLAN.md                  edit   platform/ -> ports/ in the phase 0 bullets
README.md                      edit   "Set up and run" section
requirements.txt               new    exact pins, all with Python 3.14 Windows wheels
requirements-dev.txt           new    -r requirements.txt, pytest
pyproject.toml                 new    requires-python = ">=3.14,<3.15"; pytest settings
alembic.ini                    new
migrations/
  env.py  script.py.mako
  versions/0001_proposal_log_notifications.py
scripts/
  setup_db.ps1  dev.ps1
ports/                         renamed from platform/ with git mv
  __init__.py                  empty, and must stay empty (section 10)
  config.py                    Settings: the six selectors and LOCAL_BLOB_ROOT
  db/          __init__.py  base.py  session.py
  llm/         __init__.py  base.py  mock.py         azure_openai.py
  storage/     __init__.py  base.py  local.py        azure_blob.py
  scheduler/   __init__.py  base.py  local.py        azure_functions.py
  identity/    __init__.py  base.py  dev.py          entra_id.py
  secrets/     __init__.py  base.py  env.py          key_vault.py
  notify/      __init__.py  base.py  inapp.py        email_teams.py
  connectors/  __init__.py  base.py  local_file.py   http.py
core/
  __init__.py
  proposal_log.py              table, ProposalLog.record(), record_decision()
  domain/__init__.py  steps/__init__.py  engine/__init__.py  entitlement/__init__.py
agents/__init__.py
apps/
  __init__.py
  api/         __init__.py  main.py  deps.py
               routers/__init__.py  routers/health.py  routers/dev_identity.py
  backend-ui/  package.json  vite.config.ts  tsconfig.json  index.html
               src/main.tsx  src/App.tsx  src/api.ts
  consumer-ui/ the same seven files
fixtures/
  dev_users.json
  llm/phase0_smoke/v1.json
tests/
  conftest.py                  test database, migration and rollback fixtures
  test_python_version.py       fails unless the interpreter is 3.14
  test_import_boundaries.py
  test_settings.py
  test_port_llm.py       test_port_storage.py   test_port_scheduler.py
  test_port_identity.py  test_port_secrets.py   test_port_notify.py
  test_port_connectors.py
  test_azure_stubs.py    test_port_factories.py
  test_proposal_log.py   test_migrations.py     test_api.py
```

`.gitkeep` files are removed from folders that receive a real file and kept in folders that stay empty (`agents/prompts`, `fixtures/gold`, `fixtures/glossary`, `fixtures/sources`, `infra`).

## 6. Ports: interface, local adapter, Azure stub

| Port | Selector | Local adapter (works) | Azure stub (raises) | Env keys the stub reads |
|---|---|---|---|---|
| `LLMProvider` | `LLM_PROVIDER=mock\|azure_openai` | `MockProvider` | `AzureOpenAIProvider` | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_AUTH`, `AZURE_OPENAI_API_KEY` |
| `BlobStore` | `STORAGE_PROVIDER=local\|azure_blob` | `LocalBlobStore` (folder `LOCAL_BLOB_ROOT`) | `AzureBlobStore` | `AZURE_STORAGE_ACCOUNT`, `AZURE_STORAGE_CONTAINER` |
| `Scheduler` | `SCHEDULER_PROVIDER=local\|azure_functions` | `LocalScheduler` (APScheduler 3.x) | `AzureFunctionsScheduler` | `AZURE_FUNCTIONS_APP` |
| `Identity` | `IDENTITY_PROVIDER=dev\|entra_id` | `DevIdentity` | `EntraIdIdentity` | `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` |
| `Secrets` | `SECRETS_PROVIDER=env\|key_vault` | `EnvSecrets` | `KeyVaultSecrets` | `AZURE_KEY_VAULT_URL` |
| `Notifier` | `NOTIFY_PROVIDER=inapp\|email` | `InAppNotifier` (`notifications` table) | `EmailNotifier` | `NOTIFY_EMAIL_FROM`, `TEAMS_WEBHOOK_URL` |
| `SourceConnector` | none; chosen per source | `LocalFileConnector`, `HttpConnector` | none defined | none |
| Database | `DATABASE_URL` | local PostgreSQL | same code, different URL | `DATABASE_URL` |

Method signatures. The Azure stub has the same public methods and signatures as the local adapter.

```python
# ports/llm/base.py  (as in CLAUDE.md)
class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class LLMResult(BaseModel):
    output: BaseModel
    provider: str
    model: str
    model_version: str
    prompt_id: str
    prompt_version: str
    latency_ms: int
    raw_ref: str | None

class LLMProvider(Protocol):
    name: str
    def complete(self, *, prompt_id: str, prompt_version: str, messages: list[Message],
                 response_schema: type[BaseModel], temperature: float = 0.0) -> LLMResult: ...

# ports/storage/base.py
class BlobStore(Protocol):
    name: str
    def put(self, key: str, data: bytes) -> str: ...          # returns a reference to the blob
    def get(self, key: str) -> bytes: ...                     # raises BlobNotFound
    def exists(self, key: str) -> bool: ...
    def list_keys(self, prefix: str = "") -> list[str]: ...

# ports/scheduler/base.py
class Scheduler(Protocol):
    name: str
    def start(self) -> None: ...
    def shutdown(self) -> None: ...
    def schedule_daily(self, job_id: str, func: Callable[[], None], *,
                       hour: int, minute: int = 0) -> JobInfo: ...   # UTC
    def run_now(self, job_id: str) -> None: ...
    def remove(self, job_id: str) -> None: ...
    def list_jobs(self) -> list[JobInfo]: ...

# ports/identity/base.py
class Identity(Protocol):
    name: str
    def authenticate(self, headers: Mapping[str, str]) -> User | None: ...
    def get_user(self, user_id: str) -> User | None: ...

# ports/secrets/base.py
class Secrets(Protocol):
    name: str
    def get(self, key: str) -> str: ...                       # raises SecretNotFound

# ports/notify/base.py
class Notifier(Protocol):
    name: str
    def notify(self, *, recipient: str, subject: str, body: str,
               link: str | None = None) -> str: ...           # returns the notification id

# ports/connectors/base.py
class SourceConnector(Protocol):
    name: str
    def fetch(self, source_ref: str) -> FetchedFile: ...      # raises SourceUnavailable
```

Rules that apply to every port:

1. **Selection.** Each port package has its own factory in its `__init__.py` (for example `get_llm_provider()`), which reads `Settings` and imports only the chosen adapter. There is no central factory, because one would make every caller import `ports.llm` (section 10).
2. **Unknown selector value.** `Settings` rejects it at start-up and names the variable and its allowed values.
3. **Stubs import no Azure SDK.** The constructor reads its env keys; every method raises `NotImplementedError("Azure adapter: implement after move")`.
4. **`DATABASE_URL` is read through the `Secrets` port**, so Key Vault can supply it later with no change to database code.

How the ports are checked:

- `test_port_*.py`: one file per port, testing the local adapter's real behaviour. Examples: a blob round trip, a key that tries to escape the root folder is refused, a scheduled job runs with `run_now`, a missing secret raises `SecretNotFound`, a missing source file raises `SourceUnavailable`.
- `test_azure_stubs.py`: for every stub, the method names and signatures equal the local adapter's, every method raises the exact `NotImplementedError` message, the constructor reads its env keys, and each key exists in `.env.example`.
- `test_port_factories.py`: the default is the local adapter, each selector value returns the right class, and an unknown value fails with a clear message.
- `test_settings.py`: `Settings` loaded from a copy of `.env.example` gives the local defaults. This proves the inline comments in that file are parsed correctly.

## 7. MockProvider (`LLM_PROVIDER=mock`)

1. `complete()` loads `fixtures/llm/<prompt_id>/<prompt_version>.json` and validates it against `response_schema`.
2. It returns an `LLMResult` with `provider="mock"`, `model="mock"`, `model_version="1"`, `latency_ms=0`, `raw_ref=None`, and the prompt id and version it was given.
3. **Deterministic:** no randomness and no clock. Two identical calls return equal results.
4. **Missing fixture:** raises `MockFixtureMissing` and names the path it expected. It never invents an output.
5. **Fixture that does not match the schema:** raises Pydantic's `ValidationError`.
6. Phase 0 ships one synthetic fixture, `phase0_smoke/v1.json`, used by the tests.

Checked by `test_port_llm.py`: schema-valid output, equal results on repeat, both failure cases, and the metadata fields.

## 8. ProposalLog table

Table `proposal_log`, created by the first Alembic migration.

| Column | Type | Rule 4 field |
|---|---|---|
| `id` | uuid, primary key | |
| `created_at` | timestamptz | |
| `agent_name` | text | agent name |
| `provider` | text | provider |
| `model` | text | model |
| `model_version` | text | model version |
| `prompt_id` | text | |
| `prompt_version` | text | prompt version |
| `input_ref` | text | input reference |
| `output` | jsonb | output |
| `raw_ref` | text, nullable | |
| `latency_ms` | integer | |
| `status` | text: `pending`, `approved` or `rejected` | |
| `decided_by` | text, nullable | who approved or rejected |
| `decided_at` | timestamptz, nullable | |
| `decision_note` | text, nullable | |

```python
class ProposalLog:
    def __init__(self, session: Session): ...
    def record(self, *, agent_name: str, input_ref: str, result: LLMResult) -> ProposalLogEntry: ...
    def record_decision(self, entry_id: UUID, *, decision: Literal["approved", "rejected"],
                        decided_by: str, note: str | None = None) -> ProposalLogEntry: ...
```

- `record()` writes one `pending` row per model call.
- `record_decision()` fills the decision columns once. A second decision on the same row raises `ProposalAlreadyDecided`; a decision is never overwritten.
- A database check constraint keeps `status` to the three values.

Checked by `test_proposal_log.py` (all rule 4 fields stored, decision stored with who, second decision refused) and `test_migrations.py` (upgrade, downgrade, upgrade again on the test database).

The same migration creates `notifications`: `id`, `created_at`, `recipient`, `subject`, `body`, `link`, `read_at`.

## 9. Seeded dev users

`fixtures/dev_users.json`. All names are invented; emails use the reserved `example.test` domain.

| User id | Display name | Role | Appears in |
|---|---|---|---|
| `dev-requester` | Riley Requester | `requester` | consumer-ui |
| `dev-backend-team` | Blake Backend | `backend_team` | backend-ui |
| `dev-data-owner` | Devon Owner | `data_owner` | backend-ui |
| `dev-data-steward` | Sam Steward | `data_steward` | backend-ui |
| `dev-checker` | Casey Checker | `checker` | backend-ui |
| `dev-glossary-owner` | Glenn Glossary | `glossary_owner` | backend-ui |
| `dev-operations` | Opal Operations | `operations` | backend-ui |

Checked by `test_port_identity.py`: exactly one user per role, a known header resolves to its user, an unknown or missing header resolves to no user.

## 10. Import-boundary test

`tests/test_import_boundaries.py` enforces rule 2 without running any code:

1. It parses every Python file in `core`, `agents`, `ports` and `apps` and builds the import graph, including relative imports and the parent packages that an import loads.
2. For each module under `core/engine/` and `core/entitlement/` it follows imports **directly and through other modules**.
3. It fails if any path reaches `agents` or `ports.llm`, and prints the chain of imports that got there.
4. **It tests itself.** It runs the same checker on a temporary folder with planted violations (direct import, `from ports import llm`, relative import, and one reached through a helper module) and asserts each is caught. An empty `core/engine/` therefore cannot make the test pass by accident.

Consequence: `ports/__init__.py` must stay empty, and factories live in each port's own package.

Limit: the check is static. An import built from a string at run time is not detected.

## 11. Scripts

### `scripts/setup_db.ps1` — database and user

1. Reads `DATABASE_URL` and `TEST_DATABASE_URL` from `.env`. Stops with a clear message if `.env` is missing or the password is still `CHANGE_ME`.
2. Asks for the `postgres` superuser password with a hidden prompt, unless `PGPASSWORD` is already set. The password is kept for the script's process only.
3. Creates the `amaze` role if it is missing; otherwise sets its password to match `.env`.
4. Creates the `amaze` and `amaze_test` databases, owned by `amaze`, if they are missing.
5. Connects as `amaze` to both databases and prints the result.
6. **Safe to run again.** It never drops anything.

Checked by running it twice (the second run reports "already exists") and by the connection test in step 5.

### `scripts/dev.ps1` — API and both UIs

1. Checks that `.env`, `.venv` and both `node_modules` folders exist. If one is missing it prints the exact command that fixes it and stops.
2. Opens three titled windows: `uvicorn apps.api.main:app --reload --port 8000`, and `npm run dev` in each UI folder.
3. Waits up to 60 seconds for ports 8000, 5173 and 5174, then prints OK or FAILED for each, followed by the three URLs.
4. Exits with a non-zero code if any service failed to start.

Checked by running it and by the page checks in section 14.

Neither script has an automated test; both are checked by running them. Both are plain ASCII (no curly quotes, dashes or other non-ASCII characters), checked with a byte scan before each commit.

## 12. Python environment and the two npm projects

**Python**

- `py -3.14 -m venv .venv` at the repository root.
- Pinned, and confirmed on 2026-11-13 to install cleanly into a fresh venv with `pip install --only-binary=:all:` (no source builds) and `pip check`:

  | Package | Version | Package | Version |
  |---|---|---|---|
  | fastapi | 0.162.5 | alembic | 1.20.1 |
  | uvicorn[standard] | 0.54.0 | psycopg[binary] | 3.3.9 |
  | pydantic | 2.14.3 | apscheduler | 3.11.3 (below 4) |
  | pydantic-settings | 2.16.2 | httpx | 0.28.1 |
  | sqlalchemy | 2.1.8 | pytest (dev) | 9.2.0 |

- Only the direct dependencies are pinned; their own dependencies are resolved by pip.
- `pyproject.toml` holds `requires-python = ">=3.14,<3.15"` and the pytest settings (repository root on the import path, tests in `tests/`).
- `tests/test_python_version.py` fails unless `sys.version_info[:2] == (3, 14)`.

**API (phase 0 endpoints only)**

- `GET /api/health`: status, database reachable or not, and the six selected adapters. Returns 503 if the database is down.
- `GET /api/dev/users`: the seeded users. Returns 404 unless `IDENTITY_PROVIDER=dev`.
- `GET /api/me`: the user resolved from `X-Dev-User`, or 401.
- The local scheduler starts and stops with the API, with no jobs.

**npm projects** (`apps/backend-ui`, `apps/consumer-ui`)

- Vite, React and TypeScript, created from the official `react-ts` template and trimmed to one page.
- The page shows the app title, API and database status from `/api/health`, a dev-user dropdown, and "Signed in as" from `/api/me`.
- backend-ui lists the six backend users; consumer-ui lists the requester.
- `vite.config.ts` fixes the port (5173 and 5174), fails if it is taken, and proxies `/api` to `http://localhost:8000`.

Checked by `npm run build` in each project (type check and build) and by loading each page.

## 13. Tasks, in order

Each task: tests first, then code, then its check, then one commit. I stop and report after tasks 1, 5 and 16, and continue without stopping between them.

| # | Task | Tests written first | Check |
|---|---|---|---|
| 1 | `setup_db.ps1`, `.env`, `TEST_DATABASE_URL` in `.env.example` | none (script) | **STOP.** You run `.\scripts\setup_db.ps1` once. I then confirm both databases accept the `amaze` user |
| 2 | Rename to `ports/`, package files, `.gitattributes`, `pyproject.toml`, CLAUDE.md and BUILD_PLAN.md rename edits | `test_python_version.py` | `python -c "import platform, ports, core; print(platform.system())"` prints `Windows`; pytest passes |
| 3 | Import-boundary test | `test_import_boundaries.py` | Passes, including its planted-violation self-tests |
| 4 | `Settings`; Secrets port | `test_settings.py`, `test_port_secrets.py`; first rows of `test_azure_stubs.py` and `test_port_factories.py` | pytest |
| 5 | `ports/db`, Alembic, first migration, test fixtures | `test_migrations.py` | pytest; `alembic upgrade head`; `alembic current` shows `0001 (head)`. **STOP:** I show you both outputs |
| 6 | LLM port: `MockProvider`, stub, fixture | `test_port_llm.py` | pytest |
| 7 | `ProposalLog` | `test_proposal_log.py` | pytest |
| 8 | Storage port | `test_port_storage.py` | pytest |
| 9 | Scheduler port | `test_port_scheduler.py` | pytest |
| 10 | Identity port and `dev_users.json` | `test_port_identity.py` | pytest |
| 11 | Notifier port | `test_port_notify.py` | pytest |
| 12 | Connectors | `test_port_connectors.py` | pytest |
| 13 | API: health, dev users, me | `test_api.py` | pytest; `GET /api/health` returns `"status":"ok"` |
| 14 | backend-ui and consumer-ui | none | `npm run build` in both; both pages load |
| 15 | `dev.ps1`, README section | none (script) | Section 14 run in full; results shown to you |
| 16 | Review against CLAUDE.md's seven rules and this plan | none | Findings reported; fixes committed. **STOP:** full section 14 results shown to you |

Tasks 6 to 12 each add their port's rows to `test_azure_stubs.py` and `test_port_factories.py`.

## 14. Commands that prove phase 0 is done

Run from `F:\amaze-cm-poc` in Windows PowerShell.

**Set-up** (I run these during implementation, except `setup_db.ps1`; listed so you can repeat them on a clean copy):

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env        # only if .env does not exist; then set the password in both URLs
.\scripts\setup_db.ps1             # asks for the postgres superuser password
.\.venv\Scripts\python -m alembic upgrade head
npm --prefix apps\backend-ui install
npm --prefix apps\consumer-ui install
```

**Proof:**

```powershell
.\.venv\Scripts\python -m pytest -v
.\.venv\Scripts\python -m alembic current
npm --prefix apps\backend-ui run build
npm --prefix apps\consumer-ui run build
.\scripts\dev.ps1
```

| Command | Expected result |
|---|---|
| `pytest -v` | All tests pass. 0 failed, 0 skipped |
| `alembic current` | `0001 (head)` |
| `npm ... run build` (twice) | Exit code 0, no TypeScript errors |
| `dev.ps1` | Three windows open; three `OK` lines |

Then open in the browser:

| URL | Expected result |
|---|---|
| `http://localhost:8000/api/health` | `{"status":"ok","database":"ok","adapters":{"llm":"mock","storage":"local","scheduler":"local","identity":"dev","secrets":"env","notify":"inapp"}}` |
| `http://localhost:5173` | Backend workbench page: API ok, database ok, six users in the dropdown, "Signed in as" changes with the dropdown |
| `http://localhost:5174` | Marketplace page: API ok, database ok, signed in as Riley Requester |

I will also load both pages myself and show you screenshots.

## 15. Not in phase 0

Agents and prompts, steps and `StepResult`, domain models, glossary seed, per-platform routers, DuckDB, the Playwright test, Bicep content, mock heuristics, any UI feature beyond status and the dev user, linting and CI.

## 16. Risks

1. **The local scheduler keeps jobs in memory.** A restart of the API loses every registered job. Phase 3 (the daily fetch) needs jobs stored in PostgreSQL, or re-registered from the database at start-up.
2. **Azure Functions support for Python 3.14 is not confirmed.** Check it at the Azure move (phase 8) before choosing the Functions timer trigger for the scheduler.
3. **Azure Database for PostgreSQL version is not confirmed.** Local and office laptop run 18.4; phase 0 uses nothing newer than PostgreSQL 14 until the Azure version is known.
4. **`py` with no version now selects Python 3.15 on this machine.** Every command names `py -3.14`; inside the venv this does not matter. `test_python_version.py` catches a venv built with the wrong interpreter.
5. **The Vite scaffolder may ask questions interactively.** If it does, I write the same seven files by hand.
6. **APScheduler 4 has a different API.** The version is held below 4.
7. **Inline comments in `.env.example`** could be read as part of a value. `test_settings.py` covers this.
8. **Only direct dependencies are pinned.** A transitive dependency could change between installs on the two laptops. If that causes a difference, add a full lock file.
