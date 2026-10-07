# Exercise the PP-Structure import format using clearly labeled invented examples. This
# demonstrates the data contract without claiming real engine accuracy.
"""Exercise PP imports using clearly labeled synthetic model contract fixtures."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_docling_concepts import model_export  # noqa: E402
from test_ppstructure_concepts import pp_export, replace_json  # noqa: E402

from course_parser.io import read_json, sha256  # noqa: E402
from course_parser.layout_stage import prepare_layout, verify_layout  # noqa: E402
from course_parser.reference import ReferenceProject  # noqa: E402


# Read the command-line options, then exercise the PP-Structure import format using clearly
# labeled invented examples.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-project", type=Path, required=True)
    parser.add_argument("--evidence-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    inputs = args.output / "synthetic-model-inputs"
    inputs.mkdir()
    docling = model_export(inputs, args.evidence_run)
    pp = pp_export(inputs, args.evidence_run)
    export = inputs / "ppstructure.json"
    raw = read_json(export)
    raw["res"]["overall_ocr_res"]["rec_scores"][0] = 0.42
    replace_json(export, raw)
    manifest = read_json(pp)
    manifest["pages"][0]["result_json_sha256"] = sha256(export)
    replace_json(pp, manifest)
    result = prepare_layout(
        ReferenceProject(args.reference_project),
        args.evidence_run,
        args.output / "layout",
        docling_manifest=docling,
        ppstructure_manifest=pp,
    )
    result["verification"] = verify_layout(args.output / "layout", recheck_inputs=True)
    result["example_kind"] = "SYNTHETIC_CONTRACT_FIXTURE_NO_NEURAL_INFERENCE"
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
