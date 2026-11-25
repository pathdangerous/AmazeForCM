"""Migration 0038 (phase 7.19, T1): a glossary_import proposal is one element row from an upload, made by a person, with no
feed, decided by the glossary owner. Approving it writes a new element (version 1 of a new key) or the next version of an
existing key, never an edit in place; an alias another element holds cannot be approved. Every guard written for a feed's
glossary or mapping proposal is as strict as before."""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text

from core.repositories.glossary import GlossaryRepository
from core.repositories.proposals import ProposalRepository
from tests.conftest import dev_user_for
from tests.test_db_onboarding_guards import refused

OWNER = "dev-glossary-owner"
UPLOAD = ("INSERT INTO glossary_uploads (uploaded_by, file_name, file_sha256, counts, rows) VALUES (:by, :name, :sha, "
          "CAST(:counts AS jsonb), CAST(:rows AS jsonb)) RETURNING id")
PROPOSAL = ("INSERT INTO proposals (kind, payload, autonomy, required_approver_roles, feed_id, origin, author) "
            "VALUES ('glossary_import', CAST(:payload AS jsonb), 'A1', :roles, :feed, :origin, :author) RETURNING id")
ELEMENT = ("INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, aliases, unit, created_by, "
           "proposal_id) VALUES (:key, :version, :name, :domain, :data_type, :definition, CAST(:aliases AS text[]), "
           ":unit, :by, :proposal)")


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def upload(s) -> str:
    return s.execute(text(UPLOAD), {"by": OWNER, "name": "glossary.csv", "sha": hashlib.sha256(b"x").hexdigest(),
                                    "counts": json.dumps({"new": 1}), "rows": json.dumps([])}).scalar_one()


def element(key="best_fcf", **more) -> dict:
    return {"key": key, "name": "Best free cash flow", "domain": "pricing", "data_type": "decimal",
            "definition": "Analyst consensus free cash flow.", "aliases": ["fcf_consensus"], "unit": None, **more}


def payload(upload, *, label="new", base_version=None, **more) -> dict:
    el = element(**more)
    return {"upload_id": str(upload), "row": 1, "label": label, "key": el["key"], "element": el,
            "base_version": base_version, "used_by_mappings": 0, "summary": f"{label} element {el['key']}"}


def propose(s, upload, *, feed=None, origin="manual", author=OWNER, roles=("glossary_owner",), **kw) -> str:
    return s.execute(text(PROPOSAL), {"payload": json.dumps(payload(upload, **kw)), "roles": list(roles), "feed": feed,
                                      "origin": origin, "author": author}).scalar_one()


def approve(s, proposal_id) -> None:
    ProposalRepository(s).record_decision(proposal_id, user=dev_user_for("glossary_owner"), decision="approved")


def write(s, proposal_id, **more) -> None:
    row = {**element(), "version": 1, "by": OWNER, "proposal": proposal_id, **more}
    s.execute(text(ELEMENT), row)


# ---------------------------------------------------------------- the proposal


def test_a_glossary_import_proposal_has_no_feed_and_no_logged_call(s, upload):
    pid = propose(s, upload)
    row = s.execute(text("SELECT origin, author, feed_id, proposal_log_id, status FROM proposals WHERE id = :i"),
                    {"i": pid}).one()
    assert (row.origin, row.author, row.feed_id, row.proposal_log_id, row.status) == ("manual", OWNER, None, None,
                                                                                    "pending")


def test_every_other_kind_without_a_feed_is_still_refused(s):
    insert = ("INSERT INTO proposals (kind, payload, autonomy, required_approver_roles, feed_id, origin, author) "
              "VALUES (:kind, CAST(:payload AS jsonb), 'A1', :roles, NULL, 'manual', :author)")
    refused(s, "manual_kinds", insert, kind="glossary_element", payload=json.dumps({"source_field": "px"}),
            roles=["glossary_owner"], author=OWNER)
    refused(s, "manual_kinds", insert, kind="mapping", payload=json.dumps({"source_field": "px"}),
            roles=["data_steward"], author=OWNER)
    refused(s, "manual_kinds", insert, kind="rule", payload=json.dumps({}), roles=["data_steward", "checker"],
            author=OWNER)


