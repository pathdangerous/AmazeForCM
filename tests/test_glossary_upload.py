"""Glossary upload (phase 7.19, T2): a CSV or XLSX file of elements. Each row is labelled; a proposal is written only for a
new or an update row, in one transaction with the upload record; the glossary owner decides each (rule 1), an approved
row is a new element or the next version (rule 7). No model is called anywhere (rule 4 has nothing to log)."""

from __future__ import annotations

import hashlib
import io

import openpyxl
import pytest
from sqlalchemy import text

from core.domain.mapping import ElementOption
from core.repositories.errors import NotAnApprover
from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from core.steps.errors import NoteNeeded
from core.steps.glossary_upload import (
    COLUMNS,
    FileRefused,
    decide_imports,
    list_uploads,
    read_glossary_file,
    record_upload,
    template_csv,
    upload_page,
)
from tests.configured import a_registered_feed, approved_element, approved_mapping
from tests.conftest import dev_user_for

HEADER = ",".join(COLUMNS)
MB = 1024 * 1024


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def owner():
    return dev_user_for("glossary_owner")


def csv_of(*rows: str, header: str = HEADER) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")


def upload(s, data: bytes, user, *, name: str = "glossary.csv"):
    rows = read_glossary_file(data, filename=name, max_bytes=MB)
    assert not isinstance(rows, FileRefused), rows
    return record_upload(s, rows=rows, file_name=name, sha256=hashlib.sha256(data).hexdigest(), user=user)


def labels(result) -> dict[str, tuple[str, str | None]]:
    return {r.key: (r.label, r.detail) for r in result.rows}


def logged_calls(s) -> int:
    return s.scalar(text("SELECT count(*) FROM proposal_log"))


def current_row(s, key: str, **change) -> str:
    """One CSV row of an existing element, with the given fields changed."""
    e = GlossaryRepository(s).get_current(key)
    fields = {"key": e.key, "name": e.name, "domain": e.domain, "data_type": e.data_type, "definition": e.definition,
              "aliases": ";".join(e.aliases), **change}
    return ",".join(f'"{fields[c]}"' for c in COLUMNS)


NEW_ROW = '"best_fcf","Best free cash flow","pricing","decimal","Consensus free cash flow.","fcf_consensus"'


# ---------------------------------------------------------------- reading the file


def test_the_template_is_the_header_row(s):
    assert template_csv().strip() == HEADER


def test_a_file_missing_columns_is_refused_and_names_them():
    refused = read_glossary_file(csv_of("a,b", header="key,name"), filename="g.csv", max_bytes=MB)
    assert isinstance(refused, FileRefused) and refused.reason == "missing_columns"
    assert all(c in refused.detail for c in ("domain", "data_type", "definition", "aliases"))


def test_a_file_over_the_limit_is_refused():
    refused = read_glossary_file(csv_of(NEW_ROW), filename="g.csv", max_bytes=10)
    assert isinstance(refused, FileRefused) and refused.reason == "too_large"


@pytest.mark.parametrize("name", ["g.txt", "g.pdf", "g"])
def test_another_file_type_is_refused(name):
    refused = read_glossary_file(csv_of(NEW_ROW), filename=name, max_bytes=MB)
    assert isinstance(refused, FileRefused) and refused.reason == "unsupported_format"


def test_a_file_with_no_rows_is_refused():
    refused = read_glossary_file(csv_of(), filename="g.csv", max_bytes=MB)
    assert isinstance(refused, FileRefused) and refused.reason == "no_rows"


def test_a_file_with_too_many_rows_is_refused():
    refused = read_glossary_file(csv_of(NEW_ROW, NEW_ROW, NEW_ROW), filename="g.csv", max_bytes=MB, max_rows=2)
    assert isinstance(refused, FileRefused) and refused.reason == "too_many_rows"


def test_a_file_that_is_not_text_is_refused():
    refused = read_glossary_file(b"\xff\xfe\x00bad", filename="g.csv", max_bytes=MB)
    assert isinstance(refused, FileRefused) and refused.reason == "unreadable"


def test_a_byte_order_mark_and_another_column_order_are_read():
    data = ("﻿name,key,aliases,domain,definition,data_type\n"
            "Best FCF,best_fcf,fcf_consensus;fcf_est,pricing,Consensus.,decimal\n").encode("utf-8")
    rows = read_glossary_file(data, filename="G.CSV", max_bytes=MB)
    assert [r.values["key"] for r in rows] == ["best_fcf"] and rows[0].values["aliases"] == "fcf_consensus;fcf_est"


