# Save both native parser runs and targeted OCR evidence before scoring the Phase 10
# experiment. Compare the same permitted samples without opening reserved data.
"""Freeze both exposed native runs and targeted OCR evidence before strict scoring."""

import argparse
import json
from pathlib import Path

from audit_phase10_ocr import audit_cached_crops

from course_parser.crop_ppstructure import run_crop_ppstructure
from course_parser.entry_learning import compare_learning_runs
from course_parser.field_scoring import enforce_policy
from course_parser.io import read_json, sha256, write_json
from course_parser.learning import run_learning_baseline
from course_parser.learning_evaluation import score_learning
from course_parser.omission_review import finalize_omission_review, prepare_omission_review
from course_parser.reference import ReferenceProject

WORKSPACE = Path(__file__).resolve().parents[1]
EXPOSED = {
    "original": (
        "artifacts/source-learning-v4/pilot",
        "artifacts/reading-order-phase9-v1/candidate-v2/original-evaluation",
    ),
    "expanded": (
        "artifacts/table-ownership-v1/expanded-pilot",
        "artifacts/reading-order-phase9-v1/candidate-v2/expanded-evaluation",
    ),
}


# Read the command-line options, then save both native parser runs and targeted OCR evidence
# before scoring the Phase 10 experiment.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    frozen = {
        str(p.relative_to(WORKSPACE)): sha256(p)
        for p in (WORKSPACE / "src/course_parser").glob("*.py")
    }

    # Reject changes to the recorded source or parser snapshot before comparison.
    def check_frozen():
        # Stop if the parser changed during source-only measurement.
        if any(sha256(WORKSPACE / p) != h for p, h in frozen.items()):
            raise ValueError("parser changed during source-only measurement")

    for _name, (pilot, baseline) in EXPOSED.items():
        enforce_policy(WORKSPACE / baseline, args.output)
        # Stop if the exposed pilot identity changed.
        if (
            sha256(WORKSPACE / pilot / "pilot_manifest.json")
            != read_json(WORKSPACE / baseline / "evaluation_manifest.json")["pilot_manifest_sha256"]
        ):
            raise ValueError("exposed pilot identity changed")
    reference = ReferenceProject(args.reference_project)
    args.output.mkdir(parents=True, exist_ok=False)
    for name, (pilot, _) in EXPOSED.items():
        prediction = run_learning_baseline(
            reference,
            WORKSPACE / pilot,
            args.output / (name + "-predictions"),
            contextual_course_assembly=True,
        )
        check_frozen()
        print(
            json.dumps({"pilot": name, "source_prediction_status": prediction["status"]}),
            flush=True,
        )
    cached = audit_cached_crops(
        args.output / "original-predictions", args.output / "cached-ocr-audit"
    )
    inbox = args.output / "gated-omission-review"
    prepared = prepare_omission_review(
        WORKSPACE / EXPOSED["original"][0],
        args.output / "original-predictions",
        inbox,
        crop_limit=12,
    )
    # Avoid model initialization when the source gate requests no independent OCR.
    # Any eligible crops already retain separate Tesseract outputs; cached PP is evidence in
    # the legacy sample, not silently copied to different new crops.
    optional = (
        run_crop_ppstructure(inbox)
        if prepared["ocr_eligible_crops"] == 0
        else {"status": "OPTIONAL_PP_NOT_RUN"}
    )
    finalized = finalize_omission_review(inbox)
    check_frozen()
    write_json(
        args.output / "source_only_freeze.json",
        {
            "status": "BOTH_PREDICTIONS_AND_OCR_EVIDENCE_SEALED_BEFORE_SCORING",
            "parser_sha256": frozen,
            "source_truth_read": False,
            "reserved_cohort_access": "SEALED",
            "cached_audit": cached,
            "gated_preparation": prepared,
            "gated_review": finalized,
            "optional_pp": optional,
            "audit_producer_sha256": sha256(Path(__file__).with_name("audit_phase10_ocr.py")),
            "cached_audit_manifest_sha256": sha256(
                args.output / "cached-ocr-audit/audit_manifest.json"
            ),
            "gated_review_manifest_sha256": sha256(inbox / "review_manifest.json"),
        },
    )
    for name, (pilot, baseline) in EXPOSED.items():
        check_frozen()
        score_learning(
            reference,
            WORKSPACE / pilot,
            args.output / (name + "-predictions"),
            args.output / (name + "-evaluation"),
        )
        comparison = compare_learning_runs(
            WORKSPACE / baseline,
            args.output / (name + "-evaluation"),
            args.output / (name + "-comparison"),
        )
        metrics = (
            "gold_courses",
            "complete_content_correct",
            "complete_all_fields_correct",
            "false_course_proposals",
        )
        print(
            json.dumps(
                {
                    "pilot": name,
                    "before": {k: comparison["before"][k] for k in metrics},
                    "after": {k: comparison["after"][k] for k in metrics},
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
