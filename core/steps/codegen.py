"""Generated code of a feed, from its approved configuration (phase 7.12, T3). No model: the text comes from the engine's own
functions (core/engine/codegen.py). A go-live approval stores it as the feed's next code version and pins it.

code_for_feed()       read only: the code the engine would run now (the preview the owner sees before approving)
engine_plan_for()     read only: the LoadPlan the engine runs for a load, from the same configuration (for the proof test)
store_code_version()  the owner's approval: the next code_versions row (the caller holds the feed lock, then adds the go-live)
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from core.domain.code_version import CodeVersion
from core.domain.load import Load
from core.engine.codegen import GeneratedCode, generate_code
from core.engine.run import LoadPlan, configuration_of
from core.repositories.code_versions import CodeVersionRepository
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feeds import FeedRepository
from core.repositories.loads import LoadRepository
from core.repositories.standard_records import StandardRecordRepository


def code_for_feed(session: Session, feed_id: UUID) -> GeneratedCode:
    feed = FeedRepository(session).get(feed_id)
    profile = FeedProfileRepository(session).latest(feed_id)
    return generate_code(feed_code=feed.code, feed_name=feed.name, configuration=configuration_of(session, feed_id),
                         header=[c.name for c in profile.columns] if profile else [])


def engine_plan_for(session: Session, feed_id: UUID, load: Load) -> LoadPlan:
    """The plan the engine runs for `load` with the feed's current configuration, whatever the load's status."""
    config = configuration_of(session, feed_id)
    return LoadPlan(load=load, configuration_changed=False, columns=config.columns, rules=config.rules,
                    transformations=config.transformations, key_policy=config.key_policy)


def store_code_version(session: Session, *, feed_id: UUID, approved_by: str, test_load_id: UUID) -> CodeVersion:
    """The code of the configuration being approved, with the test load's result, as the feed's next version."""
    code = code_for_feed(session, feed_id)
    config = configuration_of(session, feed_id)
    load = LoadRepository(session).get(test_load_id)
    result = {"load_id": str(load.id), "status": load.status, "rows": StandardRecordRepository(session).count(load.id)}
    return CodeVersionRepository(session).add_version(
        feed_id=feed_id, files=code.files, sha256=code.sha256, mapping_ids=[c.mapping_id for c in config.columns],
        rule_ids=[r.rule_id for r in config.rules], transformation_ids=[t.transformation_id for t in config.transformations],
        test_load_id=test_load_id, test_load_result=result, approved_by=approved_by)
