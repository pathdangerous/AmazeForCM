# Phase 7.10 — One entrance, two personas

Owner-approved scope from the current request. Work on `phase-7.10` from main;
do not merge this phase. Baseline main update was explicitly authorized separately.

## Tasks and verification

1. Add `scripts/setup_replit.sh`: recreate the existing standalone Python 3.14
   runtime and pip target packages from `requirements-dev.txt`, without a virtualenv.
   Prove it against a fresh runtime directory, preserving the working runtime until success.
2. Route `/market/` to consumer UI (Vite base) through the sole port-5000 proxy;
   other paths go to backend UI. Keep direct local ports 5173 and 5174 usable.
   Expose only 5000 -> 80 in `.replit`; cover HTTP and WebSocket routing.
3. Both pickers show exactly dev-operator and dev-requester when four-eyes is off;
   retain all existing users when it is on. Share a stored choice and navigate to the
   selected persona's app, including on local development ports. Keep fixtures intact.
4. Update Playwright stories/screens and product links for `/market/`.
   Run both B10 commands, both UI unit suites, and `pytest -q -n 6`.

Tests first: report actual RED before implementation and GREEN afterwards.
One implementation commit per numbered task. Final verification is the checkpoint;
push the branch and report results without merging.

## Files in scope

`BUILD_PLAN.md`, this plan, `scripts/setup_replit.sh`, `scripts/preview-proxy.mjs`,
`.replit`, consumer Vite configuration, both UI App components and their tests,
shared UI persona helpers and tests, dev identity API and tests if needed,
backend product-link component and affected tests, e2e configs/stories/screens,
and focused runtime/proxy verification tests.
No fixture user removal, dependency upgrades, or changes to business workflows.

## Results and deviations

- The build index lacked a 7.9 heading; added a pointer to its existing plan before 7.10.
- Baseline main was fast-forwarded and pushed to `30a2929` as authorized.
- Verification deviation: `tests/test_settings.py` must clear both model variables it
  supplies through its temporary dotenv file. Reproduced failure: the Replit
  LLM_RETRY_AFTER_CAP_SECONDS environment value correctly overrides that file.
  Fix the test's isolation, not settings precedence or the development configuration.
- Bootstrap uses bundled pip directly with `--target`: uv marks its Python externally
  managed, so installing pip into the interpreter with ensurepip was refused.
- Replit automatically restored old port mappings when test servers started. The
  committed configuration exposes only 5000 -> 80; disable automatic port forwarding
  in User Settings to prevent future additions. Removed the restored entries after startup.
- Required polish log update: `docs/ui-polish.md` records the consumer header wrapping
  with the operator's long role label; no cosmetic change was made.

## Verification evidence

- Task 1 RED: 2 failed; GREEN: 2 passed on the fresh interpreter. Fresh install:
  `RUNTIME_ROOT="$PWD/.runtime/fresh-proof-v2" bash scripts/setup_replit.sh`, exit 0.
- Task 2 RED: proxy import failed; GREEN: 2 passed (HTTP and WebSocket).
- Task 3 RED: 2 App tests failed in each UI; GREEN: 7 backend persona/App checks
  and 2 consumer App checks passed.
- Task 4 RED: 2 product-link checks failed, 25 passed. GREEN: full backend suite
  197 passed; consumer suite 28 passed; both production builds passed.
- PoC story: 1 passed (52.0s). Operator command: 2 passed (29.6s), including
  the new local-port persona round trip.
- Browser verification through the Replit proxy passed requester/operator round trip
  and reload; both `/` and `/market/` rendered. Health returned HTTP 200, database ok,
  no unavailable adapters and four_eyes false.
- A combined shell command timed out during the first operator run; the isolated
  diagnostic run and then the exact requested operator command both passed.
- Full Python GREEN on the freshly installed runtime: 3277 passed in 184.46s
  (0:03:04), `pytest -q -n 6`, exit 0.
