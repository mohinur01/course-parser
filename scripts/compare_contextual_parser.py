# Run a paired comparison of course assembly with and without page context. Use the same
# reviewed sample and source answers for both methods.
"""Reproduce a paired course-content comparison without changing source truth."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from course_parser.entry_learning import compare_learning_runs
from course_parser.learning import run_learning_baseline
from course_parser.learning_evaluation import score_learning
from course_parser.reference import ReferenceProject


# Read the command-line options, then run a paired comparison of course assembly with and
# without page context.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-project", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    reference = ReferenceProject(args.reference_project)
    run_learning_baseline(
        reference, args.pilot, args.output / "predictions", contextual_course_assembly=True
    )
    score_learning(reference, args.pilot, args.baseline, args.output / "before")
    score_learning(reference, args.pilot, args.output / "predictions", args.output / "after")
    result = compare_learning_runs(
        args.output / "before", args.output / "after", args.output / "comparison"
    )
    keys = (
        "gold_courses",
        "matched_course_proposals",
        "complete_content_correct",
        "complete_content_recall",
        "false_course_proposals",
        "ambiguous_course_proposals",
        "course_precision_conservative",
        "complete_all_fields_correct",
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "before": {k: result["before"][k] for k in keys},
                "after": {k: result["after"][k] for k in keys},
                "viewer": str((args.output / "comparison/index.html").resolve()),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