def test_an_agent_proposal_without_a_feed_is_still_refused(s, logged_call):
    insert = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
              "VALUES (:log, :kind, CAST(:payload AS jsonb), 'A2', :roles, NULL)")
    refused(s, "names_a_column", insert, log=logged_call.id, kind="glossary_element",
            payload=json.dumps({"source_field": "px"}), roles=["glossary_owner"])
    refused(s, "names_a_column", insert, log=logged_call.id, kind="mapping", payload=json.dumps({"source_field": "px"}),
            roles=["data_steward"])


def test_a_glossary_import_proposal_is_a_persons_not_a_models(s, upload, logged_call):
    refused(s, "glossary_import_shape", PROPOSAL, payload=json.dumps(payload(upload)), roles=["glossary_owner"],
            feed=None, origin="rule", author=None)
    refused(s, "glossary_import_shape", PROPOSAL, payload=json.dumps(payload(upload)), roles=["glossary_owner"],
            feed=None, origin="agent", author=None)


def test_a_glossary_import_payload_missing_a_field_is_refused_not_passed_as_null(s, upload):
    whole = payload(upload)
    for broken in ({k: v for k, v in whole.items() if k != "label"}, {k: v for k, v in whole.items() if k != "key"},
                   {**whole, "element": None}, {**whole, "upload_id": None},
                   {**whole, "element": {**whole["element"], "key": "another_key"}}):
        refused(s, "glossary_import_shape|no upload", PROPOSAL, payload=json.dumps(broken), roles=["glossary_owner"],
                feed=None, origin="manual", author=OWNER)


def test_a_glossary_import_proposal_is_decided_by_the_glossary_owner_alone(s, upload):
    for roles in (["data_steward"], ["glossary_owner", "data_steward"]):
        refused(s, "glossary_import_shape", PROPOSAL, payload=json.dumps(payload(upload)), roles=roles, feed=None,
                origin="manual", author=OWNER)


def test_a_glossary_import_proposal_has_no_feed(s, upload):
    from tests.configured import a_registered_feed

    refused(s, "glossary_import_shape", PROPOSAL, payload=json.dumps(payload(upload)), roles=["glossary_owner"],
            feed=a_registered_feed(s, "syn_import_feed"), origin="manual", author=OWNER)


def test_a_glossary_import_proposal_names_a_real_upload(s):
    refused(s, "no upload", PROPOSAL, payload=json.dumps(payload("00000000-0000-0000-0000-000000000000")),
            roles=["glossary_owner"], feed=None, origin="manual", author=OWNER)


def test_the_author_is_the_uploader(s, upload):
    refused(s, "uploaded by", PROPOSAL, payload=json.dumps(payload(upload)), roles=["glossary_owner"], feed=None,
            origin="manual", author="someone-else")


def test_a_new_row_is_for_a_key_that_does_not_exist_and_an_update_for_one_that_does(s, upload):
    refused(s, "already a glossary element", PROPOSAL, payload=json.dumps(payload(upload, key="close_price")),
            roles=["glossary_owner"], feed=None, origin="manual", author=OWNER)
    refused(s, "is not a glossary element", PROPOSAL, payload=json.dumps(payload(upload, label="update", base_version=1)),
            roles=["glossary_owner"], feed=None, origin="manual", author=OWNER)


def test_two_pending_rows_for_one_key_are_refused(s, upload):
    propose(s, upload)
    refused(s, "uq_proposals_one_pending_glossary_import_per_key", PROPOSAL, payload=json.dumps(payload(upload)),
            roles=["glossary_owner"], feed=None, origin="manual", author=OWNER)


def test_a_glossary_import_proposal_cannot_be_edited(s, upload):
    from core.repositories.errors import WrongProposalKind

    pid = propose(s, upload)
    with pytest.raises(WrongProposalKind):
        ProposalRepository(s).edit(pid, payload=payload(upload), user=dev_user_for("glossary_owner"))


# ---------------------------------------------------------------- the element it writes


def test_an_approved_new_row_writes_version_1_of_a_new_key(s, upload):
    pid = propose(s, upload)
    approve(s, pid)
    write(s, pid)
    assert GlossaryRepository(s).get_current("best_fcf").version == 1


def test_a_pending_row_writes_nothing(s, upload):
    pid = propose(s, upload)
    refused(s, "not approved", ELEMENT, **{**element(), "version": 1, "by": OWNER, "proposal": pid})


