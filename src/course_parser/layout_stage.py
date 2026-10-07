# Prepare verified PDF pages for layout analysis and match proposed regions to their original
# text. Check the saved results against the same source files before reuse.
"""Bounded backend -> layout proposals -> cell assignment -> typed assembly stage."""

from __future__ import annotations

import hashlib
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import fitz

from . import __version__
from .bridge import READY, verify_run
from .docling_adapter import parse_docling_page
from .io import (
    artifact_hashes,
    contained_file,
    read_json,
    read_jsonl,
    sha256,
    verify_files,
    write_json,
    write_jsonl,
)
from .layout_models import (
    Box,
    PagePredictions,
    Region,
    Span,
    TextCell,
    assign_cells,
    intersection_fraction,
)
from .pdf_backend import PyMuPDFPageBackend, project_frozen_hit
from .ppstructure_adapter import POLICY as PPSTRUCTURE_POLICY
from .ppstructure_stage import (
    import_ppstructure,
    load_ppstructure_manifest,
    ppstructure_counts,
    verify_ppstructure_page,
)
from .reference import ReferenceProject


# Read the export index that identifies the Docling result saved for each page.
def load_docling_manifest(path: Path | None, pages: list[dict]):
    if path is None:
        return {}, {}
    value = read_json(path)
    # Reject unsupported Docling import or unspecified coordinate frame.
    if (
        value.get("schema_version") != "course-parser-docling-input-v1"
        or value.get("coordinate_frame") != "VERIFIED_UPRIGHT_PAGE"
    ):
        raise ValueError("unsupported Docling import or unspecified coordinate frame")
    inventory = {page["page_key"]: page for page in pages}
    result, inputs = {}, {str(path.resolve()): sha256(path)}
    for entry in value["pages"]:
        key = entry["page_key"]
        page = inventory.get(key)
        # Stop if the Docling import does not bind to one verified evidence page.
        if (
            key in result
            or page is None
            or page["evidence_status"] != READY
            or entry["source_pdf_checksum"] != page["source_pdf_checksum"]
            or entry["image_sha256"] != page["image_sha256"]
            or type(entry["docling_page_no"]) is not int
            or entry["docling_page_no"] < 1
            or not entry["engine_version"]
        ):
            raise ValueError("Docling import does not bind to one verified evidence page")
        raw_path = contained_file(path.parent, entry["document_json_path"])
        # Reject this input if its file fingerprint no longer matches the recorded version.
        if sha256(raw_path) != entry["document_json_sha256"]:
            raise ValueError("Docling JSON checksum changed")
        result[key] = {**entry, "raw_path": raw_path}
        inputs[str(raw_path)] = entry["document_json_sha256"]
    return result, inputs


# Locate the approved PDF for the page and verify its recorded input checksum.
def _pdf_for_page(page, input_hashes):
    candidates = [
        Path(path)
        for path, digest in input_hashes.items()
        if digest == page["source_pdf_checksum"] and Path(path).suffix.lower() == ".pdf"
    ]
    # Stop if the verified preparation manifest has no bound source PDF.
    if not candidates:
        raise ValueError("verified preparation manifest has no bound source PDF")
    for candidate in candidates:
        if candidate.is_file() and sha256(candidate) == page["source_pdf_checksum"]:
            return candidate
    raise ValueError("bound source PDF is unavailable or changed")


