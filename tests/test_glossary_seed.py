"""The synthetic enterprise glossary seed (fixtures/glossary/enterprise_glossary.json) and core.seed.

Rule 6 (synthetic data only): identifier type names such as isin, cusip, sedol and figi are allowed;
real identifier values, vendor names and vendor field codes are not.
"""

import json
import re

import pytest

from core.domain.glossary import GlossaryElementDraft
from core.repositories.glossary import GlossaryRepository
from core.seed import SEED_FILE, load_seed_elements, seed_glossary

SECURITY_MASTER = {
    "instrument_id", "isin", "cusip", "sedol", "figi", "instrument_name", "asset_class", "instrument_type",
    "issuer_name", "issuer_lei", "currency", "country_of_risk", "exchange_mic", "ticker", "issue_date",
    "maturity_date", "coupon_rate", "lot_size", "instrument_status",
}
PRICING = {
    "price_date", "close_price", "open_price", "high_price", "low_price", "bid_price", "ask_price",
    "mid_price", "volume", "price_currency", "price_source", "price_type", "accrued_interest",
    "yield_to_maturity",
}

# ---- 1. the file is valid


def test_33_elements_in_two_domains():
    elements = load_seed_elements()
    assert len(elements) == 33
    by_domain = {}
    for e in elements:
        by_domain.setdefault(e["domain"], set()).add(e["key"])
    assert by_domain == {"security_master": SECURITY_MASTER, "pricing": PRICING}
    assert (len(SECURITY_MASTER), len(PRICING)) == (19, 14)


def test_every_element_is_a_valid_draft_with_aliases():
    for element in load_seed_elements():
        draft = GlossaryElementDraft(**element)
        assert 2 <= len(draft.aliases) <= 3, draft.key


# ---- 2. no key or alias twice across the whole glossary


def test_no_key_or_alias_appears_twice_ignoring_case():
    names = [n.lower() for e in load_seed_elements() for n in [e["key"], *e["aliases"]]]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert duplicates == []


# ---- 3. no real data

IDENTIFIER_VALUE_SHAPES = {
    "ISIN": r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b",
    "CUSIP": r"\b[0-9]{3}[A-Z0-9]{5}[0-9]\b",
    "SEDOL": r"\b[0-9B-DF-HJ-NP-TV-Z]{6}[0-9]\b",
    "FIGI": r"\bBBG[A-Z0-9]{9}\b",
    "LEI": r"\b[A-Z0-9]{18}[0-9]{2}\b",
    "any long code with letters and digits": r"\b(?=[A-Z0-9]*[0-9])(?=[A-Z0-9]*[A-Z])[A-Z0-9]{7,}\b",
}
VENDOR_FIELD_CODE_SHAPES = {
    "UPPER_SNAKE field mnemonic": r"\b[A-Z]{2,}_[A-Z0-9_]+\b",
    "dotted field code": r"\b[A-Z]{2,}\.[A-Z][A-Za-z]+\b",
}
VENDOR_NAMES = r"\b(Bloomberg|Refinitiv|Reuters|FactSet|Markit|Morningstar|Telekurs|Moody'?s|Fitch|LSEG|ICE|S&P|RIC)\b"


def find_real_data(text: str) -> list[str]:
    found = [label for label, shape in {**IDENTIFIER_VALUE_SHAPES, **VENDOR_FIELD_CODE_SHAPES}.items()
             if re.search(shape, text)]
    if re.search(VENDOR_NAMES, text):
        found.append("vendor name")
    return found


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def test_seed_file_has_no_real_identifier_values_vendor_names_or_field_codes():
    raw = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    offending = {s: find_real_data(s) for s in _strings(raw) if find_real_data(s)}
    assert offending == {}


def test_identifier_type_names_are_allowed():
    keys = {e["key"] for e in load_seed_elements()}
    assert {"isin", "cusip", "sedol", "figi", "issuer_lei", "exchange_mic"} <= keys
    for name in ("isin", "cusip", "sedol", "figi", "ISIN", "CUSIP", "SEDOL", "FIGI", "LEI", "MIC"):
        assert find_real_data(name) == [], name


@pytest.mark.parametrize(
    "shaped",  # made-up strings with the shape of real data; none is a real identifier or field
    ["XX0000000000", "000AAAAA0", "BBG000000000", "0000AAAAAAAAAAAAAA00", "AB_CDEF", "XY.Close", "Fitch"],
)
def test_the_detector_flags_identifier_and_field_code_shapes(shaped):
    assert find_real_data(f"example {shaped} here")


# ---- 4. seeding


def test_seeding_twice_inserts_33_then_0(db_session):
    assert seed_glossary(db_session) == (33, 0)
    assert seed_glossary(db_session) == (0, 33)
    repo = GlossaryRepository(db_session)
    assert repo.domains() == [("pricing", 14), ("security_master", 19)]
    assert all(e.version == 1 and e.created_by == "seed" for e in repo.list_current())
