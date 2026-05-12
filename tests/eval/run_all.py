"""Master entrypoint for the BioSeq eval suite.

Usage:
    python -m tests.eval.run_all                  # default: L1 + aggregate
    python -m tests.eval.run_all --suite L1
    python -m tests.eval.run_all --suite L2       # NOT YET IMPLEMENTED
    python -m tests.eval.run_all --suite L3       # NOT YET IMPLEMENTED
    python -m tests.eval.run_all --suite all

The runner first validates the YAML datasets, then dispatches to the per-
level harnesses. Each level lands its CSV + report.md in its own
`tests/eval/runs/<ISO-timestamp>-<level>/` directory.

See `report/EVALUATION_PLAN.md` Appendix A for the implementation roadmap.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from tests.eval import validate_data
from tests.eval import retriever_eval
from tests.eval import aggregate_report


SUITES = ("L1", "L2", "L3", "all")


def _run_L1() -> int:
    print("\n=== L1: retriever eval ===")
    rc = retriever_eval.main([])
    if rc != 0:
        return rc
    print("\n=== L1: aggregation ===")
    return aggregate_report.main([])


def _run_L2() -> int:
    print("L2 (LLM) harness not yet implemented — see EVALUATION_PLAN.md Appendix A.2.", file=sys.stderr)
    return 2


def _run_L3() -> int:
    print("L3 (end-to-end) harness not yet implemented — see EVALUATION_PLAN.md Appendix A.2.", file=sys.stderr)
    return 2


SUITE_DISPATCH: dict[str, Callable[[], int]] = {
    "L1": _run_L1,
    "L2": _run_L2,
    "L3": _run_L3,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BioSeq eval — master runner.")
    parser.add_argument("--suite", choices=SUITES, default="L1", help="Which level(s) to run. Default: L1.")
    parser.add_argument("--skip-validate", action="store_true", help="Skip dataset validation step.")
    args = parser.parse_args(argv)

    if not args.skip_validate:
        print("=== Validating YAML datasets ===")
        rc = validate_data.main()
        if rc != 0:
            return rc

    if args.suite == "all":
        levels = ["L1", "L2", "L3"]
    else:
        levels = [args.suite]

    last_rc = 0
    for lvl in levels:
        rc = SUITE_DISPATCH[lvl]()
        if rc != 0:
            print(f"\n[!] {lvl} returned exit code {rc}", file=sys.stderr)
            last_rc = rc
    return last_rc


if __name__ == "__main__":
    sys.exit(main())
