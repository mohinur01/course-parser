# Measure the changes to local course-heading pattern checks on the two permitted exposed
# samples. Keep the comparison tied to the same sources and answers.
"""Compare local page-pattern changes on the two explicitly permitted exposed pilots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from course_parser.entry_learning import compare_learning_runs
from course_parser.field_scoring import enforce_policy
from course_parser.io import read_json, sha256
from course_parser.learning import run_learning_baseline
from course_parser.learning_evaluation import score_learning
from course_parser.reference import ReferenceProject

WORKSPACE = Path(__file__).resolve().parents[1]
EXPOSED = {
    "original": (
        "artifacts/source-learning-v4/pilot",
        "artifacts/ownership-phase3-v1/candidate-v2/original-evaluation",
    ),
    "expanded": (
        "artifacts/table-ownership-v1/expanded-pilot",
        "artifacts/ownership-phase3-v1/candidate-v2/expanded-evaluation",
    ),
}


# Read the command-line options, then measure the changes to local course-heading pattern
# checks on the two permitted exposed samples.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reference = ReferenceProject(args.reference_project)
    # There is intentionally no arbitrary pilot argument. Before opening either known pilot,
    # check its identity against its approved, exposed evaluation.
    for name, (pilot_path, evaluation_path) in EXPOSED.items():
        evaluation = WORKSPACE / evaluation_path
        enforce_policy(evaluation, args.output / (name + "-evaluation"))
        manifest = read_json(evaluation / "evaluation_manifest.json")
        # Stop if the exposed pilot identity changed.
        if manifest["pilot_manifest_sha256"] != sha256(
            WORKSPACE / pilot_path / "pilot_manifest.json"
        ):
            raise ValueError("exposed pilot identity changed")
    args.output.mkdir(parents=True, exist_ok=False)
    # Seal BOTH source-only runs before either answer subset is opened.
    for name, (pilot_path, _) in EXPOSED.items():
        result = run_learning_baseline(
            reference,
            WORKSPACE / pilot_path,
            args.output / (name + "-predictions"),
            contextual_course_assembly=True,
        )
        print(json.dumps({"pilot": name, "prediction": result}), flush=True)
    for name, (pilot_path, evaluation_path) in EXPOSED.items():
        score_learning(
            reference,
            WORKSPACE / pilot_path,
            args.output / (name + "-predictions"),
            args.output / (name + "-evaluation"),
        )
        comparison = compare_learning_runs(
            WORKSPACE / evaluation_path,
            args.output / (name + "-evaluation"),
            args.output / (name + "-comparison"),
        )
        metrics = (
            "gold_courses",
            "matched_course_proposals",
            "complete_content_correct",
            "complete_all_fields_correct",
            "false_course_proposals",
            "ambiguous_course_proposals",
        )
        print(
            json.dumps(
                {
                    "pilot": name,
                    "status": comparison["status"],
                    "before": {k: comparison["before"][k] for k in metrics},
                    "after": {k: comparison["after"][k] for k in metrics},
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
