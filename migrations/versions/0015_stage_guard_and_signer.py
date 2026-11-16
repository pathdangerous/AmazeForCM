"""stage guard checks the registration's sign-off; a feed is registered once

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-16

Phase 4, fixes after the independent review (plan, section 17). 0014 is applied, so it is not edited.
- feeds (finding 1): amaze_guard_feed_stage is replaced. candidate -> registered needs, besides a
  registration of the same owner, that registration's sign-off to be the feed's latest sign-off, to
  accept the feed, and to be on the feed's latest profile (the same three checks as
  amaze_guard_feed_registration, made again at the stage change). candidate -> rejected needs the
  latest sign-off to be rejected and to be on the latest profile.
- feed_registrations (finding 1): a row with a version above 1 is refused; phase 4 registers once.
- feed_signoffs (finding 4, decision D1): trg_feed_signoffs_d_signer. The signer's own backend_team
  approval row must be 'approved' when the outcome is the summary's recommended_outcome, and 'rejected'
  when it differs; another person's decision no longer stands in for the signer's.
- proposals (finding 4): trg_proposals_decision_only. An UPDATE may change status and decided_at only:
  the two columns the decision path (ProposalRepository.record_decision) writes; every other column
  (payload with its recommended_outcome, kind, roles, links) is fixed once written. DELETE is refused.

Function bodies are written out here on purpose, the 0014 ones too (for the downgrade): a migration must
not change when code changes. PostgreSQL 14 features only: plpgsql, BEFORE and AFTER row triggers.
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

FEED_STAGE_0015 = """
CREATE OR REPLACE FUNCTION amaze_guard_feed_stage() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    r_owner text;
    r_signoff uuid;
    s_id uuid;
    s_outcome text;
    s_profile uuid;
    latest_profile uuid;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.stage IS DISTINCT FROM 'candidate' THEN
            RAISE EXCEPTION 'a new feed is a candidate, not %', NEW.stage USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    IF NEW.stage = OLD.stage THEN
        IF NEW.stage <> 'candidate' AND NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
            RAISE EXCEPTION 'the owner of a % feed is fixed', NEW.stage USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    SELECT id, outcome, profile_id INTO s_id, s_outcome, s_profile
      FROM feed_signoffs WHERE feed_id = NEW.id ORDER BY version DESC LIMIT 1;
    SELECT id INTO latest_profile FROM feed_profiles WHERE feed_id = NEW.id ORDER BY version DESC LIMIT 1;
    IF OLD.stage = 'candidate' AND NEW.stage = 'registered' THEN
        SELECT owner_id, signoff_id INTO r_owner, r_signoff
          FROM feed_registrations WHERE feed_id = NEW.id ORDER BY version DESC LIMIT 1;
        IF r_owner IS NULL OR r_owner IS DISTINCT FROM NEW.owner_id THEN
            RAISE EXCEPTION 'feed % needs a registration of owner % to be registered', NEW.id, NEW.owner_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF r_signoff IS DISTINCT FROM s_id THEN
            RAISE EXCEPTION 'feed % cannot be registered: its registration''s sign-off % is not the latest sign-off %',
                NEW.id, r_signoff, s_id USING ERRCODE = 'check_violation';
        END IF;
        IF s_outcome IS DISTINCT FROM 'meets' AND s_outcome IS DISTINCT FROM 'meets_with_gaps' THEN
            RAISE EXCEPTION 'feed % cannot be registered: sign-off % does not accept the feed (%)', NEW.id, s_id,
                s_outcome USING ERRCODE = 'check_violation';
        END IF;
        IF s_profile IS DISTINCT FROM latest_profile THEN
            RAISE EXCEPTION 'feed % cannot be registered: a newer profile waits for a sign-off', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    IF OLD.stage = 'candidate' AND NEW.stage = 'rejected' THEN
        IF s_outcome IS DISTINCT FROM 'rejected' THEN
            RAISE EXCEPTION 'feed % needs a rejected sign-off to be rejected', NEW.id USING ERRCODE = 'check_violation';
        END IF;
        IF s_profile IS DISTINCT FROM latest_profile THEN
            RAISE EXCEPTION 'feed % cannot be rejected: its rejected sign-off is not on the latest profile', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'feed % cannot move from % to %', NEW.id, OLD.stage, NEW.stage USING ERRCODE = 'check_violation';