# Extract the checked page text, import available layout suggestions and write their links to
# the original source.
def prepare_layout(
    reference: ReferenceProject,
    evidence: Path,
    output: Path,
    *,
    docling_manifest: Path | None = None,
    ppstructure_manifest: Path | None = None,
) -> dict:
    evidence, output = evidence.resolve(), output.resolve()
    verify_run(evidence, recheck_inputs=True)
    pages = read_jsonl(evidence / "page_evidence.jsonl")
    hits = read_jsonl(evidence / "hit_evidence.jsonl")
    source_manifest = read_json(evidence / "run_manifest.json")
    imports, extra_inputs = load_docling_manifest(docling_manifest, pages)
    pp_imports, pp_inputs = load_ppstructure_manifest(ppstructure_manifest, pages, evidence)
    output.mkdir(parents=True, exist_ok=False)
    (output / "assets").mkdir()
    (output / "docling_exports").mkdir()
    inputs = {
        str(evidence / "run_manifest.json"): sha256(evidence / "run_manifest.json"),
        **source_manifest["input_sha256"],
        **extra_inputs,
        **pp_inputs,
    }
    for name, digest in source_manifest["files_sha256"].items():
        inputs[str(contained_file(evidence, name))] = digest
    records, backends = [], {}
    pdf_paths = {}
    backend = PyMuPDFPageBackend()
    try:
        for page in pages:
            key = page["page_key"]
            record = {
                "page_key": key,
                "source_id": page["source_id"],
                "source_pdf_checksum": page["source_pdf_checksum"],
                "physical_page": page["physical_page"],
                "status": "SOURCE_EVIDENCE_INCOMPLETE",
                "routing": "SOURCE_REPAIR_REQUIRED",
                "frozen_representation": None,
                "backend_representation": None,
                "frozen_native_alignment": "UNAVAILABLE",
                "native_cells": [],
                "width": None,
                "height": None,
                "rotation_matrix": None,
                "image_relative_path": "",
                "image_sha256": None,
                "predictions": None,
                "ppstructure": None,
                "ppstructure_status": "NOT_REQUESTED"
                if key not in pp_imports
                else "INPUT_INCOMPLETE",
                "ppstructure_routing": None,
                "ppstructure_binding": None,
                "layout_comparison": None,
                "errors": list(page["errors"]),
            }
            records.append(record)
            if page["evidence_status"] != READY:
                continue
            try:
                frozen = page["v3_page_source"]["representations"][0]
                record["frozen_representation"] = frozen
                image = contained_file(evidence, page["image_relative_path"])
                image_name = hashlib.sha256(key.encode()).hexdigest() + ".png"
                shutil.copyfile(image, output / "assets" / image_name)
                record.update(
                    image_relative_path=f"assets/{image_name}", image_sha256=page["image_sha256"]
                )
                if page["source_pdf_checksum"] not in pdf_paths:
                    pdf_paths[page["source_pdf_checksum"]] = _pdf_for_page(
                        page, source_manifest["input_sha256"]
                    )
                pdf_path = pdf_paths[page["source_pdf_checksum"]]
                with fitz.open(pdf_path) as pdf:
                    native = backend.extract(pdf[page["physical_page"] - 1], key)
                backends[key] = native
                channel = (
                    reference.models.SourceChannel.EMBEDDED_OCR
                    if frozen["channel"] == "EMBEDDED_OCR"
                    else reference.models.SourceChannel.EMBEDDED_TEXT
                )
                method = (
                    reference.models.ExtractionMethod.PDF_EMBEDDED_OCR
                    if channel == reference.models.SourceChannel.EMBEDDED_OCR
                    else reference.models.ExtractionMethod.PYMUPDF_EMBEDDED_TEXT
                )
                representation = reference.models.TextRepresentation(
                    representation_id=f"{key}:native-geometry",
                    physical_page=page["physical_page"],
                    channel=channel,
                    extraction_method=method,
                    raw_text=native.raw_text,
                )
                record.update(
                    status="BACKEND_READY",
                    routing="LAYOUT_MODEL_OPTIONAL" if native.cells else "OCR_REQUIRED",
                    backend_representation=representation.model_dump(mode="json"),
                    frozen_native_alignment="EXACT"
                    if native.raw_text == frozen["raw_text"]
                    else "UNRESOLVED",
                    native_cells=[cell.model_dump() for cell in native.cells],
                    width=native.width,
                    height=native.height,
                    rotation_matrix=list(native.rotation_matrix),
                )
                proposal = PagePredictions(
                    engine="PDF_BACKEND",
                    engine_version=f"pymupdf-{fitz.VersionBind}",
                    regions=native.regions,
                    reading_order=tuple(region.region_id for region in native.regions),
                    reading_order_status="UNRESOLVED",
                    assignments=assign_cells(native.cells, native.regions),
                    warnings=(
                        "PDF extraction order is retained; logical reading order is unresolved",
                    ),
                )
                if key in imports:
                    entry = imports[key]
                    raw = read_json(entry["raw_path"])
                    imported_name = entry["document_json_sha256"] + ".json"
                    destination = output / "docling_exports" / imported_name
                    if not destination.exists():
                        shutil.copyfile(entry["raw_path"], destination)
                    record["docling_export_relative_path"] = f"docling_exports/{imported_name}"
                    record["docling_page_no"] = entry["docling_page_no"]
                    proposal = parse_docling_page(
                        raw,
                        page_no=entry["docling_page_no"],
                        page_key=key,
                        width=native.width,
                        height=native.height,
                        cells=native.cells,
                        engine_version=entry["engine_version"],
                    )
                    record.update(status="LAYOUT_PROPOSED", routing="LAYOUT_REVIEW_REQUIRED")
                record["predictions"] = proposal.model_dump(mode="json")
            # Handle oserror, value error, key error, type error and the other listed values
            # by keeping the failure explicit or using the documented fallback.
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                RuntimeError,
                RecursionError,
                fitz.mupdf.FzErrorBase,
            ) as exc:
                record["status"] = "LAYOUT_INPUT_INCOMPLETE"
                record["routing"] = "SOURCE_OR_MODEL_REPAIR_REQUIRED"
                record["errors"].append(f"{type(exc).__name__}: {exc}")
            # A PP-Structure failure never discards the native or Docling channel.
            if key in pp_imports and key in backends:
                try:
                    import_ppstructure(record, pp_imports[key], backends[key], output)
                # Handle oserror, value error, key error, type error and the other listed
                # values by keeping the failure explicit or using the documented fallback.
                except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                    record["ppstructure_routing"] = "MODEL_REPAIR_REQUIRED"
                    record["errors"].append(f"PP_STRUCTURE_V3: {type(exc).__name__}: {exc}")
        hit_geometry = []
        page_map = {page["page_key"]: page for page in records}
        for hit in hits:
            projected, errors = [], []
            for span in hit["original_source_spans"]:
                key = (
                    f"{hit['search_source_id']}:{hit['source_pdf_checksum']}:"
                    f"p{span['page_index'] + 1}"
                )
                native = backends.get(key)
                frozen = page_map[key]["frozen_representation"]
                try:
                    # Stop if the native page geometry is unavailable.
                    if native is None or frozen is None:
                        raise ValueError("native page geometry is unavailable")
                    fragments = project_frozen_hit(
                        native, frozen["raw_text"], span["raw_start"], span["raw_end"]
                    )
                    projected.append(
                        {
                            "page_key": key,
                            "frozen_representation_id": frozen["representation_id"],
                            "original_span": span,
                            "fragments": fragments,
                        }
                    )
                except ValueError as exc:
                    errors.append(str(exc))
            hit_geometry.append(
                {
                    "hit_id": hit["hit_id"],
                    "page_task_id": hit["page_task_id"],
                    "source_pdf_checksum": hit["source_pdf_checksum"],
                    "original_source_spans": hit["original_source_spans"],
                    "geometry_status": "EXACT" if not errors else "UNRESOLVED",
                    "geometry": projected,
                    "errors": errors,
                    "decision": "",
                    "proposed_owner": None,
                }
            )
        assignments = [
            assignment
            for page in records
            if page["predictions"]
            for assignment in page["predictions"]["assignments"]
        ]
        qc = {
            "scope": "BOUNDED_PAGE_LAYOUT_PROPOSALS",
            "ownership_truth_assigned": False,
            "catalog_negative_inference_allowed": False,
            "pages": len(records),
            "original_hits": len(hits),
            "page_status_counts": dict(Counter(page["status"] for page in records)),
            "geometry_status_counts": dict(Counter(hit["geometry_status"] for hit in hit_geometry)),
            "assignment_status_counts": dict(Counter(value["status"] for value in assignments)),
            "native_cells": sum(len(page["native_cells"]) for page in records),
            "cells_pending_layout": sum(
                len(page["native_cells"]) for page in records if page["predictions"] is None
            ),
            "docling_pages_imported": sum(page["status"] == "LAYOUT_PROPOSED" for page in records),
            "pending_dispositions": len(hits),
            "finalized_dispositions": 0,
            "source_evidence_batch_status": source_manifest["status"],
            "source_evidence_ready_hits": sum(hit["evidence_status"] == READY for hit in hits),
            **ppstructure_counts(records),
        }
        write_jsonl(output / "page_layout.jsonl", records)
        write_jsonl(output / "hit_geometry.jsonl", hit_geometry)
        shutil.copyfile(evidence / "hit_evidence.jsonl", output / "original_hit_inventory.jsonl")
        write_json(output / "layout_qc.json", qc)
        from .layout_viewer import write_layout_viewer

        write_layout_viewer(output, records, hit_geometry)
        manifest = {
            "schema_version": "course-parser-layout-v1",
            "software_version": __version__,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "scope": qc["scope"],
            "source_evidence_run": str(evidence),
            "ownership_truth_assigned": False,
            "catalog_negative_inference_allowed": False,
            "input_sha256": inputs,
            "reference_code_sha256": reference.provenance(),
            "software_code_sha256": {
                path.name: sha256(path) for path in Path(__file__).parent.glob("*.py")
            },
            "cell_assignment_policy": {"minimum_cell_overlap": 0.5, "ambiguity_margin": 0.1},
            "docling_inference_run": False,
            "docling_imported_output": bool(imports),
            "ppstructure_inference_run": False,
            "ppstructure_imported_output": bool(pp_imports),
            "ppstructure_review_policy": PPSTRUCTURE_POLICY,
            "files_sha256": artifact_hashes(output),
        }
        write_json(output / "layout_manifest.json", manifest)
        return {
            "status": "LAYOUT_INPUT_INCOMPLETE"
            if any(page["errors"] for page in records)
            else "LAYOUT_STAGE_COMPLETE",
            "output": str(output),
            **qc,
        }
    except Exception as exc:
        write_json(output / "layout_failure.json", {"status": "FAILED", "error": str(exc)})
        raise


