# Run the optional PP-Structure layout tool on verified page images. Save each page result or
# failure so the analysis can be inspected and reproduced.
"""Optional neural producer: analyze verified page images once per pipeline instance."""

from __future__ import annotations

from collections import Counter
from importlib.metadata import version
from pathlib import Path

import fitz

from .bridge import READY, verify_run
from .io import artifact_hashes, contained_file, read_jsonl, sha256, write_json, write_jsonl

SETTINGS = {
    "lang": "en",
    "use_doc_orientation_classify": False,
    "use_doc_unwarping": False,
    "use_textline_orientation": False,
    "use_table_recognition": True,
    "use_formula_recognition": False,
    "use_chart_recognition": False,
    "use_seal_recognition": False,
    "format_block_content": False,
    "text_rec_score_thresh": 0.0,
}


# Create the optional extraction pipeline with the configured source-preserving settings.
def _engine(device):
    try:
        from paddleocr import PPStructureV3
    # Report import error as an explicit failure: PP-Structure inference needs a separate
    # environment with PaddleOCR, PaddleX and PaddlePaddle installed; no models were run.
    except ImportError as exc:
        raise RuntimeError(
            "PP-Structure inference needs a separate environment with PaddleOCR, "
            "PaddleX and PaddlePaddle installed; no models were run"
        ) from exc
    pipeline = PPStructureV3(device=device, **SETTINGS)

    # Run the configured engine for one verified input and return its recorded output.
    def analyze(image):
        results = pipeline.predict(input=str(image))
        # Expected exactly one PP-Structure result for one page image.
        if len(results) != 1:
            raise ValueError("expected exactly one PP-Structure result for one page image")
        return results[0].json

    packages = {name: version(name) for name in ("paddleocr", "paddlex")}
    return packages, analyze


# Run the optional layout tool for each verified page image and record the outputs or
# failures.
def run_ppstructure(evidence: Path, output: Path, *, device="cpu", limit: int | None = None):
    evidence, output = evidence.resolve(), output.resolve()
    # Write the optional-engine run to a new output folder to preserve earlier results.
    if output.exists():
        raise FileExistsError(f"refusing to overwrite PP-Structure run: {output}")
    # PP-Structure page limit must be positive.
    if limit is not None and limit < 1:
        raise ValueError("PP-Structure page limit must be positive")
    verify_run(evidence, recheck_inputs=True)
    pages = [
        p for p in read_jsonl(evidence / "page_evidence.jsonl") if p["evidence_status"] == READY
    ]
    pages = pages[:limit] if limit is not None else pages
    # No verified page images are available for PP-Structure.
    if not pages:
        raise ValueError("no verified page images are available for PP-Structure")
    packages, analyze = _engine(device)  # once per run; missing dependency leaves no output bundle
    output.mkdir(parents=True)
    (output / "results").mkdir()
    entries, statuses = [], []
    for index, page in enumerate(pages):
        image_path = contained_file(evidence, page["image_relative_path"])
        identity = {
            "page_key": page["page_key"],
            "source_pdf_checksum": page["source_pdf_checksum"],
            "image_sha256": page["image_sha256"],
            "engine_version": packages["paddleocr"],
        }
        try:
            # Stop if the verified page image changed before inference.
            if sha256(image_path) != page["image_sha256"]:
                raise ValueError("verified page image changed before inference")
            image = fitz.Pixmap(str(image_path))
            raw = analyze(image_path)
            # Stop if the verified page image changed during inference.
            if sha256(image_path) != page["image_sha256"]:
                raise ValueError("verified page image changed during inference")
            # The optional engine must return a JSON object in the expected export format.
            if not isinstance(raw, dict):
                raise ValueError("PP-Structure returned a non-object JSON export")
            # Preserve the original neural output; source binding is recorded beside it.
            result_path = output / "results" / f"page-{index}.json"
            write_json(result_path, raw)
            entries.append(
                {
                    **identity,
                    "status": "EXPORTED",
                    "image_width": image.width,
                    "image_height": image.height,
                    "result_json_path": str(result_path.relative_to(output)),
                    "result_json_sha256": sha256(result_path),
                }
            )
            statuses.append({"page_key": page["page_key"], "status": "EXPORTED", "error": ""})
        except Exception as exc:  # neural failures are explicit and isolated per page
            entries.append(
                {**identity, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
            )
            statuses.append(
                {
                    "page_key": page["page_key"],
                    "status": "FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    write_json(
        output / "ppstructure-input.json",
        {
            "schema_version": "course-parser-ppstructure-input-v1",
            "coordinate_frame": "VERIFIED_UPRIGHT_IMAGE",
            "input_kind": "RENDERED_PAGE_IMAGE",
            "pages": entries,
        },
    )
    write_jsonl(output / "engine_page_status.jsonl", statuses)
    counts = dict(Counter(s["status"] for s in statuses))
    write_json(
        output / "engine_run_manifest.json",
        {
            "schema_version": "course-parser-ppstructure-run-v1",
            "packages": packages,
            "device": device,
            "requested_settings": SETTINGS,
            "pages_attempted": len(pages),
            "page_status_counts": counts,
            "neural_inference_attempted": True,
            "ownership_truth_assigned": False,
            "new_keyword_hits_created": False,
            "source_evidence_manifest_sha256": sha256(evidence / "run_manifest.json"),
            "files_sha256": artifact_hashes(output),
        },
    )
    return {
        "status": "LAYOUT_INPUT_INCOMPLETE" if counts.get("FAILED") else "PP_STRUCTURE_EXPORTED",
        "output": str(output),
        "pages_attempted": len(pages),
        "page_status_counts": counts,
    }
