"""Phase 7.19, T4: the audit view and its CSV list a glossary upload (who, when, which file, how many rows of each label) and
the decision on each of its rows (a decision of a feed-less item: no feed, made by a person)."""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from datetime import UTC, datetime

import pytest

from core.steps.audit import audit_csv, audit_rows
from core.steps.glossary_upload import decide_imports, read_glossary_file, record_upload
from tests.conftest import dev_user_for

MONTH = f"{datetime.now(UTC):%Y-%m}"
FILE = ('key,name,domain,data_type,definition,aliases\n'
        'best_fcf,Best free cash flow,pricing,decimal,Consensus free cash flow.,fcf_consensus\n'
        'best_roe,Best ROE,pricing,decimal,Consensus ROE.,\n'
        'Bad Key,X,pricing,decimal,Def.,\n').encode()


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def owner():
    return dev_user_for("glossary_owner")


def upload(s, owner, name="march.csv"):
    rows = read_glossary_file(FILE, filename=name, max_bytes=1 << 20)
    return record_upload(s, rows=rows, file_name=name, sha256=hashlib.sha256(FILE).hexdigest(), user=owner)


def test_an_upload_is_a_row_with_who_which_file_and_the_counts(s, owner):
    made = upload(s, owner)
    [row] = [r for r in audit_rows(s, month=MONTH) if r.kind == "upload"]
    assert (row.who, row.role, row.decision, row.item, row.by, row.feed_code, row.upload_id) == (
        owner.id, None, "uploaded", "glossary_upload", "manual", None, made.upload_id)
    assert row.summary == "march.csv: 2 new, 1 invalid"
    assert row.at <= datetime.now(UTC)


def test_each_decision_on_a_row_of_an_upload_is_a_row_with_no_feed(s, owner):
    made = upload(s, owner)
    first, second = (r.proposal_id for r in made.rows if r.proposal_id)
    decide_imports(s, proposal_ids=[first], user=owner, decision="approved", note="Looks right.")
    decide_imports(s, proposal_ids=[second], user=owner, decision="rejected", note="Not this one.")
    decisions = [r for r in audit_rows(s, month=MONTH) if r.kind == "decision"]
    assert [(r.item, r.summary, r.decision, r.who, r.role, r.by, r.feed_id, r.agent, r.note) for r in decisions] == [
        ("glossary_import", "New element best_fcf", "approved", owner.id, "glossary_owner", "manual", None, None,
         "Looks right."),
        ("glossary_import", "New element best_roe", "rejected", owner.id, "glossary_owner", "manual", None, None,
         "Not this one.")]
    assert sorted(r.kind for r in audit_rows(s, month=MONTH)) == ["decision", "decision", "upload"]


def test_the_person_filter_keeps_the_owners_rows_and_the_feed_filter_hides_a_feed_less_upload(s, owner):
    made = upload(s, owner)
    decide_imports(s, proposal_ids=[made.rows[0].proposal_id], user=owner, decision="approved", note=None)
    assert sorted(r.kind for r in audit_rows(s, month=MONTH, person=owner.id)) == ["decision", "upload"]
    assert audit_rows(s, month=MONTH, person="dev-data-steward") == []
    assert audit_rows(s, month=MONTH, feed_id=uuid.uuid4()) == []


def test_another_month_has_no_upload(s, owner):
    upload(s, owner)
    assert audit_rows(s, month="2020-01") == []


def test_the_csv_has_the_upload_and_its_decisions_and_neutralises_a_file_name_that_is_a_formula(s, owner):
    made = upload(s, owner, name="=HYPERLINK(1).csv")
    decide_imports(s, proposal_ids=[made.rows[0].proposal_id], user=owner, decision="approved", note=None)
    table = list(csv.DictReader(io.StringIO(audit_csv(audit_rows(s, month=MONTH)))))
    assert sorted((r["kind"], r["item"], r["decision"], r["who"]) for r in table) == [
        ("decision", "glossary_import", "approved", owner.id), ("upload", "glossary_upload", "uploaded", owner.id)]
    [uploaded] = [r for r in table if r["kind"] == "upload"]
    assert uploaded["summary"].startswith("'=HYPERLINK(1).csv")
