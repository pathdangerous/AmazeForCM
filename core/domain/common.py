"""Field types shared by the domain models."""

from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

SNAKE = r"^[a-z][a-z0-9_]*$"

SnakeName = Annotated[str, StringConstraints(pattern=SNAKE)]
"""Lower-case snake_case: keys, codes, domains and aliases."""

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
"""Text that must not be empty or only spaces."""
