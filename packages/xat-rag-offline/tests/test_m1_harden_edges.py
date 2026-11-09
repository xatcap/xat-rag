import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from xat_rag_offline import TRACING_VARIABLES, DuplicateAccessKeyError, harden_environment

# `harden_process` installs an audit hook that cannot be removed. These tests run it in a child process.
_CHILD = textwrap.dedent(
    """
    import json, os, sys
    from pathlib import Path
    from xat_rag_offline import harden_process, BlockedOperationError

    data = sys.argv[1]
    hardening = harden_process(Path(data), allowed_libraries=["liballowed.so"])
    result = {
        "access_key": hardening.access_key,
        "hf_home": os.environ.get("HF_HOME"),
        "has_access_key_var": any(n.upper() == "XATRAG__ACCESSKEY" for n in os.environ),
        "tracing": os.environ.get("LANGSMITH_TRACING"),
        "offline": os.environ.get("HF_HUB_OFFLINE"),
        "enabled": hardening.guard.enabled,
        "token_left": "HF_TOKEN" in os.environ,
    }
    try:
        import ctypes
        ctypes.CDLL(None)
        result["dlopen_none"] = "allowed"
    except BlockedOperationError:
        result["dlopen_none"] = "blocked"
    except TypeError:  # On Windows, ctypes refuses None before the guard sees it.
        if os.name != "nt":
            raise
        result["dlopen_none"] = "allowed"
    try:
        import ctypes
        ctypes.CDLL("libother.so")
    except BlockedOperationError:
        result["dlopen_other"] = "blocked"
    except OSError:
        result["dlopen_other"] = "not blocked"
    print(json.dumps(result))
    """
)


def _run(data_folder: str, env: dict[str, str]) -> dict[str, object]:
    kept = ("PATH", "VIRTUAL_ENV", "HOME", "SYSTEMROOT")
    base = {key: value for key, value in os.environ.items() if key.upper().startswith(kept)}
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, data_folder],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**base, **env},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_harden_process_sets_hf_home_below_the_data_folder(tmp_path: Path) -> None:
    result = _run(str(tmp_path), {"HF_HOME": "/somewhere/else"})
    assert result["hf_home"] == str(tmp_path / "hf")


def test_harden_process_returns_the_access_key_and_removes_it_from_the_environment(tmp_path: Path) -> None:
    result = _run(str(tmp_path), {"XatRag__AccessKey": "secret", "HF_TOKEN": "t", "LANGSMITH_TRACING": "true"})
    assert result["access_key"] == "secret"
    assert result["has_access_key_var"] is False
    assert result["token_left"] is False
    assert result["tracing"] == "false"
    assert result["offline"] == "1"
    assert result["enabled"] is True


def test_harden_process_without_an_access_key_returns_none(tmp_path: Path) -> None:
    assert _run(str(tmp_path), {})["access_key"] is None


def test_harden_process_passes_the_library_list_to_the_guard(tmp_path: Path) -> None:
    result = _run(str(tmp_path), {})
    assert result["dlopen_none"] == "allowed"
    assert result["dlopen_other"] == "blocked"


def test_hf_home_is_not_changed_when_the_folder_is_none_and_not_set_when_absent() -> None:
    env: dict[str, str] = {}
    harden_environment(env, hf_home=None)
    assert "HF_HOME" not in env
    env = {"HF_HOME": "/keep"}
    harden_environment(env, hf_home=None)
    assert env["HF_HOME"] == "/keep"


def test_hf_home_overwrites_an_old_value_and_uses_the_path_as_given() -> None:
    env = {"HF_HOME": "/old"}
    harden_environment(env, hf_home=Path("/data") / "hf")
    assert env["HF_HOME"] == str(Path("/data") / "hf")


def test_all_variables_that_point_to_another_server_are_deleted() -> None:
    env = {
        "HF_ENDPOINT": "http://evil",
        "OPENAI_BASE_ENDPOINT": "x",
        "LANGCHAIN_ENDPOINT": "x",
        "langsmith_api_key": "x",
        "ANTHROPIC_API_KEY": "x",
        "GITHUB_TOKEN": "x",
        "HOME": "/home/u",
    }
    harden_environment(env, hf_home=None)
    assert set(env) - set(TRACING_VARIABLES) >= {"HOME"}
    for name in (
        "HF_ENDPOINT",
        "OPENAI_BASE_ENDPOINT",
        "LANGCHAIN_ENDPOINT",
        "langsmith_api_key",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
    ):
        assert name not in env


def test_two_spellings_of_the_access_key_are_refused() -> None:
    env = {"XATRAG__ACCESSKEY": "a", "XatRag__AccessKey": "b"}
    with pytest.raises(DuplicateAccessKeyError):
        harden_environment(env, hf_home=None)


def test_own_access_key_is_not_deleted_by_the_suffix_rule_before_it_is_read() -> None:
    env = {"XatRag__AccessKey": "k", "XatRag__Other_API_KEY": "keep"}
    assert harden_environment(env, hf_home=None) == "k"
    assert env["XatRag__Other_API_KEY"] == "keep"


def test_environment_hardening_can_run_twice() -> None:
    env = {"LANGSMITH_TRACING": "true"}
    harden_environment(env, hf_home=Path("/d/hf"))
    snapshot = dict(env)
    assert harden_environment(env, hf_home=Path("/d/hf")) is None
    assert env == snapshot
