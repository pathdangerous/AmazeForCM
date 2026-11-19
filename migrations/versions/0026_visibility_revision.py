"""a registered feed's visibility may be revised by a next registration version, until it has a product

Revision ID: 0026
Revises: 0025

Phase 7.8, stage S7 (task 7.3a). The owner chooses the feed's visibility at go-live, after the feed is registered
(at the profile step), so a registered feed may get a next feed_registrations version that changes only visibility.

- amaze_guard_feed_registration() (0014) is replaced. A registered feed may get a registration row only when a
  previous version exists, owner_id, licence, permitted_use, redistribution and signoff_id equal the latest version's,
  visibility differs, and the feed has no product yet (a product copies the owner and visibility of the latest
  registration, 0019, and keeps them). Every other refusal is the 0014 one, with its message: a feed that is neither a
  candidate nor registered, and each sign-off check, which a revision passes through too.
- amaze_refuse_second_registration() (0015) is replaced: a version above 1 is refused unless the feed is registered
  (the guard above has then decided whether the revision is allowed). A candidate's second version is still refused.

Nothing else changes. 0018 and 0023 compare the go-live owner with the latest registration's owner (a revision keeps
the owner); 0019 compares a product's owner and visibility with the latest registration's (a revision is refused once a
product exists, so the product always has the visibility chosen at go-live).

Downgrade restores the 0014 and 0015 bodies exactly; registration rows stay (feed_registrations is append-only).
The function bodies are written out here on purpose: a migration must not change when code changes. PostgreSQL 14
features only: plpgsql, %ROWTYPE, EXISTS.
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

REGISTRATION_GUARD_0026 = """
CREATE OR REPLACE FUNCTION amaze_guard_feed_registration() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    s_feed uuid;
    s_outcome text;
    s_profile uuid;
    latest_signoff uuid;
    latest_profile uuid;
    f_stage text;
    previous feed_registrations%ROWTYPE;
BEGIN
    SELECT stage INTO f_stage FROM feeds WHERE id = NEW.feed_id;
    IF f_stage IS DISTINCT FROM 'candidate' THEN
        -- a registered feed may get a next version that changes only its visibility, before it has a product
        IF f_stage IS NOT DISTINCT FROM 'registered' THEN
            SELECT * INTO previous FROM feed_registrations WHERE feed_id = NEW.feed_id ORDER BY version DESC LIMIT 1;
            IF FOUND
               AND previous.owner_id IS NOT DISTINCT FROM NEW.owner_id
               AND previous.licence IS NOT DISTINCT FROM NEW.licence
               AND previous.permitted_use IS NOT DISTINCT FROM NEW.permitted_use
               AND previous.redistribution IS NOT DISTINCT FROM NEW.redistribution
               AND previous.signoff_id IS NOT DISTINCT FROM NEW.signoff_id
               AND previous.visibility IS DISTINCT FROM NEW.visibility
               AND NOT EXISTS (SELECT 1 FROM products WHERE feed_id = NEW.feed_id) THEN
                RETURN NEW;
            END IF;
        END IF;
        RAISE EXCEPTION 'feed % is %: only a candidate feed is registered (a registered feed with no product may only '
                        'change its visibility)', NEW.feed_id, f_stage
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT feed_id, outcome, profile_id INTO s_feed, s_outcome, s_profile FROM feed_signoffs WHERE id = NEW.signoff_id;
    SELECT id INTO latest_signoff FROM feed_signoffs WHERE feed_id = NEW.feed_id ORDER BY version DESC LIMIT 1;
    SELECT id INTO latest_profile FROM feed_profiles WHERE feed_id = NEW.feed_id ORDER BY version DESC LIMIT 1;
    IF s_feed IS DISTINCT FROM NEW.feed_id OR latest_signoff IS DISTINCT FROM NEW.signoff_id THEN
        RAISE EXCEPTION 'sign-off % is not the latest sign-off of feed %', NEW.signoff_id, NEW.feed_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF s_outcome IS DISTINCT FROM 'meets' AND s_outcome IS DISTINCT FROM 'meets_with_gaps' THEN
        RAISE EXCEPTION 'sign-off % is %: it does not accept the feed', NEW.signoff_id, s_outcome
            USING ERRCODE = 'check_violation';
    END IF;
    IF s_profile IS DISTINCT FROM latest_profile THEN
        RAISE EXCEPTION 'feed % has a newer profile than its sign-off: a newer profile waits for a sign-off',
            NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

REGISTERED_ONCE_0026 = """
CREATE OR REPLACE FUNCTION amaze_refuse_second_registration() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    f_stage text;
BEGIN
    IF NEW.version IS DISTINCT FROM 1 THEN
        SELECT stage INTO f_stage FROM feeds WHERE id = NEW.feed_id;
        IF f_stage IS DISTINCT FROM 'registered' THEN
            RAISE EXCEPTION 'feed % is registered once in phase 4: registration version % is refused', NEW.feed_id,
                NEW.version USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;
"""

# The 0014 body of amaze_guard_feed_registration, restored by downgrade.
REGISTRATION_GUARD_0014 = """
CREATE OR REPLACE FUNCTION amaze_guard_feed_registration() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    s_feed uuid;
    s_outcome text;
    s_profile uuid;
    latest_signoff uuid;
    latest_profile uuid;
    f_stage text;
BEGIN
    SELECT stage INTO f_stage FROM feeds WHERE id = NEW.feed_id;
    IF f_stage IS DISTINCT FROM 'candidate' THEN
        RAISE EXCEPTION 'feed % is %: only a candidate feed is registered', NEW.feed_id, f_stage
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT feed_id, outcome, profile_id INTO s_feed, s_outcome, s_profile FROM feed_signoffs WHERE id = NEW.signoff_id;
    SELECT id INTO latest_signoff FROM feed_signoffs WHERE feed_id = NEW.feed_id ORDER BY version DESC LIMIT 1;
    SELECT id INTO latest_profile FROM feed_profiles WHERE feed_id = NEW.feed_id ORDER BY version DESC LIMIT 1;
    IF s_feed IS DISTINCT FROM NEW.feed_id OR latest_signoff IS DISTINCT FROM NEW.signoff_id THEN
        RAISE EXCEPTION 'sign-off % is not the latest sign-off of feed %', NEW.signoff_id, NEW.feed_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF s_outcome IS DISTINCT FROM 'meets' AND s_outcome IS DISTINCT FROM 'meets_with_gaps' THEN
        RAISE EXCEPTION 'sign-off % is %: it does not accept the feed', NEW.signoff_id, s_outcome
            USING ERRCODE = 'check_violation';
    END IF;
    IF s_profile IS DISTINCT FROM latest_profile THEN
        RAISE EXCEPTION 'feed % has a newer profile than its sign-off: a newer profile waits for a sign-off',
            NEW.feed_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

# The 0015 body of amaze_refuse_second_registration, restored by downgrade.
REGISTERED_ONCE_0015 = """
CREATE OR REPLACE FUNCTION amaze_refuse_second_registration() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.version IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'feed % is registered once in phase 4: registration version % is refused', NEW.feed_id,
            NEW.version USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.execute(REGISTRATION_GUARD_0026)
    op.execute(REGISTERED_ONCE_0026)


def downgrade() -> None:
    op.execute(REGISTERED_ONCE_0015)
    op.execute(REGISTRATION_GUARD_0014)
