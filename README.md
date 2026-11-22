# Amaze for CM — Proof of Concept

Agentic data onboarding and consumption PoC. Built locally first, with no containers; Azure adapters are switched on after the move to the office laptop.

Before you start: Python 3.14 (3.14.5), Node.js (LTS), Git and PostgreSQL 18 (any 18.x minor) installed on Windows (the same versions as the office laptop).

Start here:

1. `SETUP_PLUGINS.md`: install the Claude Code plugins.
2. `CLAUDE.md`: the rules Claude Code follows in this repository.
3. `BUILD_PLAN.md`: the phases. Begin with phase 0.
4. `STARTER_PROMPT.md`: paste its prompt into Claude Code.
5. Copy `.env.example` to `.env` and set the database password.

Rule 6 (synthetic data only) was voided on 2026-11-22: see `CLAUDE.md`. Secrets never go in the repository. On Replit, read `replit.md` first.

## Set up and run (Windows PowerShell, from the repository root)

One-time set-up:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env        # then replace CHANGE_ME in DATABASE_URL and TEST_DATABASE_URL
.\scripts\setup_db.ps1             # asks once for the postgres superuser password
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m core.seed          # synthetic glossary: 33 elements; safe to run again
.\.venv\Scripts\python -m core.seed --demo   # 3 synthetic demo products (codes demo_...); needs the glossary
npm --prefix apps\backend-ui install
npm --prefix apps\consumer-ui install
```

`py` with no version may start another Python; always name `-3.14`. The database password may use
letters, digits and `. _ ~ -` only, so it needs no URL encoding.

Run:

```powershell
.\scripts\dev.ps1
```

It opens three windows (API, backend-ui, consumer-ui), waits for them and prints OK or FAILED for
each. Close the three windows to stop.

| What | URL |
|---|---|
| API health | http://localhost:8000/api/health |
| Backend workbench: status | http://localhost:5173/#/status |
| Backend workbench: glossary | http://localhost:5173/#/glossary |
| Backend workbench: feed registry | http://localhost:5173/#/feeds |
| Backend workbench: one feed | http://localhost:5173/#/feed/<id> (linked from the registry) |
| Marketplace: search (consumer-ui) | http://localhost:5174/#/search |
| Marketplace: my requests | http://localhost:5174/#/requests |
| Marketplace: one requirement | http://localhost:5174/#/requirement/<id> (linked from My requests) |

Pick a dev user in the header; the glossary and feed registry need one of the six backend roles,
and the marketplace needs Riley Requester. Try the searches `close price`, `maturity date`,
`close price and bid price` and `volume` (no product: "Request this data" raises a requirement).

After a raise, Amaze looks for a known source in the synthetic catalogue (`fixtures/sources/`).
`volume` finds one: its file is fetched and stored under `data/blob/raw/`, a candidate feed appears
in the feed registry, and the requester sees "Your data is getting onboarded; we will notify you once
it is ready." `accrued interest` finds a known source whose file is missing on purpose: it waits, and
the daily fetch (06:00 UTC, registered at every API start) tries again.
`open price` has no known source: Amaze proposes where to look (confirm or reject it), then lists the
fitting sources with their conditions. Tick "I accept these conditions" and Pick: the file is fetched
and the requirement is onboarding. Picking "Backup end-of-day prices" (no file, on purpose) leaves it
waiting; "Choose another source" releases it and you pick again. `esg score` (tick the term) finds an
open-data source; `yield to maturity` with the purpose "internal desk data" finds none. Search
`closing prices` and press "Ask the assistant" for a suggested glossary element.

Phase 4, as Blake Backend in the backend workbench: open a feed from the registry, then Profile the
latest file (DuckDB), Summarise (the ProfilerAgent proposes an outcome), Sign off and Register (name a
data owner; open or restricted). `issue date` picks Instrument lifecycle, whose file has empty issue
dates: sign it off with gaps and the requester sees them. `lot size` picks Listing details, whose file
has no rows: Meets is not offered; a rejection tells the requester why. A `volume` request after its
feed is registered is linked to it at once, with no new fetch.

Phase 5, steps 05 and 06 (glossary builder and mapping). Registering a feed queues a glossary builder run
(a `glossary_runs` row) and starts it in the background, with no click; the banner under the header shows
it running, then how many proposals wait for the glossary owner. As Glenn Glossary, open Glossary:
approve or reject each proposal (a new element, an alias on an existing element, or a conflict, which can
only be rejected with a note). As Sam Steward, open a registered feed: Propose mappings, then approve each
recommendation, choose one candidate of a tie, or reject with a note; Propose again on a mapped column makes
a new version, and the old one stays listed. A column with no element is sent back to the glossary builder.
Try it on Open ESG scores (Run the glossary builder, approve both new elements, then map its three columns)
and on Bond terms (its file holds four undeclared columns: `rating`, `venue_mic_code`, `date`, `maturity`).
Bond terms is reached by a requirement for `coupon rate`. From phase 7 a fresh `--demo` seed leaves
`coupon_rate` out of the demo product Bond reference data, so that request raises a requirement and goes on to
source picking; a database seeded earlier (`amaze`) keeps the older product, which holds it, and answers the
request by reuse (a product's elements never change).

Phase 6, steps 09, 11 and 13 (rules, go-live, the run engine). On a registered feed with its columns mapped,
as Sam Steward: Propose rules. The RuleRecommenderAgent proposes SQL rules over the table `curated`; each is
back-tested on five synthetic past deliveries (Exchange daily volumes has them; a source without is "not
back-tested"). A rule that flags more than half of the history is returned, not proposed. Sam approves, then
Casey Checker; only then is there a rule. As Blake Backend: Prepare go-live runs a test load of the latest
file and drafts the summary (with the unmapped columns and the rejected glossary proposals); Devon Owner, the
owner named at registration, approves it (the daily job then loads the feed every day at 06:00 UTC, and every
load is reported for 10 days, `HYPERCARE_DAYS`) or rejects it back to step 05, 06 or 09. As Opal Operations:
Load now fetches and loads one feed. Try it on Exchange daily volumes (map its three columns, propose rules,
prepare, approve as Devon, Load now): `volume_in_whole_lots` is returned (it flags 40 of 50 history rows).
A load is four stages (raw, curated, validated, standard model), a value that does not cast to its element's
type holds the whole file, and a rule's SQL runs only in a locked-down DuckDB (`RULE_TIMEOUT_SECONDS`).
Mastering and transformations are a marked pass-through.

Phase 7, steps 12, 15 and 20 (every load, the release and the requester's "Ready"). Every scheduled load (the
daily job, or Load now as Opal Operations) is first checked by step 12 against the feed's latest profile: the
same bytes as an earlier delivery are `resent_quarantined` (**an unchanged fixture file is therefore
quarantined on every later daily run**, and only the first load of a file runs), a template column missing or
no CSV table is `breaking_held`, a new column is `additive_parked` (profiled, sent to the glossary builder, not
loaded until mapped and a new go-live), else `on_time_complete`. A loaded file is then certified (step 15): the
trust score is 100 x rows with no rule flag / rows; at or above `TRUST_RELEASE_THRESHOLD` (90) it is released,
below it the load is held and its consumers, operations and the backend team are told. On a release, step 20
creates the feed's product (owner and visibility from the registration) and each waiting requirement becomes
Ready, or Access restricted for a restricted product, or is told what is missing. A load a dead process left
received is abandoned after `LOAD_STALE_MINUTES` (30). The Notifications tab of both UIs shows what a person was
told. On `amaze`, as Opal, Load now on Exchange daily volumes: the first load passes (no hash is stored for the
phase 6 load), the second is quarantined; the six requirements waiting on it become Ready or are listed as not
covered.

REQUIRE_FOUR_EYES (phase 7.8): `false` (the default) lets one operator hold every role (`dev-operator`, Olive Operator)
and approve every step of a proposal in one click; `true` keeps today's rules exactly (steward then checker, the named
owner alone approves a go-live, the same person cannot decide two steps). The API writes the value to the append-only
`amaze_settings` table at start-up (a new version only when it changed), and the database guards read that row; no row
means `true`. `/api/health` shows it as `four_eyes`.

`/api/health` returns 503 and lists `unavailable` adapters when any selected adapter is still an
Azure stub.

Check:

```powershell
.\.venv\Scripts\python -m pytest -q -n 6      # uses the amaze_test database, a schema per worker; about 2.5 minutes
.\.venv\Scripts\python -m alembic current     # expect: 0019 (head)
npm --prefix apps\backend-ui run build
npm --prefix apps\consumer-ui run build
npm --prefix apps\backend-ui test              # component checks (vitest, jsdom, a stubbed fetch); phase 7.5
npm --prefix apps\consumer-ui test
```

Run `npm test` from PowerShell (or Git Bash at `/F/`, not `/f/`): from a lower-case drive path vitest loads
itself twice and every file fails with "failed to find the current suite".

Both UIs share one look (phase 7.5): Mantine 9.7.4 and `src/theme.ts`, byte-identical in the two apps
(`tests/test_ui_shared_look.py` checks that, the pinned versions and that every text/background pair meets
4.5:1). Change `theme.ts` in both apps at once.

The Playwright story (phase 7) runs on its own copy of the world, schema `e2e_story` of `amaze_test`, never on
`amaze`. Stop `dev.ps1` first (the story starts the API and both UIs on ports 8000, 5173 and 5174 itself and
refuses to reuse a running one). One-time: `npm --prefix e2e install` and `cd e2e; npx playwright install chromium`.

```powershell
cd e2e; npx playwright test                   # expect: 1 passed (about 20 seconds)
```

The screenshots of phase 7.5 walk the same path with their own spec and the same servers, and write
`docs/phase75-screenshots/<SHOTS_SET>/` (`before`, `after`, `checkpoint1`; `SHOTS_ONLY` picks shots, for example
`S1,S2,S7`; `SHOTS_SCHEME=dark` takes them dark):

```powershell
cd e2e; $env:SHOTS_SET="after"; npx playwright test -c playwright.screens.config.ts   # expect: 1 passed, 25 files
```

If a story run right after a screens run fails with "The API cannot be reached" (`net::ERR_NO_BUFFER_SPACE` in its trace), run the story and the screens set with a pause between, and check that both `vite.config.ts` still give the `/api` proxy keep-alive connections (phase 7.5).

Evaluate the mapping prompt on the gold set (`fixtures/gold/mappings.json`, phase 5). Its model calls are
logged to a database of their own, `amaze_eval`, owned by the same `amaze` user; the script refuses to
run when `EVAL_DATABASE_URL` is not set or names the application database. One-time set-up, as the
PostgreSQL superuser:

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -U postgres -c "CREATE DATABASE amaze_eval OWNER amaze"
# in .env: EVAL_DATABASE_URL=postgresql+psycopg://amaze:<password>@localhost:5432/amaze_eval
# bring it to the migration head: DATABASE_URL points at amaze_eval for this one command only
$env:DATABASE_URL = 'postgresql+psycopg://amaze:<password>@localhost:5432/amaze_eval'
.\.venv\Scripts\python -m alembic upgrade head
Remove-Item Env:DATABASE_URL
```

