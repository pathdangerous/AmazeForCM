"""Alembic migrations on the test database: one head, upgrade/downgrade/upgrade, models match tables."""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import alembic_config

HEAD = "0017"
TABLES = {
    "proposal_log", "notifications", "proposals", "approvals", "glossary_elements", "requirements", "feeds",
    "mappings", "rules", "loads", "products",
}
PHASE_2_TABLES = {  # migration 0006
    "product_elements", "product_grants", "requirement_elements", "requirement_terms", "consumer_requests",
}
PHASE_3_TABLES = {"source_choices", "source_fetches", "requirement_scopes",  # migrations 0008 and 0009
                  "proposal_log_discards"}  # 0012
PHASE_4_TABLES = {"feed_profiles", "feed_signoffs", "feed_registrations"}  # 0014
PHASE_5_TABLES = {"glossary_runs"}  # 0016
TABLES |= PHASE_2_TABLES | PHASE_3_TABLES | PHASE_4_TABLES | PHASE_5_TABLES


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
    }  # 0011 added discarded_reason; 0012 moved discards to proposal_log_discards and dropped it


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


# ---------------------------------------------------------------- phase 2: migration 0006


def test_0006_refuses_when_products_has_rows(migrated_engine, test_database_url):
    """Upgrading from 0005 must stop rather than invent an owner and a visibility for a product."""
    config = alembic_config(test_database_url)
    try:
        command.downgrade(config, "0005")
        with migrated_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO products (code, name, domain) VALUES ('before_0006', 'Before 0006', 'pricing')")
            )
        with pytest.raises(DBAPIError, match="migration 0006"):
            command.upgrade(config, "0006")
    finally:
        with migrated_engine.begin() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if version == "0005":
                connection.execute(text("DELETE FROM products WHERE code = 'before_0006'"))
        command.upgrade(config, "head")


def _check_constraints(engine, table: str) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT conname FROM pg_constraint WHERE contype = 'c' AND conrelid = to_regclass(:t)"), {"t": table}
        )
        return {name for (name,) in rows}


def test_no_constraint_name_repeats_its_prefix(migrated_engine):
    """A full name passed through the naming convention gets its prefix twice (ck_x_ck_x_...).
    Migration 0007 renamed the ones 0002 to 0004 created; this keeps any from coming back."""
    with migrated_engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT c.conrelid::regclass::text, c.conname FROM pg_constraint c "
            "JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'public'"
        )).all()
    def repeats(table: str, name: str) -> bool:
        prefix = f"{name.split('_')[0]}_{table}_"  # ck_products_, uq_feeds_, ...
        return name.startswith(prefix + prefix)

    assert sorted(f"{table}.{name}" for table, name in rows if repeats(table, name)) == []


def test_0006_check_constraints_have_the_convention_names(migrated_engine):
    """A full name passed through the naming convention is prefixed twice (ck_products_ck_products_...),
    as 0002 to 0004 did. 0006 marks its names as final with op.f()."""
    assert {"ck_products_owner_present", "ck_products_visibility"} <= _check_constraints(migrated_engine, "products")
    for table in sorted(PHASE_2_TABLES):
        doubled = {name for name in _check_constraints(migrated_engine, table) if f"ck_{table}_ck_" in name}
        assert doubled == set(), table


# ---------------------------------------------------------------- checks on nullable columns

