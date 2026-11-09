"""Environment hardening: tracing off, offline settings, and removal of secrets."""

from collections.abc import Mapping, MutableMapping
from pathlib import Path
from types import MappingProxyType

TRACING_VARIABLES: tuple[str, ...] = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
)
"""The variables that turn on LangSmith tracing. They are set to `false`."""

_OFFLINE_VARIABLES: Mapping[str, str] = MappingProxyType(
    {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_UPDATE_CHECK": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "DO_NOT_TRACK": "1",
    }
)

_DELETED_PREFIXES: tuple[str, ...] = ("LANGSMITH_", "LANGCHAIN_")
_SECRET_SUFFIXES: tuple[str, ...] = ("_API_KEY", "_TOKEN", "_ENDPOINT", "_ENDPOINTS")
_OWN_PREFIX = "XATRAG__"


def harden_environment(environ: MutableMapping[str, str], hf_home: Path | None) -> None:
    """Change `environ` for service mode.

    The function overwrites old values. Names are compared without case, because Windows
    changes the case of environment names.
    """
    for name in list(environ):
        upper = name.upper()
        if upper.startswith(_DELETED_PREFIXES) or (
            upper.endswith(_SECRET_SUFFIXES) and not upper.startswith(_OWN_PREFIX)
        ):
            del environ[name]

    for name in TRACING_VARIABLES:
        environ[name] = "false"
    for name, value in _OFFLINE_VARIABLES.items():
        environ[name] = value
    if hf_home is not None:
        environ["HF_HOME"] = str(hf_home)
