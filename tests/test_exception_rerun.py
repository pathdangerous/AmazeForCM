"""Phase 7.14 T3: bulk decisions on exception groups, and the rerun of a held load (plan docs/plans/phase-7.14.md,
sections 2.4 to 2.6). A rerun is a NEW scheduled load of the held load's own file, with `rerun_of_load_id` and a reason.
Step 12 skips the resend check against that one load only. The engine applies the feed's current waivers to the new load's
records (waived, never flags); the trust score counts them as unflagged and the certification shows them apart. No record
of the held load is ever updated.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from core.repositories.certifications import LoadCertificationRepository
from core.repositories.errors import NotAnApprover
from core.repositories.loads import LoadRepository
from core.repositories.tables import StandardRecordRow
from core.steps.errors import StepInputError
from core.steps.exceptions import (
    GroupDecisionRefused,
    add_manual_fix,
    decide_exception_fixes,
    load_exceptions,
    start_rerun,
)
from core.steps.s10_transform import decide_transformation
from tests.conftest import dev_user_for
from tests.configured import VOLUME_RULE, go_live, volume_csv
from tests.step_chains import run_received
from tests.test_exception_fixes import (  # noqa: F401 (agent is a fixture)
    ID_RULE,
    NEGATIVE_RULE,
    a_held_load_with_three_kinds_of_group,
    agent,
    propose,
)
from tests.test_exceptions_view import deliver, world  # noqa: F401 (world is a fixture)

STEWARD, OWNER = dev_user_for("data_steward"), dev_user_for("data_owner")


@pytest.fixture(autouse=True)
def template(world):
    """Step 12 compares a delivery with the feed's latest profile: the one the volume file matches."""
    from core.domain.profiling import FileColumn
    from tests.onboarded import profiled

    profiled(world.session, world.feed, columns=[
        FileColumn(name=n, type=t, null_count=0, null_rate=0.0, distinct_count=1)
        for n, t in (("instr_id", "VARCHAR"), ("trade_date", "DATE"), ("traded_qty", "BIGINT"))])


def group_of(world, load_id, rule):
    return next(g for g in load_exceptions(world.session, load_id).groups if g.rule_key == rule)


def waived(world, load_id, rule):
    """A waiver of the rule's group, added by hand and approved by the steward and the registered owner."""
    key = group_of(world, load_id, rule).group_key
    fix = add_manual_fix(world.session, load_id=load_id, group_key=key, action="waive", reason="Real values.", user=STEWARD)
    decide_exception_fixes(world.session, load_id=load_id, group_keys=[key], user=STEWARD, decision="approved")
    decide_exception_fixes(world.session, load_id=load_id, group_keys=[key], user=OWNER, decision="approved")
    return fix


def records(world, load_id):
    return [(r.row_number, r.values, r.flags, r.waived) for r in world.session.scalars(
        select(StandardRecordRow).where(StandardRecordRow.load_id == load_id).order_by(StandardRecordRow.row_number))]


def rerun(world, held, reason="Waived the large volumes."):
    started = start_rerun(world.session, load_id=held.id, reason=reason, user=STEWARD)
    assert started.outcome == "rerun_started", started
    return started, run_received(world.session, world.blobs, started.load_id, context=world.context)


# ---------------------------------------------------------------- bulk decisions


