# Measure the Phase 7 boundary checks on the two permitted exposed samples. Save predictions
# and compare them with the fixed earlier results.
"""Compare Phase 7 boundary evidence changes on the two explicitly permitted exposed pilots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from course_parser.entry_learning import compare_learning_runs
from course_parser.field_scoring import enforce_policy
from course_parser.io import read_json, sha256, write_json
from course_parser.learning import run_learning_baseline
from course_parser.learning_evaluation import score_learning
from course_parser.reference import ReferenceProject

WORKSPACE = Path(__file__).resolve().parents[1]
EXPOSED = {
    "original": (
        "artifacts/source-learning-v4/pilot",
        "artifacts/historical-detection-phase5-v1/candidate-v1/original-evaluation",
    ),
    "expanded": (
        "artifacts/table-ownership-v1/expanded-pilot",
        "artifacts/historical-detection-phase5-v1/candidate-v1/expanded-evaluation",
    ),
}


# Read the command-line options, then measure the Phase 7 boundary checks on the two permitted
# exposed samples.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reference = ReferenceProject(args.reference_project)
    frozen_code = {
        str(p.relative_to(WORKSPACE)): sha256(p)
        for p in (WORKSPACE / "src/course_parser").glob("*.py")
    }

    # Reject changes to the recorded source or parser snapshot before comparison.
    def assert_frozen():
        # Stop if the parser code changed during source-only measurement.
        if any(sha256(WORKSPACE / p) != digest for p, digest in frozen_code.items()):
            raise ValueError("parser code changed during source-only measurement")

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
        assert_frozen()
        print(json.dumps({"pilot": name, "prediction": result}), flush=True)
    write_json(
        args.output / "source_only_freeze.json",
        {
            "status": "BOTH_PREDICTIONS_SEALED_BEFORE_SCORING",
            "parser_sha256": frozen_code,
            "source_truth_read": False,
            "reserved_cohort_access": "SEALED",
        },
    )
    for name, (pilot_path, evaluation_path) in EXPOSED.items():
        assert_frozen()
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