def test_an_xlsx_file_reads_the_same_rows_as_the_csv():
    book = openpyxl.Workbook()
    book.active.append(list(COLUMNS))
    book.active.append(["best_fcf", "Best free cash flow", "pricing", "decimal", "Consensus free cash flow.",
                        "fcf_consensus"])
    book.active.append([None, None, None, None, None, None])  # an empty row is skipped
    buffer = io.BytesIO()
    book.save(buffer)
    from_xlsx = read_glossary_file(buffer.getvalue(), filename="g.xlsx", max_bytes=MB)
    from_csv = read_glossary_file(csv_of(NEW_ROW), filename="g.csv", max_bytes=MB)
    assert [r.values for r in from_xlsx] == [r.values for r in from_csv]


# ---------------------------------------------------------------- the labels


def test_a_new_element_is_labelled_new(s, owner):
    result = upload(s, csv_of(NEW_ROW), owner)
    assert labels(result) == {"best_fcf": ("new", None)}


def test_a_changed_element_is_labelled_update_and_names_the_fields(s, owner):
    result = upload(s, csv_of(current_row(s, "close_price", definition="The official closing price.")), owner)
    row = result.rows[0]
    assert (row.label, row.changes) == ("update", ["definition"])


def test_an_element_equal_to_its_current_version_is_unchanged(s, owner):
    reordered = current_row(s, "close_price", aliases=";".join(reversed(GlossaryRepository(s).get_current(
        "close_price").aliases)))
    result = upload(s, csv_of(reordered), owner)
    assert labels(result) == {"close_price": ("unchanged", None)}


@pytest.mark.parametrize("row, reason", [
    ('"x_one","","pricing","decimal","Def.",""', "missing field: name"),
    ('"x_one","X","pricing","decimal","",""', "missing field: definition"),
    ('"Bad Key","X","pricing","decimal","Def.",""', "bad key"),
    ('"x_one","X","pricing","decimal","Def.","Not Snake"', "bad alias"),
    ('"x_one","X","pricing","decimal","Def.","x_one"', "bad alias"),
    ('"x_one","X","pricing","decimal","Def.","dup;dup"', "bad alias"),
    ('"x_one","X","pricing","number","Def.",""', "unknown data_type"),
    ('"x_one","X","crypto","decimal","Def.",""', "unknown domain"),
])
def test_an_invalid_row_says_why(s, owner, row, reason):
    result = upload(s, csv_of(row), owner)
    label, detail = labels(result)["x_one" if "Bad Key" not in row else "Bad Key"]
    assert label == "invalid" and reason in detail
    assert result.proposals == 0


def test_a_row_whose_alias_belongs_to_another_element_is_an_alias_conflict(s, owner):
    result = upload(s, csv_of('"my_close","My close","pricing","decimal","Def.","closing_price"'), owner)
    assert labels(result) == {"my_close": ("alias_conflict", "close_price")}
    assert result.proposals == 0


def test_a_name_another_element_owns_is_an_alias_conflict_too(s, owner):
    close = GlossaryRepository(s).get_current("close_price")
    result = upload(s, csv_of(f'"my_close","{close.name}","pricing","decimal","Def.",""'), owner)
    assert labels(result)["my_close"] == ("alias_conflict", "close_price")


def test_the_second_row_for_a_key_is_a_duplicate_in_the_file(s, owner):
    result = upload(s, csv_of(NEW_ROW, NEW_ROW), owner)
    assert [(r.row, r.label) for r in result.rows] == [(1, "new"), (2, "duplicate_in_file")]
    assert result.proposals == 1


def test_two_rows_of_a_file_cannot_claim_one_alias(s, owner):
    result = upload(s, csv_of('"alpha","Alpha","pricing","decimal","A.","shared_one"',
                              '"beta","Beta","pricing","decimal","B.","shared_one"'), owner)
    assert labels(result) == {"alpha": ("new", None), "beta": ("alias_conflict", "alpha")}


def test_a_key_with_a_pending_row_from_an_earlier_upload_is_invalid(s, owner):
    upload(s, csv_of(NEW_ROW), owner)
    again = upload(s, csv_of(NEW_ROW), owner, name="again.csv")
    label, detail = labels(again)["best_fcf"]
    assert label == "invalid" and "pending" in detail


# ---------------------------------------------------------------- what is written


