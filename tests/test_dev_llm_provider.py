"""The launcher selects a provider for its API child, not the caller or tests."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("override, expected", [(None, "mock"), ("azure_openai", "azure_openai")])
def test_api_provider_is_scoped_to_the_child(override, expected):
    source = Path("scripts/dev.sh").read_text()
    line = next(line for line in source.splitlines() if "-m uvicorn" in line)
    assert line.startswith('LLM_PROVIDER="${DEV_LLM_PROVIDER:-mock}" setsid ')
    env = {**os.environ, "LLM_PROVIDER": "mock"}
    env.pop("DEV_LLM_PROVIDER", None)
    if override:
        env["DEV_LLM_PROVIDER"] = override
    assignment = line.split(" setsid ", 1)[0]
    result = subprocess.run(
        ["bash", "-c", assignment + " bash -c 'printf \"%s\" \"$LLM_PROVIDER\"'; printf '/%s' \"$LLM_PROVIDER\""],
        env=env, capture_output=True, text=True, check=True,
    )
    assert result.stdout == f"{expected}/mock"
