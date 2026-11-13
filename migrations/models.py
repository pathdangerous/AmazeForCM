"""Every module that declares tables, imported so Base.metadata is complete.

Used by migrations/env.py (autogenerate) and by tests/test_migrations.py (models match migrations).
Add a line here whenever a new module declares a table.
"""

import core.proposal_log  # noqa: F401  proposal_log
from ports.db import Base

metadata = Base.metadata
