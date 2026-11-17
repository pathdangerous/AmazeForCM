"""core/steps/candidates.py (phase 5 plan, assumptions 5, 6, 8, 9 and 14): pure, deterministic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.domain.glossary import GlossaryElementDraft
from core.domain.profiling import FileColumn, SourceSpec
from core.steps import candidates as c

SEED = Path(__file__).resolve().parents[1] / "fixtures" / "glossary" / "enterprise_glossary.json"


@pytest.fixture(scope="module")
def glossary() -> list[GlossaryElementDraft]:
    raw = json.loads(SEED.read_text(encoding="utf-8"))["elements"]
    return [GlossaryElementDraft(**{k: e[k] for k in ("key", "name", "domain", "data_type", "definition", "aliases")})
            for e in raw]


def by_key(glossary, key):
    return next(e for e in glossary if e.key == key)


# ---------------------------------------------------------------- score and type fit


def test_a_name_equal_to_a_key_name_or_alias_scores_one(glossary):
    assert c.score("close_price", by_key(glossary, "close_price")) == 1.0
    assert c.score("Close Price", by_key(glossary, "close_price")) == 1.0
    assert c.score("day_high", by_key(glossary, "high_price")) == 1.0


def test_otherwise_the_score_is_the_best_word_overlap(glossary):
    assert c.score("price", by_key(glossary, "close_price")) == 0.5
    assert c.score("price_value_date", by_key(glossary, "price_date")) == pytest.approx(2 / 3)


def test_character_similarity_counts_only_at_point_eight_or_more(glossary):
    assert c.score("mid_px", by_key(glossary, "mid_price")) == 0.5  # overlap with "mid"; similarity 0.67 ignored
    assert c.score("issue_dt", by_key(glossary, "issue_date")) == pytest.approx(16 / 18)
    assert c.score("ticker_symbl", by_key(glossary, "ticker")) >= 0.8


@pytest.mark.parametrize(("duckdb", "family"), [
    ("VARCHAR", "string"), ("BIGINT", "integer"), ("INTEGER", "integer"), ("DOUBLE", "decimal"),
    ("DECIMAL(18,3)", "decimal"), ("DATE", "date"), ("TIMESTAMP", "datetime"),
    ("TIMESTAMP WITH TIME ZONE", "datetime"), ("BOOLEAN", "boolean"), ("BLOB", None)])
def test_the_type_family_of_a_duckdb_type(duckdb, family):
    assert c.type_family(duckdb) == family


def test_type_fit_is_equal_or_integer_into_decimal():
    assert c.type_fits("DOUBLE", "decimal")
    assert c.type_fits("BIGINT", "decimal")
    assert not c.type_fits("DOUBLE", "integer")
    assert not c.type_fits("VARCHAR", "date")
    assert not c.type_fits("BLOB", "string")


def test_an_unknown_column_type_drops_no_candidate():
    assert c.type_fits(None, "date")


# ---------------------------------------------------------------- candidate lists


def test_a_list_is_never_longer_than_ten(glossary):
    found = c.candidate_list("price", "DOUBLE", glossary)  # eleven elements score 0.5
    assert len(found) == 10
    assert all(x.score == 0.5 for x in found)
    assert [x.key for x in found] == sorted(x.key for x in found)


def test_a_list_is_the_first_five_and_those_that_tie_with_the_fifth(glossary):
    found = c.candidate_list("instrument_code", "VARCHAR", glossary)  # 0.857, 0.8, 0.8, then six at 1/3
    scores = [x.score for x in found]
    assert len(found) == 9
    assert scores == sorted(scores, reverse=True)
    assert all(s == pytest.approx(1 / 3) for s in scores[3:])
    assert [x.key for x in found][3:] == ["currency", "cusip", "figi", "instrument_status", "isin", "sedol"]


def test_a_list_is_best_first_then_by_key_and_drops_below_point_three(glossary):
    found = c.candidate_list("close", "DOUBLE", glossary)
    assert found[0].key == "close_price" and found[0].score == 1.0
    assert all(x.score >= 0.3 for x in found)
    assert c.candidate_list("zzz_qqq", "VARCHAR", glossary) == []


def test_a_candidate_carries_what_the_agent_needs(glossary):
    first = c.candidate_list("close", "VARCHAR", glossary)[0]
    assert first.model_dump().keys() == {"key", "name", "data_type", "definition", "aliases", "score",
                                         "from_hint", "type_fits"}
    assert first.type_fits is False and first.from_hint is False


def test_the_hint_is_added_and_marked(glossary):
    found = c.candidate_list("lot", "VARCHAR", glossary, hint="lot_size")
    lot = next(x for x in found if x.key == "lot_size")
    assert lot.from_hint and lot.type_fits is False
    absent = c.candidate_list("zzz_qqq", "VARCHAR", glossary, hint="isin")
    assert [(x.key, x.from_hint) for x in absent] == [("isin", True)]


def test_a_hint_added_to_a_full_list_keeps_the_list_at_ten(glossary):
    found = c.candidate_list("price", "DOUBLE", glossary, hint="isin")
    assert len(found) == 10 and "isin" in [x.key for x in found]


def test_a_hint_that_names_no_element_is_ignored(glossary):
    assert c.candidate_list("zzz_qqq", "VARCHAR", glossary, hint="no_such_key") == []


# ---------------------------------------------------------------- new columns and coverage


def col(name, type_="VARCHAR"):
    return FileColumn(name=name, type=type_, null_count=0, null_rate=0.0, distinct_count=3)


SPEC = SourceSpec(source_code="syn_x", elements=["instrument_id"], other_fields=["esg score"],
                  columns={"instrument": "instrument_id", "esg_score": "esg score"})


def test_new_columns_are_undeclared_or_declared_as_other_fields():
    found = c.new_columns([col("instrument"), col("esg_score", "DOUBLE"), col("score_year", "BIGINT")], SPEC)
    assert [(n.name, n.declared_as) for n in found] == [("esg_score", "esg score"), ("score_year", None)]
    assert found[0].type == "DOUBLE"


def test_a_column_sent_back_by_step_06_is_new_at_any_time():
    found = c.new_columns([col("instrument"), col("score_year")], SPEC, sent_back=["instrument"])
    assert [(n.name, n.sent_back) for n in found] == [("instrument", True), ("score_year", False)]


def test_covered_by_name_needs_exactly_one_element(glossary):
    maturity = c.NewColumn(name="maturity", type="VARCHAR")
    assert c.covered_by(maturity, glossary) == "name"
    shared = [*glossary, GlossaryElementDraft(key="tenor", name="Tenor", domain="pricing", data_type="string",
                                              definition="d", aliases=["maturity"])]
    assert c.covered_by(maturity, shared) is None


def test_a_declared_other_field_name_counts_for_coverage(glossary):
    column = c.NewColumn(name="px_last", type="DOUBLE", declared_as="close price")
    assert c.covered_by(column, glossary) == "name"


def test_covered_by_a_person_or_already_asked(glossary):
    column = c.NewColumn(name="score_year", type="BIGINT")
    assert c.covered_by(column, glossary) is None
    assert c.covered_by(column, glossary, mapped={"score_year"}) == "mapping"
    assert c.covered_by(column, glossary, asked={"score_year"}) == "asked"


def test_a_column_sent_back_skips_the_name_rule_only(glossary):
    column = c.NewColumn(name="maturity", type="VARCHAR", sent_back=True)
    assert c.covered_by(column, glossary) is None
    assert c.covered_by(column, glossary, asked={"maturity"}) == "asked"


# ---------------------------------------------------------------- classify (assumption 14)


def new_el(key, name, data_type="string"):
    return GlossaryElementDraft(key=key, name=name, domain="pricing", data_type=data_type,
                                definition=f"{name}: synthetic.")


def test_no_hit_is_proposed_with_the_new_element(glossary):
    column = c.NewColumn(name="score_year", type="BIGINT")
    cands = c.candidate_list(column.name, column.type, glossary)
    got = c.classify_element(column, cands, same_as=None, new_element=new_el("score_year", "Score year", "integer"))
    assert got.outcome == "proposed" and got.recommended == 0
    assert [(o.kind, o.key) for o in got.options] == [("new_element", "score_year")]


def test_one_hit_that_fits_is_a_duplicate_alias(glossary):
    column = c.NewColumn(name="venue_mic_code", type="VARCHAR")
    cands = c.candidate_list(column.name, column.type, glossary)
    got = c.classify_element(column, cands, same_as="exchange_mic", new_element=None)
    assert got.outcome == "duplicate_alias" and got.recommended == 0
    assert [(o.kind, o.key, o.alias) for o in got.options] == [("alias", "exchange_mic", "venue_mic_code")]


def test_a_new_element_named_like_a_candidate_is_a_duplicate_alias(glossary):
    column = c.NewColumn(name="px_shut", type="DOUBLE")
    cands = c.candidate_list("close price", column.type, glossary)
    got = c.classify_element(column, cands, same_as=None, new_element=new_el("closing_price", "Closing", "decimal"))
    assert got.outcome == "duplicate_alias" and got.options[0].key == "close_price"
    assert got.options[0].alias == "px_shut"


def test_one_hit_whose_type_differs_is_a_conflict_with_no_option_when_the_names_are_taken(glossary):
    column = c.NewColumn(name="maturity", type="VARCHAR", sent_back=True)
    cands = c.candidate_list(column.name, column.type, glossary)
    got = c.classify_element(column, cands, same_as=None, new_element=new_el("maturity", "Maturity"))
    assert got.outcome == "conflict_to_owner" and got.recommended is None
    assert got.options == []


def test_one_hit_that_fits_and_already_has_the_name_is_a_conflict_with_no_option(glossary):
    """Review finding 1: an alias the element already has is never offered, in the single-hit branch too."""
    column = c.NewColumn(name="Venue MIC", type="VARCHAR", sent_back=True)
    cands = c.candidate_list(column.name, column.type, glossary)
    got = c.classify_element(column, cands, same_as="exchange_mic", new_element=None)
    assert (got.outcome, got.options, got.recommended, got.clashes) == ("conflict_to_owner", [], None,
                                                                        ["exchange_mic"])


@pytest.mark.parametrize(("name", "alias"), [("venue_mic_code", "venue_mic_code"), ("Venue MIC", "venue_mic"),
                                             ("1d_volume", "col_1d_volume"), ("52 Week High", "col_52_week_high"),
                                             ("%", "")])
def test_an_alias_starts_with_a_letter(name, alias):
    """Review finding 3: col_ before a digit, as the mock's new element key; no word at all gives no alias."""
    assert c.alias_of(name) == alias