# A check that evaluates to NULL passes, so a check on a nullable column must be read with NULL in mind.
# Every such check is listed here with why NULL is handled; a new one fails this test until it is
# reviewed and added (audit at phase 3 checkpoint 2).
REVIEWED_NULLABLE_CHECKS = {
    "consumer_requests.ck_consumer_requests_requirement_matches_outcome":
        "IS NOT NULL on requirement_id; outcome is NOT NULL: never NULL",
    "feeds.ck_feeds_connector":
        "NULL connector passes on purpose: a feed without a source",
    "feeds.ck_feeds_connector_has_source_ref": "IS NULL / IS NOT NULL only: never NULL",
    "feeds.ck_feeds_registered_at_matches_stage": "IS NOT NULL; stage is NOT NULL: never NULL",
    "feeds.ck_feeds_registered_has_owner": "IS NOT NULL; stage is NOT NULL: never NULL",
    "feeds.ck_feeds_source_and_connector_together": "IS NULL on both sides: never NULL",
    "glossary_elements.ck_glossary_elements_proposal_unless_seed":
        "IS NULL; created_by is NOT NULL: never NULL",
    "proposals.ck_proposals_decided_at_matches_status": "IS NULL; status is NOT NULL: never NULL",
    "source_fetches.ck_source_fetches_down_has_a_reason":
        "detail IS NOT NULL spelt out before btrim (fixed before 0008 was applied)",
    "source_fetches.ck_source_fetches_fetched_has_a_file":
        "size_bytes IS NOT NULL spelt out before > 0 (fixed before 0008 was applied)",
    # 0009 (phase 3 plan, assumption 50). Each has a NULL case in test_db_source_guards.py.
    "source_choices.ck_source_choices_basis":
        "NULL basis passes on purpose (a release); ck_source_choices_selection_or_release needs it on a selection",
    "source_choices.ck_source_choices_reason":
        "NULL reason passes on purpose (a selection); ck_source_choices_selection_or_release needs it on a release",
    "source_choices.ck_source_choices_selection_or_release":
        "IS NULL / IS NOT NULL on basis and reason; action is NOT NULL: never NULL",
    "source_choices.ck_source_choices_pick_has_its_acceptance":
        "COALESCE(basis, '') and IS NOT NULL only: never NULL",
    "source_choices.ck_source_choices_decided_by_only_for_a_person":
        "COALESCE on basis and reason, IS NOT NULL on decided_by: never NULL",
    "source_choices.ck_source_choices_conditions_are_an_object":
        "accepted_conditions IS NULL OR ... spelt out before jsonb_typeof",
    # 0014 (phase 4 plan, section 6). Each has its NULL cases in test_db_onboarding_guards.py.
    "feed_profiles.ck_feed_profiles_profiled_has_rows":
        "row_count IS NOT NULL and detail IS NULL spelt out; outcome is NOT NULL: never NULL",
    "feed_profiles.ck_feed_profiles_spec_only_has_a_reason":
        "COALESCE(row_count, 0) and detail IS NOT NULL spelt out before btrim: never NULL",
    # 0016 (phase 5 plan, section 6). Each has its NULL cases in test_db_configuration_guards.py.
    "glossary_runs.ck_glossary_runs_running_has_a_lease": "IS NOT NULL on lease_until; status is NOT NULL: never NULL",
    "glossary_runs.ck_glossary_runs_failed_has_an_error": "IS NOT NULL on error; status is NOT NULL: never NULL",
    "glossary_runs.ck_glossary_runs_done_has_results": "IS NOT NULL on results; status is NOT NULL: never NULL",
    "glossary_runs.ck_glossary_runs_finished_at_matches_status":
        "IS NOT NULL on finished_at; status is NOT NULL: never NULL",
    "glossary_runs.ck_glossary_runs_call_only_when_done": "IS NULL on proposal_log_id; status is NOT NULL: never NULL",
    # 0017 (phase 5 plan, section 17). Its NULL cases (no feed, a JSON null column) are in
    # test_db_configuration_guards.py.
    "proposals.ck_proposals_names_a_column":
        "feed_id IS NOT NULL spelt out; COALESCE(jsonb_typeof(...) = 'string', false) for a missing or null "
        "source_field; kind and payload are NOT NULL: never NULL",
}


def test_every_check_on_a_nullable_column_is_reviewed(migrated_engine):
    with migrated_engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT DISTINCT c.conrelid::regclass::text || '.' || c.conname FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
            "WHERE c.contype = 'c' AND c.connamespace = 'public'::regnamespace AND NOT a.attnotnull"
        )).scalars().all()
    assert sorted(rows) == sorted(REVIEWED_NULLABLE_CHECKS)


# ---------------------------------------------------------------- phase 3a: migration 0008


def test_0008_downgrade_refuses_while_a_requirement_is_source_selected(migrated_engine, test_database_url):
    """0007's status list has no source_selected: the downgrade stops instead of failing half-way."""
    config = alembic_config(test_database_url)
    row_id = uuid.uuid4()
    try:
        with migrated_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO requirements (id, requester_id, title, status) "
                     "VALUES (:id, 'dev-requester', 'T', 'source_selected')"),
                {"id": row_id},
            )
        with pytest.raises(DBAPIError, match="migration 0008"):
            command.downgrade(config, "0007")
    finally:
        with migrated_engine.begin() as connection:
            connection.execute(text("DELETE FROM requirements WHERE id = :id"), {"id": row_id})
        command.upgrade(config, "head")


