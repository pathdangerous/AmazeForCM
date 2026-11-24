"""Migration 0034's guards (phase 7.14; plan docs/plans/phase-7.14.md, sections 2.4, 2.6 and 2.7), by raw SQL and the
repositories: the exception_fix proposal, exception_resolutions, the waiver owner's step, waived records, the
certification's waived rows and a load rerun. Each refusal is checked by its message. Against 0033 every test fails.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core.repositories.proposals import ProposalRepository
from tests.conftest import dev_user_for
from tests.configured import VOLUME_RULE, logged_call, volume_csv
from tests.test_exceptions_view import deliver, world  # noqa: F401 (world is a fixture)

OWNER, STEWARD = dev_user_for("data_owner"), dev_user_for("data_steward")
WAIVE_ROLES, REFER_ROLES = ["data_steward", "data_owner"], ["data_steward"]


def refused(session, match: str, sql: str, **params):
    with pytest.raises(DBAPIError, match=match):
        with session.begin_nested():
            session.execute(text(sql), params)


def fix_payload(load_id, *, action: str = "waive", **over) -> dict:
    return {"load_id": str(load_id), "rule_key": VOLUME_RULE, "element_keys": ["volume"], "pattern": "9",
            "group_key": "g9", "action": action, "reason": "Large volumes are real for this feed.", **over}


def a_fix(w, load_id, *, action: str = "waive", roles=None, manual: bool = False, **over):
    repo = ProposalRepository(w.session)
    payload = fix_payload(load_id, action=action, **over)
    roles = roles or (WAIVE_ROLES if action == "waive" else REFER_ROLES)
    if manual:
        return repo.create_manual(kind="exception_fix", payload=payload, required_approver_roles=roles,
                                  feed_id=w.feed, author="dev-data-steward")
    return repo.create(proposal_log_id=logged_call(w.session, "ExceptionFixAgent", "exception_fix").id,
                       kind="exception_fix", payload=payload, autonomy="A2", required_approver_roles=roles,
                       feed_id=w.feed)


def approved(w, proposal, *people):
    repo = ProposalRepository(w.session)
    for person in people:
        repo.record_decision(proposal.id, user=person, decision="approved")
    return repo.get(proposal.id)


RESOLVE = ("INSERT INTO exception_resolutions (feed_id, load_id, rule_key, element_keys, pattern, group_key, action, "
           "reason, proposal_id, decided_by, applied_by) VALUES (:feed, :load, :rule, CAST(:keys AS text[]), :pattern, "
           ":group, :action, :reason, :proposal, :by, :applied) RETURNING id")


def resolve_params(w, load_id, proposal, **over) -> dict:
    p = proposal.payload
    return {"feed": w.feed, "load": load_id, "rule": p["rule_key"], "keys": "{" + ",".join(p["element_keys"]) + "}",
            "pattern": p["pattern"], "group": p["group_key"], "reason": p["reason"], "proposal": proposal.id,
            "action": {"waive": "waived", "refer": "referred"}[p["action"]], "by": "dev-data-owner",
            "applied": "agent" if proposal.origin == "agent" else "manual", **over}


@pytest.fixture
def held(world):
    return deliver(world, volume_csv(10, 2))  # 80.0: held, 2 rows flagged by VOLUME_RULE


# ---------------------------------------------------------------- the exception_fix proposal


@pytest.mark.parametrize("over, roles", [
    ({"reason": " "}, WAIVE_ROLES),
    ({"reason": None}, WAIVE_ROLES),
    ({"action": None}, WAIVE_ROLES),
    ({"action": "delete"}, WAIVE_ROLES),
    ({"element_keys": "volume"}, WAIVE_ROLES),
    ({}, REFER_ROLES),  # a waiver needs the owner after the steward
    ({"action": "refer"}, WAIVE_ROLES),  # a referral is the steward's alone
], ids=["blank reason", "null reason", "null action", "unknown action", "keys not a list", "waive without the owner",
        "refer with the owner"])
def test_an_exception_fix_names_its_group_and_its_approvers_fit_the_action(world, held, over, roles):
    with pytest.raises(DBAPIError, match="ck_proposals_exception_fix"):
        with world.session.begin_nested():
            a_fix(world, held.id, roles=roles, **over)


def test_a_missing_payload_key_is_refused(world, held):
    payload = fix_payload(held.id)
    del payload["group_key"]
    refused(world.session, "ck_proposals_exception_fix",
            "INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
            "VALUES (:log, 'exception_fix', CAST(:p AS jsonb), 'A2', ARRAY['data_steward', 'data_owner'], :f)",
            log=logged_call(world.session, "ExceptionFixAgent", "exception_fix").id, p=json.dumps(payload), f=world.feed)


def test_one_pending_fix_per_group_of_a_load(world, held):
    a_fix(world, held.id)
    with pytest.raises(DBAPIError, match="uq_proposals_one_pending_exception_fix"):
        with world.session.begin_nested():
            a_fix(world, held.id, action="refer")


def test_a_person_may_add_an_exception_fix(world, held):
    assert a_fix(world, held.id, manual=True).origin == "manual"


# ---------------------------------------------------------------- the owner's step of a waiver (D5)


def test_the_owner_step_of_a_waiver_is_decided_only_by_the_registered_owner(world, held):
    fix = a_fix(world, held.id)
    repo = ProposalRepository(world.session)
    repo.record_decision(fix.id, user=STEWARD, decision="approved")
    operator = dev_user_for("backend_team").model_copy(update={"id": "dev-operator",
                                                               "roles": ["data_owner", "data_steward"]})
    with pytest.raises(DBAPIError, match="registered owner dev-data-owner, not dev-operator"):
        with world.session.begin_nested():
            repo.record_decision(fix.id, user=operator, decision="approved")
    assert approved(world, fix, OWNER).status == "approved"


# ---------------------------------------------------------------- exception_resolutions


def test_an_approved_waiver_is_recorded_once(world, held):
    fix = approved(world, a_fix(world, held.id), STEWARD, OWNER)
    world.session.execute(text(RESOLVE), resolve_params(world, held.id, fix))
    refused(world.session, "uq_exception_resolutions_proposal_id", RESOLVE, **resolve_params(world, held.id, fix))


def test_a_fix_is_recorded_only_once_every_approver_decided_it(world, held):
    fix = approved(world, a_fix(world, held.id), STEWARD)  # the owner has not decided
    refused(world.session, "with 1 of 2 approvals", RESOLVE, **resolve_params(world, held.id, fix, by="dev-data-steward"))


@pytest.mark.parametrize("over, match", [
    ({"pattern": "-9"}, "does not match its proposal"),
    ({"rule": "other_rule"}, "does not match its proposal"),
    ({"action": "referred"}, "does not match its proposal"),
    ({"keys": "{volume,price_date}"}, "does not match its proposal"),
    ({"reason": "Another reason."}, "does not match its proposal"),
    ({"applied": "manual"}, "so the fix was applied by agent"),
    ({"by": "dev-data-steward"}, "was decided last by dev-data-owner"),
])
def test_a_resolution_matches_its_proposal(world, held, over, match):
    fix = approved(world, a_fix(world, held.id), STEWARD, OWNER)
    refused(world.session, match, RESOLVE, **resolve_params(world, held.id, fix, **over))


def test_a_manual_fix_is_recorded_as_applied_by_hand(world, held):
    fix = approved(world, a_fix(world, held.id, action="refer", manual=True), STEWARD)
    world.session.execute(text(RESOLVE), resolve_params(world, held.id, fix, by="dev-data-steward"))
    assert world.session.scalar(text("SELECT applied_by FROM exception_resolutions WHERE proposal_id = :p"),
                                {"p": fix.id}) == "manual"


def test_only_a_held_loads_group_is_resolved(world):
    released = deliver(world, volume_csv(10, 1))  # 90.0: released
    fix = approved(world, a_fix(world, released.id, action="refer"), STEWARD)
    refused(world.session, "is not a held load of feed", RESOLVE,
            **resolve_params(world, released.id, fix, by="dev-data-steward"))


def test_resolutions_are_append_only(world, held):
    fix = approved(world, a_fix(world, held.id), STEWARD, OWNER)
    row = world.session.execute(text(RESOLVE), resolve_params(world, held.id, fix)).scalar_one()
    refused(world.session, "append-only", "UPDATE exception_resolutions SET reason = 'x' WHERE id = :i", i=row)
    refused(world.session, "append-only", "DELETE FROM exception_resolutions WHERE id = :i", i=row)


# ---------------------------------------------------------------- waived records (D3, D4)

RECORD = ("INSERT INTO standard_records (load_id, feed_id, row_number, \"values\", flags, waived) "
          "VALUES (:load, :feed, 1, CAST(:v AS jsonb), CAST(:flags AS text[]), CAST(:waived AS text[]))")


def a_received_load(world):
    from core.repositories.loads import LoadRepository

    return LoadRepository(world.session).create(feed_id=world.feed, file_ref="raw/x.csv", purpose="scheduled",
                                                go_live_id=world.live.id)


def test_a_waived_key_needs_a_current_waiver_matching_the_rows_pattern(world, held):
    load = a_received_load(world)
    params = {"load": load.id, "feed": world.feed, "v": json.dumps({"volume": "200000"}), "flags": "{}",
              "waived": "{" + VOLUME_RULE + "}"}
    refused(world.session, "no current waiver of feed", RECORD, **params)
    fix = approved(world, a_fix(world, held.id), STEWARD, OWNER)
    world.session.execute(text(RESOLVE), resolve_params(world, held.id, fix))
    refused(world.session, "no current waiver of feed", RECORD, **{**params, "v": json.dumps({"volume": "-5"})})
    world.session.execute(text(RECORD), params)  # pattern 9 matches the waiver


def test_a_later_referral_ends_the_waiver(world, held):
    load = a_received_load(world)
    waiver = approved(world, a_fix(world, held.id), STEWARD, OWNER)
    world.session.execute(text(RESOLVE), resolve_params(world, held.id, waiver))
    referral = approved(world, a_fix(world, held.id, action="refer", group_key="g9b"), STEWARD)
    world.session.execute(text(RESOLVE), resolve_params(world, held.id, referral, by="dev-data-steward"))
    refused(world.session, "no current waiver of feed", RECORD, load=load.id, feed=world.feed,
            v=json.dumps({"volume": "200000"}), flags="{}", waived="{" + VOLUME_RULE + "}")


def test_a_key_is_flagged_or_waived_never_both(world, held):
    load = a_received_load(world)
    fix = approved(world, a_fix(world, held.id), STEWARD, OWNER)  # a current waiver, so only the CHECK refuses
    world.session.execute(text(RESOLVE), resolve_params(world, held.id, fix))
    refused(world.session, "ck_standard_records_flagged_or_waived", RECORD, load=load.id, feed=world.feed,
            v=json.dumps({"volume": "1"}), flags="{" + VOLUME_RULE + "}", waived="{" + VOLUME_RULE + "}")


# ---------------------------------------------------------------- the certification's waived rows (D1, D2)


def test_a_certification_counts_waived_rows_as_the_database_recounts_them(world, held):
    from core.repositories.certifications import LoadCertificationRepository
    from tests.test_db_arrival_guards import CERT, cert_params, scheduled_loaded

    stored = LoadCertificationRepository(world.session).for_load(held.id)
    assert (stored.waived_rows, stored.waived_counts) == (0, {})  # a load from before any waiver
    load = scheduled_loaded(world.session, (world.feed, world.live.id), rows=10, flagged=2)
    with_waived = CERT.replace("threshold, outcome)", "threshold, outcome, waived_rows)").replace(
        ":threshold, :outcome)", ":threshold, :outcome, :waived)")
    refused(world.session, "has 0 waived rows", with_waived, **cert_params(load, world.feed, flagged=2, waived=1))
    world.session.execute(text(with_waived), cert_params(load, world.feed, flagged=2, waived=0))


# ---------------------------------------------------------------- a load rerun (2.6)

RERUN = ("INSERT INTO loads (feed_id, file_ref, purpose, go_live_id, rerun_of_load_id, rerun_reason) "
         "VALUES (:feed, :file, 'scheduled', :live, :of, :reason) RETURNING id")


def test_a_rerun_is_of_a_held_load_with_its_own_file_and_a_reason(world, held):
    params = {"feed": world.feed, "file": held.file_ref, "live": world.live.id, "of": held.id, "reason": "Waived."}
    refused(world.session, "ck_loads_rerun_has_reason", RERUN, **{**params, "reason": None})
    refused(world.session, "ck_loads_rerun_has_reason", RERUN, **{**params, "reason": " "})
    refused(world.session, "a rerun loads the held load's own file", RERUN, **{**params, "file": "raw/other.csv"})
    rerun = world.session.execute(text(RERUN), params).scalar_one()
    refused(world.session, "is set once", "UPDATE loads SET rerun_reason = 'Other.' WHERE id = :i", i=rerun)


def test_a_released_load_is_not_rerun(world):
    released = deliver(world, volume_csv(10, 0))
    refused(world.session, "a rerun is a scheduled load of a held load", RERUN, feed=world.feed,
            file=released.file_ref, live=world.live.id, of=released.id, reason="Again.")


def test_an_unknown_load_is_not_rerun(world):
    refused(world.session, "fk_loads_rerun_of_load_id_loads|a rerun is a scheduled load", RERUN, feed=world.feed,
            file="raw/x.csv", live=world.live.id, of=uuid.uuid4(), reason="Again.")