Then, after every migration (the evaluation database must be at the same head), run:

```powershell
.\.venv\Scripts\python -m agents.evaluate_mappings   # LLM_PROVIDER selects the provider; prints the score
```

Adapters are chosen in `.env` (`LLM_PROVIDER`, `STORAGE_PROVIDER`, ...). The local ones are the
defaults; the Azure ones are stubs until the move and raise `NotImplementedError`, except the model
adapter, which is real (phase 7.8).

### Live evaluation (phase 7.8)

Every agent prompt but mapping has a gold set, `fixtures/gold/<prompt_id>.json` (six invented cases; three for
`table_extractor`), scored by fixed checks on the structured output. The command uses the same `amaze_eval` database
and refusals as the mapping evaluation above, and the provider `LLM_PROVIDER` selects:

```powershell
.\.venv\Scripts\python -m agents.evaluate_agents --runs 2 --mappings   # all agents twice, plus the mapping gold set
.\.venv\Scripts\python -m agents.evaluate_agents --agents intent,profiler --runs 2 --max-calls 50 --json eval.json
```

It counts the planned calls first (cases x runs, plus 4 mapping sources x runs) and refuses to start above
`--max-calls` (default 200; `--runs 2 --mappings` plans 110). It prints counts and latency only, never a model
answer: per agent and run the cases, passes, failures, mean latency and requests made (retries included), the failed
case ids with the failed check's name, a `FLAG` line when two runs of an agent differ by more than one pass, and the
total.

