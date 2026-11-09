"""Check that ONNX Runtime opens no connection outside loopback.

The script loads a model in a child process, with the CI variables removed because ONNX Runtime turns telemetry off
when they are set. It reads the sockets of the child many times per second. It does not read the traffic, so a very
short connection between two reads is not seen. Run it also with `--unprotected`: a clean protected run is
evidence only if the unprotected run saw a connection.

    uv run python tools/check_onnx_network.py models/bge-small-en-v1.5/model.onnx
    uv run python tools/check_onnx_network.py models/bge-small-en-v1.5/model.onnx --unprotected
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_CI_VARIABLES = ("CI", "GITHUB_ACTIONS", "TF_BUILD", "ORT_DISABLE_TELEMETRY", "ORT_RUNNING_UNIT_TESTS")
_LOCAL = re.compile(r"(127\.\d+\.\d+\.\d+|\[?::1\]?|\*|0\.0\.0\.0|\[::\])[:.]")
_CHILD = """
import sys, time
if sys.argv[3] == "protected":
    import xat_rag_onnx.session
import onnxruntime
session = onnxruntime.InferenceSession(sys.argv[1], providers=["CPUExecutionProvider"])
time.sleep(float(sys.argv[2]))
"""


def _sockets(pid: int) -> list[str]:
    """Return the socket lines of the process that have an address outside loopback."""
    if os.name == "nt":
        results = [
            subprocess.run(["netstat", "-ano", "-p", protocol], capture_output=True, text=True, check=False)  # noqa: S603, S607
            for protocol in ("TCP", "TCPv6")
        ]
        lines = [line for result in results for line in result.stdout.splitlines() if line.split()[-1:] == [str(pid)]]
        failed = next((result for result in results if result.returncode != 0), None)
    else:
        result = subprocess.run(  # noqa: S603 - fixed tool.
            ["lsof", "-nP", "-a", "-p", str(pid), "-i"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        lines = [line for line in result.stdout.splitlines() if not line.startswith("COMMAND ")]
        # lsof exits with 1 when the process has no socket, and then it writes no error text.
        failed = result if result.returncode != 0 and result.stderr.strip() else None
    if failed is not None:
        sys.exit(f"The check is not valid, because the socket list failed: {failed.stderr.strip()}")
    return [line for line in lines if any(not _LOCAL.match(item) for item in _endpoints(line))]


def _endpoints(line: str) -> list[str]:
    """Return each address of a socket line. lsof writes a connection as `local->remote`."""
    return [part for token in line.split() for part in token.split("->") if re.search(r"[:.]\d+$", part)]


def main() -> None:
    """Run the check. The exit code is 1 if a connection outside loopback was seen."""
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("--seconds", type=float, default=45.0)
    parser.add_argument("--unprotected", action="store_true", help="Load ONNX Runtime with no off switch.")
    args = parser.parse_args()
    environment = {name: value for name, value in os.environ.items() if name not in _CI_VARIABLES}
    mode = "unprotected" if args.unprotected else "protected"
    child = subprocess.Popen(  # noqa: S603 - fixed arguments.
        [sys.executable, "-c", _CHILD, str(args.model), str(args.seconds), mode], env=environment
    )
    seen: set[str] = set()
    reads = 0
    while child.poll() is None:
        seen.update(_sockets(child.pid))
        reads += 1
        time.sleep(0.02)
    if child.returncode != 0:
        sys.exit(f"The child process failed with code {child.returncode}.")
    print(f"{mode}: {reads} reads of the sockets in {args.seconds} seconds, {len(seen)} connections outside loopback")
    for line in sorted(seen):
        print(line)
    if seen:
        sys.exit(1)


if __name__ == "__main__":
    main()