def test_a_column_whose_option_is_not_valid_is_skipped_and_the_others_get_their_payload(glossary):
    """Review finding 3: one unusable column does not fail the answer of the whole feed."""
    from core.domain.mapping import GlossaryBuilderInput, GlossaryBuilderOutput

    def column(name, type_, also=()):
        return c.NewColumn(name=name, type=type_, candidates=c.candidate_list(name, type_, glossary, also=also))

    agent_input = GlossaryBuilderInput(
        run_id="00000000-0000-0000-0000-000000000001", feed_id="00000000-0000-0000-0000-000000000002",
        source_code="syn_odd", source_name="Odd", source_domain="pricing", domains=["pricing"],
        columns=[column("%", "BIGINT", also=("volume traded today",)), column("1d_volume", "BIGINT")])
    output = GlossaryBuilderOutput(answers=[{"column": "%", "same_as": "volume", "reason": "r"},
                                            {"column": "1d_volume", "same_as": "volume", "reason": "r"}])
    payloads, skipped = c.glossary_payloads(agent_input, output)
    assert [(p.source_field, p.outcome, p.options[0].alias) for p in payloads] == [
        ("1d_volume", "duplicate_alias", "col_1d_volume")]
    assert list(skipped) == ["%"] and skipped["%"].startswith("alias: ")


