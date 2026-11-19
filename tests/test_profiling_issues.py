"""Phase 7.8, task 3.1: column facts and the issues code finds in a file (plan, assumptions 23 and 24).

Every fact here is found by DuckDB over the canonical CSV; no model is involved. The ProfilerAgent only
proposes the key columns (task 3.2); duplicate_key_issue() is the count it will be wired to.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from core.domain.profiling import FeedProfile, FileColumn, ProfileIssue
from core.intake import canonical_csv, read_xlsx_bytes
from core.repositories.feeds import FeedRepository
from core.steps.feed_page import feed_page
from core.steps.profiling import duplicate_key_issue, profile_csv
from core.steps.s02_profile import Profiled
from tests.step_chains import profile_feed
from tests.test_s00_intake import DEMO, blobs, catalogue_for, confirm, inspect  # noqa: F401  (blobs: fixture)


def write(tmp_path, text: str, name: str = "t.csv") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


def column(tmp_path, values: list[str], name: str = "v"):
    """The profile of one column holding these cells (an empty string is an empty cell)."""
    body = "".join(f"{i},{v}\n" for i, v in enumerate(values))
    profile = profile_csv(write(tmp_path, f"id,{name}\n{body}"))
    return profile, profile.columns[1]


# ---------------------------------------------------------------- placeholders, samples, constants


def test_placeholders_are_counted_and_listed_as_they_appear_and_a_null_is_not_one(tmp_path):
    profile, col = column(tmp_path, ["ALL", "na", " N/A ", "-", "   ", "", "x", "NA", "y"])
    assert (col.type, col.null_count, col.placeholder_count) == ("VARCHAR", 1, 6)
    assert col.placeholder_values == ["   ", " N/A ", "-", "ALL", "NA"]  # six spellings, five kept, sorted
    assert col.samples == ["x", "y"]
    assert not col.all_empty and not col.constant


def test_a_column_with_no_placeholder_has_none(tmp_path):
    _, col = column(tmp_path, ["a", "b", ""])
    assert (col.placeholder_count, col.placeholder_values) == (0, [])


def test_samples_are_five_distinct_sorted_values_cut_to_64_and_never_a_placeholder(tmp_path):
    long = "z" * 100
    _, col = column(tmp_path, ["g", "b", "a", "N/A", "f", "e", "d", "c", "a", long])
    assert col.samples == ["a", "b", "c", "d", "e"]
    _, col = column(tmp_path, ["x", long])
    assert col.samples == ["x", "z" * 64]


def test_samples_of_a_number_column_are_its_values_as_text(tmp_path):
    _, col = column(tmp_path, ["3", "1", "2"])
    assert col.type == "BIGINT" and sorted(col.samples) == ["1", "2", "3"]


def test_an_all_null_column_is_all_empty_and_has_no_placeholder(tmp_path):
    profile, col = column(tmp_path, ["", "", ""])
    assert col.all_empty and col.placeholder_count == 0 and not col.constant
    assert [(i.kind, i.columns, i.count) for i in profile.issues] == [("all_empty", ["v"], 3)]


def test_a_column_of_nulls_and_placeholders_is_placeholder_only_not_all_empty(tmp_path):
    profile, col = column(tmp_path, ["NA", "", "-"])
    assert col.all_empty and col.placeholder_count == 2
    assert [(i.kind, i.count) for i in profile.issues] == [("placeholder_only", 2)]
    assert "NA" in profile.issues[0].detail


def test_some_placeholders_among_real_values_is_placeholders(tmp_path):
    profile, col = column(tmp_path, ["a", "b", "N/A", "c"])
    assert not col.all_empty
    assert [(i.kind, i.count) for i in profile.issues] == [("placeholders", 1)]


def test_a_constant_column_has_one_value_and_at_least_two_rows(tmp_path):
    profile, col = column(tmp_path, ["same", "same", "same"])
    assert col.constant and col.samples == ["same"]
    assert [(i.kind, i.count) for i in profile.issues] == [("constant", 3)]
    assert "same" in profile.issues[0].detail
    _, one = column(tmp_path, ["same"])
    assert not one.constant
    _, two = column(tmp_path, ["same", "other"])
    assert not two.constant


def test_a_constant_column_with_nulls_or_placeholders_is_still_constant(tmp_path):
    profile, col = column(tmp_path, ["same", "", "NA", "same"])
    assert col.constant
    assert [(i.kind, i.count) for i in profile.issues] == [("placeholders", 1), ("constant", 4)]


def test_a_clean_file_has_no_issue(tmp_path):
    profile = profile_csv(write(tmp_path, "id,a\n1,x\n2,y\n3,z\n"))
    assert profile.issues == []


def test_issues_come_in_column_order_one_per_column_and_kind(tmp_path):
    profile = profile_csv(write(tmp_path, "id,a,b,c\n1,NA,,k\n2,NA,,k\n3,-,,k\n"))
    assert [(i.kind, i.columns) for i in profile.issues] == [
        ("placeholder_only", ["a"]), ("all_empty", ["b"]), ("constant", ["c"])]


# ---------------------------------------------------------------- dates held as text


@pytest.mark.parametrize("fmt, values", [
    ("DD-MM-YYYY", ["31-12-2019", "05-01-2020"]),
    ("MM-DD-YYYY", ["12-31-2019", "01-05-2020"]),
    ("DD/MM/YYYY", ["31/12/2019", "05/01/2020"]),
    ("MM/DD/YYYY", ["12/31/2019", "01/05/2020"]),
    ("YYYY-MM-DD", ["2019-12-31", "2020-01-05"]),
    ("YYYYMMDD", ["20191231", "20200105"]),
    ("DD.MM.YYYY", ["31.12.2019", "05.01.2020"]),
])
def test_each_text_date_format_is_found_and_not_ambiguous(tmp_path, fmt, values):
    profile, col = column(tmp_path, [*values, "NA"], name="d")  # a placeholder keeps DuckDB from typing a date
    assert col.type == "VARCHAR", col.type
    assert (col.text_date_format, col.date_format_ambiguous) == (fmt, False)
    kinds = [(i.kind, i.count, i.detail) for i in profile.issues if i.kind == "date_as_text"]
    assert len(kinds) == 1 and kinds[0][1] == 3 and fmt in kinds[0][2] and "ambiguous" not in kinds[0][2]


def test_a_value_with_a_first_part_over_12_decides_day_first(tmp_path):
    _, col = column(tmp_path, ["31-12-2019", "01-02-2020", "NA"])
    assert (col.text_date_format, col.date_format_ambiguous) == ("DD-MM-YYYY", False)


def test_a_value_with_a_second_part_over_12_decides_month_first(tmp_path):
    _, col = column(tmp_path, ["12-31-2019", "01-02-2020", "NA"])
    assert (col.text_date_format, col.date_format_ambiguous) == ("MM-DD-YYYY", False)


def test_values_that_fit_both_orders_are_day_first_and_ambiguous(tmp_path):
    profile, col = column(tmp_path, ["01-02-2020", "03-04-2021", "NA"])
    assert (col.text_date_format, col.date_format_ambiguous) == ("DD-MM-YYYY", True)
    assert "ambiguous" in [i for i in profile.issues if i.kind == "date_as_text"][0].detail
    _, slash = column(tmp_path, ["01/02/2020", "NA"])
    assert (slash.text_date_format, slash.date_format_ambiguous) == ("DD/MM/YYYY", True)


def test_one_value_that_is_not_a_date_means_no_format(tmp_path):
    profile, col = column(tmp_path, ["31-12-2019", "05-01-2020", "soon"])
    assert col.text_date_format is None and not col.date_format_ambiguous
    assert not [i for i in profile.issues if i.kind == "date_as_text"]


def test_impossible_dates_and_mixed_orders_are_no_format(tmp_path):
    assert column(tmp_path, ["32-13-2019", "NA"])[1].text_date_format is None
    assert column(tmp_path, ["31-12-2019", "12-31-2019", "NA"])[1].text_date_format is None


def test_a_column_duckdb_typed_as_a_date_is_not_a_date_as_text(tmp_path):
    profile, col = column(tmp_path, ["2019-12-31", "2020-01-05"], name="d")
    assert col.type == "DATE" and col.text_date_format is None
    assert profile.issues == []


def test_text_dates_duckdb_sniffed_into_a_date_type_are_still_dates_as_text(tmp_path):
    profile, col = column(tmp_path, ["31-12-2019", "05-01-2020"], name="d")
    assert col.type == "DATE"  # DuckDB's own sniffing hid the text
    assert (col.text_date_format, col.date_format_ambiguous) == ("DD-MM-YYYY", False)
    assert [(i.kind, i.count) for i in profile.issues] == [("date_as_text", 2)]
    assert col.samples == ["05-01-2020", "31-12-2019"]  # the text as written, not DuckDB's parse


def test_slash_and_dot_dates_sniffed_into_a_date_type_are_flagged_and_iso_is_not(tmp_path):
    assert column(tmp_path, ["31/12/2019", "05/01/2020"])[1].text_date_format == "DD/MM/YYYY"
    assert column(tmp_path, ["31.12.2019", "05.01.2020"])[1].text_date_format == "DD.MM.YYYY"
    profile, col = column(tmp_path, ["2019-12-31", "2020-01-05"])
    assert col.type == "DATE" and col.text_date_format is None and profile.issues == []


def test_mixed_day_first_and_month_first_dates_are_a_profile_with_text_types_not_a_refusal(tmp_path):
    profile, col = column(tmp_path, ["31-12-2019", "12-31-2019"])
    assert col.type == "VARCHAR" and col.text_date_format is None
    assert profile.row_count == 2 and not profile.issues


def test_a_text_column_of_placeholders_has_no_date_format(tmp_path):
    assert column(tmp_path, ["NA", "-"])[1].text_date_format is None


# ---------------------------------------------------------------- the duplicate key count (wired by task 3.2)


KEYED = "id,k1,k2\n1,a,x\n2,b,x\n3,c,y\n"


def test_unique_keys_have_no_duplicate_issue(tmp_path):
    assert duplicate_key_issue(write(tmp_path, KEYED), ["id"]) is None
    assert duplicate_key_issue(write(tmp_path, KEYED), ["k1"]) is None


def test_one_extra_row_is_counted_and_the_value_named(tmp_path):
    path = write(tmp_path, "id,k\n1,a\n2,b\n3,a\n")
    issue = duplicate_key_issue(path, ["k"])
    assert isinstance(issue, ProfileIssue) and (issue.kind, issue.columns, issue.count) == ("duplicate_key", ["k"], 1)
    assert "a" in issue.detail


def test_two_duplicated_keys_count_every_extra_row(tmp_path):
    path = write(tmp_path, "id,k\n1,KA\n2,KA\n3,KA\n4,KB\n5,KB\n6,KC\n")
    issue = duplicate_key_issue(path, ["k"])
    assert issue.count == 3 and "KA" in issue.detail and "KB" in issue.detail and "KC" not in issue.detail


def test_at_most_five_duplicated_values_are_named(tmp_path):
    rows = "".join(f"{n},key{c}\n{n + 100},key{c}\n" for n, c in enumerate("abcdefg"))
    issue = duplicate_key_issue(write(tmp_path, "id,k\n" + rows), ["k"])
    assert issue.count == 7
    named = issue.detail.split("Duplicated: ", 1)[1]
    assert named.count("key") == 5 and "keya" in named and "keyg" not in named


def test_a_two_column_key_is_the_pair(tmp_path):
    path = write(tmp_path, "id,k1,k2\n1,a,x\n2,a,y\n3,b,x\n4,a,x\n")
    assert duplicate_key_issue(path, ["k1"]).count == 2
    issue = duplicate_key_issue(path, ["k1", "k2"])
    assert (issue.columns, issue.count) == (["k1", "k2"], 1)


def test_empty_keys_count_as_a_value_of_their_own(tmp_path):
    path = write(tmp_path, "id,k\n1,a\n2,\n3,\n4,b\n")
    issue = duplicate_key_issue(path, ["k"])
    assert issue.count == 1


def test_an_unknown_key_column_is_refused(tmp_path):
    with pytest.raises(ValueError, match="nope"):
        duplicate_key_issue(write(tmp_path, KEYED), ["nope"])


# ---------------------------------------------------------------- the security master


def master_csv(tmp_path) -> Path:
    table, _ = read_xlsx_bytes(DEMO.read_bytes(), original_name=DEMO.name)
    return write(tmp_path, canonical_csv(table).decode("utf-8"), "master.csv")


def test_the_security_master_issues_are_exactly_these(tmp_path):
    profile = profile_csv(master_csv(tmp_path))
    # RECORD_DATE is text '31-12-2019' in the workbook; DuckDB's sniffer types it DATE, the raw text decides.
    assert [(i.kind, i.columns, i.count) for i in profile.issues] == [
        ("constant", ["SOURCE"], 70),
        ("placeholder_only", ["COUNTRY"], 70),
        ("placeholder_only", ["REGION"], 70),
        ("placeholder_only", ["F_PERIOD"], 70),
        ("date_as_text", ["RECORD_DATE"], 70),
        ("constant", ["RECORD_DATE"], 70),
        ("all_empty", ["BEST_EPS_1_WEEK_PERCENT_CHANGE"], 70),
    ]
    by = {i.columns[0]: i for i in profile.issues}
    assert "'ALL'" in by["COUNTRY"].detail and "'ALL'" in by["REGION"].detail and "'NA'" in by["F_PERIOD"].detail
    assert "'SYN'" in by["SOURCE"].detail
    date_issues = {i.kind: i for i in profile.issues if i.columns == ["RECORD_DATE"]}
    assert "DD-MM-YYYY" in date_issues["date_as_text"].detail and "31-12-2019" in date_issues["constant"].detail
    assert {c.name: c.text_date_format for c in profile.columns}["RECORD_DATE"] == "DD-MM-YYYY"


def test_the_security_master_ticker_has_one_extra_row(tmp_path):
    issue = duplicate_key_issue(master_csv(tmp_path), ["TICKER_NAME"])
    assert issue is not None and issue.count == 1 and "100001 SY Equity" in issue.detail


# ---------------------------------------------------------------- stored with the profile


def test_an_old_shaped_column_and_profile_still_load():
    old = {"name": "a", "type": "VARCHAR", "null_count": 1, "null_rate": 0.5, "distinct_count": 1}
    col = FileColumn.model_validate(old)
    assert (col.placeholder_count, col.placeholder_values, col.text_date_format, col.date_format_ambiguous,
            col.all_empty, col.constant, col.samples) == (0, [], None, False, False, False, [])
    stored = {"id": uuid4(), "created_at": datetime.now(UTC), "feed_id": uuid4(), "version": 1, "fetch_id": uuid4(),
              "outcome": "profiled", "row_count": 2, "columns": [old], "coverage": [],
              "spec": {"source_code": "syn_a", "elements": [], "other_fields": [], "columns": {}},
              "detail": None, "engine": "duckdb x", "profiled_by": "dev"}
    assert FeedProfile.model_validate(stored).issues == []


def test_the_issues_of_a_stored_profile_are_found_from_its_columns(tmp_path):
    file = profile_csv(write(tmp_path, "id,a,b\n1,NA,k\n2,-,k\n3,NA,k\n"))
    stored = {"id": uuid4(), "created_at": datetime.now(UTC), "feed_id": uuid4(), "version": 1, "fetch_id": uuid4(),
              "outcome": "profiled", "row_count": file.row_count,
              "columns": [c.model_dump() for c in file.columns], "coverage": [],
              "spec": {"source_code": "syn_a", "elements": [], "other_fields": [], "columns": {}},
              "detail": None, "engine": file.engine, "profiled_by": "dev"}
    assert FeedProfile.model_validate(stored).issues == file.issues != []


def test_step_02_keeps_the_new_facts_and_the_feed_page_shows_the_issues(db_session, blobs):  # noqa: F811
    upload = inspect(blobs, DEMO.read_bytes(), DEMO.name)
    db_session.commit()
    uploaded = confirm(db_session, blobs, upload.upload_id, filename=DEMO.name, feed_code="demo_security_master")
    db_session.commit()
    catalogue = catalogue_for(db_session)
    result = profile_feed(db_session, feed_id=uploaded.feed_id, catalogue=catalogue, blob_store=blobs,
                          profiled_by="dev-backend-team")
    assert isinstance(result, Profiled)
    db_session.commit()
    stored = {c.name: c for c in result.profile.columns}
    assert stored["SOURCE"].constant and stored["BEST_EPS_1_WEEK_PERCENT_CHANGE"].all_empty
    assert stored["RECORD_DATE"].constant and stored["RECORD_DATE"].text_date_format == "DD-MM-YYYY"
    assert stored["COUNTRY"].placeholder_count == 70 and stored["COUNTRY"].placeholder_values == ["ALL"]
    assert stored["COUNTRY"].samples == []
    assert len(result.profile.issues) == 7
    page = feed_page(db_session, FeedRepository(db_session).get(uploaded.feed_id), catalogue)
    assert page.issues == result.profile.issues and page.profile.issues == result.profile.issues
