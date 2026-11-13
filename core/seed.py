"""Load the synthetic enterprise glossary seed into the database named by DATABASE_URL.

Run: .venv\\Scripts\\python -m core.seed

Inserts version 1 (created_by "seed") of every element whose key is not in the glossary yet, and
never changes an existing element. Safe to run again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.repositories.glossary import GlossaryRepository
from ports.config import REPO_ROOT

SEED_FILE = REPO_ROOT / "fixtures" / "glossary" / "enterprise_glossary.json"


def load_seed_elements(path: Path = SEED_FILE) -> list[dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["elements"]


def seed_glossary(session: Session, path: Path = SEED_FILE) -> tuple[int, int]:
    """Returns (inserted, already present). Flushes; the caller commits."""
    repo = GlossaryRepository(session)
    inserted = sum(repo.add_seed_element(**element) for element in load_seed_elements(path))
    return inserted, len(load_seed_elements(path)) - inserted


def main() -> None:
    from ports.db import get_sessionmaker

    with get_sessionmaker()() as session:
        inserted, present = seed_glossary(session)
        session.commit()
    print(f"glossary seed: {inserted} inserted, {present} already present")


if __name__ == "__main__":
    main()
