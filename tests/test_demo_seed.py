"""The demo products (fixtures/demo/products.json) and python -m core.seed --demo (decision 6).

Separate from the glossary seed. Codes start with demo_. Synthetic data only (rule 6).
"""

import json
from contextlib import nullcontext

import pytest

from core.repositories.products import ProductRepository
from core.seed import DEMO_FILE, DemoNeedsGlossary, load_demo_products, main, seed_demo, seed_glossary
from ports.identity.dev import DevIdentity
from tests.test_glossary_seed import _strings, find_real_data

PRODUCT = {"code": "demo_x", "name": "X", "domain": "pricing", "visibility": "open", "owner_id": "dev-data-owner",
           "description": "Synthetic.", "element_keys": ["close_price"]}


def test_every_demo_code_starts_with_demo():
    codes = [p["code"] for p in load_demo_products()]
    assert codes == ["demo_daily_close_prices", "demo_bid_ask_quotes", "demo_bond_reference"]
    assert all(code.startswith("demo_") for code in codes)


def test_loader_refuses_a_code_without_the_prefix(tmp_path):
    path = tmp_path / "products.json"
    path.write_text(json.dumps({"products": [PRODUCT, {**PRODUCT, "code": "real_prices"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="real_prices"):
        load_demo_products(path)


def test_glossary_seed_creates_no_product(db_session):
    seed_glossary(db_session)
    assert ProductRepository(db_session).list() == []


def test_keys_domains_and_owners_exist():
    from core.seed import load_seed_elements

    elements = {e["key"]: e["domain"] for e in load_seed_elements()}
    users = {u.id for u in DevIdentity().list_users()}
    for product in load_demo_products():
        assert set(product["element_keys"]) <= set(elements), product["code"]
        assert product["domain"] in set(elements.values()), product["code"]
        assert product["owner_id"] in users, product["code"]


def test_demo_file_has_no_real_data():
    raw = json.loads(DEMO_FILE.read_text(encoding="utf-8"))
    assert {s: find_real_data(s) for s in _strings(raw) if find_real_data(s)} == {}


def test_seeding_twice_inserts_3_then_0(db_session):
    seed_glossary(db_session)
    assert seed_demo(db_session) == (3, 0)
    assert seed_demo(db_session) == (0, 3)
    products = {p.code: p for p in ProductRepository(db_session).list_with_elements()}
    assert products["demo_bond_reference"].visibility == "restricted"
    assert products["demo_bid_ask_quotes"].element_keys == ["ask_price", "bid_price", "instrument_id", "price_date"]


def test_a_missing_glossary_gives_the_named_message(db_session):
    with pytest.raises(DemoNeedsGlossary, match=r"Run python -m core.seed first"):
        seed_demo(db_session)
    assert ProductRepository(db_session).list() == []


def test_main_demo_prints_and_exits_1_without_glossary(db_session, monkeypatch, capsys):
    monkeypatch.setattr("ports.db.get_sessionmaker", lambda: (lambda: nullcontext(db_session)))
    with pytest.raises(SystemExit) as stop:
        main(["--demo"])
    assert stop.value.code == 1
    assert "Run python -m core.seed first" in capsys.readouterr().err
    seed_glossary(db_session)
    main(["--demo"])
    assert capsys.readouterr().out.strip() == "demo seed: 3 inserted, 0 already present"


def test_main_without_flag_loads_the_glossary_only(db_session, monkeypatch, capsys):
    monkeypatch.setattr("ports.db.get_sessionmaker", lambda: (lambda: nullcontext(db_session)))
    main([])
    assert capsys.readouterr().out.strip() == "glossary seed: 33 inserted, 0 already present"
    assert ProductRepository(db_session).list() == []
