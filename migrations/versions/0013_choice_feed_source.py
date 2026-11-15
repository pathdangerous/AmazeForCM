"""choice feed source: a source_choices row's feed must read the row's source

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-15

Independent review of phase 3, fix 2. The pick guards check that the approved proposal names the row's
source_code, but every fetch follows feed_id. A row naming source A with the feed of source B passed
every guard, so B would be fetched with no acceptance of its conditions. Now the feed's source_code
must equal the row's (IS DISTINCT FROM: a feed with no source is refused too, NULL cannot pass).

trg_source_choices_e_feed_source runs after the a to d triggers (PostgreSQL runs triggers of one event in
name order). Rows already in source_choices are not re-checked (a trigger fires on insert only); the
repository has always written matching rows. The function body is written out here on purpose: a
migration must not change when code changes. PostgreSQL 14 features only: plpgsql.
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

FUNCTION = """
CREATE FUNCTION amaze_require_feed_of_source() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    f_source text;
BEGIN
    SELECT source_code INTO f_source FROM feeds WHERE id = NEW.feed_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the foreign key reports a missing feed
    END IF;
    IF f_source IS DISTINCT FROM NEW.source_code THEN
        RAISE EXCEPTION 'feed % reads source %, not %', NEW.feed_id, f_source, NEW.source_code
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.execute(FUNCTION)
    op.execute("CREATE TRIGGER trg_source_choices_e_feed_source BEFORE INSERT ON source_choices "
               "FOR EACH ROW EXECUTE FUNCTION amaze_require_feed_of_source()")


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_source_choices_e_feed_source ON source_choices")
    op.execute("DROP FUNCTION amaze_require_feed_of_source()")
