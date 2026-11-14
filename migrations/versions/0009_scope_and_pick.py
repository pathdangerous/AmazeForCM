"""scope and pick: 3b statuses, requirement_scopes, versioned source_choices

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-14

Phase 3b (phase 3 plan, section 6; assumptions 28, 49 and 50).
- requirements: the status list gains the five 3b statuses.
- requirement_scopes (new): the scope the requester confirmed (S1), versioned per requirement.
  Append-only. Needs an approved scope proposal (0005's function) of the same requirement that holds
  exactly this scope; confirmed by the requirement's own requester.
- source_choices becomes versioned: version and action (selected | released), and for a pick the
  approved source_list proposal, the accepted conditions and the requester; for a release its reason.
  3a's rows become version 1, selected: the columns are added with defaults, which are then dropped.
  Adding a column fires no row trigger and updates no row by hand.

Triggers on insert into source_choices (assumption 50):
  trg_source_choices_a_version         lines 1 and 2: the next version; what may follow what
  trg_source_choices_b_approved_pick   line 5: 0005's function, source_list, only for a pick
  trg_source_choices_c_proposal_match  line 5: the proposal is of this requirement and this source
  trg_source_choices_d_requester       a person who decides is the requirement's own requester
PostgreSQL runs triggers of one event in name order; the letters fix that order.

Every check on a nullable column uses IS NULL, IS NOT NULL or COALESCE, never a bare comparison, so
NULL cannot pass by accident (the lesson of 0008). Function bodies and value lists are written out here
on purpose: a migration must not change when code changes. PostgreSQL 14 features only: check
constraints, arrays, jsonb, to_jsonb(record) (9.5), triggers with WHEN (9.0), partial unique indexes,
ADD COLUMN with a default (11), plpgsql.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

STATUSES_0008 = ("defined", "onboarding", "ready", "access_restricted", "rejected", "source_selected")
STATUSES_3B = ("awaiting_scope_confirmation", "scope_not_confirmed", "scope_confirmed", "awaiting_source_choice",
               "no_source_found")
STATUSES_0009 = (*STATUSES_0008, *STATUSES_3B)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


VERSION_GUARD = """
CREATE FUNCTION amaze_guard_source_choice_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    last_version integer;
    last_action text;
    last_feed uuid;
    last_source text;
BEGIN
    SELECT version, action, feed_id, source_code INTO last_version, last_action, last_feed, last_source
      FROM source_choices WHERE requirement_id = NEW.requirement_id
     ORDER BY version DESC LIMIT 1;
    IF NOT FOUND THEN
        IF NEW.version IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'source choice version % is not the next version; expected 1', NEW.version
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.action IS DISTINCT FROM 'selected' THEN
            RAISE EXCEPTION 'the first source choice of a requirement must be a selection'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.version IS DISTINCT FROM last_version + 1 THEN
        RAISE EXCEPTION 'source choice version % is not the next version; expected %', NEW.version, last_version + 1
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.action = 'selected' AND last_action = 'selected' THEN
        RAISE EXCEPTION 'a selection cannot follow a selection: release the current source first'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.action = 'released' THEN
        IF last_action <> 'selected' THEN
            RAISE EXCEPTION 'a release must follow a selection'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.feed_id IS DISTINCT FROM last_feed OR NEW.source_code IS DISTINCT FROM last_source THEN
            RAISE EXCEPTION 'a release must name the selected feed and source (%), not %', last_source, NEW.source_code
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;
"""

# Shared by source_choices (a pick) and requirement_scopes. The proposal must be for this requirement;
# a pick's proposal must name this source; a scope must hold exactly what was proposed.
PROPOSAL_MATCH = """
CREATE FUNCTION amaze_require_proposal_of_requirement() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p_requirement uuid;
    p_payload jsonb;
BEGIN
    SELECT requirement_id, payload INTO p_requirement, p_payload FROM proposals WHERE id = NEW.proposal_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'proposal % does not exist', NEW.proposal_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF p_requirement IS DISTINCT FROM NEW.requirement_id THEN
        RAISE EXCEPTION 'proposal % is for another requirement', NEW.proposal_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_TABLE_NAME = 'source_choices' THEN
        IF (p_payload ->> 'source_code') IS DISTINCT FROM NEW.source_code THEN
            RAISE EXCEPTION 'proposal % names source %, not %', NEW.proposal_id, p_payload ->> 'source_code',
                NEW.source_code USING ERRCODE = 'check_violation';
        END IF;
    ELSIF TG_TABLE_NAME = 'requirement_scopes' THEN
        IF (p_payload ->> 'visibility') IS DISTINCT FROM NEW.visibility
           OR (p_payload ->> 'provider') IS DISTINCT FROM NEW.provider
           OR jsonb_typeof(p_payload -> 'where_to_search') IS DISTINCT FROM 'array'
           OR ARRAY(SELECT jsonb_array_elements_text(p_payload -> 'where_to_search'))
              IS DISTINCT FROM NEW.where_to_search THEN
            RAISE EXCEPTION 'the scope differs from what proposal % proposed: the requester confirms or '
                            'rejects a scope, never edits it', NEW.proposal_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;
