# replit.md: instructions for Replit Agent

Read `CLAUDE.md` in full before every task. It is the rule set for this repository and overrides your defaults, and this file, wherever they differ. Then read `BUILD_PLAN.md` and the current phase plan in `docs/plans/`.

## Do not change without the owner's approval

- `CLAUDE.md`, `BUILD_PLAN.md` and this file.
- Any existing migration in `migrations/versions/`: add a new one instead.
- Any prompt in `agents/prompts/`: save a new version instead.
- The database triggers: never work around one. If a guard refuses a change, the change needs a new version or a plan decision.

## The seven rules in short (full text in `CLAUDE.md`)

1. AI proposes; people decide; rules execute. An agent writes a proposal; only an approved proposal becomes versioned configuration.
2. No model call in the run engine (step 13) or in entitlement decisions.
3. Every negative outcome is a named, typed result, never a bare error.
4. Every AI proposal is logged (agent, provider, model and version, prompt version, input, output, decision).
5. Everything Azure sits behind an interface in `ports/`; business logic never imports an Azure SDK.
6. Voided on 2026-11-22 (it was "synthetic data only").
7. Configuration is versioned, never edited in place.

## How to work

1. One phase at a time. The plan goes in `docs/plans/` before any code, and the owner approves it.
2. Tests first: paste the failing summary line (RED), then write the least code that passes and paste its summary line (GREEN).
3. Touch only the files the plan names. Any other file is a deviation, recorded in the plan's "Results and deviations" section.
4. One commit per task. Stop at each checkpoint the plan names and wait for review.
5. No database connection is held during a model call: follow the read, end the transaction, call, lock, check again, write pattern in `CLAUDE.md`.

## Running on Replit: set these up in the first session

The repository was built on Windows, and `CLAUDE.md` describes that laptop. On Replit, record what you did for each item below in the phase plan.

1. **Python.** `pyproject.toml` requires 3.14 (`>=3.14,<3.15`). If Replit cannot provide 3.14, stop and ask; do not lower the pin.
2. **Database.** Use Replit's PostgreSQL through `DATABASE_URL`. It runs PostgreSQL 16; the code uses no feature newer than 14. Run `python -m alembic upgrade head`, then the full suite `pytest -q -n 6`, and report every failure before fixing anything.
3. **Test and evaluation databases.** The tests expect `amaze_test` (one schema per worker, `TEST_DATABASE_URL`) and the evaluation expects `amaze_eval` (`EVAL_DATABASE_URL`). If extra databases cannot be created, stop and propose the change.
4. **Secrets.** Put the Azure OpenAI values and the database URLs in Replit Secrets, never in a file. `.env.example` lists the names.
5. **Run setup.** `scripts/dev.ps1` and `scripts/setup_db.ps1` are PowerShell. Add a Replit run setup that starts the API (uvicorn) and both UIs (`npm run dev` in `apps/backend-ui` and `apps/consumer-ui`). Leave the `.ps1` files in place.
6. **Files.** With `STORAGE_PROVIDER=local`, uploads and blobs go to `LOCAL_BLOB_ROOT` and the drop folders to `DROPZONE_ROOT`. Check that they survive a restart, and in a published app.
7. **Playwright.** `cd e2e; npx playwright test` needs its browsers installed. The story runs on `amaze_test`, schema `e2e_story`, never on the main database.

## Reference material

The original Amaze for CM code is kept outside this repository. Each phase plan says how it is used.
