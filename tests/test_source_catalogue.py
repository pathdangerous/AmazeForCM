"""The synthetic source catalogue (fixtures/sources/catalogue.json) and its loader
(ports/connectors/catalogue.py). Phase 3 plan, assumptions 6, 7, 15, 16 and 46; section 8.

Synthetic data only (rule 6): codes start with syn_, providers are invented, and the catalogue and
every sample file pass the glossary seed's real-data checks.
"""

import json

import pytest
from pydantic import ValidationError

from core.seed import load_seed_elements
from ports.config import REPO_ROOT, Settings
from ports.connectors.catalogue import DEFAULT_CATALOGUE, SourceCatalogue, UnknownSource
from tests.test_glossary_seed import _strings, find_real_data

SOURCES = REPO_ROOT / "fixtures" / "sources"
ENTRY = {
    "code": "syn_x", "name": "X", "provider": "Synthetic provider", "description": "Synthetic.",
    "visibility": "public", "channel": "exchanges", "known": False,
    "conditions": {"licence": "Synthetic licence", "permitted_use": "Internal analysis",
                   "redistribution": "Not allowed", "external_approvals": []},
    "elements": ["volume"], "other_fields": [], "connector": "local_file", "source_ref": "files/x.csv",
    "columns": {"qty": "volume"},
}


def write(tmp_path, *entries):
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps({"sources": list(entries)}), encoding="utf-8")
    return path


@pytest.fixture
def catalogue():
    return SourceCatalogue.load(DEFAULT_CATALOGUE)


def test_the_nine_synthetic_sources(catalogue):
    assert [s.code for s in catalogue.entries] == [
        "syn_exchange_volumes", "syn_offline_accruals", "syn_vendor_eod_prices", "syn_open_esg_scores",
        "syn_internal_desk_marks", "syn_backup_eod_prices",
        "syn_instrument_lifecycle", "syn_listing_details",  # phase 4 plan, section 8
        "syn_bond_terms",  # phase 5 plan, section 8.2
    ]
    bonds = catalogue.get("syn_bond_terms")
    assert (bonds.name, bonds.visibility, bonds.channel, bonds.known, bonds.conditions.external_approvals) == (
        "Bond terms", "public", "market_data_vendors", False, [])
    assert (bonds.elements, bonds.other_fields, bonds.columns) == (
        ["instrument_id", "maturity_date", "coupon_rate"], [],
        {"instr_id": "instrument_id", "matures_on": "maturity_date", "cpn": "coupon_rate"})
    for code in ("syn_instrument_lifecycle", "syn_listing_details"):
        source = catalogue.get(code)
        assert (source.visibility, source.known, source.conditions.external_approvals) == ("public", False, [])
    assert catalogue.get("syn_instrument_lifecycle").elements == ["instrument_id", "issue_date", "instrument_status"]
    assert catalogue.get("syn_listing_details").elements == ["instrument_id", "exchange_mic", "lot_size"]
    backup = catalogue.get("syn_backup_eod_prices")  # assumption 58
    assert (backup.visibility, backup.channel, backup.known, backup.conditions.external_approvals) == (
        "public", "market_data_vendors", False, [])
    assert backup.elements == ["instrument_id", "price_date", "open_price", "close_price"]
    assert [s.code for s in catalogue.known()] == ["syn_exchange_volumes", "syn_offline_accruals"]
    assert catalogue.get("syn_vendor_eod_prices").conditions.external_approvals == [
        "A signed data agreement with the vendor, held by your desk",
    ]
    assert all(s.connector == "local_file" for s in catalogue.entries)  # assumption 16


def test_every_element_is_in_the_glossary_seed(catalogue):
    keys = {e["key"] for e in load_seed_elements()}
    for source in catalogue.entries:
        assert set(source.elements) <= keys, source.code


def test_every_file_exists_except_the_one_missing_on_purpose(catalogue):
    missing = [s.code for s in catalogue.entries if not (SOURCES / s.source_ref).is_file()]
    assert missing == ["syn_offline_accruals", "syn_backup_eod_prices"]  # rows B and J of the plan: source_down


def test_each_file_header_is_its_spec_columns(catalogue):
    """Phase 4 plan, assumption 8: columns names the file column of every declared field. The ESG file
    keeps one column its spec does not declare (score_year), on purpose."""
    for source in catalogue.entries:
        path = SOURCES / source.source_ref
        if not path.is_file():
            continue
        header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
        extra = {"syn_open_esg_scores": ["score_year"],  # phase 5: the undeclared columns step 05 looks at
                 "syn_bond_terms": ["date", "venue_mic_code", "rating", "maturity"]}.get(source.code, [])
        assert sorted(header) == sorted([*source.columns, *extra]), source.code


