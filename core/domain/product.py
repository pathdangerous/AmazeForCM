"""Product: a data product end users search for and reuse (steps 17 to 22).

Entitlement (who may see it) arrives with phase 2; creation from a released load with phase 7.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from core.domain.common import NonBlank, SnakeName


class ProductDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: SnakeName
    name: NonBlank
    domain: SnakeName
    description: str = ""
    requirement_id: UUID | None = None


class Product(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    created_at: datetime
    code: str
    name: str
    domain: str
    description: str
    requirement_id: UUID | None
