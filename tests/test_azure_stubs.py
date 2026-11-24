"""Every Azure adapter is a stub until the move (CLAUDE.md, Placeholders):
same public methods and signatures as the local adapter, env keys read in the constructor,
every key listed in .env.example, every method raising the exact NotImplementedError,
and no Azure or OpenAI SDK import.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

from ports.config import AZURE_NOT_IMPLEMENTED, read_env

REPO_ROOT = Path(__file__).resolve().parents[1]

# (local adapter, Azure stub). Each port adds its row when it is built. The LLM pair left in phase 7.8 task 1.2:
# AzureOpenAIProvider is the real adapter now (tests/test_port_llm_azure.py); its guarantees are kept below.
PAIRS = [
    ("ports.secrets.env:EnvSecrets", "ports.secrets.key_vault:KeyVaultSecrets"),
    ("ports.storage.local:LocalBlobStore", "ports.storage.azure_blob:AzureBlobStore"),
    ("ports.scheduler.local:LocalScheduler", "ports.scheduler.azure_functions:AzureFunctionsScheduler"),
    ("ports.identity.dev:DevIdentity", "ports.identity.entra_id:EntraIdIdentity"),
    ("ports.notify.inapp:InAppNotifier", "ports.notify.email_teams:EmailNotifier"),
    ("ports.pipeline.local:LocalGitPipeline", "ports.pipeline.azure_devops:AzureDevOpsPipeline"),  # phase 7.17
]


def _load(ref: str):
    module_name, class_name = ref.split(":")
    return getattr(importlib.import_module(module_name), class_name)


def _public_methods(cls) -> dict[str, inspect.Signature]:
    return {
        name: inspect.signature(member)
        for name, member in inspect.getmembers(cls, inspect.isfunction)
        if not name.startswith("_")
    }


def _dummy_call(method, signature: inspect.Signature):
    args, kwargs = [], {}
    for param in list(signature.parameters.values())[1:]:  # skip self
        if param.default is not inspect.Parameter.empty:
            continue
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            kwargs[param.name] = None
        elif param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            args.append(None)
    return method(*args, **kwargs)


def _env_example_keys() -> set[str]:
    return set(read_env(env_file=REPO_ROOT / ".env.example", include_process_env=False))


IDS = [stub.split(":")[1] for _, stub in PAIRS]


@pytest.mark.parametrize(("local_ref", "stub_ref"), PAIRS, ids=IDS)
def test_stub_declares_is_stub_and_local_adapter_does_not(local_ref, stub_ref):
    """/api/health reports every adapter with is_stub = True as unavailable."""
    assert _load(stub_ref).is_stub is True
    assert getattr(_load(local_ref), "is_stub", False) is False


@pytest.mark.parametrize(("local_ref", "stub_ref"), PAIRS, ids=IDS)
def test_stub_has_same_public_methods_and_signatures(local_ref, stub_ref):
    local, stub = _load(local_ref), _load(stub_ref)
    assert _public_methods(stub) == _public_methods(local)


@pytest.mark.parametrize(("local_ref", "stub_ref"), PAIRS, ids=IDS)
def test_every_stub_method_raises_not_implemented(local_ref, stub_ref, tmp_path):
    stub = _load(stub_ref)(env_file=tmp_path / "missing.env")
    methods = _public_methods(type(stub))
    assert methods, "stub has no public methods"
    for name, signature in methods.items():
        with pytest.raises(NotImplementedError) as excinfo:
            _dummy_call(getattr(stub, name), signature)
        assert str(excinfo.value) == AZURE_NOT_IMPLEMENTED, name


@pytest.mark.parametrize(("local_ref", "stub_ref"), PAIRS, ids=IDS)
def test_stub_reads_its_env_keys(local_ref, stub_ref, tmp_path, monkeypatch):
    stub_cls = _load(stub_ref)
    assert stub_cls.ENV_KEYS, "stub declares no env keys"
    for key in stub_cls.ENV_KEYS:
        monkeypatch.setenv(key, f"value-of-{key}")
    stub = stub_cls(env_file=tmp_path / "missing.env")
    assert stub.config == {key: f"value-of-{key}" for key in stub_cls.ENV_KEYS}


@pytest.mark.parametrize(("local_ref", "stub_ref"), PAIRS, ids=IDS)
def test_stub_env_keys_are_in_env_example(local_ref, stub_ref):
    missing = set(_load(stub_ref).ENV_KEYS) - _env_example_keys()
    assert not missing, f"add to .env.example: {sorted(missing)}"


@pytest.mark.parametrize(("local_ref", "stub_ref"), PAIRS, ids=IDS)
def test_stub_name_differs_from_local(local_ref, stub_ref):
    assert _load(stub_ref).name != _load(local_ref).name


@pytest.mark.parametrize(("local_ref", "stub_ref"), PAIRS, ids=IDS)
def test_stub_imports_no_azure_or_openai_sdk(local_ref, stub_ref):
    module = importlib.import_module(stub_ref.split(":")[0])
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module)
    sdk = {name for name in imported if name.split(".")[0] in {"azure", "openai", "msal"}}
    assert not sdk, f"stub imports an SDK: {sorted(sdk)}"


# ---- phase 7.8 task 1.2: what the stub tests guaranteed for the LLM adapter, kept now that it is real

OPENAI_ALLOWED = {"ports/llm/azure_openai.py"}
SCANNED_FOR_OPENAI = ("agents", "apps", "core", "ports", "e2e", "tests")


def _openai_importers(root: Path) -> list[str]:
    found = []
    for top in SCANNED_FOR_OPENAI:
        for path in sorted((root / top).rglob("*.py")):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names = [node.module]
                else:
                    continue
                if any(name.split(".")[0] == "openai" for name in names) and rel not in OPENAI_ALLOWED:
                    found.append(f"{rel}:{node.lineno}")
    return found


def test_only_the_azure_openai_adapter_imports_the_openai_sdk():
    assert _openai_importers(REPO_ROOT) == []


def test_the_openai_check_catches_a_planted_import(tmp_path):
    for top in SCANNED_FOR_OPENAI:
        (tmp_path / top).mkdir()
    (tmp_path / "agents" / "x.py").write_text("from openai import AzureOpenAI\n", encoding="utf-8")
    (tmp_path / "core" / "y.py").write_text("import openai.lib\n", encoding="utf-8")
    assert _openai_importers(tmp_path) == ["agents/x.py:1", "core/y.py:1"]


def test_the_azure_openai_adapter_does_import_openai():
    """Otherwise the check above would pass with nothing to protect."""
    tree = ast.parse((REPO_ROOT / "ports" / "llm" / "azure_openai.py").read_text(encoding="utf-8"))
    assert any(isinstance(n, (ast.Import, ast.ImportFrom)) and "openai" in ast.dump(n) for n in ast.walk(tree))


def test_every_real_model_setting_is_in_the_readme():
    from ports.llm.azure_openai import AzureOpenAIProvider

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    keys = [*AzureOpenAIProvider.ENV_KEYS, "LLM_TIMEOUT_SECONDS", "LLM_RETRY_AFTER_CAP_SECONDS",
            "MAX_MODEL_CALLS_PER_ONBOARDING"]
    missing = [key for key in keys if f"`{key}`" not in readme]
    assert not missing, f"add to the README settings table: {missing}"
