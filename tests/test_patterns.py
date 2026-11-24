"""Phase 7.15 T1: recurring failure patterns, and the groups referred to the provider (plan docs/plans/phase-7.15.md, 2.1).

A pattern is a rule failing (flagging at least one row of a loaded, released or held scheduled load) on the same weekday,
day of month or provider date in at least N loads of the window. Deterministic: no model. Evidence is the dates.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from core.steps.patterns import find_patterns, referred_groups
from tests.configured import VOLUME_RULE, loadable_feed
from tests.reporting import loaded_on
from tests.test_exception_rerun import STEWARD, group_of
from tests.test_exceptions_view import deliver, world  # noqa: F401 (world is a fixture)

AS_OF = datetime(2026, 11, 30, 12, tzinfo=UTC)
RULE = "price_not_negative"


def at(day: str, hour: int = 8) -> datetime:
    return datetime.fromisoformat(day).replace(hour=hour, tzinfo=UTC)


@pytest.fixture
def feed(db_session, glossary):
    feed_id, live = loadable_feed(db_session, "syn_patterns")
    return feed_id, live.id


def found(session, feed_id, **kw):
    return find_patterns(session, feed_ids=[feed_id], as_of=AS_OF, **kw)


def test_the_same_rule_failing_on_three_mondays_is_a_weekday_pattern_with_its_dates_as_evidence(db_session, feed):
    feed_id, live = feed
    for day in ("2026-11-02", "2026-11-09", "2026-11-16"):  # Mondays
        loaded_on(db_session, feed_id, live, at(day), flags={RULE: 2})
    loaded_on(db_session, feed_id, live, at("2026-11-23"), flags={})  # a Monday that did not fail
    [pattern] = [p for p in found(db_session, feed_id) if p.dimension == "weekday"]
    assert (pattern.rule_key, pattern.value, pattern.failing_loads, pattern.loads_in_window) == (RULE, "Monday", 3, 4)
    assert [(e.received_on, e.flagged_rows) for e in pattern.evidence] == [
        (date(2026, 11, 2), 2), (date(2026, 11, 9), 2), (date(2026, 11, 16), 2)]
    assert pattern.feed_code == "syn_patterns"


def test_two_failing_loads_are_not_a_pattern_and_a_clean_rule_is_not_one(db_session, feed):
    feed_id, live = feed
    for day in ("2026-11-02", "2026-11-09"):
        loaded_on(db_session, feed_id, live, at(day), flags={RULE: 1})
    for day in ("2026-11-16", "2026-11-23"):
        loaded_on(db_session, feed_id, live, at(day), flags={})
    assert [p for p in found(db_session, feed_id) if p.rule_key == RULE] == []


def test_the_threshold_is_a_setting(db_session, feed):
    feed_id, live = feed
    for day in ("2026-11-02", "2026-11-09"):
        loaded_on(db_session, feed_id, live, at(day), flags={RULE: 1})
    assert [(p.dimension, p.failing_loads) for p in found(db_session, feed_id, min_loads=2)
            if p.dimension == "weekday"] == [("weekday", 2)]


def test_the_same_day_of_the_month_is_a_pattern_whatever_the_weekday(db_session, feed):
    feed_id, live = feed
    for day in ("2026-09-15", "2026-10-15", "2026-11-15"):  # a Tuesday, a Thursday, a Sunday
        loaded_on(db_session, feed_id, live, at(day), flags={RULE: 3})
    patterns = [p for p in found(db_session, feed_id) if p.rule_key == RULE]
    assert [(p.dimension, p.value, p.failing_loads) for p in patterns] == [("day_of_month", "15", 3)]


def test_the_same_provider_date_in_three_loads_is_a_pattern_of_that_date(db_session, feed):
    feed_id, live = feed
    for day in ("2026-11-03", "2026-11-06", "2026-11-11"):  # a Tuesday, a Friday, a Wednesday
        loaded_on(db_session, feed_id, live, at(day), flags={RULE: 1}, provider_date=date(2026, 10, 30))
    patterns = [p for p in found(db_session, feed_id) if p.rule_key == RULE]
    assert [(p.dimension, p.value, p.failing_loads) for p in patterns] == [("provider_date", "2026-10-30", 3)]
    assert [e.provider_date for e in patterns[0].evidence] == [date(2026, 10, 30)] * 3


def test_a_load_outside_the_window_and_a_rerun_do_not_count(db_session, feed):
    feed_id, live = feed
    loaded_on(db_session, feed_id, live, at("2026-05-04"), flags={RULE: 1})  # a Monday, long before the window
    loaded_on(db_session, feed_id, live, at("2026-11-02"), flags={RULE: 1})
    loaded_on(db_session, feed_id, live, at("2026-11-09"), flags={RULE: 1})
    held = loaded_on(db_session, feed_id, live, at("2026-11-17"), flags={RULE: 8})  # a Tuesday, held (20.0)
    assert held.status == "held"
    loaded_on(db_session, feed_id, live, at("2026-11-16"), flags={RULE: 1}, rerun_of=held.id,
              file_ref=held.file_ref)  # a rerun of the held load's file, received on a Monday
    assert [p for p in found(db_session, feed_id) if p.dimension == "weekday" and p.value == "Monday"] == []


def test_patterns_are_deterministic_and_each_feed_stands_alone(db_session, feed):
    feed_id, live = feed
    for day in ("2026-11-02", "2026-11-09", "2026-11-16"):
        loaded_on(db_session, feed_id, live, at(day), flags={RULE: 2, "other_rule": 1})
    first, second = found(db_session, feed_id), found(db_session, feed_id)
    assert first == second and [p.rule_key for p in first if p.dimension == "weekday"] == ["other_rule", RULE]
    other, other_live = loadable_feed(db_session, "syn_patterns_other")
    assert find_patterns(db_session, feed_ids=[other], as_of=AS_OF) == []


# ---------------------------------------------------------------- referred groups


def refer(world, load_id, rule, reason="Ask the provider."):
    from core.steps.exceptions import add_manual_fix, decide_exception_fixes

    key = group_of(world, load_id, rule).group_key
    add_manual_fix(world.session, load_id=load_id, group_key=key, action="refer", reason=reason, user=STEWARD)
    decide_exception_fixes(world.session, load_id=load_id, group_keys=[key], user=STEWARD, decision="approved")


def test_a_referred_group_appears_once_however_often_it_was_referred(world):
    from tests.configured import volume_csv

    first = deliver(world, volume_csv(10, 2))
    refer(world, first.id, VOLUME_RULE, "First load.")
    second = deliver(world, volume_csv(10, 3))
    refer(world, second.id, VOLUME_RULE, "Second load.")
    [group] = referred_groups(world.session, feed_ids=[world.feed])
    assert (group.feed_code, group.rule_key, group.pattern, group.reason, group.load_id) == (
        "syn_exceptions", VOLUME_RULE, "9", "Second load.", second.id)
    assert referred_groups(world.session, feed_ids=[]) == []


def test_a_later_waiver_ends_the_referral(world):
    from core.steps.exceptions import add_manual_fix, decide_exception_fixes
    from tests.configured import volume_csv
    from tests.test_exception_rerun import OWNER

    first = deliver(world, volume_csv(10, 2))
    refer(world, first.id, VOLUME_RULE)
    second = deliver(world, volume_csv(10, 3))
    key = group_of(world, second.id, VOLUME_RULE).group_key
    add_manual_fix(world.session, load_id=second.id, group_key=key, action="waive", reason="Real.", user=STEWARD)
    decide_exception_fixes(world.session, load_id=second.id, group_keys=[key], user=STEWARD, decision="approved")
    decide_exception_fixes(world.session, load_id=second.id, group_keys=[key], user=OWNER, decision="approved")
    assert referred_groups(world.session, feed_ids=[world.feed]) == []
