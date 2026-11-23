"""Versioned prompt files (phase 3 plan, assumption 24).

One file per version, agents/prompts/<prompt_id>.<version>.yaml, with a system text and a user template
that ends with the agent's input as JSON. A new version is a new file: the registry holds a hash of
every prompt file, so an edited file fails here until it is saved as a new version.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from agents import registry
from agents.prompt_loader import (
    INPUT_MARKER,
    PROMPTS_ROOT,
    InvalidPromptFile,
    PromptLibrary,
    UnknownPrompt,
    load_prompt,
    normalised_sha256,
)
from ports.config import REPO_ROOT

FILE_NAME = re.compile(r"^(?P<id>[a-z][a-z0-9_]*)\.(?P<version>v[0-9]+)\.yaml$")


def _prompt_files():
    return sorted(p for p in PROMPTS_ROOT.iterdir() if p.suffix == ".yaml")


def test_every_prompt_file_is_named_like_id_dot_version():
    assert _prompt_files(), "no prompt files found"
    for path in _prompt_files():
        assert FILE_NAME.match(path.name), path.name


def test_every_prompt_file_is_hashed_in_the_registry_and_unchanged():
    on_disk = {path.name: normalised_sha256(path) for path in _prompt_files()}
    assert on_disk == registry.PROMPT_FILE_HASHES, (
        "A prompt file was added, removed or edited. Never edit a prompt file: save a new version "
        "(<id>.v<n+1>.yaml) and add its hash to agents/registry.py"
    )


def test_the_hash_ignores_line_endings(tmp_path):
    lf, crlf = tmp_path / "a.yaml", tmp_path / "b.yaml"
    lf.write_bytes(b"x: 1\ny: 2\n")
    crlf.write_bytes(b"x: 1\r\ny: 2\r\n")
    assert normalised_sha256(lf) == normalised_sha256(crlf) == hashlib.sha256(b"x: 1\ny: 2\n").hexdigest()


def test_every_current_version_has_its_file_and_loads():
    for prompt_id, version in registry.PROMPT_VERSIONS.items():
        prompt = PromptLibrary().get(prompt_id)
        assert (prompt.prompt_id, prompt.version) == (prompt_id, version)
        assert prompt.system.strip() and prompt.user.rstrip().endswith(INPUT_MARKER)


def test_every_agent_in_the_registry_names_a_current_prompt():
    assert set(registry.AGENT_PROMPTS) == {"IntentAgent", "SourceDiscoveryAgent", "DiscoveryAssistant", "ProfilerAgent",
                                           "GlossaryBuilderAgent", "MappingAgent", "MappingEvaluator",
                                           "RuleRecommenderAgent", "ReviewSummariserAgent",  # phase 6: steps 09, 11
                                           "TableExtractorAgent",  # phase 7.8, task 2.3
                                           "TransformationAgent"}  # phase 7.8, task 4.3 (old: without it)
    assert (registry.AGENT_PROMPTS["TransformationAgent"], registry.PROMPT_VERSIONS["transformation"]) == (
        "transformation", "v1")
    assert registry.AGENT_PROMPTS["ReviewSummariserAgent"] == "review_summariser" and (
        registry.PROMPT_VERSIONS["review_summariser"] == "v2")  # old: v1; S8: v2 drops visibility
    assert (registry.AGENT_PROMPTS["RuleRecommenderAgent"], registry.PROMPT_VERSIONS["rule_recommender"]) == (
        "rule_recommender", "v1")
    assert (registry.AGENT_PROMPTS["ProfilerAgent"], registry.PROMPT_VERSIONS["profiler"]) == ("profiler", "v3")  # old: v2; T1: v3 adds actions
    # phase 5: the evaluator uses the mapping agent's prompt (assumption 37)
    assert [registry.AGENT_PROMPTS[a] for a in ("GlossaryBuilderAgent", "MappingAgent", "MappingEvaluator")] == [
        "glossary_builder", "mapping", "mapping"]
    # old: ("v1", "v1"); phase 7.8 task 6.1: glossary_builder.v2 (units, plain names, samples); v1 stays on disk
    assert (registry.PROMPT_VERSIONS["glossary_builder"], registry.PROMPT_VERSIONS["mapping"]) == ("v2", "v1")
    assert {"glossary_builder.v1.yaml", "glossary_builder.v2.yaml"} <= set(registry.PROMPT_FILE_HASHES)
    v2 = PromptLibrary().get("glossary_builder")
    for word in ("unit", "times", "percent", "consensus estimate", "samples"):
        assert word in v2.system, word
    for agent_name, prompt_id in registry.AGENT_PROMPTS.items():
        assert prompt_id in registry.PROMPT_VERSIONS, agent_name


def test_a_prompt_file_must_match_its_name(tmp_path):
    path = tmp_path / "echo.v2.yaml"
    path.write_text("prompt_id: echo\nversion: v1\nsystem: s\nuser: |\n  u\n  {input_json}\n", encoding="utf-8")
    with pytest.raises(InvalidPromptFile, match="version"):
        load_prompt(path)


@pytest.mark.parametrize("user", ["no marker at all", "{input_json}\nthen more text", "{input_json} {input_json}"])
def test_the_user_template_ends_with_the_input_marker_once(tmp_path, user):
    path = tmp_path / "echo.v1.yaml"
    body = "\n".join("  " + line for line in user.splitlines())
    path.write_text(f"prompt_id: echo\nversion: v1\nsystem: s\nuser: |\n{body}\n", encoding="utf-8")
    with pytest.raises(InvalidPromptFile, match="input_json"):
        load_prompt(path)


def test_a_prompt_file_needs_exactly_the_four_keys(tmp_path):
    path = tmp_path / "echo.v1.yaml"
    path.write_text("prompt_id: echo\nversion: v1\nsystem: s\nuser: '{input_json}'\nextra: 1\n", encoding="utf-8")
    with pytest.raises(InvalidPromptFile, match="extra"):
        load_prompt(path)


def test_an_unknown_prompt_id_is_refused():
    with pytest.raises(UnknownPrompt):
        PromptLibrary().get("no_such_prompt")


def test_pyyaml_is_pinned():
    lines = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "pyyaml==6.0.3" in lines
