"""The API the Playwright story runs against (phase 7 plan, assumption 39).

    python -m e2e.serve_api

Starts the API on port 8000 on the story's world only: database amaze_test, schema e2e_story, the sources under
data/e2e/sources, the blob store under data/e2e/blob, and DAILY_JOB_ENABLED=false (the 06:00 UTC fetch must not
fire during a run; plan risk 6). The first lines it prints name the database, the schema and that flag. It refuses
to start when the database is the application's (e2e/prepare.py), and rebuilds the story before it listens.
"""

from __future__ import annotations

import os
import sys

from e2e.prepare import BLOB, REPO_ROOT, SCHEMA, SOURCES, NotTheTestDatabase, rebuild, story_url


def main() -> None:
    from sqlalchemy.engine import make_url

    url = story_url()
    # Set before anything reads the settings (they are read once, at start-up).
    os.environ["DATABASE_URL"] = url
    os.environ["SOURCE_CATALOGUE"] = str(SOURCES / "catalogue.json")
    os.environ["LOCAL_BLOB_ROOT"] = str(BLOB)
    os.environ["DAILY_JOB_ENABLED"] = "false"
    os.environ["DROPZONE_ROOT"] = str(REPO_ROOT / "data" / "e2e" / "dropzone")  # never the real data/dropzone
    os.environ["LLM_PROVIDER"] = "mock"  # the stories run on recorded responses, never the live model
    os.environ.setdefault("REQUIRE_FOUR_EYES", "false")  # each Playwright config sets it: true for the poc story and screens, false for the operator story and workspace shots
    print(f"e2e API: REQUIRE_FOUR_EYES={os.environ['REQUIRE_FOUR_EYES']}", flush=True)
    print(f"e2e API: database={make_url(url).database}", flush=True)
    print(f"e2e API: schema={SCHEMA}", flush=True)
    print(f"e2e API: DAILY_JOB_ENABLED={os.environ['DAILY_JOB_ENABLED']}", flush=True)
    print(f"e2e API: sources={SOURCES.relative_to(REPO_ROOT)} blob={BLOB.relative_to(REPO_ROOT)}", flush=True)
    rebuild(url)
    import uvicorn

    uvicorn.run("apps.api.main:app", host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except NotTheTestDatabase as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(1)
