"""Term matching (core/steps/matching.py): request text to glossary elements and domains.

Decision 2: case is ignored, the longest phrase is tried first, stop-words are skipped. Assumptions
7 to 13 of the phase 2 plan give the exact rules. Most tests use the synthetic 33-element seed.
"""

import pytest

from core.domain.glossary import GlossaryElementDraft
from core.seed import load_seed_elements
from core.steps.matching import STOP_WORDS, Ambiguous, MatchedElement, build_phrases, match, words

SEED = build_phrases(GlossaryElementDraft(**e) for e in load_seed_elements())


def element(key, *, name=None, domain="pricing", aliases=()):
    return GlossaryElementDraft(key=key, name=name or key.replace("_", " ").capitalize(), domain=domain,
                                data_type="string", definition="Synthetic.", aliases=list(aliases))


def keys(result):
    return [(e.key, e.ticked) for e in result.elements]


def test_case_is_ignored():
    assert keys(match("CLOSE Price", SEED)) == [("close_price", True)]


def test_longest_phrase_first():
    """'maturity' alone is an alias of maturity_date; 'yield to maturity' is the longer phrase."""
    assert keys(match("yield to maturity", SEED)) == [("yield_to_maturity", True)]
    assert keys(match("maturity", SEED)) == [("maturity_date", False)]


def test_stop_words_are_skipped():
    result = match("Please show me all the close price data", SEED)
    assert keys(result) == [("close_price", True)]
    assert result.terms == []


def test_stop_word_list_is_pinned():
    assert STOP_WORDS == (
        "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "at", "by", "from", "with", "per", "as",
        "i", "we", "me", "my", "our", "us", "need", "want", "would", "like", "please", "show", "give", "get",
        "find", "have", "is", "are", "all", "any", "some", "data",
    )
    assert len(STOP_WORDS) == 38


def test_a_phrase_starting_with_a_stop_word_still_matches():
    """The alias as_of_date: a stop-word is skipped only where no phrase starts at it."""
    assert keys(match("as of date", SEED)) == [("price_date", True)]


def test_a_stop_word_closes_an_unmatched_term():
    assert match("esg score and carbon footprint", SEED).terms == ["esg score", "carbon footprint"]


def test_a_match_closes_an_unmatched_term():
    result = match("esg score volume carbon footprint", SEED)
    assert keys(result) == [("volume", True)]
    assert result.terms == ["esg score", "carbon footprint"]


def test_key_and_name_matches_arrive_ticked():
    assert keys(match("close_price", SEED)) == [("close_price", True)]  # key
    assert keys(match("bid price", SEED)) == [("bid_price", True)]  # name
    assert keys(match("closing price", SEED)) == [("close_price", True)]  # alias of two words
    assert keys(match("volume", SEED)) == [("volume", True)]  # one-word key


def test_a_one_word_alias_match_arrives_unticked():
    assert keys(match("ask", SEED)) == [("ask_price", False)]
    assert keys(match("open and high", SEED)) == [("open_price", False), ("high_price", False)]
    # matched once by a one-word alias and once by its name: ticked
    assert keys(match("ask, ask price", SEED)) == [("ask_price", True)]


def test_a_domain_word_is_never_a_term_or_an_element():
    result = match("pricing", SEED)
    assert (result.elements, result.terms, result.domains) == ([], [], ["pricing"])
    result = match("security master isin", SEED)
    assert (keys(result), result.terms, result.domains) == ([("isin", True)], [], ["security_master"])


def test_a_phrase_of_two_elements_lists_both():
    phrases = build_phrases([element("bid_price", aliases=["px"]), element("ask_price", aliases=["px"])])
    result = match("px", phrases)
    assert result.elements == []
    assert result.ambiguous == [Ambiguous(phrase="px", candidates=["ask_price", "bid_price"])]
    assert phrases.clashes() == {"px": ["ask_price", "bid_price"]}


def test_an_element_phrase_beats_a_domain_phrase():
    phrases = build_phrases([element("pricing_flag", aliases=["pricing"])])
    result = match("pricing", phrases)
    assert (keys(result), result.domains) == ([("pricing_flag", False)], [])
    assert phrases.domain_clashes() == ["pricing"]


def test_punctuation_cuts_a_phrase_and_hyphens_join_it():
    result = match("close.price", SEED)
    assert keys(result) == [("close_price", False)]  # "close" alone is a one-word alias
    assert result.terms == ["price"]
    assert keys(match("close-price", SEED)) == [("close_price", True)]


def test_each_list_is_in_order_of_first_appearance_without_duplicates():
    result = match("volume, close price, esg score, volume, esg score", SEED)
    assert keys(result) == [("volume", True), ("close_price", True)]
    assert result.terms == ["esg score"]


def test_no_stemming_or_plurals():
    result = match("prices", SEED)
    assert (result.elements, result.terms) == ([], ["prices"])


@pytest.mark.parametrize("text", ["", "   ", "the and of", "!!!"])
def test_blank_or_stop_words_only_match_nothing(text):
    result = match(text, SEED)
    assert (result.elements, result.ambiguous, result.domains, result.terms) == ([], [], [], [])


def test_words_normalises_a_term():
    assert words("  ESG   Score ") == ("esg", "score")
    assert words("carbon-footprint_total") == ("carbon", "footprint", "total")


def test_is_phrase():
    assert SEED.is_phrase("closing price") and SEED.is_phrase("Pricing")
    assert not SEED.is_phrase("esg score")


def test_matched_element_is_a_plain_model():
    assert MatchedElement(key="volume", ticked=True).model_dump() == {"key": "volume", "ticked": True}
