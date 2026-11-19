"""FourEyesRepository: the REQUIRE_FOUR_EYES setting, as the database guards read it (migration 0020).

The value lives in amaze_settings (append-only, versioned, latest row wins; no row means on). The application and
the same-approver trigger on approvals both ask the database function amaze_four_eyes(), so they cannot disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.repositories.tables import AmazeSettingRow


@dataclass(frozen=True)
class FourEyesSetting:
    version: int
    require_four_eyes: bool
    set_by: str
    set_at: datetime
    reason: str


class FourEyesRepository:
    def __init__(self, session: Session):
        self._session = session

    def current(self) -> bool:
        """True when a second person must decide the next step. No row: true."""
        return bool(self._session.execute(text("SELECT amaze_four_eyes()")).scalar_one())

    def history(self) -> list[FourEyesSetting]:
        rows = self._session.scalars(select(AmazeSettingRow).order_by(AmazeSettingRow.version))
        return [FourEyesSetting(r.version, r.require_four_eyes, r.set_by, r.set_at, r.reason) for r in rows]

    def set(self, require: bool, *, set_by: str, reason: str) -> bool:
        """Append the next version when the value differs from the current one; True when a row was written."""
        # two writers (two API processes starting together) take turns, so the version they pick is the next one
        self._session.execute(text("SELECT pg_advisory_xact_lock(hashtext('amaze_settings'))"))
        if self.current() == require:
            return False
        latest = self._session.scalar(select(func.max(AmazeSettingRow.version))) or 0
        self._session.add(AmazeSettingRow(version=latest + 1, require_four_eyes=require, set_by=set_by, reason=reason))
        self._session.flush()
        return True
