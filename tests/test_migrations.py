"""Alembic migrations on the test database: one head, upgrade/downgrade/upgrade, models match tables."""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import alembic_config

HEAD = "0032"  # old: "0031" (phase 7.12, T4: rule-made proposals), "0030" (T3: code versions), "0029" (T1: profile actions), "0028" (phase 7.12, T7), "0027" (phase 7.12, T7: manual items), "0026" (phase 7.9: pause and delete of a feed)
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
PHASE_6_TABLES = {"feed_go_lives", "load_stages", "standard_records"}  # 0018
PHASE_7_TABLES = {"load_certifications"}  # 0019
PHASE_78_TABLES = {"amaze_settings", "upload_sources", "transformations", "feed_key_policies",  # 0020, 0021, 0023
                   "onboarding_sessions", "onboarding_events", "onboarding_jobs"}  # 0024
TABLES |= (PHASE_2_TABLES | PHASE_3_TABLES | PHASE_4_TABLES | PHASE_5_TABLES | PHASE_6_TABLES | PHASE_7_TABLES
           | PHASE_78_TABLES)


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
            "JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.oid = current_schema()::regnamespace"
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
    # 0028 (phase 7.12, T7). Their NULL cases are in test_db_manual_items.py.
    "proposals.ck_proposals_origin_matches_source":
        "proposal_log_id IS NULL / author IS NULL spelt out on both branches; length(btrim(author)) is only reached "
        "when author IS NOT NULL; origin is NOT NULL: never NULL",
    "proposals.ck_proposals_manual_kinds":
        "origin = 'agent' first (NOT NULL); a manual row needs feed_id IS NOT NULL spelt out; kind is NOT NULL",
    # 0031 (phase 7.12, T4)
    "proposals.ck_proposals_rule_made_kinds": "origin is NOT NULL; feed_id IS NOT NULL spelt out on the rule branch",
    # 0029 (phase 7.12, T1)
    "proposals.ck_proposals_names_a_feed_for_actions": "kind is NOT NULL; feed_id IS NOT NULL spelt out on the branch",
    # 0018 (phase 6 plan, section 6). Their NULL cases are in test_db_rule_guards.py and test_db_load_guards.py.
    "proposals.ck_proposals_names_a_rule":
        "feed_id IS NOT NULL spelt out; COALESCE(jsonb_typeof(...) = 'string', false) for a missing or null "
        "rule_key or sql; kind and payload are NOT NULL: never NULL",
    "loads.ck_loads_purpose_fits":
        "go_live_id IS NOT NULL / IS NULL spelt out on both sides; purpose is NOT NULL: never NULL",
    # 0023 (phase 7.8, task 4.1). Its NULL cases are in test_db_transformation_guards.py.
    "proposals.ck_proposals_names_a_transformation":
        "feed_id IS NOT NULL spelt out; COALESCE(jsonb_typeof(...) = 'string', false) for a missing or null "
        "source_field; kind and payload are NOT NULL: never NULL",
    # 0024 (phase 7.8, task 5.2). Their NULL cases are in test_proposal_edit.py and
    # test_db_onboarding_session_guards.py.
    "proposals.ck_proposals_edited_from_and_by_together": "IS NULL on both sides: never NULL",
    "proposals.ck_proposals_edited_by": "edited_by IS NULL OR ...: never NULL",
    "onboarding_jobs.ck_onboarding_jobs_running_has_a_lease": "IS NOT NULL on lease_until; status is NOT NULL: never NULL",
    "onboarding_jobs.ck_onboarding_jobs_failed_has_an_error": "IS NOT NULL on error; status is NOT NULL: never NULL",
    "onboarding_jobs.ck_onboarding_jobs_finished_at_matches_status":
        "IS NOT NULL on finished_at; status is NOT NULL: never NULL",
    # 0025 (phase 7.8, task 6.1). Its NULL case (an element with no unit) is in test_db_configuration_guards.py.
    "glossary_elements.ck_glossary_elements_unit": "unit IS NULL OR ...: never NULL",
    # 0019 (phase 7 plan, section 6). Their NULL case (a load with no arrival) is in test_db_arrival_guards.py.
    "loads.ck_loads_content_sha256": "content_sha256 IS NULL OR ...: never NULL",
    "loads.ck_loads_arrival_outcome": "arrival_outcome IS NULL OR ...: never NULL",
}


def test_every_check_on_a_nullable_column_is_reviewed(migrated_engine):
    with migrated_engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT DISTINCT c.conrelid::regclass::text || '.' || c.conname FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
            "WHERE c.contype = 'c' AND c.connamespace = current_schema()::regnamespace AND NOT a.attnotnull"
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


def _a_log_and_a_feed(connection):
    log = connection.execute(text(
        "INSERT INTO proposal_log (agent_name, provider, model, model_version, prompt_id, prompt_version, "
        "input_ref, output, latency_ms) VALUES ('A', 'test', 'test', '0', 'p', 'v1', 'test://x', '{}', 0) "
        "RETURNING id")).scalar_one()
    feed = connection.execute(text("INSERT INTO feeds (code, name) VALUES ('syn_a', 'A') RETURNING id")).scalar_one()
    return log, feed


