# Amaze for CM — Proof of Concept

Agentic data onboarding and consumption PoC. Built locally first, with no containers; Azure adapters are switched on after the move to the office laptop.

Before you start: Python 3.14 (3.14.5), Node.js (LTS), Git and PostgreSQL 18 (any 18.x minor) installed on Windows (the same versions as the office laptop).

Start here:

1. `SETUP_PLUGINS.md`: install the Claude Code plugins.
2. `CLAUDE.md`: the rules Claude Code follows in this repository.
3. `BUILD_PLAN.md`: the phases. Begin with phase 0.
4. `STARTER_PROMPT.md`: paste its prompt into Claude Code.
5. Copy `.env.example` to `.env` and set the database password.

Use synthetic data only.

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
Bond terms is only reached by a requirement for `coupon rate` while the demo product Bond reference data is
not loaded: with `--demo`, that request is answered by the product (reuse).

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
`/api/health` returns 503 and lists `unavailable` adapters when any selected adapter is still an
Azure stub.

Check:

```powershell
.\.venv\Scripts\python -m pytest -v          # uses the amaze_test database; rebuilds its schema each run
.\.venv\Scripts\python -m alembic current     # expect: 0018 (head)
npm --prefix apps\backend-ui run build
npm --prefix apps\consumer-ui run build
```

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
defaults; the Azure ones are stubs until the move and raise `NotImplementedError`.

The database protects its own rules: append-only tables (`proposal_log`, `approvals`,
`glossary_elements`, `mappings`, `rules`, and from phase 2 `product_elements`, `product_grants`,
`requirement_elements`, `requirement_terms`, `consumer_requests`, and from phase 3
`source_choices`, `source_fetches`, `requirement_scopes`, and from phase 4 `feed_profiles`,
`feed_signoffs`, `feed_registrations`) refuse UPDATE, DELETE and
TRUNCATE, and configuration rows are accepted only for an approved proposal. Correct a mistake by
adding a new version. Requests made in the marketplace therefore stay in the database for good.
