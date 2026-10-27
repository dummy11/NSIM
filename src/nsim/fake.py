"""Deterministic subprocess simulator for local development and failure testing."""

import argparse
import sys
import time
from pathlib import Path

from nsim.storage import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--outcome", default="pass")
    parser.add_argument("--duration", type=float, default=.02)
    args, plusargs = parser.parse_known_args()
    print(f"case={args.case} seed={args.seed} plusargs={plusargs}", flush=True)
    time.sleep(args.duration)
    if args.outcome == "crash":
        print("fake simulator crashed", file=sys.stderr)
        return 3
    if args.outcome == "missing":
        return 0
    if args.outcome == "malformed":
        args.result.write_text('{"complete":', encoding="utf-8")
        return 0
    checks = {"liveness": {"executed": True, "passed": True},
              "functional": {"executed": True, "passed": args.outcome != "fail"},
              "golden": {"executed": args.outcome != "no_compare", "passed": True,
                         "comparisons": 0 if args.outcome == "no_compare" else 1},
              "performance": {"executed": True, "passed": args.outcome != "performance_fail"}}
    write_json(args.result, {"schema_version": 1, "case": args.case, "seed": args.seed,
                             "complete": True, "checks": checks})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
