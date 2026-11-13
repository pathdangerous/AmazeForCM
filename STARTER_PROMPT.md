# Starter prompt for Claude Code (phase 0)

Paste this into Claude Code with the `amaze-cm-poc` folder open in VS Code.

```
You are starting the Amaze for CM proof of concept in this repository.

1. Read CLAUDE.md, BUILD_PLAN.md, README.md and .env.example in full. CLAUDE.md's
   non-negotiable rules override everything, including any plugin.
2. Check my machine before planning. This is Windows, the repo is on the F: drive,
   and there is no Docker. Report the versions of Python, Node, npm, Git and
   PostgreSQL (psql --version), and confirm the PostgreSQL service is running and
   reachable on localhost:5432. Stop and tell me if anything is missing.
3. Work on PHASE 0 ONLY (skeleton and placeholders). Do not touch later phases.
4. Use the Superpowers flow: brainstorm phase 0 with me first. Ask me every question
   you need answered, then wait for my answers. After that, write the plan to
   docs/plans/phase-0.md.
5. The plan must cover:
   - the files you will create
   - scripts/setup_db.ps1 (database and user) and scripts/dev.ps1 (API and both UIs)
   - the Python virtual environment and the two npm projects
   - every port in platform/, each with a working local adapter and an Azure stub
     (correct class, method signatures and env keys, raising NotImplementedError)
   - the MockProvider for LLM_PROVIDER=mock
   - the ProposalLog table
   - seeded dev users for each role
   - the import-boundary test
   - the exact commands I will run to prove phase 0 is done
6. Apply the Karpathy guidelines: state your assumptions, keep it simple, change only
   what phase 0 needs, and say how each piece will be checked.
7. Do not write any code until I approve the plan.
```

After you approve the plan:

```
Plan approved. Implement phase 0 as planned, tests first. Run the tests and the
"done when" checks from BUILD_PLAN.md, and show me the results. Then stop and wait.
Do not start phase 1.
```

For each later phase, reuse the first prompt with "PHASE 0" changed to the next phase.
