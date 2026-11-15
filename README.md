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
`/api/health` returns 503 and lists `unavailable` adapters when any selected adapter is still an
Azure stub.

Check:

```powershell
.\.venv\Scripts\python -m pytest -v          # uses the amaze_test database; rebuilds its schema each run
.\.venv\Scripts\python -m alembic current     # expect: 0011 (head)
npm --prefix apps\backend-ui run build
npm --prefix apps\consumer-ui run build
```

Adapters are chosen in `.env` (`LLM_PROVIDER`, `STORAGE_PROVIDER`, ...). The local ones are the
defaults; the Azure ones are stubs until the move and raise `NotImplementedError`.

The database protects its own rules: append-only tables (`proposal_log`, `approvals`,
`glossary_elements`, `mappings`, `rules`, and from phase 2 `product_elements`, `product_grants`,
`requirement_elements`, `requirement_terms`, `consumer_requests`, and from phase 3
`source_choices`, `source_fetches`, `requirement_scopes`) refuse UPDATE, DELETE and
TRUNCATE, and configuration rows are accepted only for an approved proposal. Correct a mistake by
adding a new version. Requests made in the marketplace therefore stay in the database for good.