@pytest.fixture
def two_ccy(glossary):
    return [*glossary, new_el("trade_currency", "Trade currency").model_copy(update={"aliases": ["ccy"]})]


def test_two_hits_are_a_conflict_offering_an_alias_on_each_that_fits(two_ccy):
    column = c.NewColumn(name="ccy_cd", type="VARCHAR")
    cands = c.candidate_list(column.name, column.type, two_ccy)
    got = c.classify_element(column, cands, same_as=None, new_element=new_el("ccy", "Ccy"))
    assert got.outcome == "conflict_to_owner" and got.recommended is None
    assert sorted((o.kind, o.key, o.alias) for o in got.options) == [
        ("alias", "currency", "ccy_cd"), ("alias", "trade_currency", "ccy_cd")]  # the new element's key is taken


def test_a_conflict_offers_a_free_new_element_and_no_alias_an_element_already_has(two_ccy):
    column = c.NewColumn(name="ccy", type="VARCHAR", sent_back=True)
    cands = c.candidate_list(column.name, column.type, two_ccy)
    got = c.classify_element(column, cands, same_as=None, new_element=new_el("ccy_code", "Ccy code"))
    assert got.outcome == "conflict_to_owner"
    assert [(o.kind, o.key) for o in got.options] == [("new_element", "ccy_code")]


def test_a_same_as_that_is_not_a_candidate_is_dropped(glossary):
    column = c.NewColumn(name="score_year", type="BIGINT")
    cands = c.candidate_list(column.name, column.type, glossary)
    assert c.classify_element(column, cands, same_as="isin", new_element=None) is None


def test_nothing_answered_is_no_proposal(glossary):
    column = c.NewColumn(name="date", type="DATE")
    cands = c.candidate_list(column.name, column.type, glossary)
    assert c.classify_element(column, cands, same_as=None, new_element=None) is None
