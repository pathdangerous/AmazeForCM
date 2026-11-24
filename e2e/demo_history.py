"""`python -m core.seed --demo-history` on the Playwright story's world (phase 7.16).

    python -m e2e.demo_history

The same environment e2e/serve_api.py gives the API (database amaze_test, schema e2e_story, the story's blob store and drop
folder, the mock provider), then the real command. The demo story calls it after the audit, with the API still running:
the history it makes is committed, so the Reports screen sees it. Refused, like the API, when the database is the application's.
"""

from __future__ import annotations

import os
import sys

from e2e.prepare import BLOB, REPO_ROOT, SOURCES, NotTheTestDatabase, story_url


def main() -> None:
    os.environ["DATABASE_URL"] = story_url()  # set before anything reads the settings
    os.environ["SOURCE_CATALOGUE"] = str(SOURCES / "catalogue.json")
    os.environ["LOCAL_BLOB_ROOT"] = str(BLOB)
    os.environ["DAILY_JOB_ENABLED"] = "false"
    os.environ["DROPZONE_ROOT"] = str(REPO_ROOT / "data" / "e2e" / "dropzone")
    os.environ["LLM_PROVIDER"] = "mock"
    from core import seed

    seed.main(["--demo-history"])


if __name__ == "__main__":
    try:
        main()
    except NotTheTestDatabase as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(1)