@pytest.mark.parametrize("case", ["a load", "a rule proposal", "a review_summary proposal"])
def test_0018_downgrade_refuses_when_phase_6_rows_exist(scratch_schema, case):
    """0017 has no purpose column, no loaded status and no rule or summary guard: the downgrade stops instead
    of failing half-way. Every row of the new tables hangs on a load or a rule/review_summary proposal."""
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0018")
    with engine.begin() as connection:
        log, feed = _a_log_and_a_feed(connection)
        if case == "a load":
            connection.execute(text("INSERT INTO loads (feed_id, file_ref, purpose) "
                                    "VALUES (:f, 'raw/a.csv', 'go_live_test')"), {"f": feed})
        else:
            kind, roles, payload = (("rule", "'data_steward', 'checker'", '{"rule_key": "k", "sql": "SELECT 1"}')
                                    if case == "a rule proposal" else ("review_summary", "'data_owner'", "{}"))
            connection.execute(text(
                f"INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
                f"VALUES (:l, '{kind}', CAST(:p AS jsonb), 'A2', ARRAY[{roles}], :f)"),
                {"l": log, "p": payload, "f": feed})
    with pytest.raises(DBAPIError, match="migration 0018 cannot be downgraded"):
        command.downgrade(config, "0017")


def test_0018_downgrade_restores_the_0017_schema(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)

    def state():
        with engine.connect() as connection:
            names = lambda sql: set(connection.execute(text(sql)).scalars())  # noqa: E731
            return {
                "tables": set(inspect(engine).get_table_names()),
                "load_columns": {c["name"] for c in inspect(engine).get_columns("loads")},
                "status_check": connection.execute(text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_loads_status' "
                    "AND connamespace = current_schema()::regnamespace")).scalar_one(),
                "triggers": names("SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                                  "WHERE c.relnamespace = current_schema()::regnamespace AND NOT t.tgisinternal"),
                "indexes": names("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"),
                "constraints": names("SELECT conname FROM pg_constraint "
                                     "WHERE connamespace = current_schema()::regnamespace"),
                "functions": names("SELECT proname FROM pg_proc "
                                   "WHERE pronamespace = current_schema()::regnamespace"),
            }

    new_columns = {"purpose", "fetch_id", "go_live_id", "finished_at", "failed_stage", "failure"}
    new_triggers = {"trg_rules_b_matches_proposal", "trg_loads_a_status", "trg_feed_go_lives_b_guard",
                    "trg_load_stages_b_order", "trg_standard_records_b_load"}
    new_indexes = {"uq_proposals_one_pending_rule_per_feed_key", "uq_proposals_one_pending_review_summary_per_feed",
                   "uq_rules_proposal_id", "uq_loads_one_received_per_feed"}
    new_constraints = {"ck_proposals_names_a_rule", "ck_loads_purpose_fits"}
    new_functions = {"amaze_require_rule_matches_proposal", "amaze_guard_feed_go_live", "amaze_guard_load_status",
                     "amaze_guard_load_stage", "amaze_guard_standard_record"}

    command.upgrade(config, "0018")
    up = state()
    assert PHASE_6_TABLES <= up["tables"] and new_columns <= up["load_columns"] and "'loaded'" in up["status_check"]
    assert new_triggers <= up["triggers"] and new_indexes <= up["indexes"] and new_constraints <= up["constraints"]
    assert new_functions <= up["functions"]
    command.downgrade(config, "0017")
    down = state()
    assert not PHASE_6_TABLES & down["tables"] and not new_columns & down["load_columns"]
    assert "'loaded'" not in down["status_check"]
    assert not new_triggers & down["triggers"] and not new_indexes & down["indexes"]
    assert not new_constraints & down["constraints"] and not new_functions & down["functions"]
    command.upgrade(config, "0018")
    assert state() == up


@pytest.mark.parametrize("case", ["a certification", "a product with a feed", "a load with an arrival",
                                  "a quarantined load", "an abandoned load", "a structure_change run"])
def test_0019_downgrade_refuses_when_phase_7_rows_exist(scratch_schema, case):
    """0018 has no arrival field, no certification, no product feed, no quarantined or abandoned status and no
    structure_change reason: the downgrade stops instead of failing half-way. The rows are written with the
    user triggers off, since the guards of 0019 would refuse most of them without the chain that makes them."""
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0019")
    load_sql = "INSERT INTO loads (feed_id, file_ref, purpose{c}) VALUES (:f, 'raw/a.csv', 'go_live_test'{v}) RETURNING id"
    with engine.begin() as connection:
        _, feed = _a_log_and_a_feed(connection)
        for table in ("loads", "load_certifications", "products", "glossary_runs"):
            connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
        if case == "a certification":
            load = connection.execute(text(load_sql.format(c="", v="")), {"f": feed}).scalar_one()
            connection.execute(text(
                'INSERT INTO load_certifications (load_id, feed_id, "rows", flagged_rows, rule_counts, trust_score, '
                "threshold, outcome) VALUES (:l, :f, 1, 0, '{}', 100.0, 90.0, 'released')"), {"l": load, "f": feed})
        elif case == "a product with a feed":
            connection.execute(text("INSERT INTO products (code, name, domain, owner_id, visibility, feed_id) "
                                    "VALUES ('syn_x', 'X', 'pricing', 'dev-data-owner', 'open', :f)"), {"f": feed})
        elif case == "a load with an arrival":
            connection.execute(text(load_sql.format(c=", content_sha256", v=", repeat('a', 64)")), {"f": feed})
        elif case in ("a quarantined load", "an abandoned load"):
            status = "quarantined" if case == "a quarantined load" else "abandoned"
            connection.execute(text(load_sql.format(c=", status", v=f", '{status}'")), {"f": feed})
        else:
            fetch = connection.execute(text("INSERT INTO source_fetches (feed_id, outcome, blob_ref, size_bytes) "
                                            "VALUES (:f, 'fetched', 'raw/a.csv', 1) RETURNING id"), {"f": feed}).scalar_one()
            profile = connection.execute(text(
                "INSERT INTO feed_profiles (feed_id, version, fetch_id, outcome, row_count, columns, coverage, spec, "
                "detail, engine, profiled_by) VALUES (:f, 1, :x, 'spec_only_low_confidence', 0, '[]', '[]', '{}', "
                "'No data rows.', 'duckdb', 'b') RETURNING id"), {"f": feed, "x": fetch}).scalar_one()
            connection.execute(text("INSERT INTO glossary_runs (feed_id, profile_id, reason, requested_by) "
                                    "VALUES (:f, :p, 'structure_change', 'step_12')"), {"f": feed, "p": profile})
    with pytest.raises(DBAPIError, match="migration 0019 cannot be downgraded"):
        command.downgrade(config, "0018")


