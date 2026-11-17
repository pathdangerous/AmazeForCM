"""FastAPI dependencies. Tests override get_app_settings, get_db_engine and get_db_session."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agents.discovery_assistant import DiscoveryAssistant
from agents.intent import IntentAgent
from agents.mapping import MappingAgent
from agents.profiler import ProfilerAgent
from agents.review_summariser import ReviewSummariserAgent
from agents.rule_recommender import RuleRecommenderAgent
from agents.source_discovery import SourceDiscoveryAgent
from apps.api.jobs import DailyFetch
from ports.config import Settings, get_settings
from ports.llm import LLMProvider, get_llm_provider
from ports.connectors import SourceConnector, get_connector
from ports.connectors.catalogue import SourceCatalogue
from ports.db import get_engine
from ports.identity import Identity, User
from ports.notify import Notifier, get_notifier
from ports.secrets import SecretNotFound
from ports.storage import BlobStore, get_blob_store

log = logging.getLogger(__name__)


def get_app_settings() -> Settings:
    return get_settings()


def get_db_engine() -> Engine | None:
    """The database engine, or None if DATABASE_URL cannot be read (not set, or the Secrets
    adapter is still a stub)."""
    try:
        return get_engine()
    except (SecretNotFound, NotImplementedError):
        return None


def get_db_session(engine: Annotated[Engine | None, Depends(get_db_engine)]) -> Iterator[Session]:
    """One session per request, closed afterwards. Read endpoints never commit."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured")
    with Session(bind=engine) as session:
        yield session


def get_identity_port(request: Request) -> Identity:
    return request.app.state.identity


def current_user(request: Request) -> User:
    user = get_identity_port(request).authenticate(request.headers)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def backend_user(user: Annotated[User, Depends(current_user)]) -> User:
    """Amaze (backend-ui) endpoints: any backend role; the requester gets 403."""
    if user.app != "backend":
        raise HTTPException(status_code=403, detail="Backend role required")
    return user


def requester_user(user: Annotated[User, Depends(current_user)]) -> User:
    """Consumption (consumer-ui) endpoints: the requester only; every backend role gets 403."""
    if user.app != "consumer":
        raise HTTPException(status_code=403, detail="Requester role required")
    return user


def get_request_notifier(
    session: Annotated[Session, Depends(get_db_session)], settings: Annotated[Settings, Depends(get_app_settings)]
) -> Notifier:
    """The Notifier selected by NOTIFY_PROVIDER, on the request's session, so in-app notifications are
    committed or rolled back with the rest of the request."""
    return get_notifier(settings, session=session)


# ---- phase 3: the custom data product. Each is a dependency so tests can replace it.


def get_source_catalogue(settings: Annotated[Settings, Depends(get_app_settings)]) -> SourceCatalogue:
    return SourceCatalogue.load(settings.source_catalogue, allowed_hosts=settings.allowed_source_hosts())


def get_blob_store_port(settings: Annotated[Settings, Depends(get_app_settings)]) -> BlobStore:
    return get_blob_store(settings)


def get_connector_factory(settings: Annotated[Settings, Depends(get_app_settings)]) -> Callable[[str], SourceConnector]:
    return lambda name: get_connector(name, settings)


# ---- phase 3b: the agents. The API builds each agent and passes it to its step (assumption 22).


def get_llm_provider_port(settings: Annotated[Settings, Depends(get_app_settings)]) -> LLMProvider:
    return get_llm_provider(settings)


def get_intent_agent(provider: Annotated[LLMProvider, Depends(get_llm_provider_port)],
                     blob_store: Annotated[BlobStore, Depends(get_blob_store_port)]) -> IntentAgent:
    return IntentAgent(provider=provider, blob_store=blob_store)


def get_discovery_assistant(provider: Annotated[LLMProvider, Depends(get_llm_provider_port)],
                            blob_store: Annotated[BlobStore, Depends(get_blob_store_port)]) -> DiscoveryAssistant:
    return DiscoveryAssistant(provider=provider, blob_store=blob_store)


