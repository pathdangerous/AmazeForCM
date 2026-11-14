"""Agent registry and prompt versions (phase 3 plan, assumption 24).

AGENT_PROMPTS: each agent and the prompt id it uses.
PROMPT_VERSIONS: the current version of each prompt id; Agent.run() uses this one.
PROMPT_FILE_HASHES: every file in agents/prompts and its hash (line endings read as LF). A prompt file
is never edited: a change is a new file <id>.v<n+1>.yaml, a new hash here, and a new current version.
tests/test_prompts.py fails on any file that is missing, extra or changed.
"""

from __future__ import annotations

AGENT_PROMPTS: dict[str, str] = {
    "IntentAgent": "intent",                    # S1 scope
    "SourceDiscoveryAgent": "source_discovery",  # S2 source list
    "DiscoveryAssistant": "discovery_assistant",  # step 17, read only
}

PROMPT_VERSIONS: dict[str, str] = {
    "intent": "v1",
    "source_discovery": "v1",
    "discovery_assistant": "v1",
}

PROMPT_FILE_HASHES: dict[str, str] = {
    "discovery_assistant.v1.yaml": "ad80a7a9430302ee8b84bf1eb0aaf70806be15631dd766df4ceba794156f0af0",
    "intent.v1.yaml": "40ae53c05f86abac70d7993f22e0c78bd8456252d0aaa94f10627a949f0dbb05",
    "source_discovery.v1.yaml": "80fa3af7fdc9ba1429024fd0fe65bf6fbd40137bc9f8ddf91ee56b0103112d61",
}