### Real model (phase 7.8)

`LLM_PROVIDER=azure_openai` uses the Azure OpenAI adapter. Put these in `.env` (never in the repository).
`/api/health` lists `llm` as unavailable until the required ones are set.

| Setting | Default | Meaning |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | none | The resource endpoint (required). |
| `AZURE_OPENAI_DEPLOYMENT` | none | The deployment every prompt uses (required). |
| `AZURE_OPENAI_DEPLOYMENT_SMALL` | empty | A lighter deployment for the `intent` and `discovery_assistant` prompts; empty means use the main one. |
| `AZURE_OPENAI_API_VERSION` | none | The API version to call (required). |
| `AZURE_OPENAI_AUTH` | `key` | `key` uses the API key; `entra` uses `DefaultAzureCredential` and needs the `azure-identity` package. |
| `AZURE_OPENAI_API_KEY` | none | The API key (required when `AZURE_OPENAI_AUTH` is `key` or empty). |
| `LLM_TIMEOUT_SECONDS` | `60` | How long one model call may take before it counts as a timeout. |
| `LLM_RETRY_AFTER_CAP_SECONDS` | `20` | The longest `Retry-After` the adapter waits on an HTTP 429 before retrying (at most twice); a longer one fails at once. |
| `MAX_MODEL_CALLS_PER_ONBOARDING` | `60` | The cap on model calls in one onboarding (enforced from stage S5). |