@pytest.fixture
def scratch_schema(test_database_url):
    """(engine, url) on a new, empty schema of amaze_test; dropped afterwards. For tests that must leave
    rows in append-only tables, which cannot be deleted row by row."""
    from sqlalchemy.engine import make_url

    schema = f"migration_check_{uuid.uuid4().hex[:8]}"
    base = create_engine(test_database_url)
    with base.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    url = make_url(test_database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    url_text = url.render_as_string(hide_password=False)
    engine = create_engine(url_text)
    try:
        yield engine, url_text
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        base.dispose()


def _a_3a_choice(connection) -> uuid.UUID:
    """A requirement selected to a known source, written as 0008 knows it. Returns the requirement id."""
    requirement_id, feed_id = uuid.uuid4(), uuid.uuid4()
    connection.execute(text("INSERT INTO requirements (id, requester_id, title, status) "
                            "VALUES (:id, 'dev-requester', 'volume', 'source_selected')"), {"id": requirement_id})
    connection.execute(text("INSERT INTO feeds (id, code, name, source_code, connector, source_ref) VALUES "
                            "(:id, 'syn_x', 'X', 'syn_x', 'local_file', 'files/x.csv')"), {"id": feed_id})
    connection.execute(text("INSERT INTO source_choices (requirement_id, feed_id, source_code, basis) "
                            "VALUES (:r, :f, 'syn_x', 'known_source')"), {"r": requirement_id, "f": feed_id})
    return requirement_id


def test_0009_makes_existing_choices_version_1_selected(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0008")
    with engine.begin() as connection:
        requirement_id = _a_3a_choice(connection)
    command.upgrade(config, "0009")
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT version, action, basis, reason, proposal_id, accepted_conditions, decided_by "
            "FROM source_choices WHERE requirement_id = :r"), {"r": requirement_id}).one()
        defaults = connection.execute(text(
            "SELECT column_name, column_default FROM information_schema.columns "
            "WHERE table_name = 'source_choices' AND column_name IN ('version', 'action') "
            "AND table_schema = current_schema()")).all()
    assert tuple(row) == (1, "selected", "known_source", None, None, None, None)
    assert sorted(defaults) == [("action", None), ("version", None)]  # the defaults were only for existing rows


@pytest.mark.parametrize("case", ["a 3b status", "a release"])
def test_0009_downgrade_refuses_when_3b_rows_exist(scratch_schema, case):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0009")
    with engine.begin() as connection:
        if case == "a 3b status":
            connection.execute(text("INSERT INTO requirements (requester_id, title, status) "
                                    "VALUES ('dev-requester', 'T', 'awaiting_scope_confirmation')"))
        else:
            requirement_id, feed_id = uuid.uuid4(), uuid.uuid4()
            connection.execute(text("INSERT INTO requirements (id, requester_id, title, status) "
                                    "VALUES (:id, 'dev-requester', 'T', 'source_selected')"), {"id": requirement_id})
            connection.execute(text("INSERT INTO feeds (id, code, name, source_code, connector, source_ref) "
                                    "VALUES (:id, 'syn_x', 'X', 'syn_x', 'local_file', 'files/x.csv')"),
                               {"id": feed_id})
            for version, action, basis, reason in ((1, "selected", "known_source", None),
                                                   (2, "released", None, "source_down_n_days")):
                connection.execute(text(
                    "INSERT INTO source_choices (requirement_id, version, action, feed_id, source_code, basis, "
                    "reason) VALUES (:r, :v, :a, :f, 'syn_x', :b, :reason)"),
                    {"r": requirement_id, "v": version, "a": action, "f": feed_id, "b": basis, "reason": reason})
    with pytest.raises(DBAPIError, match="migration 0009"):
        command.downgrade(config, "0008")


