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
| Backend workbench (backend-ui) | http://localhost:5173 |
| Marketplace (consumer-ui) | http://localhost:5174 |

Check:

```powershell
.\.venv\Scripts\python -m pytest -v          # uses the amaze_test database; rebuilds its schema each run
.\.venv\Scripts\python -m alembic current     # expect: 0001 (head)
npm --prefix apps\backend-ui run build
npm --prefix apps\consumer-ui run build
```

Adapters are chosen in `.env` (`LLM_PROVIDER`, `STORAGE_PROVIDER`, ...). The local ones are the
defaults; the Azure ones are stubs until the move and raise `NotImplementedError`.