def test_a_bulk_decision_decides_each_selected_groups_fix_in_one_transaction(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    propose(world, agent, held.id)
    groups = {g.rule_key: g for g in load_exceptions(world.session, held.id).groups}
    keys = [g.group_key for g in groups.values()]
    result = decide_exception_fixes(world.session, load_id=held.id, group_keys=keys, user=STEWARD, decision="approved")
    assert result.outcome == "fixes_decided"
    assert {d.group_key: d.state for d in result.decided} == {
        groups[VOLUME_RULE].group_key: "referred", groups[NEGATIVE_RULE].group_key: "waiting_for_owner",
        groups[ID_RULE].group_key: "transformation_approved"}
    owner = decide_exception_fixes(world.session, load_id=held.id, group_keys=[groups[NEGATIVE_RULE].group_key],
                                   user=OWNER, decision="approved")
    assert owner.decided[0].state == "waived"


def test_one_refused_group_refuses_the_whole_batch_and_names_it(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    propose(world, agent, held.id)
    groups = {g.rule_key: g for g in load_exceptions(world.session, held.id).groups}
    with pytest.raises(GroupDecisionRefused, match=groups[NEGATIVE_RULE].group_key):
        decide_exception_fixes(world.session, load_id=held.id, user=OWNER, decision="approved",
                               group_keys=[groups[NEGATIVE_RULE].group_key])  # the steward has not decided yet
    with pytest.raises(GroupDecisionRefused, match="0000000000000000"):
        decide_exception_fixes(world.session, load_id=held.id, user=STEWARD, decision="approved",
                               group_keys=[groups[VOLUME_RULE].group_key, "0000000000000000"])
    assert group_of(world, held.id, VOLUME_RULE).state == "fix_pending"  # the first group was not decided either


def test_nothing_selected_is_typed_and_a_rejection_needs_a_note(world, agent):
    held = a_held_load_with_three_kinds_of_group(world)
    propose(world, agent, held.id)
    assert decide_exception_fixes(world.session, load_id=held.id, group_keys=[], user=STEWARD,
                                  decision="approved").outcome == "nothing_selected"
    key = group_of(world, held.id, VOLUME_RULE).group_key
    with pytest.raises(StepInputError):
        decide_exception_fixes(world.session, load_id=held.id, group_keys=[key], user=STEWARD, decision="rejected")


# ---------------------------------------------------------------- the rerun


def test_a_waiver_is_applied_to_the_reruns_records_and_the_trust_score_counts_it_as_unflagged(world):
    held = deliver(world, volume_csv(10, 2))  # 80.0, held
    before = records(world, held.id)
    waived(world, held.id, VOLUME_RULE)
    started, (arrival, finished) = rerun(world, held)
    new = finished.run.load
    assert (new.status, new.purpose, new.rerun_of_load_id, new.rerun_reason) == (
        "released", "scheduled", held.id, "Waived the large volumes.")
    certification = LoadCertificationRepository(world.session).for_load(new.id)
    assert (certification.trust_score, certification.flagged_rows, certification.waived_rows,
            certification.waived_counts) == (Decimal("100.0"), 0, 2, {VOLUME_RULE: 2})
    after = records(world, new.id)
    assert [(r[2], r[3]) for r in after[:2]] == [([], [VOLUME_RULE])] * 2 and all(r[3] == [] for r in after[2:])
    assert records(world, held.id) == before  # the held load's records are as they were
    assert LoadRepository(world.session).get(held.id).status == "held"


def test_a_rerun_is_recorded_with_its_reason_and_what_step_12_decided(world):
    held = deliver(world, volume_csv(10, 2))
    waived(world, held.id, VOLUME_RULE)
    started, (arrival, finished) = rerun(world, held, "Large volumes are real.")
    assert arrival.outcome == "on_time_complete"
    detail = finished.run.load.arrival_detail["rerun"]
    assert (detail["of"], detail["reason"], detail["sha256_matches"]) == (str(held.id), "Large volumes are real.", True)
    assert finished.run.load.content_sha256 == LoadRepository(world.session).get(held.id).content_sha256


def test_a_plain_delivery_of_the_same_bytes_is_still_quarantined_as_resent(world):
    held = deliver(world, volume_csv(10, 2))
    waived(world, held.id, VOLUME_RULE)
    _, (_, released) = rerun(world, held)
    assert released.run.load.status == "released"
    # the released rerun's hash counts: only the held load's own was left out, and only for its rerun
    again = LoadRepository(world.session).create(feed_id=world.feed, file_ref=held.file_ref, purpose="scheduled",
                                                 go_live_id=world.live.id)
    arrival, ended = run_received(world.session, world.blobs, again.id, context=world.context)
    assert arrival.outcome == "resent_quarantined" and ended is None


def test_a_waiver_that_leaves_rows_flagged_holds_the_rerun_again(world):
    held = a_held_load_with_three_kinds_of_group(world)  # 3 of 6 rows flagged: 50.0
    waived(world, held.id, VOLUME_RULE)
    waived(world, held.id, NEGATIVE_RULE)
    _, (_, finished) = rerun(world, held)
    certification = finished.certification
    assert finished.run.load.status == "held" and certification.outcome == "held_consumers_warned"
    assert (certification.trust_score, certification.flagged_rows) == (Decimal("83.3"), 1)
    assert LoadCertificationRepository(world.session).for_load(finished.run.load.id).waived_rows == 2


def test_nothing_to_rerun_for_a_load_that_is_not_held_or_has_nothing_resolved(world):
    held = deliver(world, volume_csv(10, 2))
    none = start_rerun(world.session, load_id=held.id, reason="Try.", user=STEWARD)
    assert (none.outcome, none.reason) == ("nothing_to_rerun", "nothing_resolved_since")
    released = deliver(world, volume_csv(10, 1))
    other = start_rerun(world.session, load_id=released.id, reason="Try.", user=STEWARD)
    assert (other.outcome, other.reason) == ("nothing_to_rerun", "not_held")


def test_a_second_rerun_needs_something_resolved_since_the_first(world):
    held = a_held_load_with_three_kinds_of_group(world)
    waived(world, held.id, VOLUME_RULE)
    rerun(world, held)  # held again
    again = start_rerun(world.session, load_id=held.id, reason="Again.", user=STEWARD)
    assert (again.outcome, again.reason) == ("nothing_to_rerun", "nothing_resolved_since")
    waived(world, held.id, NEGATIVE_RULE)
    assert start_rerun(world.session, load_id=held.id, reason="Again.", user=STEWARD).outcome == "rerun_started"


def test_a_load_in_progress_blocks_a_rerun(world):
    held = deliver(world, volume_csv(10, 2))
    waived(world, held.id, VOLUME_RULE)
    LoadRepository(world.session).create(feed_id=world.feed, file_ref="raw/other.csv", purpose="scheduled",
                                          go_live_id=world.live.id)
    assert start_rerun(world.session, load_id=held.id, reason="Try.", user=STEWARD).outcome == "rerun_in_progress"


def test_a_transformation_fix_needs_the_owners_go_live_before_the_rerun(world):
    held = deliver(world, volume_csv(10, 2))
    key = group_of(world, held.id, VOLUME_RULE).group_key
    fix = add_manual_fix(world.session, load_id=held.id, group_key=key, action="transformation", reason="Spaces.",
                         user=STEWARD, steps=[{"op": "trim"}])
    decide_transformation(world.session, proposal_id=fix.id, user=STEWARD, decision="approved")
    assert start_rerun(world.session, load_id=held.id, reason="Trimmed.", user=STEWARD).outcome == "rerun_needs_go_live"
    go_live(world.session, world.feed)  # step 11: the owner approves the new configuration
    assert start_rerun(world.session, load_id=held.id, reason="Trimmed.", user=STEWARD).outcome == "rerun_started"


def test_only_a_steward_starts_a_rerun(world):
    held = deliver(world, volume_csv(10, 2))
    with pytest.raises(NotAnApprover):
        start_rerun(world.session, load_id=held.id, reason="Try.", user=OWNER)


def test_the_held_loads_records_cannot_be_updated(world):
    held = deliver(world, volume_csv(10, 2))
    with pytest.raises(DBAPIError, match="append-only"):
        with world.session.begin_nested():
            world.session.execute(text("UPDATE standard_records SET waived = '{x}' WHERE load_id = :l"), {"l": held.id})