"""

# TG_ARGV[0] names the column that holds the person (decided_by or confirmed_by).
REQUESTER_DECIDES = """
CREATE FUNCTION amaze_require_requirement_requester() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    person text := to_jsonb(NEW) ->> TG_ARGV[0];
    requester text;
BEGIN
    SELECT requester_id INTO requester FROM requirements WHERE id = NEW.requirement_id;
    IF person IS DISTINCT FROM requester THEN
        RAISE EXCEPTION 'only the requirement''s requester (%) decides here, not %', requester, person
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.drop_constraint(op.f("ck_requirements_status"), "requirements", type_="check")
    op.create_check_constraint(op.f("ck_requirements_status"), "requirements", _in("status", STATUSES_0009))

    op.execute(VERSION_GUARD)
    op.execute(PROPOSAL_MATCH)
    op.execute(REQUESTER_DECIDES)

    # ---- requirement_scopes
    op.create_table(
        "requirement_scopes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"),
                  nullable=False),
        sa.Column("requirement_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("where_to_search", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_scopes")),
        sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"],
                                name=op.f("fk_requirement_scopes_requirement_id_requirements")),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"],
                                name=op.f("fk_requirement_scopes_proposal_id_proposals")),
        sa.UniqueConstraint("requirement_id", "version", name=op.f("uq_requirement_scopes_requirement_id_version")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_requirement_scopes_version")),
        sa.CheckConstraint("visibility IN ('public', 'private')", name=op.f("ck_requirement_scopes_visibility")),
        sa.CheckConstraint("length(btrim(provider)) > 0", name=op.f("ck_requirement_scopes_provider")),
        sa.CheckConstraint("cardinality(where_to_search) >= 1", name=op.f("ck_requirement_scopes_where_to_search")),
    )
    op.execute("CREATE TRIGGER trg_requirement_scopes_append_only BEFORE UPDATE OR DELETE ON requirement_scopes "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_requirement_scopes_no_truncate BEFORE TRUNCATE ON requirement_scopes "
               "FOR EACH STATEMENT EXECUTE FUNCTION amaze_refuse_update_delete()")
    op.execute("CREATE TRIGGER trg_requirement_scopes_b_approved_proposal BEFORE INSERT ON requirement_scopes "
               "FOR EACH ROW EXECUTE FUNCTION amaze_require_approved_proposal('scope')")
    op.execute("CREATE TRIGGER trg_requirement_scopes_c_proposal_match BEFORE INSERT ON requirement_scopes "
               "FOR EACH ROW EXECUTE FUNCTION amaze_require_proposal_of_requirement()")
    op.execute("CREATE TRIGGER trg_requirement_scopes_d_requester BEFORE INSERT ON requirement_scopes "
               "FOR EACH ROW EXECUTE FUNCTION amaze_require_requirement_requester('confirmed_by')")

    # ---- source_choices: versioned
    op.add_column("source_choices", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("source_choices", sa.Column("action", sa.Text(), nullable=False, server_default="selected"))
    op.alter_column("source_choices", "version", server_default=None)
    op.alter_column("source_choices", "action", server_default=None)
    op.add_column("source_choices", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column("source_choices", sa.Column("proposal_id", sa.Uuid(), nullable=True))
    op.add_column("source_choices", sa.Column("accepted_conditions", postgresql.JSONB(), nullable=True))
    op.add_column("source_choices", sa.Column("decided_by", sa.Text(), nullable=True))
    op.create_foreign_key(op.f("fk_source_choices_proposal_id_proposals"), "source_choices", "proposals",
                          ["proposal_id"], ["id"])
    op.alter_column("source_choices", "basis", nullable=True)

    op.drop_constraint(op.f("uq_source_choices_requirement_id"), "source_choices", type_="unique")
    op.create_unique_constraint(op.f("uq_source_choices_requirement_id_version"), "source_choices",
                                ["requirement_id", "version"])
    op.create_index(op.f("uq_source_choices_selected_source"), "source_choices", ["requirement_id", "source_code"],
                    unique=True, postgresql_where=sa.text("action = 'selected'"))

    op.drop_constraint(op.f("ck_source_choices_basis"), "source_choices", type_="check")
    # NULL passes these two on purpose; ck_source_choices_selection_or_release decides where NULL belongs.
    op.create_check_constraint(op.f("ck_source_choices_basis"), "source_choices",
                               "basis IN ('known_source', 'requester_pick')")
    op.create_check_constraint(op.f("ck_source_choices_reason"), "source_choices",
                               "reason IN ('source_down_n_days', 'requester_asked')")
    op.create_check_constraint(op.f("ck_source_choices_action"), "source_choices", "action IN ('selected', 'released')")
    op.create_check_constraint(op.f("ck_source_choices_version"), "source_choices", "version >= 1")
    op.create_check_constraint(
        op.f("ck_source_choices_selection_or_release"), "source_choices",
        "(action = 'selected' AND basis IS NOT NULL AND reason IS NULL) OR "
        "(action = 'released' AND reason IS NOT NULL AND basis IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_source_choices_pick_has_its_acceptance"), "source_choices",
        # Each column on its own: (pick) = (a AND b) would let a non-pick hold a proposal without conditions.
        "(COALESCE(basis, '') = 'requester_pick') = (proposal_id IS NOT NULL) AND "
        "(COALESCE(basis, '') = 'requester_pick') = (accepted_conditions IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_source_choices_decided_by_only_for_a_person"), "source_choices",
        "(decided_by IS NOT NULL) = (COALESCE(basis, '') = 'requester_pick' OR COALESCE(reason, '') = 'requester_asked')",
    )
    op.create_check_constraint(
        op.f("ck_source_choices_conditions_are_an_object"), "source_choices",
        "accepted_conditions IS NULL OR jsonb_typeof(accepted_conditions) = 'object'",
    )

    op.execute("CREATE TRIGGER trg_source_choices_a_version BEFORE INSERT ON source_choices "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_source_choice_version()")
    op.execute("CREATE TRIGGER trg_source_choices_b_approved_pick BEFORE INSERT ON source_choices "
               "FOR EACH ROW WHEN (NEW.basis = 'requester_pick') "
               "EXECUTE FUNCTION amaze_require_approved_proposal('source_list')")
    op.execute("CREATE TRIGGER trg_source_choices_c_proposal_match BEFORE INSERT ON source_choices "
               "FOR EACH ROW WHEN (NEW.proposal_id IS NOT NULL) "
               "EXECUTE FUNCTION amaze_require_proposal_of_requirement()")
    op.execute("CREATE TRIGGER trg_source_choices_d_requester BEFORE INSERT ON source_choices "
               "FOR EACH ROW WHEN (NEW.decided_by IS NOT NULL) "
               "EXECUTE FUNCTION amaze_require_requirement_requester('decided_by')")


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM requirements WHERE {_in('status', STATUSES_3B)})
               OR EXISTS (SELECT 1 FROM source_choices
                           WHERE version > 1 OR action <> 'selected' OR basis IS DISTINCT FROM 'known_source')
               OR EXISTS (SELECT 1 FROM requirement_scopes) THEN
                RAISE EXCEPTION 'migration 0009 cannot be downgraded: there are 3b statuses, scopes, picks, '
                                'releases or later versions, which 0008 cannot hold';
            END IF;
        END $$;
        """
    )
    for trigger in ("a_version", "b_approved_pick", "c_proposal_match", "d_requester"):
        op.execute(f"DROP TRIGGER trg_source_choices_{trigger} ON source_choices")
    for name in ("conditions_are_an_object", "decided_by_only_for_a_person", "pick_has_its_acceptance",
                 "selection_or_release", "version", "action", "reason", "basis"):
        op.drop_constraint(op.f(f"ck_source_choices_{name}"), "source_choices", type_="check")
    op.create_check_constraint(op.f("ck_source_choices_basis"), "source_choices", "basis IN ('known_source')")
    op.drop_index(op.f("uq_source_choices_selected_source"), table_name="source_choices")
    op.drop_constraint(op.f("uq_source_choices_requirement_id_version"), "source_choices", type_="unique")
    op.create_unique_constraint(op.f("uq_source_choices_requirement_id"), "source_choices", ["requirement_id"])
    op.alter_column("source_choices", "basis", nullable=False)
    op.drop_constraint(op.f("fk_source_choices_proposal_id_proposals"), "source_choices", type_="foreignkey")
    for column in ("decided_by", "accepted_conditions", "proposal_id", "reason", "action", "version"):
        op.drop_column("source_choices", column)

    op.drop_table("requirement_scopes")  # its triggers go with it

    op.execute("DROP FUNCTION amaze_require_requirement_requester()")
    op.execute("DROP FUNCTION amaze_require_proposal_of_requirement()")
    op.execute("DROP FUNCTION amaze_guard_source_choice_version()")

    op.drop_constraint(op.f("ck_requirements_status"), "requirements", type_="check")
    op.create_check_constraint(op.f("ck_requirements_status"), "requirements", _in("status", STATUSES_0008))
