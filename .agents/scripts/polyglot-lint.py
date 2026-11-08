#!/usr/bin/env python3
"""PostToolUse polyglot linter hook. Dispatches the appropriate linter based on file extension."""

import json
from pathlib import Path
import shutil
import subprocess
import sys


def run_linter(file_path: Path) -> None:
    if not file_path.exists():
        return

    suffix = file_path.suffix.lower()

    if suffix in {".py", ".pyi"}:
        if shutil.which("uv"):
            subprocess.run(["uv", "run", "ruff", "check", str(file_path)], check=False)
        elif shutil.which("ruff"):
            subprocess.run(["ruff", "check", str(file_path)], check=False)

    elif suffix == ".dart":
        if shutil.which("dart"):
            subprocess.run(["dart", "analyze", str(file_path)], check=False)

    elif suffix == ".cs":
        if shutil.which("dotnet"):
            subprocess.run(
                ["dotnet", "format", "--verify-no-changes", "--include", str(file_path)],
                check=False,
            )

    elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
        if shutil.which("npx"):
            subprocess.run(["npx", "--no-install", "eslint", str(file_path)], check=False)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    is_claude = "tool_input" in payload
    raw_path = (
        payload.get("tool_input", {}).get("file_path", "")
        if is_claude
        else payload.get("toolCall", {}).get("args", {}).get("Path", "")
    )

    if raw_path:
        run_linter(Path(raw_path))

    if not is_claude:
        print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