# Check the saved layout files, source identities and page coordinates before reusing them.
def verify_layout(output: Path, *, recheck_inputs: bool = False) -> dict:
    manifest = read_json(output / "layout_manifest.json")
    # Reject unsupported layout manifest or evidence policy.
    if (
        manifest.get("schema_version") != "course-parser-layout-v1"
        or manifest.get("scope") != "BOUNDED_PAGE_LAYOUT_PROPOSALS"
        or manifest.get("ownership_truth_assigned") is not False
        or manifest.get("catalog_negative_inference_allowed") is not False
    ):
        raise ValueError("unsupported layout manifest or evidence policy")
    required = {
        "page_layout.jsonl",
        "hit_geometry.jsonl",
        "layout_qc.json",
        "index.html",
        "original_hit_inventory.jsonl",
    }
    # The layout manifest needs fingerprints for every required output file.
    if not required.issubset(manifest["files_sha256"]):
        raise ValueError("layout manifest omits required artifacts")
    verify_files(output, manifest["files_sha256"])
    pages, hits = (
        read_jsonl(output / "page_layout.jsonl"),
        read_jsonl(output / "hit_geometry.jsonl"),
    )
    qc = read_json(output / "layout_qc.json")
    original_hits = read_jsonl(output / "original_hit_inventory.jsonl")
    original_map = {hit["hit_id"]: hit for hit in original_hits}
    # Stop if the layout accounting or pending-decision policy differs.
    if (
        len({page["page_key"] for page in pages}) != len(pages)
        or len({hit["hit_id"] for hit in hits}) != len(hits)
        or qc["pages"] != len(pages)
        or qc["original_hits"] != len(hits)
        or qc["pending_dispositions"] != len(hits)
        or qc["finalized_dispositions"] != 0
        or qc["ownership_truth_assigned"] is not False
        or qc["catalog_negative_inference_allowed"] is not False
        or any(hit["decision"] or hit["proposed_owner"] is not None for hit in hits)
        or len(original_map) != len(original_hits)
        or set(original_map) != {hit["hit_id"] for hit in hits}
    ):
        raise ValueError("layout accounting or pending-decision policy differs")
    assignments_all = []
    native_total = 0
    page_map = {page["page_key"]: page for page in pages}
    for page in pages:
        cells = tuple(TextCell.model_validate(value) for value in page["native_cells"])
        native = page["backend_representation"]
        native_total += len(cells)
        # Each original text piece may appear only once in the page inventory.
        if len({cell.cell_id for cell in cells}) != len(cells):
            raise ValueError("repeated native cell")
        # Stop if the native cell text differs from its source span.
        if native is not None and any(
            native["raw_text"][cell.span.start : cell.span.end] != cell.text for cell in cells
        ):
            raise ValueError("native cell text differs from its source span")
        # Stop if the layout image binding differs.
        if (
            page["image_relative_path"]
            and sha256(contained_file(output, page["image_relative_path"])) != page["image_sha256"]
        ):
            raise ValueError("layout image binding differs")
        if page["predictions"] is not None:
            predictions = PagePredictions.model_validate(page["predictions"])
            assignments_all.extend(predictions.assignments)
            assignments = predictions.assignments
            regions = {region.region_id for region in predictions.regions}
            # Keep every text piece and region relationship exactly once when building the
            # layout.
            if (
                len(assignments) != len(cells)
                or {assignment.cell_id for assignment in assignments}
                != {cell.cell_id for cell in cells}
                or len(regions) != len(predictions.regions)
                or len(set(predictions.reading_order)) != len(predictions.reading_order)
                or not set(predictions.reading_order).issubset(regions)
                or any(
                    assignment.region_id is not None and assignment.region_id not in regions
                    for assignment in assignments
                )
            ):
                raise ValueError("layout lost or duplicated cells or region relationships")
            # Assigned regions must agree with the text boxes recorded in the source.
            if predictions.assignments != assign_cells(cells, predictions.regions):
                raise ValueError("layout cell assignments differ from recorded geometry")
            for region in predictions.regions:
                _check_page_box(region.box, page)
            for table in predictions.tables:
                parent = next(
                    region for region in predictions.regions if region.region_id == table.region_id
                )
                table_words = tuple(
                    cell for cell in cells if intersection_fraction(cell.box, parent.box) >= 0.5
                )
                cell_regions = tuple(
                    Region(
                        region_id=cell.cell_id,
                        label="table_cell",
                        box=cell.box,
                        predicted_text=cell.predicted_text,
                        origin="DOCLING",
                        source_ref=parent.source_ref,
                    )
                    for cell in table.cells
                    if cell.box is not None
                )
                # Table word assignments must fit the recorded table-cell geometry.
                if table.word_assignments != assign_cells(table_words, cell_regions):
                    raise ValueError("table word assignments differ from the logical-cell geometry")
        verify_ppstructure_page(output, page)
    expected_counts = {
        "page_status_counts": dict(Counter(page["status"] for page in pages)),
        "geometry_status_counts": dict(Counter(hit["geometry_status"] for hit in hits)),
        "assignment_status_counts": dict(Counter(value.status for value in assignments_all)),
        "native_cells": native_total,
        "cells_pending_layout": sum(
            len(page["native_cells"]) for page in pages if page["predictions"] is None
        ),
        "docling_pages_imported": sum(page["status"] == "LAYOUT_PROPOSED" for page in pages),
    }
    if "ppstructure_page_status_counts" in qc:
        expected_counts.update(ppstructure_counts(pages))
        # Use the recorded review thresholds when interpreting the optional layout results.
        if manifest.get("ppstructure_review_policy") != PPSTRUCTURE_POLICY:
            raise ValueError("PP-Structure review thresholds differ from the recorded policy")
    # Quality-check totals must agree with the underlying evidence records.
    if any(qc.get(key) != value for key, value in expected_counts.items()):
        raise ValueError("layout QC counts differ from their evidence records")
    for hit in hits:
        original = original_map[hit["hit_id"]]
        # Stop if the layout changed an original hit identity or source span.
        if any(
            hit[key] != original[key]
            for key in ("page_task_id", "source_pdf_checksum", "original_source_spans")
        ):
            raise ValueError("layout changed an original hit identity or source span")
        for projection in hit["geometry"]:
            page = page_map.get(projection["page_key"])
            # Stop if the hit geometry is not bound to its exact frozen page representation.
            if (
                page is None
                or page["frozen_representation"] is None
                or projection["original_span"] not in original["original_source_spans"]
                or page["source_pdf_checksum"] != original["source_pdf_checksum"]
                or page["physical_page"] != projection["original_span"]["page_index"] + 1
                or projection["frozen_representation_id"]
                != page["frozen_representation"]["representation_id"]
                or page["frozen_native_alignment"] != "EXACT"
                or page["frozen_representation"]["raw_text"]
                != page["backend_representation"]["raw_text"]
            ):
                raise ValueError(
                    "hit geometry is not bound to its exact frozen page representation"
                )
            for fragment in projection["fragments"]:
                box, span = (
                    Box.model_validate(fragment["box"]),
                    Span.model_validate(fragment["span"]),
                )
                _check_page_box(box, page)
                original_span = projection["original_span"]
                # Stop if the hit geometry fragment extends beyond its original source span.
                if (
                    not original_span["raw_start"]
                    <= span.start
                    < span.end
                    <= original_span["raw_end"]
                ):
                    raise ValueError(
                        "hit geometry fragment extends beyond its original source span"
                    )
        # Stop if the hit geometry readiness differs from its frozen source spans.
        if hit["geometry_status"] not in {"EXACT", "UNRESOLVED"} or (
            hit["geometry_status"] == "EXACT"
            and (
                hit["errors"]
                or [projection["original_span"] for projection in hit["geometry"]]
                != hit["original_source_spans"]
            )
        ):
            raise ValueError("hit geometry readiness differs from its frozen source spans")
    if recheck_inputs:
        for path, expected in {
            **manifest["input_sha256"],
            **manifest["reference_code_sha256"],
        }.items():
            # Stop if the layout preparation input changed.
            if sha256(Path(path)) != expected:
                raise ValueError(f"layout preparation input changed: {path}")
        # Re-extract coordinates to check exact projection, not only artifact hashes.
        cache = {}
        backend = PyMuPDFPageBackend()
        for page in pages:
            if page["backend_representation"] is None:
                continue
            with fitz.open(_pdf_for_page(page, manifest["input_sha256"])) as pdf:
                native = backend.extract(pdf[page["physical_page"] - 1], page["page_key"])
            cache[page["page_key"]] = native
            # Stop if the layout backend evidence differs from a fresh verified PDF
            # extraction.
            if (
                native.raw_text != page["backend_representation"]["raw_text"]
                or [cell.model_dump() for cell in native.cells] != page["native_cells"]
            ):
                raise ValueError(
                    "layout backend evidence differs from a fresh verified PDF extraction"
                )
        for hit in hits:
            for projection in hit["geometry"]:
                page = page_map[projection["page_key"]]
                span = projection["original_span"]
                expected = project_frozen_hit(
                    cache[page["page_key"]],
                    page["frozen_representation"]["raw_text"],
                    span["raw_start"],
                    span["raw_end"],
                )
                # Stop if the hit geometry differs from the original PDF characters.
                if projection["fragments"] != expected:
                    raise ValueError("hit geometry differs from the original PDF characters")
    return {
        "status": "PASS",
        "pages": len(pages),
        "original_hits": len(hits),
        "inputs_rechecked": recheck_inputs,
        "ownership_truth_assigned": False,
    }


# Reject a box that does not fit within the recorded page dimensions.
def _check_page_box(box: Box, page: dict):
    # Stop if the layout geometry extends beyond the upright page.
    if box.x1 > page["width"] + 0.01 or box.y1 > page["height"] + 0.01:
        raise ValueError("layout geometry extends beyond the upright page")
