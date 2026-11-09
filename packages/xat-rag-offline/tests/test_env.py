from pathlib import Path

from xat_rag_offline import harden_environment


def test_secret_variables_are_deleted() -> None:
    env = {"LANGSMITH_API_KEY": "k", "hf_token": "t", "PATH": "/usr/bin"}
    harden_environment(env, hf_home=None)
    assert "LANGSMITH_API_KEY" not in env
    assert "hf_token" not in env
    assert env["PATH"] == "/usr/bin"


def test_offline_variables_overwrite_old_values() -> None:
    env = {"HF_HUB_OFFLINE": "0", "DO_NOT_TRACK": "0", "ORT_DISABLE_TELEMETRY": "0"}
    harden_environment(env, hf_home=Path("/data/hf"))
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["DO_NOT_TRACK"] == "1"
    assert env["ORT_DISABLE_TELEMETRY"] == "1"
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