def test_the_bond_terms_file_has_ten_rows_and_profiles_with_the_types_step_05_needs(catalogue):
    from core.steps.profiling import profile_csv

    profile = profile_csv(SOURCES / catalogue.get("syn_bond_terms").source_ref)
    assert profile.row_count == 10
    assert {c.name: c.type for c in profile.columns} == {
        "instr_id": "VARCHAR", "matures_on": "DATE", "cpn": "DOUBLE", "date": "DATE", "venue_mic_code": "VARCHAR",
        "rating": "VARCHAR", "maturity": "VARCHAR"}


def test_the_new_files_show_a_null_rate_and_no_rows():
    lifecycle = (SOURCES / "files" / "instrument_lifecycle.csv").read_text(encoding="utf-8").splitlines()
    assert len(lifecycle) == 11 and sum(1 for line in lifecycle[1:] if line.split(",")[1] == "") == 2
    assert (SOURCES / "files" / "listing_details.csv").read_text(encoding="utf-8").splitlines() == [
        "instr_id,venue_code,board_lot"]


@pytest.mark.parametrize(
    ("columns", "message"),
    [
        ({"qty": "volume", "px": "close_price"}, "not declared"),
        ({}, "no column"),
        ({"qty": "volume", "qty2": "volume"}, "two columns"),
    ],
)
def test_the_loader_refuses_columns_that_do_not_match_the_declared_fields(tmp_path, columns, message):
    with pytest.raises((ValueError, ValidationError), match=message):
        SourceCatalogue.load(write(tmp_path, {**ENTRY, "columns": columns}))


def test_a_column_may_hold_an_other_field(tmp_path):
    entry = {**ENTRY, "other_fields": ["esg score"], "columns": {"qty": "volume", "esg": "esg score"}}
    assert SourceCatalogue.load(write(tmp_path, entry)).get("syn_x").columns == {"qty": "volume", "esg": "esg score"}


def test_channels_each_once_with_their_visibility(catalogue):
    assert catalogue.channels() == [("exchanges", "public"), ("market_data_vendors", "public"),
                                    ("public_websites", "public"), ("internal_systems", "private")]


def test_catalogue_and_files_have_no_real_data(catalogue):
    raw = json.loads(DEFAULT_CATALOGUE.read_text(encoding="utf-8"))
    assert {s: find_real_data(s) for s in _strings(raw) if find_real_data(s)} == {}
    for source in catalogue.entries:
        path = SOURCES / source.source_ref
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert find_real_data(text) == [], source.code


def test_unknown_source(catalogue):
    with pytest.raises(UnknownSource, match="syn_nothing"):
        catalogue.get("syn_nothing")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"code": "vendor_prices"}, "syn_"),
        ({"known": True, "conditions": {**ENTRY["conditions"], "external_approvals": ["Sign it"]}},
         "known source"),
        ({"connector": "http", "source_ref": "https://example.test/x.csv"}, "not allowed"),
        ({"connector": "ftp"}, "connector"),
        ({"visibility": "secret"}, "visibility"),
        ({"elements": ["Volume"]}, "elements"),
        ({"elements": [], "other_fields": []}, "at least one"),
    ],
)
def test_the_loader_refuses(tmp_path, change, message):
    with pytest.raises((ValueError, ValidationError), match=message):
        SourceCatalogue.load(write(tmp_path, {**ENTRY, **change}))


def test_the_loader_refuses_a_code_twice(tmp_path):
    with pytest.raises(ValueError, match="syn_x"):
        SourceCatalogue.load(write(tmp_path, ENTRY, ENTRY))


def test_an_http_source_on_this_machine_is_accepted(tmp_path):
    entry = {**ENTRY, "connector": "http", "source_ref": "http://127.0.0.1:8765/x.csv"}
    assert SourceCatalogue.load(write(tmp_path, entry)).get("syn_x").connector == "http"


def test_settings_defaults():
    settings = Settings(_env_file=None)
    assert settings.source_catalogue == DEFAULT_CATALOGUE
    assert settings.allowed_source_hosts() == ("127.0.0.1", "localhost")
    assert Settings(_env_file=None, source_http_allowed_hosts=" 127.0.0.1 ").allowed_source_hosts() == ("127.0.0.1",)


@pytest.mark.parametrize("relative", ["./fixtures/sources/catalogue.json", "fixtures/sources/catalogue.json"])
def test_a_relative_catalogue_path_is_read_from_the_repository_not_the_current_folder(relative, tmp_path,
                                                                                       monkeypatch):
    """As .env.example writes it; the API and the tests may run from another folder."""
    monkeypatch.chdir(tmp_path)
    assert Settings(_env_file=None, source_catalogue=relative).source_catalogue == DEFAULT_CATALOGUE
