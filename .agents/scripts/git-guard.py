#!/usr/bin/env python3
"""PreToolUse hook to block git commit, branch creation, and push unless explicitly approved."""

import json
import re
import sys

BLOCKED_GIT_PATTERNS = re.compile(
    r"(?:^|[;&|]\s*|\s+)"
    r"git\s+(?:-\S+\s+)*"
    r"(commit|push|branch\s+(?!-[dD]|--list|-a|-r|-v|--show-current)|checkout\s+-[bB]|switch\s+-[cC])\b"
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print(json.dumps({"decision": "allow"}))
        return 0

    # Antigravity format: payload["toolCall"]["args"]["CommandLine"]
    # Claude Code format: payload["tool_input"]["command"]
    is_claude = "tool_input" in payload
    cmd = (
        payload.get("tool_input", {}).get("command", "")
        if is_claude
        else payload.get("toolCall", {}).get("args", {}).get("CommandLine", "")
    )

    if BLOCKED_GIT_PATTERNS.search(cmd):
        reason = (
            "Blocked by git-guard hook: Never commit code, push, or create branches "
            "unless explicitly instructed by the user."
        )
        if is_claude:
            sys.stderr.write(reason + "\n")
            return 2
        print(json.dumps({"decision": "ask", "reason": reason}))
        return 0

    if not is_claude:
        print(json.dumps({"decision": "allow"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

