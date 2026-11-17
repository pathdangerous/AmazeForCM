"""The rule templates offered to the RuleRecommenderAgent (phase 6 plan, assumption 7; phase 5 note 3)."""

from __future__ import annotations

import duckdb
import pytest

from core.engine.sandbox import check_rule_sql
from core.steps.rule_candidates import element_offers, feed_offers


def keys(offers):
    return [o.rule_key for o in offers]


@pytest.mark.parametrize(("key", "data_type", "expected"), [
    ("instrument_id", "string", ["instrument_id_present"]),
    ("price_date", "date", ["price_date_present"]),
    ("volume", "integer", ["volume_not_negative", "volume_in_whole_lots"]),
    ("lot_size", "integer", ["lot_size_not_negative"]),  # whole lots is for the traded volume only
    ("close_price", "decimal", ["close_price_not_negative"]),
    ("created_at", "datetime", ["created_at_present", "created_at_not_in_future"]),
    ("is_active", "boolean", []),
])
def test_an_element_is_offered_the_templates_that_fit_its_type(key, data_type, expected):
    assert keys(element_offers(key, data_type)) == expected


def test_at_most_five_templates_per_element():
    from core.domain.rules import MAX_TEMPLATES

    assert all(len(element_offers(k, t)) <= MAX_TEMPLATES for k in ("volume", "x") for t in
               ("string", "integer", "decimal", "date", "datetime", "boolean"))


def test_one_row_per_key_uses_the_first_identifier_and_the_price_date():
    (offer,) = feed_offers(["volume", "price_date", "isin", "instrument_id"])
    assert (offer.rule_key, offer.element_keys) == ("one_row_per_instrument_id_and_price_date",
                                                    ["instrument_id", "price_date"])
    assert keys(feed_offers(["isin", "close_price"])) == ["one_row_per_isin"]
    assert feed_offers(["volume", "price_date"]) == []


def test_every_offered_sql_is_a_plain_select_that_returns_the_rows_that_break_it():
    elements = {"instrument_id": "string", "price_date": "date", "volume": "integer", "close_price": "decimal",
                "created_at": "datetime"}
    offers = [o for k, t in elements.items() for o in element_offers(k, t)] + feed_offers(list(elements))
    connection = duckdb.connect(":memory:")
    connection.execute('CREATE TABLE curated AS SELECT * FROM (VALUES (1, \'a\', DATE \'2026-01-01\', 100, 1.5, '
                       "TIMESTAMP '2026-01-01 00:00:00'), (2, NULL, NULL, -5, -1.0, TIMESTAMP '2999-01-01 00:00:00'), "
                       "(3, 'a', DATE '2026-01-01', 150, 2.0, NULL)) t(_row, instrument_id, price_date, volume, "
                       "close_price, created_at)")
    flagged = {}
    for offer in offers:
        check_rule_sql(offer.sql)
        flagged[offer.rule_key] = sorted(r[0] for r in connection.execute(offer.sql).fetchall())
    assert flagged["instrument_id_present"] == [2] and flagged["price_date_present"] == [2]
    assert flagged["volume_not_negative"] == [2] and flagged["close_price_not_negative"] == [2]
    assert flagged["volume_in_whole_lots"] == [2, 3]
    assert flagged["created_at_present"] == [3] and flagged["created_at_not_in_future"] == [2]
    assert flagged["one_row_per_instrument_id_and_price_date"] == [1, 3]  # rows 1 and 3 share a key