def test_0009_downgrade_restores_0008_when_there_are_no_3b_rows(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0008")
    with engine.begin() as connection:
        _a_3a_choice(connection)
    command.upgrade(config, "0009")
    command.downgrade(config, "0008")
    with engine.connect() as connection:
        columns = {c["name"] for c in inspect(connection).get_columns("source_choices")}
        unique = {u["name"] for u in inspect(connection).get_unique_constraints("source_choices")}
    assert columns == {"id", "created_at", "requirement_id", "feed_id", "source_code", "basis"}
    assert unique == {"uq_source_choices_requirement_id"}
    assert "requirement_scopes" not in inspect(engine).get_table_names()


def test_0012_moves_a_0011_discard_into_its_own_table(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0011")
    with engine.begin() as connection:
        log_id = connection.execute(text(
            "INSERT INTO proposal_log (agent_name, provider, model, model_version, prompt_id, prompt_version, "
            "input_ref, output, latency_ms, discarded_reason) VALUES "
            "('a', 'mock', 'mock', '1', 'p', 'v1', 'ref', '{}', 0, 'moved on') RETURNING id")).scalar_one()
    command.upgrade(config, "0012")
    with engine.connect() as connection:
        moved = connection.execute(text("SELECT proposal_log_id, reason FROM proposal_log_discards")).all()
    assert moved == [(log_id, "moved on")]


def test_0014_adds_the_third_release_reason_and_keeps_existing_rows(scratch_schema):
    """Note 4c of the phase 3 plan: the reason list grows in a new migration; 0009 is never edited."""
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0008")
    with engine.begin() as connection:
        requirement = _a_3a_choice(connection)
    command.upgrade(config, "0013")
    with engine.begin() as connection:  # released by its requester, as 0013 knows it
        feed = connection.execute(text("SELECT feed_id FROM source_choices WHERE requirement_id = :r"),
                                  {"r": requirement}).scalar_one()
        connection.execute(text(
            "INSERT INTO source_choices (requirement_id, version, action, feed_id, source_code, reason, decided_by) "
            "VALUES (:r, 2, 'released', :f, 'syn_x', 'requester_asked', 'dev-requester')"), {"r": requirement, "f": feed})
        candidate = connection.execute(text("SELECT count(*) FROM feeds WHERE stage = 'candidate'")).scalar_one()
    command.upgrade(config, "0014")
    with engine.connect() as connection:
        reasons = connection.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_source_choices_reason' "
            "AND connamespace = current_schema()::regnamespace")).scalar_one()
        kept = connection.execute(text("SELECT version, action, reason FROM source_choices WHERE requirement_id = :r "
                                       "ORDER BY version"), {"r": requirement}).all()
    assert "candidate_rejected" in reasons
    assert kept == [(1, "selected", None), (2, "released", "requester_asked")]
    assert candidate >= 1  # the existing feed is a candidate, which the new stage guard accepts