def test_only_new_and_update_rows_get_a_proposal_and_all_rows_are_recorded(s, owner):
    data = csv_of(NEW_ROW, current_row(s, "close_price", definition="Changed."), current_row(s, "open_price"),
                  '"x_one","","pricing","decimal","Def.",""')
    before = logged_calls(s)
    result = upload(s, data, owner, name="march.csv")
    assert result.counts == {"new": 1, "update": 1, "unchanged": 1, "invalid": 1}
    assert result.proposals == 2
    proposals = ProposalRepository(s).list(kind="glossary_import")
    assert sorted(p.payload["key"] for p in proposals) == ["best_fcf", "close_price"]
    assert all((p.origin, p.author, p.feed_id, p.proposal_log_id, p.status, p.required_approver_roles) ==
               ("manual", owner.id, None, None, "pending", ["glossary_owner"]) for p in proposals)
    page = upload_page(s, result.upload_id)
    assert (page.file_name, page.uploaded_by, len(page.rows)) == ("march.csv", owner.id, 4)
    assert {r.key: r.status for r in page.rows} == {"best_fcf": "pending", "close_price": "pending", "open_price": None,
                                                    "x_one": None}
    assert logged_calls(s) == before  # no model was called


def test_the_upload_and_its_proposals_are_written_together_or_not_at_all(s, owner, monkeypatch):
    calls = []
    original = ProposalRepository.create_glossary_import

    def second_fails(self, **kwargs):
        calls.append(kwargs["payload"]["key"])
        if len(calls) == 2:
            raise RuntimeError("disk full")
        return original(self, **kwargs)

    monkeypatch.setattr(ProposalRepository, "create_glossary_import", second_fails)
    data = csv_of(NEW_ROW, '"best_roe","Best ROE","pricing","decimal","Consensus ROE.",""')
    with pytest.raises(RuntimeError):
        upload(s, data, owner)
    assert s.scalar(text("SELECT count(*) FROM glossary_uploads")) == 0
    assert s.scalar(text("SELECT count(*) FROM proposals WHERE kind = 'glossary_import'")) == 0


def test_an_update_says_how_many_mappings_use_the_element(s, owner):
    feed = a_registered_feed(s, "syn_glossary_use")
    approved_mapping(s, feed, "px", "close_price")
    result = upload(s, csv_of(current_row(s, "close_price", definition="Changed."),
                              current_row(s, "open_price", definition="Changed.")), owner)
    assert {r.key: r.used_by_mappings for r in result.rows} == {"close_price": 1, "open_price": 0}
    pending = {p.payload["key"]: p.payload["used_by_mappings"] for p in ProposalRepository(s).list(
        kind="glossary_import")}
    assert pending == {"close_price": 1, "open_price": 0}


def test_only_the_glossary_owner_uploads(s):
    with pytest.raises(NotAnApprover):
        upload(s, csv_of(NEW_ROW), dev_user_for("data_steward"))


def test_the_uploads_are_listed_newest_first(s, owner):
    first = upload(s, csv_of(NEW_ROW), owner, name="one.csv")
    second = upload(s, csv_of('"best_roe","Best ROE","pricing","decimal","Consensus ROE.",""'), owner, name="two.csv")
    listed = list_uploads(s)
    assert [u.file_name for u in listed[:2]] == ["two.csv", "one.csv"]
    assert listed[0].id == second.upload_id and listed[1].id == first.upload_id and listed[1].counts == {"new": 1}


# ---------------------------------------------------------------- the glossary owner's decisions


def test_approving_selected_rows_writes_a_new_element_and_the_next_version(s, owner):
    result = upload(s, csv_of(NEW_ROW, current_row(s, "close_price", definition="The official closing price.")), owner)
    ids = [r.proposal_id for r in result.rows]
    before = logged_calls(s)
    items = decide_imports(s, proposal_ids=ids, user=owner, decision="approved", note=None)
    assert [i.outcome for i in items] == ["approved", "approved"]
    glossary = GlossaryRepository(s)
    assert glossary.get_current("best_fcf").version == 1
    assert glossary.get_current("best_fcf").aliases == ["fcf_consensus"]
    history = glossary.history("close_price")
    assert [e.version for e in history] == [1, 2]
    assert history[1].definition == "The official closing price." and history[0].definition != history[1].definition
    assert all(e.created_by == owner.id and e.proposal_id in ids for e in (glossary.get_current("best_fcf"), history[1]))
    approvals = {i: ProposalRepository(s).approvals(i) for i in ids}
    assert all(len(a) == 1 and (a[0].decided_by, a[0].role, a[0].decision) == (owner.id, "glossary_owner", "approved")
               for a in approvals.values())
    assert {r.key: r.status for r in upload_page(s, result.upload_id).rows} == {"best_fcf": "approved",
                                                                              "close_price": "approved"}
    assert logged_calls(s) == before


