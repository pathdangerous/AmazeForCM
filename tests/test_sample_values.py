"""Phase 7.8, task 3.2 (decision D3): SEND_SAMPLE_VALUES, default false. When true at most 5 distinct non-empty
values per column reach a model; when false none does, anywhere in the messages."""

from __future__ import annotations

from agents.prompt_loader import PromptLibrary
from core.domain.profiling import FileColumn
from core.steps.s02_profile import summary_input
from core.steps.samples import SAMPLE_LIMIT, samples_for_model
from ports.config import Settings
from ports.storage.local import LocalBlobStore
from tests.step_chains import end_read
from tests.test_agent_profiler import master_feed

SAMPLE_TEXTS = ("100001 SY Equity", "Retail", "BRINETH GROUP")


def settings(on: bool) -> Settings:
    return Settings(_env_file=None, send_sample_values=on)


def column(name: str, samples: list[str]) -> FileColumn:
    return FileColumn(name=name, type="VARCHAR", null_count=0, null_rate=0.0, distinct_count=len(samples), samples=samples)


def test_the_setting_defaults_to_false():
    assert Settings(_env_file=None).send_sample_values is False


def test_samples_for_model_is_empty_when_false():
    assert samples_for_model([column("a", ["x", "y"])], settings(False)) == {}


def test_samples_for_model_keeps_five_non_blank_values_per_column_when_true():
    columns = [column("a", ["", "x", "  ", "y", "z", "u", "v", "w"]), column("b", []), column("c", ["q"])]
    got = samples_for_model(columns, settings(True))
    assert got == {"a": ["x", "y", "z", "u", "v"], "c": ["q"]}
    assert SAMPLE_LIMIT == 5


def test_none_of_the_files_values_is_in_the_messages_when_false(db_session, tmp_path):
    feed_id = master_feed(db_session, LocalBlobStore(tmp_path / "blob"))
    agent_input = summary_input(db_session, feed_id=feed_id, settings=settings(False))
    end_read(db_session)
    assert agent_input.samples == {} and all(c.samples == [] for c in agent_input.columns)
    text = " ".join(m.content for m in PromptLibrary().get("profiler").render(agent_input))
    for value in SAMPLE_TEXTS:
        assert value not in text
    # and the default (no settings given) is the same
    default = summary_input(db_session, feed_id=feed_id)
    assert default.samples == {} and all(c.samples == [] for c in default.columns)


def test_the_transformation_agent_gets_no_values_when_false_and_at_most_five_per_mapped_column_when_true(
        db_session, glossary, tmp_path):
    """Task 4.3 (D3): the TransformationAgent's input follows the same switch, through samples_for_model()."""
    from core.steps.s10_transform import transform_input
    from tests.test_agent_transformation import master_mapped

    feed_id = master_mapped(db_session, tmp_path)
    off = transform_input(db_session, feed_id=feed_id, settings=settings(False))
    default = transform_input(db_session, feed_id=feed_id)
    assert off.samples == {} and default.samples == {}
    text = " ".join(m.content for m in PromptLibrary().get("transformation").render(off))
    for value in SAMPLE_TEXTS:
        assert value not in text
    on = transform_input(db_session, feed_id=feed_id, settings=settings(True))
    mapped = {c.source_field for c in on.columns}
    assert on.samples and set(on.samples) <= mapped  # no sample of an unmapped column (SHORTNAME, SECTOR) is sent
    assert all(0 < len(v) <= SAMPLE_LIMIT for v in on.samples.values())
    text = " ".join(m.content for m in PromptLibrary().get("transformation").render(on))
    assert any(value in text for value in SAMPLE_TEXTS if value in str(on.samples))
    assert "Retail" not in text  # SECTOR is not mapped, so its values stay home


def test_the_glossary_builder_gets_no_values_when_false_and_at_most_five_per_asked_column_when_true(
        db_session, glossary, tmp_path):
    """Task 6.1 (D3): the GlossaryBuilderAgent's input follows the same switch, through samples_for_model()."""
    from core.steps.s05_glossary import builder_input, claim_run, queue_run
    from tests.test_s05_glossary import d8_registered

    feed = d8_registered(db_session, tmp_path)
    queue_run(db_session, feed_id=feed.id, reason="registered", requested_by="dev-backend-team")
    run = claim_run(db_session, lease_seconds=60)
    end_read(db_session)
    off = builder_input(db_session, run, settings=settings(False)).agent_input
    default = builder_input(db_session, run).agent_input
    assert off.samples == {} and default.samples == {} and all(c.model_dump().get("samples") is None for c in off.columns)
    text = " ".join(m.content for m in PromptLibrary().get("glossary_builder").render(off))
    for value in SAMPLE_TEXTS:
        assert value not in text
    on = builder_input(db_session, run, settings=settings(True)).agent_input
    asked = {c.name for c in on.columns}
    assert on.samples and set(on.samples) <= asked
    assert all(0 < len(v) <= SAMPLE_LIMIT for v in on.samples.values())
    text = " ".join(m.content for m in PromptLibrary().get("glossary_builder").render(on))
    assert any(value in text for value in SAMPLE_TEXTS if value in str(on.samples))


def test_at_most_five_values_per_column_are_in_the_input_and_the_messages_when_true(db_session, tmp_path):
    feed_id = master_feed(db_session, LocalBlobStore(tmp_path / "blob"))
    agent_input = summary_input(db_session, feed_id=feed_id, settings=settings(True))
    end_read(db_session)
    assert agent_input.samples and all(0 < len(v) <= 5 for v in agent_input.samples.values())
    assert all(c.samples == [] for c in agent_input.columns)  # they travel in `samples` only
    text = " ".join(m.content for m in PromptLibrary().get("profiler").render(agent_input))
    for values in agent_input.samples.values():
        for value in values:
            assert value in text
    assert any(value in text for value in SAMPLE_TEXTS)