### Uploading a file (phase 7.8)

The operator uploads a CSV, TSV, XLSX or table PDF in backend-ui ("Upload a file"); `UPLOAD_MAX_MB` (default 50)
is the size limit. Measured on the development laptop (2026-11-19):

| Format | Rows × columns | Time |
|---|---|---|
| CSV | 100,000 × 10 | about 3.1 s to inspect, confirm and profile (`tests/test_s00_intake.py`) |
| XLSX | 5,000 × 10 | 0.9 s to read (`tests/test_intake_xlsx.py`, bound 10 s) |
| XLSX | 20,000 × 10 | about 10 s to read (openpyxl reads about 20,000 cells a second here) |

Excel is the slowest format; for a larger table, upload it as CSV.

### Other settings added in phase 7.8

| Setting | Default | Meaning |
|---|---|---|
| `REQUIRE_FOUR_EYES` | `false` | `false`: one operator holds every role. `true`: steward, then checker, then owner, as before. Synced to the database at start-up; no row means `true`. |
| `AUTO_ACCEPT_CONFIDENCE` | `0.85` | A proposal at or above this and not a tie is shown as pre-accepted in the workspace. |
| `ONBOARDING_JOB_LEASE_SECONDS` | `300` | An onboarding job left running this long by a worker that died is claimed again. |
| `DROPZONE_ROOT` | `data/dropzone` | The watched folder of uploaded feeds, `<root>/<feed code>/`. |
| `SEND_SAMPLE_VALUES` | `false` | `true` sends at most 5 distinct non-empty values per column to a model. |
| `UPLOAD_MAX_MB` | `50` | The largest upload. |

`.env.example` cannot be edited by the file tools in this repository (`.claude/settings.json`), so these keys are listed
here; add them to `.env.example` by hand if you want them there.

### The operator story and the workspace shots (phase 7.8)

One operator (Olive Operator) uploads `fixtures/synthetic/Synthetic_Security_Master.xlsx`, walks the nine steps of
the workspace (one edited proposal; rules that flag rows rejected), the first load is released, and a consumer
previews and downloads the product. Both runs start the API (mock provider, database `amaze_test`, schema
`e2e_story`, its own drop folder) and both UIs themselves.

```powershell
cd e2e
npx playwright test -c playwright.operator.config.ts   # the operator story; the config sets REQUIRE_FOUR_EYES=false
npx playwright test                                    # the old story with four eyes on (D7); the config sets true
$env:SHOTS_SET="final"; npx playwright test -c playwright.workspace.config.ts  # docs/phase78-screenshots/final/
```

The path is written once in `e2e/operator_flow.ts`; the story and the shots both use it.

The database protects its own rules: append-only tables (`proposal_log`, `approvals`,
`glossary_elements`, `mappings`, `rules`, and from phase 2 `product_elements`, `product_grants`,
`requirement_elements`, `requirement_terms`, `consumer_requests`, and from phase 3
`source_choices`, `source_fetches`, `requirement_scopes`, and from phase 4 `feed_profiles`,
`feed_signoffs`, `feed_registrations`) refuse UPDATE, DELETE and
TRUNCATE, and configuration rows are accepted only for an approved proposal. Correct a mistake by
adding a new version. Requests made in the marketplace therefore stay in the database for good.
