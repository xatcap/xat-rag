from pathlib import Path

import pytest
from xat_rag_offline import TRACING_VARIABLES, DuplicateAccessKeyError, Hardening, NetworkGuard, harden_environment


def test_tracing_is_forced_off_and_langsmith_variables_are_deleted() -> None:
    env = {
        "LANGSMITH_TRACING": "true",
        "langchain_tracing_v2": "true",
        "LANGSMITH_RUNS_ENDPOINTS": '{"https://x": "k"}',
        "LANGSMITH_ADDRESS": "x",
        "LANGSMITH_OTEL_ONLY": "true",
        "PATH": "/usr/bin",
    }
    harden_environment(env, hf_home=None)
    for name in TRACING_VARIABLES:
        assert env[name] == "false"
    assert "langchain_tracing_v2" not in env
    assert "LANGSMITH_RUNS_ENDPOINTS" not in env
    assert "LANGSMITH_ADDRESS" not in env
    assert "LANGSMITH_OTEL_ONLY" not in env
    assert env["PATH"] == "/usr/bin"


def test_offline_variables_overwrite_old_values() -> None:
    env = {"HF_HUB_OFFLINE": "0", "DO_NOT_TRACK": "0"}
    harden_environment(env, hf_home=Path("/data/hf"))
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["DO_NOT_TRACK"] == "1"
    assert env["HF_HOME"] == str(Path("/data/hf"))


def test_secrets_are_deleted_but_own_settings_stay() -> None:
    env = {
        "OPENAI_API_KEY": "s",
        "HF_TOKEN": "s",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://x",
        "XatRag__Some_TOKEN": "keep",
        "XATRAG__OTHER_API_KEY": "keep",
        "XatRag__DataFolder": "/d",
    }
    harden_environment(env, hf_home=None)
    assert "OPENAI_API_KEY" not in env
    assert "HF_TOKEN" not in env
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env
    assert env["XatRag__Some_TOKEN"] == "keep"
    assert env["XATRAG__OTHER_API_KEY"] == "keep"
    assert env["XatRag__DataFolder"] == "/d"


def test_access_key_is_returned_once_and_removed() -> None:
    env = {"XatRag__AccessKey": "secret"}
    assert harden_environment(env, hf_home=None) == "secret"
    assert "XatRag__AccessKey" not in env
    assert harden_environment(env, hf_home=None) is None


def test_access_key_name_is_case_insensitive() -> None:
    env = {"XATRAG__ACCESSKEY": "secret"}
    assert harden_environment(env, hf_home=None) == "secret"


def test_two_access_key_variables_are_refused() -> None:
    with pytest.raises(DuplicateAccessKeyError):
        harden_environment({"XatRag__AccessKey": "a", "XATRAG__ACCESSKEY": "b"}, hf_home=None)


def test_hardening_repr_hides_the_access_key() -> None:
    assert "secret" not in repr(Hardening(access_key="secret", guard=NetworkGuard()))
