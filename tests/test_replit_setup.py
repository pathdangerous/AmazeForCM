"""Replit bootstrap refuses to overwrite a populated runtime."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
# On Windows the `bash` on PATH is WSL's, which cannot read Windows paths.
needs_bash = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                                reason="needs bash (Linux/Replit only)")


@needs_bash
def test_setup_script_exists_and_is_valid_bash():
    script = ROOT / "scripts/setup_replit.sh"
    assert script.is_file()
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


@needs_bash
def test_setup_refuses_populated_target_without_touching_it(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    marker = runtime / "preserve"
    marker.write_text("keep")
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/setup_replit.sh")],
        env={**os.environ, "RUNTIME_ROOT": str(runtime)},
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "not empty" in result.stderr
    assert marker.read_text() == "keep"