def test_a_rejected_row_writes_nothing(s, upload):
    pid = propose(s, upload)
    ProposalRepository(s).record_decision(pid, user=dev_user_for("glossary_owner"), decision="rejected", note="no")
    refused(s, "not approved", ELEMENT, **{**element(), "version": 1, "by": OWNER, "proposal": pid})


def test_an_element_written_from_a_row_equals_the_row(s, upload):
    pid = propose(s, upload)
    approve(s, pid)
    refused(s, "does not match the row", ELEMENT, **{**element(definition="Something else."), "version": 1, "by": OWNER,
                                                   "proposal": pid})
    refused(s, "does not match the row", ELEMENT, **{**element(key="other_fcf"), "version": 1, "by": OWNER,
                                                   "proposal": pid})


def test_only_the_owner_who_approved_writes_the_element(s, upload):
    pid = propose(s, upload)
    approve(s, pid)
    refused(s, "is not the glossary_owner who approved", ELEMENT, **{**element(), "version": 1, "by": "dev-steward",
                                                                     "proposal": pid})


def test_an_approved_update_is_the_next_version_never_an_edit(s, upload):
    before = GlossaryRepository(s).get_current("close_price")
    changed = {"name": before.name, "domain": before.domain, "data_type": before.data_type,
               "definition": "The official closing price.", "aliases": list(before.aliases), "unit": before.unit}
    pid = propose(s, upload, label="update", base_version=before.version, key="close_price", **{
        k: v for k, v in changed.items() if k != "unit"}, unit=before.unit)
    approve(s, pid)
    row = {"key": "close_price", "version": before.version + 1, **changed, "by": OWNER, "proposal": pid}
    refused(s, "does not match the row", ELEMENT, **{**row, "version": before.version})  # not in place
    refused(s, "does not match the row", ELEMENT, **{**row, "version": before.version + 2})
    s.execute(text(ELEMENT), row)
    assert GlossaryRepository(s).get_current("close_price").definition == "The official closing price."
    assert [e.version for e in GlossaryRepository(s).history("close_price")] == [1, 2]


def test_an_update_of_an_older_version_than_the_latest_is_refused(s, upload):
    before = GlossaryRepository(s).get_current("close_price")
    pid = propose(s, upload, label="update", base_version=before.version + 5, key="close_price",
                  name=before.name, domain=before.domain, data_type=before.data_type, definition="Changed.",
                  aliases=list(before.aliases), unit=before.unit)
    approve(s, pid)
    refused(s, "does not match the row", ELEMENT, key="close_price", version=before.version + 1, name=before.name,
            domain=before.domain, data_type=before.data_type, definition="Changed.", aliases=list(before.aliases),
            unit=before.unit, by=OWNER, proposal=pid)


def test_an_alias_another_element_holds_cannot_be_approved(s, upload):
    pid = propose(s, upload, aliases=["closing_price"])  # close_price's alias
    approve(s, pid)
    refused(s, "already held by close_price", ELEMENT, **{**element(aliases=["closing_price"]), "version": 1, "by": OWNER,
                                                         "proposal": pid})


def test_an_alias_the_element_already_has_is_not_a_conflict(s, upload):
    before = GlossaryRepository(s).get_current("close_price")
    pid = propose(s, upload, label="update", base_version=before.version, key="close_price", name=before.name,
                  domain=before.domain, data_type=before.data_type, definition="Changed.", aliases=list(before.aliases),
                  unit=before.unit)
    approve(s, pid)
    s.execute(text(ELEMENT), {"key": "close_price", "version": before.version + 1, "name": before.name,
                              "domain": before.domain, "data_type": before.data_type, "definition": "Changed.",
                              "aliases": list(before.aliases), "unit": before.unit, "by": OWNER, "proposal": pid})


# ---------------------------------------------------------------- the upload record


def test_an_upload_record_is_append_only(s, upload):
    refused(s, "append-only", "UPDATE glossary_uploads SET file_name = 'x' WHERE id = :i", i=upload)
    refused(s, "append-only", "DELETE FROM glossary_uploads WHERE id = :i", i=upload)
    refused(s, "TRUNCATE refused", "TRUNCATE glossary_uploads")


def test_an_upload_record_names_who_and_what(s):
    refused(s, "glossary_uploads", UPLOAD, by=" ", name="a.csv", sha=hashlib.sha256(b"x").hexdigest(), counts="{}",
            rows="[]")
    refused(s, "glossary_uploads", UPLOAD, by=OWNER, name="a.csv", sha="nothex", counts="{}", rows="[]")
