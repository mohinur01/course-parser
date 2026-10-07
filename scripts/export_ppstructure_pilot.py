# Run the local PP-Structure layout and ordering tools on the verified page images shared by
# the sample. Preserve outputs and failures for comparison.
"""Run PP-Structure's local layout/order pipeline on the same verified page images."""

from __future__ import annotations

import argparse
import importlib.metadata
import time
from pathlib import Path

import fitz

from course_parser.io import read_json, read_jsonl, sha256, write_json
from course_parser.learning import verify_learning_pilot

SETTINGS = dict(
    device="cpu",
    engine="paddle",
    cpu_threads=4,
    enable_mkldnn=False,
    layout_detection_model_name="PP-DocLayout-S",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    use_region_detection=False,
    use_table_recognition=False,
    use_formula_recognition=False,
    use_chart_recognition=False,
    use_seal_recognition=False,
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="en_PP-OCRv4_mobile_rec",
    format_block_content=False,
)


# Read the command-line options, then run the local PP-Structure layout and ordering tools on
# the verified page images shared by the sample.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify_learning_pilot(args.pilot)
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = dict(
        schema_version="verified-engine-exports-v1",
        engine="ppstructure",
        engine_version=importlib.metadata.version("paddleocr"),
        source_truth_read=False,
        pilot_manifest_sha256=sha256(args.pilot / "pilot_manifest.json"),
        settings=SETTINGS,
        software={
            n: importlib.metadata.version(n) for n in ("paddleocr", "paddlex", "paddlepaddle")
        },
        pages={},
        failures=[],
        status="RUN_COMPLETE",
    )
    try:
        from paddleocr import PPStructureV3

        pipeline = PPStructureV3(**SETTINGS)
    except Exception as exc:
        manifest.update(status="ENGINE_UNAVAILABLE", initialization_error=str(exc))
        write_json(args.output / "manifest.json", manifest)
        print(f"ENGINE_UNAVAILABLE: {exc}", flush=True)
        return
    for selected in read_jsonl(args.pilot / "selected_pages.jsonl"):
        if not selected["review_available"]:
            continue
        page_id = selected["dataset_page_id"]
        packet = args.pilot / selected["packet_path"]
        source = read_json(packet / "source_reference.json")
        for asset in source["page_artifacts"]:
            started = time.monotonic()
            key = f"{page_id}:p{asset['physical_page']}"
            image_path = packet / Path(asset["image_path"]).name
            try:
                results = list(pipeline.predict(input=str(image_path)))
                # Expected exactly one result for one page image.
                if len(results) != 1:
                    raise ValueError("expected exactly one result for one page image")
                export = key.replace(":", "_") + ".json"
                write_json(args.output / export, results[0].json)
                image = fitz.Pixmap(str(image_path))
                manifest["pages"][key] = dict(
                    source_pdf_checksum=selected["source_pdf_checksum"],
                    image_sha256=asset["image_sha256"],
                    embedded_geometry_sha256=asset["embedded_geometry_sha256"],
                    export_path=export,
                    export_sha256=sha256(args.output / export),
                    image_width=image.width,
                    image_height=image.height,
                )
                print(f"EXPORTED {key} ({time.monotonic() - started:.1f}s)", flush=True)
            except Exception as exc:
                manifest["failures"].append(dict(page_key=key, error=str(exc)))
                print(f"FAILED {key}: {exc}", flush=True)
    write_json(args.output / "manifest.json", manifest)
    print(f"Sealed {len(manifest['pages'])} pages, {len(manifest['failures'])} failures.")


if __name__ == "__main__":
    main()
