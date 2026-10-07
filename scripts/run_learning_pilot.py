#!/usr/bin/env python3
# Purpose: Build an immutable real-source DEVELOPMENT learning release end to end.
"""Build an immutable real-source DEVELOPMENT learning release end to end."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from course_parser.entry_learning import build_review_queue, train_entry_ranker
from course_parser.learning import prepare_learning_pilot, run_learning_baseline
from course_parser.learning_evaluation import score_learning
from course_parser.reference import ReferenceProject


# Read the command-line options, then prepare or run the development learning sample from the
# command line.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--partition-registry", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    reference = ReferenceProject(args.reference_project)
    results = {}
    results["sampling"] = prepare_learning_pilot(
        reference, args.output / "pilot", partition_registry=args.partition_registry
    )
    results["baseline"] = run_learning_baseline(
        reference, args.output / "pilot", args.output / "baseline"
    )
    results["evaluation"] = score_learning(
        reference, args.output / "pilot", args.output / "baseline", args.output / "evaluation"
    )
    results["entry_experiment"] = train_entry_ranker(
        args.output / "evaluation", args.output / "entry-ranker"
    )
    results["review_queue"] = build_review_queue(
        args.output / "evaluation", args.output / "entry-ranker", args.output / "review-queue"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
