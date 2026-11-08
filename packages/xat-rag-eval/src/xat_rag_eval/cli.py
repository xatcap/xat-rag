"""The command line of the evaluation harness: `xat-rag-eval run`."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from xat_rag import Settings, XatRag
from xat_rag_core.errors import XatRagError

from xat_rag_eval.corpus import load_corpus
from xat_rag_eval.harness import Report, gate, load_golden, report_json, run, write_json


def main() -> None:
    """Run a golden set, print the report, and fail if the gate fails."""
    parser = argparse.ArgumentParser(prog="xat-rag-eval")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run", help="Run a golden set.")
    command.add_argument("--setup", type=Path, required=True, help="A folder with recipes/, prompts/ and documents/.")
    command.add_argument("--golden", type=Path, required=True)
    command.add_argument("--models", type=Path, default=Path("models"))
    command.add_argument("--lock", type=Path, default=Path("build/models.lock.json"))
    command.add_argument("--data", type=Path, required=True, help="An empty folder for the indexes of this run.")
    command.add_argument("--baseline", type=Path)
    command.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()

    setup = cast(Path, args.setup)
    data_folder = cast(Path, args.data)
    baseline_path = cast(Path | None, args.baseline)
    if data_folder.exists() and any(data_folder.iterdir()):
        sys.exit("xat-rag-eval: --data must be a new or empty folder, so that old indexes do not change the result.")
    if baseline_path is None:
        sys.exit("xat-rag-eval: give --baseline, also with --write-baseline.")
    if not args.write_baseline and not baseline_path.exists():
        sys.exit("xat-rag-eval: the baseline file does not exist. Write one with --write-baseline first.")
    prompts = setup / "prompts"
    try:
        rag = XatRag.open(
            Settings(
                data_folder=data_folder,
                recipes_folder=setup / "recipes",
                models_folder=cast(Path, args.models),
                lock_file=cast(Path, args.lock),
                prompts_folder=prompts if prompts.is_dir() else None,
                threads=1,
            )
        )
        cases = load_golden(cast(Path, args.golden))
    except XatRagError as error:
        sys.exit(f"xat-rag-eval: {error.message}")
    try:
        failed = [result for result in rag.ingest(load_corpus(setup / "documents")) if result.status == "failed"]
        if failed:
            sys.exit(f"xat-rag-eval: {len(failed)} documents of the corpus failed to ingest.")
        report = run(rag, cases)
    except XatRagError as error:
        sys.exit(f"xat-rag-eval: {error.message}")
    finally:
        rag.close()
    data = report_json(report)
    print(json.dumps(data, indent=2, sort_keys=True))
    _finish(report, cast(bool, args.write_baseline), baseline_path)


def _finish(report: Report, write_baseline: bool, baseline_path: Path) -> None:
    """Print the report, then write the baseline or run the gate."""
    data = report_json(report)
    print(json.dumps(data, indent=2, sort_keys=True))
    if write_baseline:
        problems = gate(report, {"kinds": data["kinds"]})  # Only the budget and determinism rules apply here.
        if problems:
            sys.exit("xat-rag-eval: the run has problems, so it cannot be the baseline: " + "; ".join(problems))
        write_json(baseline_path, {"kinds": data["kinds"]})  # Timings change on each run, so they stay out.
        return
    try:
        baseline = cast(dict[str, Any], json.loads(baseline_path.read_text("utf-8")))
    except ValueError:
        sys.exit("xat-rag-eval: the baseline file is not valid JSON.")
    problems = gate(report, baseline)
    for problem in problems:
        print(f"GATE: {problem}", file=sys.stderr)
    if problems:
        sys.exit(1)