def get_profiler_agent(provider: Annotated[LLMProvider, Depends(get_llm_provider_port)],
                       blob_store: Annotated[BlobStore, Depends(get_blob_store_port)]) -> ProfilerAgent:
    return ProfilerAgent(provider=provider, blob_store=blob_store)


def backend_team_user(user: Annotated[User, Depends(backend_user)]) -> User:
    """Steps 02 to 04 act for the backend team only; every other backend role reads (phase 4 plan, assumption 3)."""
    if "backend_team" not in user.roles:
        raise HTTPException(status_code=403, detail="Backend team role required")
    return user


def get_source_discovery_agent(provider: Annotated[LLMProvider, Depends(get_llm_provider_port)],
                               blob_store: Annotated[BlobStore, Depends(get_blob_store_port)]) -> SourceDiscoveryAgent:
    return SourceDiscoveryAgent(provider=provider, blob_store=blob_store)


# ---- phase 5: steps 05 and 06 (plan, section 9)


def get_mapping_agent(provider: Annotated[LLMProvider, Depends(get_llm_provider_port)],
                      blob_store: Annotated[BlobStore, Depends(get_blob_store_port)]) -> MappingAgent:
    return MappingAgent(provider=provider, blob_store=blob_store)


def get_glossary_kick(request: Request) -> Callable[[], None]:
    """Wakes the step 05 worker (app.state.glossary_runs) through the scheduler. Called after a commit that
    queued a run; tests replace it to record the kicks. A kick that fails (the stub scheduler's
    NotImplementedError included) is logged and swallowed: the write is already committed, the answer stays
    200, and the run is taken by the next kick or start-up (phase 6 plan, assumption 30)."""
    from apps.api.jobs import kick_glossary_runs

    def kick() -> None:
        try:
            kick_glossary_runs(request.app.state.scheduler, request.app.state.glossary_runs)
        except Exception:
            log.exception("the kick after a committed write failed; the run waits for the next kick")

    return kick


# ---- phase 6: steps 09, 11 and 13 (plan, section 9)


def get_rule_agent(provider: Annotated[LLMProvider, Depends(get_llm_provider_port)],
                   blob_store: Annotated[BlobStore, Depends(get_blob_store_port)]) -> RuleRecommenderAgent:
    return RuleRecommenderAgent(provider=provider, blob_store=blob_store)


def get_review_summariser(provider: Annotated[LLMProvider, Depends(get_llm_provider_port)],
                          blob_store: Annotated[BlobStore, Depends(get_blob_store_port)]) -> ReviewSummariserAgent:
    return ReviewSummariserAgent(provider=provider, blob_store=blob_store)


def get_daily_fetch(request: Request) -> DailyFetch:
    """The job's code, for operations' "Load now": app.state.daily_fetch when a test (or start-up) set one, else
    one built from the settings."""
    daily = getattr(request.app.state, "daily_fetch", None)
    return daily or DailyFetch.from_settings(get_settings(), request.app.state.identity)


def operations_user(user: Annotated[User, Depends(backend_user)]) -> User:
    """Load now: operations only."""
    if "operations" not in user.roles:
        raise HTTPException(status_code=403, detail="Operations role required")
    return user


def glossary_run_user(user: Annotated[User, Depends(backend_user)]) -> User:
    """Run the glossary builder: the glossary owner or the backend team."""
    if not {"glossary_owner", "backend_team"} & set(user.roles):
        raise HTTPException(status_code=403, detail="Glossary owner or backend team role required")
    return user


def data_steward_user(user: Annotated[User, Depends(backend_user)]) -> User:
    """Propose mappings (step 06): the data steward only."""
    if "data_steward" not in user.roles:
        raise HTTPException(status_code=403, detail="Data steward role required")
    return user
