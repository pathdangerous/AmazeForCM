"""Phase 7.15 T3: the daily and monthly report jobs (plan docs/plans/phase-7.15.md, 2.3).

One job, once a day: yesterday's daily report for every data owner and data steward, and on the 1st last month's monthly
report. It skips a report that exists, tells each subject through the Notifier (in-app; email stays a stub), and one
subject's failure does not stop the others.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from agents.report_summariser import ReportSummariserAgent
from apps.api.jobs import REPORT_JOB_ID, ReportJob, register_report_job
from core.repositories.tables import ReportRow
from ports.config import Settings
from ports.db import get_sessionmaker
from ports.llm.mock import MockProvider
from ports.notify.inapp import NotificationRow
from ports.storage.local import LocalBlobStore
from tests.configured import VOLUME_RULE
from tests.reporting import loaded_on
from tests.test_commit_visibility import committed, own_schema  # noqa: F401 (own_schema is a fixture)
from tests.test_reports import at, feeds  # noqa: F401 (feeds is a fixture)

STEWARDS = ["dev-data-steward"]


def job(session_factory, tmp_path, *, agent: bool = True) -> ReportJob:
    blobs = LocalBlobStore(tmp_path / "blob")
    return ReportJob(session_factory=session_factory, blob_store=blobs, stewards=lambda: STEWARDS,
                     agent_factory=(lambda: ReportSummariserAgent(provider=MockProvider(), blob_store=blobs)) if agent
                     else (lambda: None), settings=Settings(_env_file=None))


@pytest.fixture
def at_work(db_session, feeds, tmp_path):
    """The job on the test's shared session (its commit only releases a savepoint)."""
    from contextlib import contextmanager

    @contextmanager
    def sessions():
        yield db_session

    loaded_on(db_session, feeds[0], feeds[1], at("2026-11-02", 8), flags={VOLUME_RULE: 4})
    loaded_on(db_session, feeds[2], feeds[3], at("2026-11-02", 9))
    db_session.commit()
    return job(sessions, tmp_path)


def reports(session):
    return [(r.kind, r.subject_id, r.subject_role, r.period_start.isoformat(), r.version)
            for r in session.scalars(select(ReportRow).order_by(ReportRow.subject_id, ReportRow.subject_role, ReportRow.kind))]


def test_the_daily_job_makes_yesterdays_report_for_every_owner_and_steward(db_session, at_work):
    done = at_work.run(now=datetime(2026, 11, 3, 6, tzinfo=UTC))
    assert reports(db_session) == [
        ("daily", "dev-data-owner", "data_owner", "2026-11-02", 1), ("daily", "dev-data-steward", "data_steward", "2026-11-02", 1),
        ("daily", "dev-operator", "data_owner", "2026-11-02", 1)]
    assert set(done.values()) == {"report_written"} and len(done) == 3


def test_a_report_that_exists_is_not_made_again(db_session, at_work):
    at_work.run(now=datetime(2026, 11, 3, 6, tzinfo=UTC))
    again = at_work.run(now=datetime(2026, 11, 3, 7, tzinfo=UTC))  # a restart, or a second job
    assert set(again.values()) == {"report_exists"} and len(reports(db_session)) == 3


def test_the_first_of_the_month_also_makes_last_months_monthly_report(db_session, at_work):
    at_work.run(now=datetime(2026, 12, 1, 6, tzinfo=UTC))
    kinds = {(r[0], r[3]) for r in reports(db_session)}
    assert kinds == {("daily", "2026-11-30"), ("monthly", "2026-11-01")}
    monthly = db_session.scalars(select(ReportRow).where(ReportRow.kind == "monthly", ReportRow.subject_id == "dev-data-owner")).one()
    assert monthly.numbers["totals"]["loads"] == 1 and monthly.period_end.isoformat() == "2026-12-01"


