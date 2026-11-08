"""Check the runtime packages of the xat-rag service (ADR 0011, NFR-11).

The packages come from `uv.lock`: the closure of `xat-rag-service`, without dev groups. The check fails if:

- a package is on the denylist,
- the set of packages differs from `build/dependencies.allow.json` (a new or a removed package), or
- a license is not on the list of licenses that allow redistribution in a commercial desktop app.

    uv run python tools/check_dependencies.py            # check (CI)
    uv run python tools/check_dependencies.py --write    # write the allowlist after a reviewed change
"""

import argparse
import importlib.metadata
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
ALLOW = ROOT / "build" / "dependencies.allow.json"
ROOT_PACKAGE = "xat-rag-service"

DENIED = (
    "posthog",
    "openai",
    "tiktoken",
    "nltk",
    "torch",
    "transformers",
    "sentence-transformers",
    "sentry-sdk",
    "opentelemetry-sdk",
    "unstructured",
    "sqlalchemy",
    "aiohttp",
    "cryptography",
    "langchain-community",
    "langchain-classic",
    "langchain",
    "langgraph",
    "chromadb",
    "haystack-ai",
    "llama-index-core",
)
DENIED_PREFIXES = ("opentelemetry-exporter",)
LICENSES = re.compile(
    r"\b(MIT|BSD|Apache|PSF|Python Software Foundation|ISC|MPL-2\.0|Mozilla Public License 2\.0|Unlicense|HPND|0BSD"
    r"|Zlib|BSL-1\.0|CC0)\b",
    re.IGNORECASE,
)
COPYLEFT = re.compile(r"\b(A?GPL|LGPL|GNU (Affero |Lesser )?General Public|SSPL|EUPL|CC-BY-NC)", re.IGNORECASE)


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def closure() -> tuple[dict[str, str], set[str]]:
    """Return the runtime packages of the service with their versions, and the workspace packages, from `uv.lock`."""
    lock = cast(dict[str, Any], tomllib.loads((ROOT / "uv.lock").read_text("utf-8")))
    packages = {_normalize(p["name"]): p for p in cast(list[dict[str, Any]], lock["package"])}
    workspace = {name for name, package in packages.items() if {"editable", "virtual"} & set(package.get("source", {}))}
    seen: dict[str, str] = {}
    stack = [ROOT_PACKAGE]
    while stack:
        name = stack.pop()
        if name in seen or name not in packages:
            continue
        package = packages[name]
        seen[name] = str(package.get("version", ""))
        for dependency in cast(list[dict[str, Any]], package.get("dependencies", [])):
            if "sys_platform == 'emscripten'" in str(dependency.get("marker", "")):
                continue  # Browser Python only. The service never runs there.
            stack.append(_normalize(dependency["name"]))  # Other platform packages count on all platforms.
            for extra in cast(list[str], dependency.get("extra", [])):
                optional = cast(
                    dict[str, list[dict[str, Any]]],
                    packages.get(_normalize(dependency["name"]), {}).get("optional-dependencies", {}),
                )
                stack.extend(_normalize(item["name"]) for item in optional.get(extra, []))
    return seen, workspace


# Licenses of packages that are not installed on every build machine (for example Windows-only ones).
# Each value was checked on PyPI. A change of version needs a new check.
KNOWN_LICENSES = {"colorama": "BSD-3-Clause"}


def license_of(name: str) -> str:
    """Return the license text of an installed package from its metadata, or from `KNOWN_LICENSES`."""
    try:
        metadata = importlib.metadata.metadata(name)
    except importlib.metadata.PackageNotFoundError:
        return KNOWN_LICENSES.get(name, "")
    parts = [metadata.get("License-Expression") or "", metadata.get("License") or ""]
    parts += [c for c in metadata.get_all("Classifier") or [] if c.startswith("License ::")]
    return " ".join(parts)


def main() -> None:
    """Check the dependencies, or write the allowlist."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    packages, workspace = closure()
    third_party = {name: version for name, version in packages.items() if name not in workspace}
    problems: list[str] = []
    for name in sorted(third_party):
        if name in DENIED or name.startswith(DENIED_PREFIXES):
            problems.append(f"denied package: {name}")
        text = license_of(name)
        if COPYLEFT.search(text) or not LICENSES.search(text):
            problems.append(f"license not allowed or unknown: {name} ({text[:80] or 'no license data'})")
    if args.write and problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        sys.exit("The allowlist was not written, because of the problems above.")
    if args.write:
        ALLOW.write_text(json.dumps({"packages": third_party}, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"wrote {ALLOW} with {len(third_party)} packages")
    else:
        allowed = cast(dict[str, str], json.loads(ALLOW.read_text("utf-8"))["packages"])
        problems += [f"new package, review it: {name}" for name in sorted(set(third_party) - set(allowed))]
        problems += [
            f"package left the lock, update the allowlist: {name}" for name in sorted(set(allowed) - set(third_party))
        ]
        problems += [
            f"version changed, review it: {name} {allowed[name]} -> {third_party[name]}"
            for name in sorted(set(allowed) & set(third_party))
            if allowed[name] != third_party[name]
        ]
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        sys.exit(1)
    print(f"{len(third_party)} runtime packages checked")


if __name__ == "__main__":
    main()
