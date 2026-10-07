# Compare PP-Structure layout detection and XY-cut ordering using the same verified native PDF
# text. Save optional ordering evidence for later review.
"""Compare PP-Structure's detector and XY-cut using the common verified native text.

This is a component comparison, not end-to-end PP-Structure OCR evaluation.
The official detector and pinned PaddleX XY-cut code run locally; no truth is read.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import statistics
import time
from pathlib import Path

import fitz
from paddleocr import LayoutDetection
from paddlex.inference.pipelines.layout_parsing.layout_objects import LayoutBlock, LayoutRegion
from paddlex.inference.pipelines.layout_parsing.xycut_enhanced import xycut_enhanced

from course_parser.io import read_json, read_jsonl, sha256, write_json
from course_parser.learning import verify_learning_pilot


# Read the command-line options, then compare PP-Structure layout detection and XY-cut
# ordering using the same verified native PDF text.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="PP-DocLayout_plus-L")
    args = parser.parse_args()
    verify_learning_pilot(args.pilot)
    args.output.mkdir(parents=True, exist_ok=False)
    settings = dict(
        model_name=args.model,
        device="cpu",
        engine="paddle",
        cpu_threads=4,
        enable_mkldnn=False,
    )
    detector = LayoutDetection(**settings)
    manifest = dict(
        schema_version="verified-engine-exports-v1",
        engine="ppstructure",
        engine_version=importlib.metadata.version("paddleocr"),
        source_truth_read=False,
        pilot_manifest_sha256=sha256(args.pilot / "pilot_manifest.json"),
        settings=settings,
        component_mode="PP_LAYOUT_DETECTOR_AND_OFFICIAL_XYCUT_WITH_NATIVE_TEXT",
        software={
            n: importlib.metadata.version(n) for n in ("paddleocr", "paddlex", "paddlepaddle")
        },
        pages={},
        failures=[],
        status="RUN_COMPLETE",
    )
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
            image = fitz.Pixmap(str(image_path))
            try:
                result = list(detector.predict(input=str(image_path)))
                # The detector must return exactly one result for the requested page.
                if len(result) != 1:
                    raise ValueError("layout detector did not return one page")
                raw = result[0].json
                boxes = raw.get("res", raw)["boxes"]
                geometry = read_json(packet / Path(asset["embedded_geometry_path"]).name)
                # Canonical original text pieces determine text-line scale inside a model
                # region; they never determine the region's place in the page.
                width, height = (
                    geometry["canonical_width_points"],
                    geometry["canonical_height_points"],
                )
                blocks = []
                for item in boxes:
                    bbox = item["coordinate"]
                    cells = []
                    for line in geometry["lines"]:
                        x0, y0, x1, y1 = line["bbox_canonical_pdf_points"]
                        center = (
                            (x0 + x1) * image.width / width / 2,
                            (y0 + y1) * image.height / height / 2,
                        )
                        if bbox[0] <= center[0] <= bbox[2] and bbox[1] <= center[1] <= bbox[3]:
                            cells.append(line)
                    block = LayoutBlock(
                        item["label"], bbox, content="\n".join(c["text"] for c in cells)
                    )
                    if cells:
                        block.num_of_lines = len(cells)
                        block.text_line_height = statistics.median(
                            (c["bbox_canonical_pdf_points"][3] - c["bbox_canonical_pdf_points"][1])
                            * image.height
                            / height
                            for c in cells
                        )
                        block.text_line_width = statistics.median(
                            (c["bbox_canonical_pdf_points"][2] - c["bbox_canonical_pdf_points"][0])
                            * image.width
                            / width
                            for c in cells
                        )
                    blocks.append(block)
                ordered = xycut_enhanced(LayoutRegion([0, 0, image.width, image.height], blocks))
                # XY-cut ordering must retain every detected region exactly once.
                if {id(b) for b in ordered} != {id(b) for b in blocks} or len(ordered) != len(
                    blocks
                ):
                    raise ValueError("PP XY-cut lost or duplicated detected regions")
                export = key.replace(":", "_") + ".json"
                write_json(
                    args.output / export,
                    dict(
                        width=image.width,
                        height=image.height,
                        page_index=None,
                        model_settings=dict(
                            use_doc_preprocessor=False,
                            use_table_recognition=False,
                            use_formula_recognition=False,
                        ),
                        parsing_res_list=[
                            dict(
                                block_id=blocks.index(b),
                                block_label=b.label,
                                block_content=b.content,
                                block_bbox=b.bbox,
                                block_order=i,
                            )
                            for i, b in enumerate(ordered)
                        ],
                    ),
                )
                raw_path = key.replace(":", "_") + ".detection.json"
                write_json(args.output / raw_path, raw)
                manifest["pages"][key] = dict(
                    source_pdf_checksum=selected["source_pdf_checksum"],
                    image_sha256=asset["image_sha256"],
                    embedded_geometry_sha256=asset["embedded_geometry_sha256"],
                    export_path=export,
                    export_sha256=sha256(args.output / export),
                    detection_path=raw_path,
                    detection_sha256=sha256(args.output / raw_path),
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