def test_0019_downgrade_restores_the_0018_schema(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)

    def state():
        with engine.connect() as connection:
            names = lambda sql: set(connection.execute(text(sql)).scalars())  # noqa: E731
            definition = lambda name: connection.execute(text(  # noqa: E731
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n "
                "AND connamespace = current_schema()::regnamespace"), {"n": name}).scalar_one()
            return {
                "tables": set(inspect(engine).get_table_names()),
                "load_columns": {c["name"] for c in inspect(engine).get_columns("loads")},
                "product_columns": {c["name"] for c in inspect(engine).get_columns("products")},
                "status_check": definition("ck_loads_status"),
                "reason_check": definition("ck_glossary_runs_reason"),
                "status_guard": connection.execute(text(
                    "SELECT prosrc FROM pg_proc WHERE proname = 'amaze_guard_load_status' "
                    "AND pronamespace = current_schema()::regnamespace")).scalar_one(),
                "triggers": names("SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                                  "WHERE c.relnamespace = current_schema()::regnamespace AND NOT t.tgisinternal"),
                "indexes": names("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"),
                "constraints": names("SELECT conname FROM pg_constraint "
                                     "WHERE connamespace = current_schema()::regnamespace"),
                "functions": names("SELECT proname FROM pg_proc "
                                   "WHERE pronamespace = current_schema()::regnamespace"),
            }

    new_load_columns = {"content_sha256", "arrival_outcome", "arrival_detail", "arrival_profile_id"}
    new_triggers = {"trg_loads_b_arrival", "trg_load_certifications_b_guard", "trg_load_certifications_append_only",
                    "trg_load_certifications_no_truncate", "trg_products_b_feed"}
    new_constraints = {"ck_loads_content_sha256", "ck_loads_arrival_outcome", "ck_load_certifications_outcome",
                       "ck_load_certifications_counts", "ck_load_certifications_score",
                       "uq_load_certifications_load_id"}
    new_functions = {"amaze_guard_load_arrival", "amaze_guard_load_certification", "amaze_guard_product_feed"}

    command.upgrade(config, "0019")
    up = state()
    assert PHASE_7_TABLES <= up["tables"] and new_load_columns <= up["load_columns"] and "feed_id" in up["product_columns"]
    assert "'quarantined'" in up["status_check"] and "'abandoned'" in up["status_check"]
    assert "'structure_change'" in up["reason_check"] and "quarantined" in up["status_guard"]
    assert new_triggers <= up["triggers"] and "uq_products_feed_id" in up["indexes"]
    assert new_constraints <= up["constraints"] and new_functions <= up["functions"]
    command.downgrade(config, "0018")
    down = state()
    assert not PHASE_7_TABLES & down["tables"] and not new_load_columns & down["load_columns"]
    assert "feed_id" not in down["product_columns"]
    assert "'quarantined'" not in down["status_check"] and "'structure_change'" not in down["reason_check"]
    assert "quarantined" not in down["status_guard"] and "moves only to loaded or failed_rolled_back" in down["status_guard"]
    assert not new_triggers & down["triggers"] and "uq_products_feed_id" not in down["indexes"]
    assert not new_constraints & down["constraints"] and not new_functions & down["functions"]
    command.upgrade(config, "0019")
    assert state() == up


def _0020_state(engine):
    with engine.connect() as connection:
        names = lambda sql: set(connection.execute(text(sql)).scalars())  # noqa: E731
        return {
            "tables": set(inspect(engine).get_table_names()),
            "triggers": names("SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                              "WHERE c.relnamespace = current_schema()::regnamespace AND NOT t.tgisinternal"),
            "constraints": names("SELECT conname FROM pg_constraint "
                                 "WHERE connamespace = current_schema()::regnamespace"),
            "functions": names("SELECT proname FROM pg_proc WHERE pronamespace = current_schema()::regnamespace"),
        }


def test_0020_downgrade_restores_the_same_approver_constraint(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    new_triggers = {"trg_approvals_four_eyes", "trg_amaze_settings_append_only", "trg_amaze_settings_no_truncate"}
    new_functions = {"amaze_four_eyes", "amaze_approvals_four_eyes"}
    command.upgrade(config, "0020")
    up = _0020_state(engine)
    assert {"amaze_settings"} <= up["tables"] and new_triggers <= up["triggers"] and new_functions <= up["functions"]
    assert "uq_approvals_proposal_id_decided_by" not in up["constraints"]
    assert "uq_approvals_proposal_id_step" in up["constraints"]  # the other guard stays
    command.downgrade(config, "0019")
    down = _0020_state(engine)
    assert not {"amaze_settings"} & down["tables"] and not new_triggers & down["triggers"]
    assert not new_functions & down["functions"] and "uq_approvals_proposal_id_decided_by" in down["constraints"]
    command.upgrade(config, "0020")
    assert _0020_state(engine) == up


def test_0020_downgrade_refuses_when_one_person_decided_two_steps(scratch_schema):
    """0019 has the unique constraint (proposal, person): the downgrade stops, naming 0020, instead of failing
    half-way, when a false setting let one person decide two steps."""
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0020")
    with engine.begin() as connection:
        log, _ = _a_log_and_a_feed(connection)
        proposal = connection.execute(text(
            "INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles) "
            "VALUES (:l, 'scope', '{}', 'A2', ARRAY['data_steward', 'checker']) RETURNING id"), {"l": log}).scalar_one()
        connection.execute(text("INSERT INTO amaze_settings (version, require_four_eyes, set_by, reason) "
                                "VALUES (1, false, 'p', 'r')"))
        for step, role in ((1, "data_steward"), (2, "checker")):
            connection.execute(text("INSERT INTO approvals (proposal_id, step, role, decided_by, decision) "
                                    "VALUES (:p, :s, :r, 'dev-operator', 'approved')"),
                               {"p": proposal, "s": step, "r": role})
    with pytest.raises(DBAPIError, match="migration 0020 cannot be downgraded"):
        command.downgrade(config, "0019")


def _0021_state(engine):
    with engine.connect() as connection:
        names = lambda sql: set(connection.execute(text(sql)).scalars())  # noqa: E731
        return {
            "tables": set(inspect(engine).get_table_names()),
            "connector_check": connection.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_feeds_connector' "
                "AND connamespace = current_schema()::regnamespace")).scalar_one(),
            "triggers": names("SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                              "WHERE c.relnamespace = current_schema()::regnamespace AND NOT t.tgisinternal"),
            "constraints": names("SELECT conname FROM pg_constraint "
                                 "WHERE connamespace = current_schema()::regnamespace"),
            "functions": names("SELECT proname FROM pg_proc WHERE pronamespace = current_schema()::regnamespace"),
        }


def test_0021_downgrade_restores_the_0020_schema(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    new_triggers = {"trg_upload_sources_append_only", "trg_upload_sources_no_truncate", "trg_upload_sources_b_guard"}
    new_constraints = {"pk_upload_sources", "uq_upload_sources_code", "uq_upload_sources_feed_id",
                       "fk_upload_sources_feed_id_feeds", "ck_upload_sources_code_format",
                       "ck_upload_sources_intake_options", "ck_upload_sources_manifest"}
    command.upgrade(config, "0021")
    up = _0021_state(engine)
    assert "upload_sources" in up["tables"] and "'dropzone'" in up["connector_check"]
    assert new_triggers <= up["triggers"] and new_constraints <= up["constraints"]
    assert "amaze_guard_upload_source" in up["functions"]
    command.downgrade(config, "0020")
    down = _0021_state(engine)
    assert "upload_sources" not in down["tables"] and "'dropzone'" not in down["connector_check"]
    assert "'local_file'" in down["connector_check"] and "'http'" in down["connector_check"]
    assert not new_triggers & down["triggers"] and not new_constraints & down["constraints"]
    assert "amaze_guard_upload_source" not in down["functions"]
    command.upgrade(config, "0021")
    assert _0021_state(engine) == up


@pytest.mark.parametrize("case", ["an upload source", "a dropzone feed"])
def test_0021_downgrade_refuses_when_an_upload_exists(scratch_schema, case):
    """0020 has no upload_sources and its connector check refuses dropzone: the downgrade stops, naming 0021."""
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0021")
    with engine.begin() as connection:
        feed = connection.execute(text(
            "INSERT INTO feeds (code, name, source_code, connector, source_ref) "
            "VALUES ('upload_a', 'A', 'upl_upload_a', 'dropzone', 'dropzone/upload_a') RETURNING id")).scalar_one()
        if case == "an upload source":
            connection.execute(text(
                "INSERT INTO upload_sources (code, feed_id, name, provider, description, licence, permitted_use, "
                "redistribution, original_name, original_ref, manifest_ref, manifest, intake_options, created_by) "
                "VALUES ('upl_upload_a', :f, 'A', 'P', 'D', 'L', 'U', 'R', 'a.csv', 'uploads/u/a.csv', 'raw/m.json', "
                "'{}', '{\"sheet\": null, \"header_row\": 1}', 'p')"), {"f": feed})
    with pytest.raises(DBAPIError, match="migration 0021 cannot be downgraded"):
        command.downgrade(config, "0020")


def _kind_check(connection):
    return connection.execute(text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_proposals_kind' "
        "AND connamespace = current_schema()::regnamespace")).scalar_one()


_EXTRACTED = ("INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles) "
              "VALUES (:l, 'extracted_table', '{}', 'A2', ARRAY['backend_team'])")


def test_0022_adds_the_extracted_table_kind_and_downgrade_restores_the_0021_list(scratch_schema):
    """The new kind needs no feed (the feed is made at confirm); the 0021 list refuses it."""
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0021")
    with engine.connect() as connection:
        assert "extracted_table" not in _kind_check(connection)
    command.upgrade(config, "0022")
    with engine.connect() as connection:
        transaction = connection.begin()
        assert "extracted_table" in _kind_check(connection)
        log, _ = _a_log_and_a_feed(connection)
        connection.execute(text(_EXTRACTED), {"l": log})  # no feed, no requirement
        transaction.rollback()  # the downgrade below refuses while such a proposal exists
    command.downgrade(config, "0021")
    with engine.connect() as connection:
        assert "extracted_table" not in _kind_check(connection) and "'review_summary'" in _kind_check(connection)


def test_0022_downgrade_refuses_while_an_extracted_table_proposal_exists(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0022")
    with engine.begin() as connection:
        log, _ = _a_log_and_a_feed(connection)
        connection.execute(text(_EXTRACTED), {"l": log})
    with pytest.raises(DBAPIError, match="migration 0022 cannot be downgraded"):
        command.downgrade(config, "0021")


def _0023_state(engine):
    with engine.connect() as connection:
        names = lambda sql: set(connection.execute(text(sql)).scalars())  # noqa: E731
        return {
            "tables": set(inspect(engine).get_table_names()),
            "go_live_columns": {c["name"] for c in inspect(engine).get_columns("feed_go_lives")},
            "kind_check": _kind_check(connection),
            "go_live_guard": connection.execute(text(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.proname = 'amaze_guard_feed_go_live' "
                "AND p.pronamespace = current_schema()::regnamespace")).scalar_one(),
            "triggers": names("SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                              "WHERE c.relnamespace = current_schema()::regnamespace AND NOT t.tgisinternal"),
            "indexes": names("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"),
            "constraints": names("SELECT conname FROM pg_constraint WHERE connamespace = current_schema()::regnamespace"),
            "functions": names("SELECT proname FROM pg_proc WHERE pronamespace = current_schema()::regnamespace"),
        }


def test_0023_downgrade_restores_the_0022_schema(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    new_tables = {"transformations", "feed_key_policies"}
    new_functions = {"amaze_valid_transform_steps", "amaze_valid_key_columns",
                     "amaze_require_transformation_matches_proposal", "amaze_guard_feed_key_policy"}
    new_triggers = {"trg_transformations_approved_proposal", "trg_transformations_b_matches_proposal",
                    "trg_transformations_append_only", "trg_transformations_no_truncate",
                    "trg_feed_key_policies_b_guard", "trg_feed_key_policies_append_only",
                    "trg_feed_key_policies_no_truncate"}
    new_indexes = {"uq_proposals_one_pending_transformation_per_feed_column", "uq_transformations_proposal_id"}
    command.upgrade(config, "0023")
    up = _0023_state(engine)
    assert new_tables <= up["tables"] and "transformation_ids" in up["go_live_columns"]
    assert "'transformation'" in up["kind_check"] and "transformation_ids" in up["go_live_guard"]
    assert new_functions <= up["functions"] and new_triggers <= up["triggers"] and new_indexes <= up["indexes"]
    assert "ck_proposals_names_a_transformation" in up["constraints"]
    command.downgrade(config, "0022")
    down = _0023_state(engine)
    assert not new_tables & down["tables"] and "transformation_ids" not in down["go_live_columns"]
    assert "'transformation'" not in down["kind_check"] and "'extracted_table'" in down["kind_check"]
    assert "transformation_ids" not in down["go_live_guard"] and "rule_ids are not those" in down["go_live_guard"]
    assert not new_functions & down["functions"] and not new_triggers & down["triggers"]
    assert not new_indexes & down["indexes"] and "ck_proposals_names_a_transformation" not in down["constraints"]
    command.upgrade(config, "0023")
    assert _0023_state(engine) == up


def test_0023_downgrade_refuses_while_a_transformation_proposal_exists(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0023")
    with engine.begin() as connection:
        log, feed = _a_log_and_a_feed(connection)
        connection.execute(text(
            "INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
            "VALUES (:l, 'transformation', '{\"source_field\": \"px\", \"steps\": []}', 'A2', "
            "ARRAY['data_steward'], :f)"), {"l": log, "f": feed})
    with pytest.raises(DBAPIError, match="migration 0023 cannot be downgraded"):
        command.downgrade(config, "0022")


def _0024_state(engine):
    with engine.connect() as connection:
        names = lambda sql: set(connection.execute(text(sql)).scalars())  # noqa: E731
        return {
            "tables": set(inspect(engine).get_table_names()),
            "proposal_columns": {c["name"] for c in inspect(engine).get_columns("proposals")},
            "status_check": connection.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_proposals_status' "
                "AND connamespace = current_schema()::regnamespace")).scalar_one(),
            "proposal_guard": connection.execute(text(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.proname = 'amaze_guard_proposal_change' "
                "AND p.pronamespace = current_schema()::regnamespace")).scalar_one(),
            "triggers": names("SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                              "WHERE c.relnamespace = current_schema()::regnamespace AND NOT t.tgisinternal"),
            "indexes": names("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"),
            "constraints": names("SELECT conname FROM pg_constraint WHERE connamespace = current_schema()::regnamespace"),
            "functions": names("SELECT proname FROM pg_proc WHERE pronamespace = current_schema()::regnamespace"),
        }


def test_0024_downgrade_restores_the_0023_schema(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    new_tables = {"onboarding_sessions", "onboarding_events", "onboarding_jobs"}
    new_functions = {"amaze_edit_proposal", "amaze_guard_proposal_edit", "amaze_require_edit_of_superseded",
                     "amaze_refuse_superseded_approval", "amaze_valid_step_states", "amaze_guard_onboarding_session",
                     "amaze_guard_onboarding_job"}
    new_triggers = {"trg_proposals_edit", "trg_proposals_superseded_has_an_edit", "trg_approvals_not_superseded",
                    "trg_onboarding_sessions_a_guard", "trg_onboarding_sessions_no_truncate",
                    "trg_onboarding_events_append_only", "trg_onboarding_events_no_truncate",
                    "trg_onboarding_jobs_a_guard", "trg_onboarding_jobs_no_truncate"}
    new_indexes = {"uq_proposals_edited_from", "uq_onboarding_jobs_one_active_per_session"}
    new_constraints = {"ck_proposals_edited_by", "ck_proposals_edited_from_and_by_together",
                       "fk_proposals_edited_from_proposals"}
    command.upgrade(config, "0023")
    before = _0024_state(engine)
    command.upgrade(config, "0024")
    up = _0024_state(engine)
    assert new_tables <= up["tables"] and {"edited_from", "edited_by"} <= up["proposal_columns"]
    assert "'superseded'" in up["status_check"] and "superseded" in up["proposal_guard"]
    assert new_functions <= up["functions"] and new_triggers <= up["triggers"] and new_indexes <= up["indexes"]
    assert new_constraints <= up["constraints"]
    command.downgrade(config, "0023")
    down = _0024_state(engine)
    assert down == before
    assert not new_tables & down["tables"] and "'superseded'" not in down["status_check"]
    assert "moves only to approved or rejected" in down["proposal_guard"]
    command.upgrade(config, "0024")
    assert _0024_state(engine) == up


@pytest.mark.parametrize("case", ["a superseded proposal", "a session"])
def test_0024_downgrade_refuses_while_an_edit_or_a_session_exists(scratch_schema, case):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0024")
    with engine.begin() as connection:
        log, feed = _a_log_and_a_feed(connection)
        if case == "a session":
            states = ", ".join(f'"{step}": "waiting"' for step in (
                "upload", "profile", "glossary", "mapping", "transformations", "rules", "go_live", "first_load",
                "product_ready"))
            connection.execute(text(
                "INSERT INTO onboarding_sessions (feed_id, current_step, step_states, created_by) "
                "VALUES (:f, 'profile', CAST(:s AS jsonb), 'dev-operator')"), {"f": feed, "s": "{" + states + "}"})
        else:
            old = connection.execute(text(
                "INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id) "
                "VALUES (:l, 'mapping', '{\"source_field\": \"px\", \"candidates\": [\"a\", \"b\"]}', 'A2', "
                "ARRAY['data_steward'], :f) RETURNING id"), {"l": log, "f": feed}).scalar_one()
            connection.execute(text("SELECT amaze_edit_proposal(:o, "
                                    "'{\"source_field\": \"px\", \"candidates\": [\"a\", \"b\"], \"recommended\": \"a\"}', 'x')"),
                               {"o": old})
    with pytest.raises(DBAPIError, match="migration 0024 cannot be downgraded"):
        command.downgrade(config, "0023")


def _0025_state(engine):
    with engine.connect() as connection:
        return {
            "glossary_columns": {c["name"] for c in inspect(engine).get_columns("glossary_elements")},
            "constraints": set(connection.execute(text(
                "SELECT conname FROM pg_constraint WHERE connamespace = current_schema()::regnamespace")).scalars()),
            "option_guard": connection.execute(text(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.proname = 'amaze_require_glossary_option' "
                "AND p.pronamespace = current_schema()::regnamespace")).scalar_one(),
        }


def test_0025_downgrade_restores_the_0024_schema(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0024")
    before = _0025_state(engine)
    command.upgrade(config, "0025")
    up = _0025_state(engine)
    assert "unit" in up["glossary_columns"] and "ck_glossary_elements_unit" in up["constraints"]
    assert "'unit'" in up["option_guard"] and "'unit'" not in before["option_guard"]
    command.downgrade(config, "0024")
    assert _0025_state(engine) == before
    command.upgrade(config, "0025")
    assert _0025_state(engine) == up


def test_0025_downgrade_refuses_while_an_element_has_a_unit(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0025")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO glossary_elements (key, version, name, domain, data_type, definition, aliases, unit, created_by) "
            "VALUES ('probe_ratio', 1, 'Probe ratio', 'estimates', 'decimal', 'Synthetic.', '{}', 'times', 'seed')"))
    with pytest.raises(DBAPIError, match="migration 0025 cannot be downgraded"):
        command.downgrade(config, "0024")


def _0026_state(engine):
    with engine.connect() as connection:
        return {name: connection.execute(text(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.proname = :n "
            "AND p.pronamespace = current_schema()::regnamespace"), {"n": name}).scalar_one()
            for name in ("amaze_guard_feed_registration", "amaze_refuse_second_registration")}


def test_0026_downgrade_restores_the_0015_registration_guards(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0025")
    before = _0026_state(engine)
    command.upgrade(config, "0026")
    up = _0026_state(engine)
    assert up != before and "visibility" in up["amaze_guard_feed_registration"]
    assert "visibility" not in before["amaze_guard_feed_registration"]
    command.downgrade(config, "0025")
    assert _0026_state(engine) == before
    command.upgrade(config, "0026")
    assert _0026_state(engine) == up


GUARDS_0027 = ("amaze_refuse_update_delete", "amaze_guard_glossary_run", "amaze_guard_onboarding_job",
               "amaze_guard_onboarding_session", "amaze_guard_proposal_change", "amaze_guard_feed_stage")


def _0027_state(engine):
    with engine.connect() as connection:
        functions = {name: connection.execute(text(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.proname = :n "
            "AND p.pronamespace = current_schema()::regnamespace"), {"n": name}).scalar_one()
            for name in GUARDS_0027}
        return {
            "functions": functions,
            "tables": set(inspect(connection).get_table_names()),
            "other_functions": set(connection.execute(text(
                "SELECT proname FROM pg_proc WHERE proname IN ('amaze_delete_feed', 'amaze_may_delete') "
                "AND pronamespace = current_schema()::regnamespace")).scalars()),
            "feed_checks": sorted(connection.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'feeds'::regclass "
                "AND contype = 'c' AND conname LIKE 'ck_feeds_%'")).scalars()),
            "proposals_feed_fk": connection.execute(text(
                "SELECT count(*) FROM pg_constraint WHERE conname = 'fk_proposals_feed_id_feeds' "
                "AND connamespace = current_schema()::regnamespace")).scalar_one(),
        }


def test_0027_adds_pause_delete_and_the_guards_that_let_one_feed_be_deleted(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0026")
    before = _0027_state(engine)
    command.upgrade(config, "0027")
    up = _0027_state(engine)
    assert {"feed_status_events", "feed_deletions"} <= up["tables"] and not {"feed_status_events", "feed_deletions"} & before["tables"]
    assert up["other_functions"] == {"amaze_delete_feed", "amaze_may_delete"} and before["other_functions"] == set()
    assert all("amaze_may_delete" in up["functions"][name] for name in GUARDS_0027[:5])
    assert all("amaze_may_delete" not in before["functions"][name] for name in GUARDS_0027[:5])
    assert "paused" in up["functions"]["amaze_guard_feed_stage"] and "paused" not in before["functions"]["amaze_guard_feed_stage"]
    assert any("paused" in c and "candidate" in c for c in up["feed_checks"])
    assert up["proposals_feed_fk"] == 0 and before["proposals_feed_fk"] == 1  # kept proposals keep their feed id only


def test_0027_downgrade_restores_the_0026_schema_and_upgrades_again(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0026")
    before = _0027_state(engine)
    command.upgrade(config, "0027")
    up = _0027_state(engine)
    command.downgrade(config, "0026")
    assert _0027_state(engine) == before
    command.upgrade(config, "0027")
    assert _0027_state(engine) == up


def test_0027_downgrade_refuses_while_a_feed_is_paused_or_a_deletion_is_recorded(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0027")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO feed_deletions (feed_id, code, name, \"by\", reason, rows_deleted) "
            "VALUES (gen_random_uuid(), 'syn_gone', 'Gone', 'dev-operator', 'Synthetic.', '{}'::jsonb)"))
    with pytest.raises(DBAPIError, match="migration 0027 cannot be downgraded"):
        command.downgrade(config, "0026")


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


# ---------------------------------------------------------------- 0028 (phase 7.12, T7): manual items

GUARDS_0028 = ("amaze_guard_proposal_edit", "amaze_edit_proposal", "amaze_require_mapping_matches_proposal",
               "amaze_delete_feed")


def _0028_state(engine):
    with engine.connect() as connection:
        return {
            "functions": {name: connection.execute(text(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.proname = :n "
                "AND p.pronamespace = current_schema()::regnamespace"), {"n": name}).scalar_one()
                for name in GUARDS_0028},
            "columns": sorted(c["name"] for c in inspect(connection).get_columns("proposals")),
            "log_nullable": [c["nullable"] for c in inspect(connection).get_columns("proposals")
                             if c["name"] == "proposal_log_id"],
            "view": connection.execute(text("SELECT pg_get_viewdef('proposal_audit'::regclass)")).scalar_one(),
            "events": connection.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_onboarding_events_event' "
                "AND connamespace = current_schema()::regnamespace")).scalar_one(),
        }


def test_0028_makes_a_proposal_agent_or_manual_and_downgrade_restores_0027(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0027")
    before = _0028_state(engine)
    command.upgrade(config, "0028")
    up = _0028_state(engine)
    assert {"origin", "author"} <= set(up["columns"]) and not {"origin", "author"} & set(before["columns"])
    assert up["log_nullable"] == [True] and before["log_nullable"] == [False]
    assert "item_added" in up["events"] and "item_added" not in before["events"]
    assert "LEFT JOIN" in up["view"].upper() and "LEFT JOIN" not in before["view"].upper()
    assert "p_origin" in up["functions"]["amaze_require_mapping_matches_proposal"]
    assert "a manual item has no logged call" in up["functions"]["amaze_delete_feed"]
    assert "a manual item has no logged call" not in before["functions"]["amaze_delete_feed"]
    command.downgrade(config, "0027")
    assert _0028_state(engine) == before
    command.upgrade(config, "0028")
    assert _0028_state(engine) == up


def test_0028_downgrade_refuses_while_a_manual_item_exists(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0028")
    with engine.begin() as connection:
        feed = connection.execute(text("INSERT INTO feeds (code, name) VALUES ('syn_a', 'A') RETURNING id")).scalar_one()
        connection.execute(text(
            "INSERT INTO proposals (proposal_log_id, kind, payload, autonomy, required_approver_roles, feed_id, origin, "
            "author) VALUES (NULL, 'rule', CAST(:p AS jsonb), 'A1', ARRAY['data_steward', 'checker'], :feed, "
            "'manual', 'dev-operator')"),
            {"feed": feed,
             "p": '{"rule_key": "r", "sql": "SELECT 1", "description": "d", "backtest": {"status": "not_back_tested"}}'})
    with pytest.raises(DBAPIError, match="migration 0028 cannot be downgraded"):
        command.downgrade(config, "0027")


# ---------------------------------------------------------------- 0030 (phase 7.12, T3): code versions


def _0030_state(engine):
    with engine.connect() as connection:
        return {
            "tables": set(inspect(connection).get_table_names()),
            "go_live_columns": sorted(c["name"] for c in inspect(connection).get_columns("feed_go_lives")),
            "delete_function": connection.execute(text(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.proname = 'amaze_delete_feed' "
                "AND p.pronamespace = current_schema()::regnamespace")).scalar_one(),
            "functions": set(connection.execute(text(
                "SELECT proname FROM pg_proc WHERE proname IN ('amaze_guard_code_version', "
                "'amaze_go_live_pins_code_version') AND pronamespace = current_schema()::regnamespace")).scalars()),
        }


def test_0030_adds_code_versions_and_downgrade_restores_0029(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0029")
    before = _0030_state(engine)
    command.upgrade(config, "0030")
    up = _0030_state(engine)
    assert "code_versions" in up["tables"] and "code_versions" not in before["tables"]
    assert "code_version_id" in up["go_live_columns"] and "code_version_id" not in before["go_live_columns"]
    assert "DELETE FROM code_versions" in up["delete_function"] and "code_versions" not in before["delete_function"]
    assert up["functions"] == {"amaze_guard_code_version", "amaze_go_live_pins_code_version"} and not before["functions"]
    command.downgrade(config, "0029")
    assert _0030_state(engine) == before
    command.upgrade(config, "0030")
    assert _0030_state(engine) == up


def _0032_state(engine):
    with engine.connect() as connection:
        def function(name: str) -> str | None:
            return connection.execute(text(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.proname = :n "
                "AND p.pronamespace = current_schema()::regnamespace"), {"n": name}).scalar_one_or_none()

        return {
            "tables": set(inspect(connection).get_table_names()),
            "session_columns": sorted(c["name"] for c in inspect(connection).get_columns("onboarding_sessions")),
            "delete_function": function("amaze_delete_feed"),
            "session_guard": function("amaze_guard_onboarding_session"),
            "events": _check_definition(connection, "onboarding_events", "ck_onboarding_events_event"),
            "functions": {n for n in ("amaze_guard_proposal_base", "amaze_require_bases_approved") if function(n)},
        }


def _check_definition(connection, table: str, name: str) -> str:
    return connection.execute(text(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c WHERE c.conname = :n AND c.conrelid = CAST(:t AS regclass)"),
        {"n": name, "t": table}).scalar_one()


def test_0032_adds_the_chain_of_drafts_and_downgrade_restores_0031(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0031")
    before = _0032_state(engine)
    command.upgrade(config, "0032")
    up = _0032_state(engine)
    assert "proposal_bases" in up["tables"] and "proposal_bases" not in before["tables"]
    assert "run_mode" in up["session_columns"] and "run_mode" not in before["session_columns"]
    assert "DELETE FROM proposal_bases" in up["delete_function"] and "proposal_bases" not in before["delete_function"]
    assert "run_mode" in up["session_guard"] and "run_mode" not in before["session_guard"]
    assert "chain_started" in up["events"] and "chain_started" not in before["events"]
    assert up["functions"] == {"amaze_guard_proposal_base", "amaze_require_bases_approved"} and not before["functions"]
    command.downgrade(config, "0031")
    assert _0032_state(engine) == before
    command.upgrade(config, "0032")
    assert _0032_state(engine) == up


def test_0032_downgrade_refuses_while_a_chain_session_exists(scratch_schema):
    engine, url = scratch_schema
    config = alembic_config(url)
    command.upgrade(config, "0032")
    with engine.begin() as connection:
        feed = connection.execute(text(
            "INSERT INTO feeds (code, name, source_code, connector, source_ref) "
            "VALUES ('syn_chain_mig', 'Chain', 'syn_chain_mig', 'local_file', 'files/x.csv') RETURNING id")).scalar_one()
        connection.execute(text(
            "INSERT INTO onboarding_sessions (feed_id, current_step, step_states, created_by, run_mode) VALUES "
            "(:f, 'profile', CAST(:s AS jsonb), 'dev-operator', 'chain')"),
            {"f": feed, "s": '{"upload": "done", "profile": "waiting", "glossary": "waiting", "mapping": "waiting", '
                             '"transformations": "waiting", "rules": "waiting", "go_live": "waiting", '
                             '"first_load": "waiting", "product_ready": "waiting"}'})
    with pytest.raises(DBAPIError, match="migration 0032"):
        command.downgrade(config, "0031")
