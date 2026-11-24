"""Phase 7.15 T2: the report per data owner and data steward (plan docs/plans/phase-7.15.md, 2.2).

The numbers are counted by code from the tables (each is checked here against its own SQL); a report is append-only and
versioned; the narrative (ReportSummariserAgent, A1, read-only) is separate, logged, and never changes a number: a narrative
that states a figure the report does not hold is refused and the report is stored without it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from agents.report_summariser import ReportSummariserAgent
from core.repositories.tables import ProposalLogEntry, ReportRow
from core.steps.reports import (
    Period,
    ReportRepository,
    build_numbers,
    figure_not_in_numbers,
    report_input,
    subjects,
    write_report,
)
from ports.config import Settings
from ports.llm.base import LLMUnavailable
from ports.llm.mock import MockProvider
from ports.storage.local import LocalBlobStore
from tests.configured import VOLUME_RULE, loadable_feed, volume_csv
from tests.reporting import loaded_on
from tests.step_chains import end_read
from tests.stand_in_agents import FixedProvider
from tests.test_exception_rerun import group_of, rerun, volume_template, waived  # noqa: F401
from tests.test_exceptions_view import deliver, world  # noqa: F401 (world is a fixture)

DAY = date(2026, 11, 2)
PERIOD = Period.daily(DAY)
SETTINGS = Settings(_env_file=None)


def at(day: str, hour: int) -> datetime:
    return datetime.fromisoformat(day).replace(hour=hour, tzinfo=UTC)


@pytest.fixture
def feeds(db_session, glossary):
    mine, mine_live = loadable_feed(db_session, "syn_report_mine")  # owner dev-data-owner
    other, other_live = loadable_feed(db_session, "syn_report_other", owner="dev-operator")
    return mine, mine_live.id, other, other_live.id


def sql(session, query: str, **params):
    return session.execute(text(query), params).all()


def numbers_of(session, subject="dev-data-owner", role="data_owner", period=PERIOD):
    return build_numbers(session, subject_id=subject, role=role, period=period, settings=SETTINGS)


@pytest.fixture
def agent(tmp_path):
    return ReportSummariserAgent(provider=MockProvider(), blob_store=LocalBlobStore(tmp_path / "agent-blob"))


def a_day(session, feeds):
    mine, live, other, other_live = feeds
    loaded_on(session, mine, live, at("2026-11-02", 8), rows=10)  # released, 100.0
    loaded_on(session, mine, live, at("2026-11-02", 9), rows=10, flags={VOLUME_RULE: 4})  # held, 60.0
    loaded_on(session, mine, live, at("2026-11-03", 8), rows=10)  # the next day: not in the period
    loaded_on(session, other, other_live, at("2026-11-02", 10), rows=20, flags={VOLUME_RULE: 2})  # another owner's


# ---------------------------------------------------------------- the numbers equal the tables


def test_the_owners_numbers_are_the_tables_counted_by_the_period(db_session, feeds):
    a_day(db_session, feeds)
    totals = numbers_of(db_session).totals
    [row] = sql(db_session, "SELECT count(*), count(*) FILTER (WHERE status = 'released'), "
                            "count(*) FILTER (WHERE status = 'held') FROM loads WHERE feed_id = :f AND purpose = 'scheduled' "
                            "AND received_at >= '2026-11-02' AND received_at < '2026-11-03'", f=feeds[0])
    assert (totals.feeds, totals.loads, totals.released, totals.held) == (1, row[0], row[1], row[2]) == (1, 2, 1, 1)
    [cert] = sql(db_session, "SELECT sum(c.rows), sum(c.flagged_rows), avg(c.trust_score) FROM load_certifications c "
                             "JOIN loads l ON l.id = c.load_id WHERE l.feed_id = :f AND l.received_at >= '2026-11-02' "
                             "AND l.received_at < '2026-11-03'", f=feeds[0])
    assert (totals.rows, totals.flagged_rows, totals.average_trust_score) == (cert[0], cert[1], Decimal("80.0")) == (
        20, 4, Decimal("80.0")) and cert[2] == Decimal("80")
    [feed] = numbers_of(db_session).feeds
    assert (feed.feed_code, feed.loads, feed.held, feed.flagged_rows) == ("syn_report_mine", 2, 1, 4)


def test_a_data_owner_sees_their_feeds_and_a_data_steward_all_of_them(db_session, feeds):
    a_day(db_session, feeds)
    assert [f.feed_code for f in numbers_of(db_session).feeds] == ["syn_report_mine"]
    assert [f.feed_code for f in numbers_of(db_session, "dev-operator").feeds] == ["syn_report_other"]
    steward = numbers_of(db_session, "dev-data-steward", "data_steward")
    assert [f.feed_code for f in steward.feeds] == ["syn_report_mine", "syn_report_other"]
    assert steward.totals.loads == 3 and steward.totals.rows == 40


def test_a_day_with_nothing_is_a_report_of_zeros_not_a_missing_one(db_session, feeds):
    empty = numbers_of(db_session, period=Period.daily(date(2026, 1, 5)))
    assert (empty.totals.loads, empty.totals.average_trust_score, empty.patterns, empty.referred_groups) == (0, None, [], [])
    assert [f.feed_code for f in empty.feeds] == ["syn_report_mine"]


def test_a_monthly_report_is_the_month(db_session, feeds):
    a_day(db_session, feeds)
    month = numbers_of(db_session, period=Period.monthly(2026, 11))
    assert (month.totals.loads, month.period.start, month.period.end) == (3, "2026-11-01", "2026-12-01")
    assert numbers_of(db_session, period=Period.monthly(2026, 10)).totals.loads == 0


def test_drift_events_and_patterns_are_counted_from_the_loads(db_session, feeds):
    mine, live, *_ = feeds
    drift = {"drift": {"new_columns": ["x"], "missing_columns": [], "type_changes": [], "order_changed": False}}
    loaded_on(db_session, mine, live, at("2026-11-02", 8), detail=drift)
    for day in ("2026-10-12", "2026-10-19", "2026-10-26"):  # three Mondays before the report's own day
        loaded_on(db_session, mine, live, at(day, 8), flags={VOLUME_RULE: 1})
    loaded_on(db_session, mine, live, at("2026-11-02", 11), flags={VOLUME_RULE: 1})  # the fourth, in the period
    numbers = numbers_of(db_session)
    assert numbers.drift_events == 1
    [pattern] = [p for p in numbers.patterns if p.dimension == "weekday"]
    assert (pattern.rule_key, pattern.value, pattern.failing_loads) == (VOLUME_RULE, "Monday", 4)


# ---------------------------------------------------------------- exceptions, waivers, reruns, referrals, decisions


def today_period():
    return Period.daily(datetime.now(UTC).date())


def test_fixes_waivers_reruns_and_decisions_are_counted_from_the_tables(world):
    volume_template(world)
    held = deliver(world, volume_csv(10, 2))
    waived(world, held.id, VOLUME_RULE)
    rerun(world, held)
    numbers = numbers_of(world.session, period=today_period())
    assert (numbers.waivers, numbers.reruns.released, numbers.reruns.still_held) == (1, 1, 0)
    assert (numbers.exceptions.resolved_by_hand, numbers.exceptions.resolved_by_agent) == (1, 0)
    assert numbers.exceptions.open_now == 0
    steward = numbers_of(world.session, "dev-data-steward", "data_steward", today_period())
    [(approved, rejected)] = sql(world.session,
        "SELECT count(*) FILTER (WHERE decision = 'approved'), count(*) FILTER (WHERE decision = 'rejected') FROM approvals "
        "WHERE decided_by = 'dev-data-steward' AND created_at >= :s AND created_at < :e",
        s=today_period().start, e=today_period().end)
    assert (steward.decisions.approved, steward.decisions.rejected) == (approved, rejected) and approved >= 2
    assert steward.decisions.by_kind["exception_fix"] == 1
    assert numbers.decisions is None  # an owner's report has no decisions block


def test_a_group_referred_twice_appears_once_in_the_report(world):
    from tests.test_patterns import refer

    first = deliver(world, volume_csv(10, 2))
    refer(world, first.id, VOLUME_RULE, "First.")
    second = deliver(world, volume_csv(10, 3))
    refer(world, second.id, VOLUME_RULE, "Second.")
    numbers = numbers_of(world.session, period=today_period())
    assert [(g.rule_key, g.reason) for g in numbers.referred_groups] == [(VOLUME_RULE, "Second.")]
    assert numbers.feeds[0].referred_groups == 1 and numbers.exceptions.open_now == 0


# ---------------------------------------------------------------- stored, append-only, versioned


def written(session, period=PERIOD, agent=None, **kw):
    plan = report_input(session, subject_id="dev-data-owner", role="data_owner", period=period, settings=SETTINGS)
    end_read(session)
    return write_report(session, plan=plan, agent=agent, generated_by="dev-operator", **kw)


def stored(session, result) -> ReportRow:
    return session.get(ReportRow, result.report_id)


def test_a_report_is_stored_with_its_numbers_and_the_hash_the_database_made(db_session, feeds):
    a_day(db_session, feeds)
    result = written(db_session)
    row = stored(db_session, result)
    assert result.outcome == "report_written_without_narrative" and result.reason == "not_asked"
    assert (row.kind, row.version, row.narrative_status, row.generated_by) == ("daily", 1, "none", "dev-operator")
    assert row.numbers == numbers_of(db_session).model_dump(mode="json")
    [(sha,)] = sql(db_session, "SELECT encode(sha256(convert_to(numbers::text, 'UTF8')), 'hex') FROM reports WHERE id = :i",
                   i=row.id)
    assert row.numbers_sha256 == sha


def test_a_report_is_made_once_by_the_jobs_and_a_new_version_only_when_asked(db_session, feeds):
    a_day(db_session, feeds)
    first = written(db_session)
    again = written(db_session)
    assert again.outcome == "report_exists" and again.report_id == first.report_id
    second = written(db_session, new_version=True)
    assert stored(db_session, second).version == 2
    assert stored(db_session, second).numbers_sha256 == stored(db_session, first).numbers_sha256  # same numbers
    assert [r.version for r in ReportRepository(db_session).list_for("dev-data-owner", "data_owner")] == [2, 1]


def test_who_gets_a_report_is_every_registered_owner_and_every_steward(db_session, feeds):
    assert subjects(db_session, stewards=["dev-data-steward", "dev-operator"]) == [
        ("dev-data-owner", "data_owner"), ("dev-operator", "data_owner"),
        ("dev-data-steward", "data_steward"), ("dev-operator", "data_steward")]


# ---------------------------------------------------------------- the narrative never changes a number


def test_the_narrative_is_written_logged_once_and_the_numbers_are_as_without_it(db_session, feeds, agent):
    a_day(db_session, feeds)
    plain = written(db_session)
    calls = db_session.scalar(select(func.count()).select_from(ProposalLogEntry))
    result = written(db_session, agent=agent, new_version=True)
    row = stored(db_session, result)
    assert result.outcome == "report_written" and row.narrative_status == "written" and row.narrative
    assert db_session.scalar(select(func.count()).select_from(ProposalLogEntry)) == calls + 1
    log = db_session.get(ProposalLogEntry, row.narrative_log_id)
    assert (log.agent_name, log.provider) == ("ReportSummariserAgent", "mock")
    assert row.numbers == stored(db_session, plain).numbers and row.numbers_sha256 == stored(db_session, plain).numbers_sha256
    assert "2 loads" in row.narrative and "1 held" in row.narrative


def lying(narrative: str):
    return ReportSummariserAgent(provider=FixedProvider({"narrative": narrative}),
                                 blob_store=LocalBlobStore(__import__("tempfile").mkdtemp()))


def test_a_narrative_that_states_a_figure_the_report_does_not_hold_is_refused_and_the_numbers_stand(db_session, feeds):
    a_day(db_session, feeds)
    plain = written(db_session)
    result = written(db_session, agent=lying("There were 42 loads and 7 held, a record."), new_version=True)
    row = stored(db_session, result)
    assert (result.outcome, result.reason, result.figure) == ("report_written_without_narrative", "narrative_refused", "42")
    assert (row.narrative_status, row.narrative, row.narrative_log_id is not None) == ("refused", None, True)
    assert "42" in row.narrative_note
    assert row.numbers == stored(db_session, plain).numbers  # what the narrative said changed nothing


def test_a_narrative_may_state_the_reports_own_numbers_and_dates(db_session, feeds):
    a_day(db_session, feeds)
    ok = written(db_session, agent=lying("On 2026-11-02 two loads arrived: 2 loads, 1 held, trust 80.0."))
    assert stored(db_session, ok).narrative_status == "written"


def test_the_figure_check_reads_digits_with_a_point_and_sets_the_reports_own_dates_aside(db_session, feeds):
    a_day(db_session, feeds)
    numbers = numbers_of(db_session).model_dump(mode="json")
    assert figure_not_in_numbers("2 loads, 1 held, 80.0 trust, on 2026-11-02", numbers) is None
    assert figure_not_in_numbers("trust 80.5", numbers) == "80.5"
    assert figure_not_in_numbers("a 3rd of them", numbers) == "3"
    assert figure_not_in_numbers("no figures at all", numbers) is None


def test_a_model_that_is_down_gives_a_report_without_a_narrative(db_session, feeds, tmp_path):
    a_day(db_session, feeds)
    down = ReportSummariserAgent(provider=FixedProvider(error=LLMUnavailable("down")),
                                 blob_store=LocalBlobStore(tmp_path / "b"))
    result = written(db_session, agent=down, no_narrative_on=(LLMUnavailable,))
    assert (result.outcome, result.reason) == ("report_written_without_narrative", "no_narrative")
    assert stored(db_session, result).narrative_status == "none"
