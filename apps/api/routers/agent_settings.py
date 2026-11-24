"""GET /api/amaze/settings (phase 7.18, T3): read only. Each agent, which provider answers it (mock, azure_openai or
external), and whether EXTERNAL_AGENT_FOR is set. Selector values and agent names only: never a key, URL or secret."""

from __future__ import annotations

import importlib
import pkgutil
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import agents
from agents.base import Agent
from agents.registry import AGENT_PROMPTS
from apps.api.deps import backend_user, get_app_settings
from ports.config import Settings
from ports.identity import User

router = APIRouter(prefix="/api/amaze", tags=["settings"])


class AgentSetting(BaseModel):
    name: str
    autonomy: str
    approver_roles: list[str]
    provider: str  # mock | azure_openai | external
    external: bool


class AgentSettings(BaseModel):
    llm_provider: str
    external_agent_provider: str  # none | hexaware
    external_agent_for: str  # an agent name (empty when none)
    external_agent_set: bool  # EXTERNAL_AGENT_FOR is not empty
    external_agent_known: bool  # it names a registered agent
    external_agent_active: bool  # set, known and a provider other than none: that agent is answered externally
    agents: list[AgentSetting]


def _agent_classes() -> dict[str, type[Agent]]:
    for module in pkgutil.iter_modules(agents.__path__):
        importlib.import_module(f"agents.{module.name}")
    found, todo = {}, list(Agent.__subclasses__())
    while todo:
        cls = todo.pop()
        todo.extend(cls.__subclasses__())
        if getattr(cls, "name", None) in AGENT_PROMPTS:
            found[cls.name] = cls
    return found


@router.get("/settings", response_model=AgentSettings)
def agent_settings(_: Annotated[User, Depends(backend_user)],
                   settings: Annotated[Settings, Depends(get_app_settings)]) -> AgentSettings:
    classes = _agent_classes()
    name = settings.external_agent_for
    active = bool(name) and name in classes and settings.external_agent_provider != "none"
    rows = [AgentSetting(name=n, autonomy=classes[n].autonomy, approver_roles=list(classes[n].approver_roles),
                         provider="external" if active and n == name else settings.llm_provider,
                         external=active and n == name)
            for n in sorted(AGENT_PROMPTS) if n in classes]
    return AgentSettings(llm_provider=settings.llm_provider, external_agent_provider=settings.external_agent_provider,
                         external_agent_for=name, external_agent_set=bool(name), external_agent_known=name in classes,
                         external_agent_active=active, agents=rows)
