# Replay the omission audit on permitted saved sources. Compare review flags and verify that
# audit-only changes leave the existing course records unchanged.
"""Replay audit-only changes on explicitly permitted, frozen exposed pilots.

Both audit runs are sealed before reading existing answers. No extraction is
rerun, and there is intentionally no arbitrary pilot/cohort argument.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from pathlib import Path

from course_parser.field_scoring import enforce_policy
from course_parser.io import read_json, read_jsonl, sha256, write_json, write_jsonl
from course_parser.omission_ranking import group_duplicate_flags
from course_parser.omission_review import _verified_predictions
from course_parser.omission_signals import audit_page

WORKSPACE = Path(__file__).resolve().parents[1]
ROOT = WORKSPACE / "artifacts/omission-phase6-v1"
EXPOSED = {
    "original": "artifacts/source-learning-v4/pilot",
    "expanded": "artifacts/table-ownership-v1/expanded-pilot",
}
PHASE5 = WORKSPACE / "artifacts/historical-detection-phase5-v1/candidate-v1"
AUDIT_MODULES = {"omission_signals.py", "omission_review.py", "omission_ranking.py"}


# Verify that an audit-only change has not changed the existing course extraction.
def extraction_unchanged():
    before = ROOT / "before-code/src/course_parser"
    current = WORKSPACE / "src/course_parser"
    unchanged = {}
    for old in sorted(before.glob("*.py")):
        if old.name not in AUDIT_MODULES:
            if sha256(old) != sha256(current / old.name):
                raise ValueError("extraction/runtime changed during audit phase: " + old.name)
            unchanged[old.name] = sha256(old)
    extra = {p.name for p in current.glob("*.py")} - {p.name for p in before.glob("*.py")}
    # Keep an audit-only experiment from introducing a new extraction implementation.
    if extra - AUDIT_MODULES:
        raise ValueError("unexpected new extraction module")
    return unchanged


# Replay a permitted source-only snapshot through both omission auditors and compare the
# resulting review flags.
def source_replay(name, output, old_audit):
    pilot = WORKSPACE / EXPOSED[name]
    predictions = PHASE5 / (name + "-predictions")
    manifest = _verified_predictions(pilot, predictions)
    selected = {p["dataset_page_id"]: p for p in read_jsonl(pilot / "selected_pages.jsonl")}
    before_flags, flags, statuses = [], [], []
    for proposal in read_jsonl(predictions / "page_proposals.jsonl"):
        page = selected[proposal["dataset_page_id"]]
        if proposal["status"] != "PROPOSED":
            statuses.append(
                {"dataset_page_id": page["dataset_page_id"], "status": proposal["status"]}
            )
            continue
        source = read_json(pilot / page["packet_path"] / "source_reference.json")
        asset = next(
            a
            for a in source["page_artifacts"]
            if a["physical_page"] == page["central_physical_page"]
        )
        geometry_path = Path(asset["embedded_geometry_path"])
        # Stop if the native source geometry changed.
        if sha256(geometry_path) != asset["embedded_geometry_sha256"]:
            raise ValueError("native source geometry changed")
        layout = next(
            entry
            for entry in proposal["layouts"]
            if entry["physical_page"] == page["central_physical_page"]
        )
        geometry = read_json(geometry_path)
        binding = {
            k: page[k]
            for k in (
                "dataset_page_id",
                "source_document_id",
                "source_pdf_checksum",
                "learning_group",
                "learning_partition",
            )
        }
        binding["geometry_sha256"] = asset["embedded_geometry_sha256"]
        before_flags.extend(old_audit(geometry, layout, proposal, binding))
        page_flags = audit_page(geometry, layout, proposal, binding)
        flags.extend(page_flags)
        statuses.append(
            {
                "dataset_page_id": page["dataset_page_id"],
                "status": "AUDITED",
                "flags": len(page_flags),
            }
        )
    groups = group_duplicate_flags(flags)
    write_jsonl(output / (name + "_baseline_flags.jsonl"), before_flags)
    write_jsonl(output / (name + "_flags.jsonl"), flags)
    write_jsonl(output / (name + "_review_groups.jsonl"), groups)
    write_jsonl(output / (name + "_page_status.jsonl"), statuses)
    return {
        "windows": len(selected),
        "baseline_flags": len(before_flags),
        "flags": len(flags),
        "groups": len(groups),
        "aliases_grouped": len(flags) - len(groups),
        "priority_counts": dict(Counter(f["priority_tier"] for f in groups)),
        "pilot_manifest_sha256": sha256(pilot / "pilot_manifest.json"),
        "prediction_manifest_sha256": sha256(predictions / "prediction_manifest.json"),
        "course_proposals_sha256": manifest["file_sha256"]["course_proposals.jsonl"],
        "baseline_flags_sha256": sha256(output / (name + "_baseline_flags.jsonl")),
        "flags_sha256": sha256(output / (name + "_flags.jsonl")),
        "groups_sha256": sha256(output / (name + "_review_groups.jsonl")),
    }


# Read the command-line options, then replay the omission audit on permitted saved sources.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    unchanged = extraction_unchanged()
    for name in EXPOSED:
        evaluation = PHASE5 / (name + "-evaluation")
        enforce_policy(evaluation, args.output)
        # Stop if the approved exposed pilot changed.
        if read_json(evaluation / "evaluation_manifest.json")["pilot_manifest_sha256"] != sha256(
            WORKSPACE / EXPOSED[name] / "pilot_manifest.json"
        ):
            raise ValueError("approved exposed pilot changed")
    spec = importlib.util.spec_from_file_location(
        "frozen_omission_signals_v1", ROOT / "before-code/src/course_parser/omission_signals.py"
    )
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    args.output.mkdir(parents=True, exist_ok=False)
    provenance = {}
    for name in EXPOSED:
        provenance[name] = source_replay(name, args.output, old.audit_page)
        print(json.dumps({"pilot": name, **provenance[name]}), flush=True)
    extraction_unchanged()
    write_json(
        args.output / "source_only_freeze.json",
        {
            "status": "BOTH_AUDIT_RUNS_SEALED_BEFORE_ANSWER_DIAGNOSTICS",
            "provenance": provenance,
            "extraction_modules_unchanged": unchanged,
            "audit_code_sha256": {
                n: sha256(WORKSPACE / "src/course_parser" / n) for n in sorted(AUDIT_MODULES)
            },
            "source_truth_read": False,
            "reserved_cohort_access": "SEALED",
            "extraction_rerun": False,
            "accepted_courses_added": 0,
        },
    )


if __name__ == "__main__":
    main()
