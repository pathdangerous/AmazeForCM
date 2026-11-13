"""Alembic migrations on the test database: upgrade, downgrade and upgrade again; constraints hold."""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import alembic_config

TABLES = {"proposal_log", "notifications"}


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_single_head_is_0001(test_database_url):
    script = ScriptDirectory.from_config(alembic_config(test_database_url))
    assert script.get_heads() == ["0001"]


def test_upgrade_downgrade_upgrade(migrated_engine, test_database_url):
    config = alembic_config(test_database_url)
    try:
        command.upgrade(config, "head")
        assert TABLES <= _tables(test_database_url)
        command.downgrade(config, "base")
        assert not TABLES & _tables(test_database_url)
        command.upgrade(config, "head")
        assert TABLES <= _tables(test_database_url)
    finally:
        command.upgrade(config, "head")  # leave the schema at head for the other tests


def test_orm_models_match_migrations(migrated_engine):
    """The tables declared in code and the tables the migrations build must not drift apart."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from migrations.models import metadata

    def only_modelled(obj, name, type_, reflected, compare_to):
        return type_ != "table" or name in metadata.tables

    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"include_object": only_modelled, "compare_server_default": True}
        )
        diff = compare_metadata(context, metadata)
    assert diff == [], f"models and migrations differ: {diff}"


def test_proposal_log_columns(migrated_engine):
    columns = {c["name"] for c in inspect(migrated_engine).get_columns("proposal_log")}
    assert columns == {
        "id", "created_at", "agent_name", "provider", "model", "model_version", "prompt_id",
        "prompt_version", "input_ref", "output", "raw_ref", "latency_ms", "status",
        "decided_by", "decided_at", "decision_note",
    }


def test_notifications_columns(migrated_engine):
    columns = {c["name"] for c in inspect(migrated_engine).get_columns("notifications")}
    assert columns == {"id", "created_at", "recipient", "subject", "body", "link", "read_at"}


INSERT = text(
    "INSERT INTO proposal_log (id, agent_name, provider, model, model_version, prompt_id, "
    "prompt_version, input_ref, output, latency_ms, status, decided_by, decided_at) VALUES "
    "(:id, 'a', 'mock', 'mock', '1', 'p', 'v1', 'ref', '{}', 0, :status, :by, :at)"
)


def _insert(session, status, by=None, at=None):
    session.execute(INSERT, {"id": uuid.uuid4(), "status": status, "by": by, "at": at})
    session.flush()


def test_status_must_be_one_of_three(db_session):
    # An unknown status breaks both check constraints; PostgreSQL reports whichever it tests first.
    with pytest.raises(IntegrityError, match="ck_proposal_log_(status|decision_complete)"):
        _insert(db_session, "maybe", by="dev-data-steward", at="2026-01-01T00:00:00Z")


def test_decided_row_needs_who_and_when(db_session):
    with pytest.raises(IntegrityError, match="ck_proposal_log_decision_complete"):
        _insert(db_session, "approved")


def test_pending_row_cannot_carry_a_decision(db_session):
    with pytest.raises(IntegrityError, match="ck_proposal_log_decision_complete"):
        _insert(db_session, "pending", by="dev-data-steward", at="2026-01-01T00:00:00Z")


def test_valid_rows_are_accepted(db_session):
    _insert(db_session, "pending")
    _insert(db_session, "rejected", by="dev-data-steward", at="2026-01-01T00:00:00Z")
    count = db_session.execute(text("SELECT count(*) FROM proposal_log")).scalar_one()
    assert count == 2
