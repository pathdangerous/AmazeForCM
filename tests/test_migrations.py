"""Alembic migrations on the test database: one head, upgrade/downgrade/upgrade, models match tables."""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import alembic_config

HEAD = "0002"
TABLES = {"proposal_log", "notifications", "proposals", "approvals"}


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_single_head(test_database_url):
    script = ScriptDirectory.from_config(alembic_config(test_database_url))
    assert script.get_heads() == [HEAD]


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
        "prompt_version", "input_ref", "output", "raw_ref", "latency_ms",
    }


def test_notifications_columns(migrated_engine):
    columns = {c["name"] for c in inspect(migrated_engine).get_columns("notifications")}
    assert columns == {"id", "created_at", "recipient", "subject", "body", "link", "read_at"}


def test_proposal_audit_view_exists(migrated_engine):
    assert "proposal_audit" in inspect(migrated_engine).get_view_names()


def test_0002_refuses_to_drop_a_recorded_decision(migrated_engine, test_database_url):
    """Upgrading from 0001 must stop rather than lose a decision stored in proposal_log."""
    config = alembic_config(test_database_url)
    row_id = uuid.uuid4()
    try:
        command.downgrade(config, "0001")
        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO proposal_log (id, agent_name, provider, model, model_version, prompt_id, "
                    "prompt_version, input_ref, output, latency_ms, status, decided_by, decided_at) VALUES "
                    "(:id, 'a', 'mock', 'mock', '1', 'p', 'v1', 'ref', '{}', 0, 'approved', 'dev-checker', now())"
                ),
                {"id": row_id},
            )
        with pytest.raises(DBAPIError, match="would lose"):
            command.upgrade(config, "0002")
    finally:
        with migrated_engine.begin() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if version == "0001":
                connection.execute(text("DELETE FROM proposal_log WHERE id = :id"), {"id": row_id})
        command.upgrade(config, "head")
