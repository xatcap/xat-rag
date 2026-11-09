"""Environment hardening: offline settings and removal of secrets."""

from collections.abc import Mapping, MutableMapping
from pathlib import Path
from types import MappingProxyType

_OFFLINE_VARIABLES: Mapping[str, str] = MappingProxyType(
    {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_UPDATE_CHECK": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "DO_NOT_TRACK": "1",
        "ORT_DISABLE_TELEMETRY": "1",
    }
)

_SECRET_SUFFIXES: tuple[str, ...] = ("_API_KEY", "_TOKEN", "_ENDPOINT", "_ENDPOINTS")
_OWN_PREFIX = "XATRAG__"


def harden_environment(environ: MutableMapping[str, str], hf_home: Path | None) -> None:
    """Change `environ` for service mode.

    The function overwrites old values. Names are compared without case, because Windows
    changes the case of environment names.
    """
    for name in list(environ):
        upper = name.upper()
        if upper.endswith(_SECRET_SUFFIXES) and not upper.startswith(_OWN_PREFIX):
            del environ[name]

    for name, value in _OFFLINE_VARIABLES.items():
        environ[name] = value
    if hf_home is not None:
        environ["HF_HOME"] = str(hf_home)