def test_rejecting_selected_rows_writes_nothing_and_needs_a_note(s, owner):
    result = upload(s, csv_of(NEW_ROW, current_row(s, "close_price", definition="Changed.")), owner)
    ids = [r.proposal_id for r in result.rows]
    with pytest.raises(NoteNeeded):
        decide_imports(s, proposal_ids=ids, user=owner, decision="rejected", note=" ")
    items = decide_imports(s, proposal_ids=ids, user=owner, decision="rejected", note="Not these.")
    assert [i.outcome for i in items] == ["rejected", "rejected"]
    assert not GlossaryRepository(s).exists("best_fcf")
    assert [e.version for e in GlossaryRepository(s).history("close_price")] == [1]
    assert {r.status for r in upload_page(s, result.upload_id).rows} == {"rejected"}


def test_an_alias_another_element_holds_cannot_be_approved_and_does_not_stop_the_others(s, owner):
    first = upload(s, csv_of('"alpha","Alpha","pricing","decimal","A.","shared_one"'), owner, name="a.csv")
    second = upload(s, csv_of('"beta","Beta","pricing","decimal","B.","shared_one"',
                              '"gamma","Gamma","pricing","decimal","C.",""'), owner, name="b.csv")
    decide_imports(s, proposal_ids=[first.rows[0].proposal_id], user=owner, decision="approved", note=None)
    beta, gamma = (r.proposal_id for r in second.rows)
    items = decide_imports(s, proposal_ids=[beta, gamma], user=owner, decision="approved", note=None)
    assert [(i.outcome, getattr(i, "reason", None)) for i in items] == [("refused", "alias_conflict"), ("approved", None)]
    assert "alpha" in items[0].detail
    glossary = GlossaryRepository(s)
    assert not glossary.exists("beta") and glossary.exists("gamma")
    assert ProposalRepository(s).get(beta).status == "pending"  # refused is not rejected: nobody decided


def test_an_update_whose_element_changed_since_the_upload_is_refused(s, owner):
    result = upload(s, csv_of(current_row(s, "close_price", definition="Changed.")), owner)
    approved_element(s, ElementOption(kind="alias", key="close_price", alias="px_close_x"))  # version 2, meanwhile
    items = decide_imports(s, proposal_ids=[result.rows[0].proposal_id], user=owner, decision="approved", note=None)
    assert (items[0].outcome, items[0].reason) == ("refused", "base_changed")
    assert [e.version for e in GlossaryRepository(s).history("close_price")] == [1, 2]


def test_a_row_decided_twice_is_refused_the_second_time(s, owner):
    result = upload(s, csv_of(NEW_ROW), owner)
    pid = result.rows[0].proposal_id
    decide_imports(s, proposal_ids=[pid], user=owner, decision="approved", note=None)
    items = decide_imports(s, proposal_ids=[pid], user=owner, decision="approved", note=None)
    assert (items[0].outcome, items[0].reason) == ("refused", "already_decided")


def test_a_proposal_that_does_not_exist_is_refused(s, owner):
    items = decide_imports(s, proposal_ids=["00000000-0000-0000-0000-000000000000"], user=owner, decision="approved",
                           note=None)
    assert (items[0].outcome, items[0].reason) == ("refused", "unknown_proposal")


def test_only_the_glossary_owner_decides(s, owner):
    result = upload(s, csv_of(NEW_ROW), owner)
    for role in ("data_steward", "backend_team"):
        with pytest.raises(NotAnApprover):
            decide_imports(s, proposal_ids=[result.rows[0].proposal_id], user=dev_user_for(role), decision="approved",
                           note=None)
    assert not GlossaryRepository(s).exists("best_fcf")


def test_an_update_of_an_element_mappings_use_leaves_the_mappings_alone(s, owner):
    feed = a_registered_feed(s, "syn_glossary_keep")
    mapping = approved_mapping(s, feed, "px", "close_price")
    result = upload(s, csv_of(current_row(s, "close_price", definition="Changed.")), owner)
    decide_imports(s, proposal_ids=[result.rows[0].proposal_id], user=owner, decision="approved", note=None)
    assert s.scalar(text("SELECT count(*) FROM mappings WHERE feed_id = :f"), {"f": feed}) == 1
    assert s.scalar(text("SELECT element_key FROM mappings WHERE id = :i"), {"i": mapping.id}) == "close_price"