END $$;
"""

# The 0014 body, restored by downgrade.
FEED_STAGE_0014 = """
CREATE OR REPLACE FUNCTION amaze_guard_feed_stage() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    r_owner text;
    s_outcome text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.stage IS DISTINCT FROM 'candidate' THEN
            RAISE EXCEPTION 'a new feed is a candidate, not %', NEW.stage USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    IF NEW.stage = OLD.stage THEN
        IF NEW.stage <> 'candidate' AND NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
            RAISE EXCEPTION 'the owner of a % feed is fixed', NEW.stage USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    IF OLD.stage = 'candidate' AND NEW.stage = 'registered' THEN
        SELECT owner_id INTO r_owner FROM feed_registrations WHERE feed_id = NEW.id ORDER BY version DESC LIMIT 1;
        IF r_owner IS NULL OR r_owner IS DISTINCT FROM NEW.owner_id THEN
            RAISE EXCEPTION 'feed % needs a registration of owner % to be registered', NEW.id, NEW.owner_id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    IF OLD.stage = 'candidate' AND NEW.stage = 'rejected' THEN
        SELECT outcome INTO s_outcome FROM feed_signoffs WHERE feed_id = NEW.id ORDER BY version DESC LIMIT 1;
        IF s_outcome IS DISTINCT FROM 'rejected' THEN
            RAISE EXCEPTION 'feed % needs a rejected sign-off to be rejected', NEW.id USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'feed % cannot move from % to %', NEW.id, OLD.stage, NEW.stage USING ERRCODE = 'check_violation';
END $$;
"""

REGISTERED_ONCE = """
CREATE FUNCTION amaze_refuse_second_registration() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.version IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'feed % is registered once in phase 4: registration version % is refused', NEW.feed_id,
            NEW.version USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


SIGNER_DECISION = """
CREATE FUNCTION amaze_require_signer_decision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    s_decision text;
    p_recommended text;
    needed text;
BEGIN
    SELECT payload ->> 'recommended_outcome' INTO p_recommended FROM proposals WHERE id = NEW.proposal_id;
    SELECT decision INTO s_decision FROM approvals
     WHERE proposal_id = NEW.proposal_id AND role = 'backend_team' AND decided_by = NEW.signed_off_by;
    needed := CASE WHEN NEW.outcome IS NOT DISTINCT FROM p_recommended THEN 'approved' ELSE 'rejected' END;
    IF s_decision IS DISTINCT FROM needed THEN
        RAISE EXCEPTION 'the signer % decided proposal % as %; a sign-off of % needs %', NEW.signed_off_by,
            NEW.proposal_id, COALESCE(s_decision, 'nothing'), NEW.outcome, needed USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

PROPOSAL_DECISION_ONLY = """
CREATE FUNCTION amaze_guard_proposal_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'proposals: DELETE refused; a proposal is decided, never removed'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (to_jsonb(NEW) - 'status' - 'decided_at') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'decided_at') THEN
        RAISE EXCEPTION 'proposals: only status and decided_at change (by a decision); proposal % is otherwise fixed',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.execute(FEED_STAGE_0015)
    op.execute(REGISTERED_ONCE)
    op.execute("CREATE TRIGGER trg_feed_registrations_c_once BEFORE INSERT ON feed_registrations "
               "FOR EACH ROW EXECUTE FUNCTION amaze_refuse_second_registration()")
    op.execute(SIGNER_DECISION)
    op.execute("CREATE TRIGGER trg_feed_signoffs_d_signer BEFORE INSERT ON feed_signoffs "
               "FOR EACH ROW EXECUTE FUNCTION amaze_require_signer_decision()")
    op.execute(PROPOSAL_DECISION_ONLY)
    op.execute("CREATE TRIGGER trg_proposals_decision_only BEFORE UPDATE OR DELETE ON proposals "
               "FOR EACH ROW EXECUTE FUNCTION amaze_guard_proposal_change()")


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_proposals_decision_only ON proposals")
    op.execute("DROP FUNCTION amaze_guard_proposal_change()")
    op.execute("DROP TRIGGER trg_feed_signoffs_d_signer ON feed_signoffs")
    op.execute("DROP FUNCTION amaze_require_signer_decision()")
    op.execute("DROP TRIGGER trg_feed_registrations_c_once ON feed_registrations")
    op.execute("DROP FUNCTION amaze_refuse_second_registration()")
    op.execute(FEED_STAGE_0014)
