import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from xat_rag_offline import harden_environment

# `harden_process` installs an audit hook that cannot be removed. These tests run it in a child process.
_CHILD = textwrap.dedent(
    """
    import json, os, sys
    from pathlib import Path
    from xat_rag_offline import harden_process

    data = sys.argv[1]
    guard = harden_process(Path(data))
    import platform
    platform.platform()  # A new process here would be refused by the guard.
    result = {
        "hf_home": os.environ.get("HF_HOME"),
        "offline": os.environ.get("HF_HUB_OFFLINE"),
        "enabled": guard.enabled,
        "token_left": "HF_TOKEN" in os.environ,
    }
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


def test_harden_process_removes_secrets_and_sets_offline_mode(tmp_path: Path) -> None:
    result = _run(str(tmp_path), {"HF_TOKEN": "t"})
    assert result["token_left"] is False
    assert result["offline"] == "1"
    assert result["enabled"] is True


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
    assert set(env) >= {"HOME"}
    for name in (
        "HF_ENDPOINT",
        "OPENAI_BASE_ENDPOINT",
        "LANGCHAIN_ENDPOINT",
        "langsmith_api_key",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
    ):
        assert name not in env


def test_own_settings_are_not_deleted_by_the_suffix_rule() -> None:
    env = {"XatRag__Other_API_KEY": "keep"}
    harden_environment(env, hf_home=None)
    assert env["XatRag__Other_API_KEY"] == "keep"


def test_environment_hardening_can_run_twice() -> None:
    env = {"HF_TOKEN": "t"}
    harden_environment(env, hf_home=Path("/d/hf"))
    snapshot = dict(env)
    harden_environment(env, hf_home=Path("/d/hf"))
    assert env == snapshot
