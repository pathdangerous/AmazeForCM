"""Amaze platform API (backend team). Phase 1: read the glossary and the feed registry."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.deps import backend_user, get_db_session
from core.domain.feed import Feed, FeedStage
from core.domain.glossary import GlossaryElement
from core.repositories.errors import UnknownGlossaryElement
from core.repositories.feeds import FeedRepository
from core.repositories.glossary import GlossaryRepository

router = APIRouter(prefix="/api/amaze", tags=["amaze"], dependencies=[Depends(backend_user)])

DbSession = Annotated[Session, Depends(get_db_session)]


class DomainCount(BaseModel):
    domain: str
    label: str
    count: int


class ElementDetail(BaseModel):
    element: GlossaryElement
    history: list[GlossaryElement]


@router.get("/glossary/domains", response_model=list[DomainCount])
def glossary_domains(session: DbSession) -> list[DomainCount]:
    return [
        DomainCount(domain=domain, label=domain.replace("_", " ").capitalize(), count=count)
        for domain, count in GlossaryRepository(session).domains()
    ]


@router.get("/glossary/elements", response_model=list[GlossaryElement])
def glossary_elements(session: DbSession, domain: str | None = None, q: str | None = None) -> list[GlossaryElement]:
    return GlossaryRepository(session).list_current(domain=domain, q=q)


@router.get("/glossary/elements/{key}", response_model=ElementDetail)
def glossary_element(key: str, session: DbSession) -> ElementDetail:
    repo = GlossaryRepository(session)
    try:
        return ElementDetail(element=repo.get_current(key), history=repo.history(key))
    except UnknownGlossaryElement as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("/feeds", response_model=list[Feed])
def feeds(session: DbSession, stage: FeedStage | None = None) -> list[Feed]:
    return FeedRepository(session).list(stage=stage)
