"""Load synthetic seed data into the database named by DATABASE_URL.

Run:
    .venv\\Scripts\\python -m core.seed           the enterprise glossary (fixtures/glossary/)
    .venv\\Scripts\\python -m core.seed --demo    the demo products (fixtures/demo/); needs the glossary
    .venv\\Scripts\\python -m core.seed --demo-history   21 past days of the onboarded security master (docs/demo.md)

Glossary: inserts version 1 (created_by "seed") of every element whose key is not in the glossary yet.
Demo: inserts every demo product whose code does not exist yet. Demo codes start with demo_.
Both never change an existing row and are safe to run again.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.repositories.errors import ProductNotFound
from core.repositories.glossary import GlossaryRepository
from core.repositories.products import ProductRepository
from ports.config import REPO_ROOT

SEED_FILE = REPO_ROOT / "fixtures" / "glossary" / "enterprise_glossary.json"
DEMO_FILE = REPO_ROOT / "fixtures" / "demo" / "products.json"
DEMO_PREFIX = "demo_"


class DemoNeedsGlossary(RuntimeError):
    """A demo product names a glossary element that is not in the database yet."""


def load_seed_elements(path: Path = SEED_FILE) -> list[dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["elements"]


def seed_glossary(session: Session, path: Path = SEED_FILE) -> tuple[int, int]:
    """Returns (inserted, already present). Flushes; the caller commits."""
    repo = GlossaryRepository(session)
    inserted = sum(repo.add_seed_element(**element) for element in load_seed_elements(path))
    return inserted, len(load_seed_elements(path)) - inserted


def load_demo_products(path: Path = DEMO_FILE) -> list[dict[str, Any]]:
    """The demo products. Refuses a code without the demo_ prefix (decision 6)."""
    products = json.loads(Path(path).read_text(encoding="utf-8"))["products"]
    wrong = [p["code"] for p in products if not p["code"].startswith(DEMO_PREFIX)]
    if wrong:
        raise ValueError(f"Demo product codes must start with {DEMO_PREFIX!r}: {', '.join(wrong)}")
    return products


def seed_demo(session: Session, path: Path = DEMO_FILE) -> tuple[int, int]:
    """Returns (inserted, already present). Flushes; the caller commits."""
    products = load_demo_products(path)
    glossary = GlossaryRepository(session)
    missing = sorted({k for p in products for k in p["element_keys"] if not glossary.exists(k)})
    if missing:
        raise DemoNeedsGlossary(f"Glossary elements missing ({', '.join(missing)}). Run python -m core.seed first.")
    repo = ProductRepository(session)
    inserted = 0
    for product in products:
        try:
            repo.get_by_code(product["code"])
        except ProductNotFound:
            repo.create(**product)
            inserted += 1
    return inserted, len(products) - inserted


def main(argv: Sequence[str] | None = None) -> None:
    from ports import db

    parser = argparse.ArgumentParser(prog="python -m core.seed", description="Load synthetic seed data.")
    parser.add_argument("--demo", action="store_true", help="load the demo products instead of the glossary")
    parser.add_argument("--demo-history", action="store_true",
                        help="21 past days of the onboarded security master through the real daily pipeline")
    args = parser.parse_args(argv)
    if args.demo_history:
        from apps.api import demo_history  # the real pipeline lives with the jobs; core/ never imports it at load time

        done = demo_history.run()
        if done.outcome == "history_refused":
            print(done.detail, file=sys.stderr)
            raise SystemExit(1)
        print(f"demo history: {done.loaded} days loaded, {done.skipped} already there, {done.held} held"
              + (f"; {done.late} came late, {done.missing} day with no delivery" if done.late or done.missing else ""))
        if not done.rule_found:
            print("The feed has no rule best_sales_not_negative: nothing is flagged, so there is no pattern.", file=sys.stderr)
        for problem in done.problems:
            print(f"not loaded: {problem}", file=sys.stderr)
        return
    with db.get_sessionmaker()() as session:
        if args.demo:
            try:
                inserted, present = seed_demo(session)
            except DemoNeedsGlossary as exc:
                print(exc, file=sys.stderr)
                raise SystemExit(1) from None
            label = "demo seed"
        else:
            inserted, present = seed_glossary(session)
            label = "glossary seed"
        session.commit()
    print(f"{label}: {inserted} inserted, {present} already present")


if __name__ == "__main__":
    main()