def test_0014_downgrade_refuses_when_phase_4_rows_exist(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0014")
    with engine.begin() as connection:
        feed = connection.execute(text("INSERT INTO feeds (code, name) VALUES ('syn_a', 'A') RETURNING id")).scalar_one()
        fetch = connection.execute(text("INSERT INTO source_fetches (feed_id, outcome, blob_ref, size_bytes) "
                                        "VALUES (:f, 'fetched', 'raw/a.csv', 1) RETURNING id"), {"f": feed}).scalar_one()
        connection.execute(text(
            "INSERT INTO feed_profiles (feed_id, version, fetch_id, outcome, row_count, columns, coverage, spec, "
            "detail, engine, profiled_by) VALUES (:f, 1, :x, 'spec_only_low_confidence', 0, '[]', '[]', '{}', "
            "'No data rows.', 'duckdb', 'b')"), {"f": feed, "x": fetch})
    with pytest.raises(DBAPIError, match="migration 0014"):
        command.downgrade(config, "0013")


def test_0016_downgrade_refuses_when_a_glossary_run_exists(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0016")
    with engine.begin() as connection:
        feed = connection.execute(text("INSERT INTO feeds (code, name) VALUES ('syn_a', 'A') RETURNING id")).scalar_one()
        fetch = connection.execute(text("INSERT INTO source_fetches (feed_id, outcome, blob_ref, size_bytes) "
                                        "VALUES (:f, 'fetched', 'raw/a.csv', 1) RETURNING id"), {"f": feed}).scalar_one()
        profile = connection.execute(text(
            "INSERT INTO feed_profiles (feed_id, version, fetch_id, outcome, row_count, columns, coverage, spec, "
            "detail, engine, profiled_by) VALUES (:f, 1, :x, 'spec_only_low_confidence', 0, '[]', '[]', '{}', "
            "'No data rows.', 'duckdb', 'b') RETURNING id"), {"f": feed, "x": fetch}).scalar_one()
        connection.execute(text("INSERT INTO glossary_runs (feed_id, profile_id, reason, requested_by) "
                                "VALUES (:f, :p, 'requested', 'dev-glossary-owner')"), {"f": feed, "p": profile})
    with pytest.raises(DBAPIError, match="migration 0016 cannot be downgraded"):
        command.downgrade(config, "0015")


def test_0016_downgrade_restores_the_0015_guards(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0016")
    command.downgrade(config, "0015")
    with engine.connect() as connection:
        body = connection.execute(text(
            "SELECT prosrc FROM pg_proc WHERE proname = 'amaze_guard_proposal_change' "
            "AND pronamespace = current_schema()::regnamespace")).scalar_one()
        triggers = set(connection.execute(text(
            "SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE c.relnamespace = current_schema()::regnamespace")).scalars())
    assert "never changes again" not in body and "only status and decided_at change" in body
    assert not {"trg_proposals_no_truncate", "trg_glossary_elements_b_option", "trg_mappings_b_matches_proposal"} & triggers
    assert "glossary_runs" not in set(inspect(engine).get_table_names())
    command.upgrade(config, "0016")


def test_0017_downgrade_restores_the_0016_guards(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)

    def guards():
        with engine.connect() as connection:
            bodies = dict(connection.execute(text(
                "SELECT proname, prosrc FROM pg_proc WHERE pronamespace = current_schema()::regnamespace "
                "AND proname IN ('amaze_require_glossary_option', 'amaze_guard_glossary_run')")).all())
            check = connection.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_proposals_names_a_column' "
                "AND connamespace = current_schema()::regnamespace")).scalar_one()
        return ("already has the alias" in bodies["amaze_require_glossary_option"],
                "is not a profile of feed" in bodies["amaze_guard_glossary_run"], "feed_id IS NOT NULL" in check)

    command.upgrade(config, "0017")
    assert guards() == (True, True, True)
    command.downgrade(config, "0016")
    assert guards() == (False, False, False)
    command.upgrade(config, "0017")
    assert guards() == (True, True, True)


def test_0014_downgrade_restores_0013_when_there_are_no_phase_4_rows(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0014")
    command.downgrade(config, "0013")
    assert not PHASE_4_TABLES & set(inspect(engine).get_table_names())


def test_0015_downgrade_restores_the_0014_guards(scratch_schema):
    """0015 only replaces or adds guards, so it downgrades with rows in place."""
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0015")
    command.downgrade(config, "0014")
    with engine.connect() as connection:
        triggers = connection.execute(text(
            "SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE c.relnamespace = current_schema()::regnamespace AND tgname LIKE 'trg_feed_registrations_%' "
            "ORDER BY tgname")).scalars().all()
        stage = connection.execute(text(
            "SELECT prosrc FROM pg_proc WHERE proname = 'amaze_guard_feed_stage' "
            "AND pronamespace = current_schema()::regnamespace")).scalar_one()
    assert "trg_feed_registrations_c_once" not in triggers
    assert "is not the latest sign-off" not in stage
    command.upgrade(config, "0015")


def test_0008_keeps_existing_requirements(migrated_engine, test_database_url):
    """The status constraint is replaced, not the table: a defined requirement survives 0007 -> 0008."""
    config = alembic_config(test_database_url)
    row_id = uuid.uuid4()
    try:
        command.downgrade(config, "0007")
        with migrated_engine.begin() as connection:
            connection.execute(text("INSERT INTO requirements (id, requester_id, title) VALUES (:id, 'dev-requester', 'T')"),
                               {"id": row_id})
        command.upgrade(config, "0008")
        with migrated_engine.connect() as connection:
            status = connection.execute(text("SELECT status FROM requirements WHERE id = :id"), {"id": row_id}).scalar()
        assert status == "defined"
    finally:
        with migrated_engine.begin() as connection:
            connection.execute(text("DELETE FROM requirements WHERE id = :id"), {"id": row_id})
        command.upgrade(config, "head")
