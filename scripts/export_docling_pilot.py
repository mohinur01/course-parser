# Run Docling on the verified page windows from the reviewed sample. Export its proposals
# without using review answers during extraction.
"""Run real Docling on the reviewed pilot's exact source page windows, without truth."""

from __future__ import annotations

import argparse
import importlib.metadata
import time
from pathlib import Path

import fitz
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from course_parser.io import read_json, read_jsonl, sha256, write_json
from course_parser.learning import verify_learning_pilot


# Read the command-line options, then run Docling on the verified page windows from the
# reviewed sample.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify_learning_pilot(args.pilot)
    args.output.mkdir(parents=True, exist_ok=False)
    settings = dict(
        do_ocr=False,
        do_table_structure=False,
        force_backend_text=True,
        enable_remote_services=False,
    )
    options = PdfPipelineOptions(
        **settings,
        accelerator_options=AcceleratorOptions(
            device=AcceleratorDevice.CPU,
            num_threads=4,
        ),
    )
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
        }
    )
    manifest = dict(
        schema_version="verified-engine-exports-v1",
        engine="docling",
        engine_version=importlib.metadata.version("docling"),
        source_truth_read=False,
        pilot_manifest_sha256=sha256(args.pilot / "pilot_manifest.json"),
        settings=settings,
        accelerator="cpu:4",
        model_spec=options.layout_options.model_spec.model_dump(mode="json"),
        software={
            name: importlib.metadata.version(name)
            for name in ("docling", "docling-core", "docling-ibm-models", "docling-parse", "torch")
        },
        pages={},
        failures=[],
    )
    for selected in read_jsonl(args.pilot / "selected_pages.jsonl"):
        if not selected["review_available"]:
            continue
        page_id = selected["dataset_page_id"]
        packet = args.pilot / selected["packet_path"]
        source = read_json(packet / "source_reference.json")
        filename = page_id + ".pdf"
        with fitz.open(selected["source_pdf_path"]) as original, fitz.open() as window:
            for number in selected["context_physical_pages"]:
                window.insert_pdf(original, from_page=number - 1, to_page=number - 1)
            window.save(args.output / filename)
        started = time.monotonic()
        try:
            result = converter.convert(args.output / filename)
            # Import only a successful Docling conversion; preserve other statuses as
            # failures.
            if result.status != ConversionStatus.SUCCESS:
                raise ValueError(f"Docling conversion status: {result.status}")
            export = page_id + ".json"
            write_json(args.output / export, result.document.export_to_dict())
            for asset in source["page_artifacts"]:
                # Match the exact insertion order, not the order of JSON assets.
                physical = asset["physical_page"]
                manifest["pages"][f"{page_id}:p{physical}"] = dict(
                    source_pdf_checksum=selected["source_pdf_checksum"],
                    image_sha256=asset["image_sha256"],
                    embedded_geometry_sha256=asset["embedded_geometry_sha256"],
                    export_path=export,
                    export_sha256=sha256(args.output / export),
                    export_page_no=selected["context_physical_pages"].index(physical) + 1,
                    input_window_pdf_sha256=sha256(args.output / filename),
                )
            print(f"EXPORTED {page_id} ({time.monotonic() - started:.1f}s)", flush=True)
        except Exception as exc:
            manifest["failures"].append(dict(dataset_page_id=page_id, error=str(exc)))
            print(f"FAILED {page_id}: {exc}", flush=True)
        write_json(
            args.output / (page_id + ".status.json"),
            {
                "dataset_page_id": page_id,
                "elapsed_seconds": time.monotonic() - started,
                "pages_exported": [k for k in manifest["pages"] if k.startswith(page_id + ":")],
                "failures": [f for f in manifest["failures"] if f["dataset_page_id"] == page_id],
            },
        )
    write_json(args.output / "manifest.json", manifest)
    print(f"Sealed {len(manifest['pages'])} pages, {len(manifest['failures'])} failed windows.")


if __name__ == "__main__":
    main()