def test_no_monthly_report_on_other_days_and_january_looks_back_to_december(db_session, at_work):
    at_work.run(now=datetime(2026, 11, 15, 6, tzinfo=UTC))
    assert {r[0] for r in reports(db_session)} == {"daily"}
    at_work.run(now=datetime(2027, 1, 1, 6, tzinfo=UTC))
    assert ("monthly", "2026-12-01") in {(r[0], r[3]) for r in reports(db_session)}


def test_each_subject_is_told_once_with_a_link_to_the_report(db_session, at_work):
    at_work.run(now=datetime(2026, 11, 3, 6, tzinfo=UTC))
    notes = db_session.scalars(select(NotificationRow).where(NotificationRow.subject.like("Daily report%"))).all()
    assert sorted(n.recipient for n in notes) == ["dev-data-owner", "dev-data-steward", "dev-operator"]
    ids = {str(r.id) for r in db_session.scalars(select(ReportRow))}
    assert {n.link.removeprefix("#/reports/") for n in notes} == ids
    assert all("2026-11-02" in n.subject for n in notes)
    at_work.run(now=datetime(2026, 11, 3, 7, tzinfo=UTC))  # nothing new: nobody is told again
    assert len(db_session.scalars(select(NotificationRow).where(NotificationRow.subject.like("Daily report%"))).all()) == 3


def test_one_failing_subject_does_not_stop_the_others(db_session, at_work, monkeypatch, caplog):
    import apps.api.jobs as jobs

    real = jobs.write_report

    def flaky(session, *, plan, **kw):
        if plan.subject_id == "dev-data-owner":
            raise RuntimeError("the narrative store is down")
        return real(session, plan=plan, **kw)

    monkeypatch.setattr(jobs, "write_report", flaky)
    with caplog.at_level(logging.ERROR):
        done = at_work.run(now=datetime(2026, 11, 3, 6, tzinfo=UTC))
    assert sorted(v for v in done.values()) == ["error: the narrative store is down", "report_written", "report_written"]
    assert {r[1] for r in reports(db_session)} == {"dev-data-steward", "dev-operator"}
    assert "dev-data-owner" in caplog.text


def test_without_an_agent_the_report_is_made_without_a_narrative(db_session, feeds, tmp_path):
    from contextlib import contextmanager

    @contextmanager
    def sessions():
        yield db_session

    done = job(sessions, tmp_path, agent=False).run(now=datetime(2026, 11, 3, 6, tzinfo=UTC))
    assert set(done.values()) == {"report_written_without_narrative"}
    assert {r.narrative_status for r in db_session.scalars(select(ReportRow))} == {"none"}


def test_the_job_is_registered_once_a_day_at_the_report_hour():
    calls = []

    class Stub:
        def schedule_daily(self, job_id, func, *, hour, minute=0):
            calls.append((job_id, hour, minute))

    register_report_job(Stub(), ReportJob(session_factory=None, blob_store=None, stewards=lambda: [],
                                          agent_factory=lambda: None, settings=Settings(_env_file=None, report_hour=7)))
    assert calls == [(REPORT_JOB_ID, 7, 0)]


def test_the_daily_report_job_commits_its_reports_and_notices(own_schema, tmp_path):
    from tests.configured import loadable_feed

    with get_sessionmaker(own_schema)() as session:
        feed, live = loadable_feed(session, "syn_report_job")
        loaded_on(session, feed, live.id, at("2026-11-02", 8), flags={VOLUME_RULE: 4})
        session.commit()
    job(get_sessionmaker(own_schema), tmp_path).run(now=datetime(2026, 11, 3, 6, tzinfo=UTC))
    assert committed(own_schema, "SELECT subject_id, subject_role, narrative_status FROM reports ORDER BY subject_role, subject_id") == [
        ("dev-data-owner", "data_owner", "written"), ("dev-data-steward", "data_steward", "written")]
    assert committed(own_schema, "SELECT count(*) FROM notifications WHERE subject LIKE 'Daily report%'") == [(2,)]
    assert committed(own_schema, "SELECT count(*) FROM proposal_log WHERE agent_name = 'ReportSummariserAgent'") == [(2,)]
