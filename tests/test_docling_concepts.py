# Tests for importing Docling layout data without changing the original page text.
from __future__ import annotations

import gzip
import json
from pathlib import Path

import fitz
import pytest
from fixtures import create_fixture

from course_parser.bridge import prepare_bridge
from course_parser.cli import main
from course_parser.docling_adapter import convert_box, parse_docling_page
from course_parser.io import read_json, read_jsonl, sha256, write_csv, write_json
from course_parser.layout_models import Box, Region, Span, TextCell, assign_cells
from course_parser.layout_stage import prepare_layout, verify_layout
from course_parser.pdf_backend import PyMuPDFPageBackend, project_frozen_hit
from course_parser.release import TaskRelease


# Create the synthetic page inputs and original-hit records used by the layout tests.
def evidence_fixture(tmp_path, reference, *, alter_frozen=False, missing_pdf=False):
    fixture = create_fixture(tmp_path / "inputs", reference)
    if alter_frozen:
        with gzip.open(fixture.stream, "rt") as stream:
            pages = [json.loads(line) for line in stream]
        for page in pages:
            page["raw_text"] += " \n"
        with gzip.open(fixture.stream, "wt") as stream:
            for page in pages:
                stream.write(json.dumps(page) + "\n")
        for task in fixture.tasks:
            task["page_stream_sha256"] = sha256(fixture.stream)
        path = fixture.release / "page_review_tasks.csv"
        path.unlink()
        write_csv(path, fixture.tasks, list(fixture.tasks[0]))
        fixture.refresh_release()
    release = TaskRelease.load(fixture.release, fixture.vocabulary, reference)
    if missing_pdf:
        fixture.pdf.unlink()
    packet = tmp_path / "packet"
    reference.materialize(fixture.release, packet, start_rank=1, count=2)
    evidence = tmp_path / "evidence"
    prepare_bridge(reference, release, packet, evidence)
    return fixture, evidence


# Build the test box in the coordinate format expected by the adapter.
def bbox(x0, y0, x1, y1):
    return {"l": x0, "t": y0, "r": x1, "b": y1, "coord_origin": "TOPLEFT"}


# Create a small bound external-engine export for the adapter tests.
def model_export(tmp_path, evidence, *, missing_order=False):
    pages = read_jsonl(evidence / "page_evidence.jsonl")
    native_pages = []
    fixture_pdf = Path(read_jsonl(evidence / "task_evidence.jsonl")[0]["source_pdf_path"])
    with fitz.open(fixture_pdf) as pdf:
        for page in pages:
            native_pages.append(
                PyMuPDFPageBackend().extract(pdf[page["physical_page"] - 1], page["page_key"])
            )
    texts, refs = [], []
    for page, native in zip(pages, native_pages, strict=True):
        for region in native.regions:
            index = len(texts)
            reference = f"#/texts/{index}"
            texts.append(
                {
                    "self_ref": reference,
                    "label": "text",
                    "orig": region.predicted_text,
                    "text": region.predicted_text,
                    "children": [],
                    "prov": [
                        {
                            "page_no": page["physical_page"],
                            "bbox": bbox(
                                region.box.x0, region.box.y0, region.box.x1, region.box.y1
                            ),
                            "charspan": [0, len(region.predicted_text)],
                        }
                    ],
                }
            )
            refs.append({"$ref": reference})
    raw = {
        "schema_name": "DoclingDocument",
        "version": "synthetic-contract-fixture",
        "pages": {
            str(page["physical_page"]): {
                "page_no": page["physical_page"],
                "size": {"width": native.width, "height": native.height},
            }
            for page, native in zip(pages, native_pages, strict=True)
        },
        "texts": texts,
        "tables": [],
        "pictures": [],
        "groups": [{"self_ref": "#/groups/0", "children": refs}],
        "body": {"children": [] if missing_order else [{"$ref": "#/groups/0"}]},
        "furniture": {"children": []},
    }
    raw_path = tmp_path / "docling.json"
    write_json(raw_path, raw)
    central = next(page for page in pages if page["physical_page"] == 2)
    manifest = tmp_path / "docling-input.json"
    write_json(
        manifest,
        {
            "schema_version": "course-parser-docling-input-v1",
            "coordinate_frame": "VERIFIED_UPRIGHT_PAGE",
            "pages": [
                {
                    "page_key": central["page_key"],
                    "source_pdf_checksum": central["source_pdf_checksum"],
                    "image_sha256": central["image_sha256"],
                    "docling_page_no": 2,
                    "engine_version": "synthetic-contract-fixture",
                    "document_json_path": raw_path.name,
                    "document_json_sha256": sha256(raw_path),
                }
            ],
        },
    )
    return manifest


# Check that native stage keeps original hits and exact coordinate projection.
def test_native_stage_keeps_original_hits_and_exact_coordinate_projection(tmp_path, reference):
    _, evidence = evidence_fixture(tmp_path, reference)
    before = (evidence / "run_manifest.json").read_bytes()
    output = tmp_path / "layout"
    result = prepare_layout(reference, evidence, output)
    assert result["native_cells"] == 63
    assert result["geometry_status_counts"] == {"EXACT": 6}
    assert result["assignment_status_counts"] == {"ASSIGNED": 63}
    assert (evidence / "run_manifest.json").read_bytes() == before
    assert verify_layout(output, recheck_inputs=True)["status"] == "PASS"
    for hit in read_jsonl(output / "hit_geometry.jsonl"):
        assert hit["decision"] == "" and hit["proposed_owner"] is None
        assert hit["geometry"]
    assert all(
        page["predictions"]["reading_order_status"] == "UNRESOLVED"
        for page in read_jsonl(output / "page_layout.jsonl")
    )
    with pytest.raises(FileExistsError):
        prepare_layout(reference, evidence, output)


# Check that rotation is applied once in the rendered page frame.
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotation_is_applied_once_in_the_rendered_page_frame(rotation):
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((60, 100), "Ore deposit geology")
        source_box = fitz.Rect(page.get_text("words")[0][:4])
        page.set_rotation(rotation)
        result = PyMuPDFPageBackend().extract(page, "rotation-test")
        expected = source_box * page.rotation_matrix
        actual = result.cells[0].box
        assert (actual.x0, actual.y0, actual.x1, actual.y1) == pytest.approx(tuple(expected))
        assert (result.width, result.height) == ((600, 800) if rotation in {0, 180} else (800, 600))
        assert result.raw_text == "Ore deposit geology\n"
        assert project_frozen_hit(result, result.raw_text, 0, 11)


# Check that different text representations never receive guessed geometry.
def test_different_text_representations_never_receive_guessed_geometry(tmp_path, reference):
    _, evidence = evidence_fixture(tmp_path, reference, alter_frozen=True)
    output = tmp_path / "layout"
    result = prepare_layout(reference, evidence, output)
    assert result["geometry_status_counts"] == {"UNRESOLVED": 6}
    assert all(hit["geometry"] == [] for hit in read_jsonl(output / "hit_geometry.jsonl"))
    pages = read_jsonl(output / "page_layout.jsonl")
    assert all(page["frozen_native_alignment"] == "UNRESOLVED" for page in pages)
    assert all(page["frozen_representation"]["raw_text"].endswith(" \n") for page in pages)
    assert verify_layout(output)["status"] == "PASS"


# Check that Docling multi page import retains only bound page and nested order.
def test_docling_multi_page_import_retains_only_bound_page_and_nested_order(tmp_path, reference):
    _, evidence = evidence_fixture(tmp_path, reference)
    manifest = model_export(tmp_path, evidence)
    output = tmp_path / "layout"
    result = prepare_layout(reference, evidence, output, docling_manifest=manifest)
    assert result["docling_pages_imported"] == 1
    pages = read_jsonl(output / "page_layout.jsonl")
    selected = next(page for page in pages if page["physical_page"] == 2)
    prediction = selected["predictions"]
    assert prediction["engine"] == "DOCLING"
    assert prediction["reading_order_status"] == "MODEL_PROPOSED"
    assert all(
        "Degree Requirements" not in region["predicted_text"] for region in prediction["regions"]
    )
    assert prediction["reading_order"] == [region["region_id"] for region in prediction["regions"]]
    assert not read_json(output / "layout_manifest.json")["docling_inference_run"]
    assert verify_layout(output, recheck_inputs=True)["status"] == "PASS"


# Check that model output requires page and artifact binding.
@pytest.mark.parametrize("change", ["wrong_image", "wrong_json_hash", "unknown_frame"])
def test_model_output_requires_page_and_artifact_binding(tmp_path, reference, change):
    _, evidence = evidence_fixture(tmp_path, reference)
    manifest = model_export(tmp_path, evidence)
    value = read_json(manifest)
    if change == "wrong_image":
        value["pages"][0]["image_sha256"] = "0" * 64
    elif change == "wrong_json_hash":
        value["pages"][0]["document_json_sha256"] = "0" * 64
    else:
        value["coordinate_frame"] = "UNSPECIFIED"
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        prepare_layout(reference, evidence, tmp_path / "layout", docling_manifest=manifest)
    assert not (tmp_path / "layout").exists()


# Build one synthetic table cell with its declared row, column and source location.
def cell(identifier, text, x, y):
    return TextCell(
        cell_id=identifier,
        line_id="line",
        block_id="block",
        text=text,
        span=Span(start=0, end=len(text)),
        box=Box(x0=x, y0=y, x1=x + 10, y1=y + 10),
        font_name="Test",
        font_size=12,
    )


# Check that region overlap retains ambiguous and orphan text.
def test_region_overlap_retains_ambiguous_and_orphan_text():
    cells = (cell("inside", "Ore", 10, 10), cell("orphan", "deposit", 200, 200))
    regions = tuple(
        Region(
            region_id=name,
            label="text",
            predicted_text="Ore",
            box=Box(x0=0, y0=0, x1=100, y1=100),
            origin="DOCLING",
            source_ref=name,
        )
        for name in ("one", "two")
    )
    assignments = assign_cells(cells, regions)
    assert [value.status for value in assignments] == ["AMBIGUOUS", "ORPHAN"]
    assert all(value.region_id is None for value in assignments)
    assert len(assignments) == len(cells)


# Create a synthetic table export with separate grid and word-assignment evidence.
def table_export():
    return {
        "pages": {"1": {"page_no": 1, "size": {"width": 600, "height": 800}}},
        "texts": [],
        "groups": [],
        "body": {"children": [{"$ref": "#/tables/0"}]},
        "tables": [
            {
                "self_ref": "#/tables/0",
                "label": "table",
                "prov": [{"page_no": 1, "bbox": bbox(0, 0, 300, 100)}],
                "data": {
                    "num_rows": 2,
                    "num_cols": 2,
                    "table_cells": [
                        {
                            "start_row_offset_idx": 0,
                            "end_row_offset_idx": 1,
                            "start_col_offset_idx": 0,
                            "end_col_offset_idx": 2,
                            "column_header": True,
                            "text": "Ore",
                            "bbox": bbox(0, 0, 100, 30),
                        },
                        {
                            "start_row_offset_idx": 1,
                            "end_row_offset_idx": 2,
                            "start_col_offset_idx": 0,
                            "end_col_offset_idx": 1,
                            "text": "3 credits",
                            "bbox": bbox(0, 50, 100, 80),
                        },
                    ],
                },
            }
        ],
    }


# Check that table grid and word assignment are separate and preserve empty space.
def test_table_grid_and_word_assignment_are_separate_and_preserve_empty_space():
    cells = (cell("word", "Ore", 10, 10), cell("unassigned", "deposit", 200, 50))
    prediction = parse_docling_page(
        table_export(),
        page_no=1,
        page_key="table-page",
        width=600,
        height=800,
        cells=cells,
        engine_version="synthetic-test",
    )
    table = prediction.tables[0]
    assert (table.rows, table.columns) == (2, 2)
    assert table.cells[0].col_end == 2
    assert table.cells[0].column_header
    assert [value.status for value in table.word_assignments] == ["ASSIGNED", "ORPHAN"]
    assert len(table.cells) == 2  # Unoccupied logical positions are not invented cells.


# Check that invalid model geometry and grids are rejected.
@pytest.mark.parametrize("change", ["overlap", "out_of_grid", "bad_box"])
def test_invalid_model_geometry_and_grids_are_rejected(change):
    raw = table_export()
    if change == "overlap":
        raw["tables"][0]["data"]["table_cells"][1]["start_row_offset_idx"] = 0
    elif change == "out_of_grid":
        raw["tables"][0]["data"]["table_cells"][0]["end_col_offset_idx"] = 3
    else:
        raw["tables"][0]["prov"][0]["bbox"]["r"] = 700
    with pytest.raises(ValueError):
        parse_docling_page(
            raw, page_no=1, page_key="page", width=600, height=800, engine_version="test"
        )


# Map top-left and bottom-left coordinate conventions to the same page rectangle.
def test_coordinate_origins_normalize_to_the_same_box():
    top = convert_box(bbox(10, 20, 30, 40), 600, 800, 600, 800)
    bottom = convert_box(
        {"l": 10, "r": 30, "t": 780, "b": 760, "coord_origin": "BOTTOMLEFT"}, 600, 800, 600, 800
    )
    assert top == bottom


# Check that missing and cyclic order stay unresolved.
def test_missing_and_cyclic_order_stay_unresolved(tmp_path, reference):
    _, evidence = evidence_fixture(tmp_path, reference)
    manifest = model_export(tmp_path, evidence, missing_order=True)
    output = tmp_path / "layout"
    prepare_layout(reference, evidence, output, docling_manifest=manifest)
    page = next(
        page for page in read_jsonl(output / "page_layout.jsonl") if page["physical_page"] == 2
    )
    assert page["predictions"]["reading_order_status"] == "UNRESOLVED"
    raw = table_export()
    raw["groups"] = [{"self_ref": "#/groups/0", "children": [{"$ref": "#/groups/0"}]}]
    raw["body"] = {"children": [{"$ref": "#/groups/0"}]}
    proposal = parse_docling_page(
        raw, page_no=1, page_key="page", width=600, height=800, engine_version="test"
    )
    assert "DOCLING_TREE_CYCLE" in proposal.warnings
    assert proposal.reading_order_status == "UNRESOLVED"


# Request OCR for an image-only page without inventing native PDF words.
def test_image_only_page_routes_to_ocr_without_creating_words():
    with fitz.open() as pdf:
        page = pdf.new_page()
        native = PyMuPDFPageBackend().extract(page, "scan")
        assert native.cells == () and native.raw_text == ""


# Check that scanned PDF keeps frozen OCR and routes pages to OCR.
def test_scanned_pdf_keeps_frozen_ocr_and_routes_pages_to_ocr(tmp_path, reference):
    fixture = create_fixture(tmp_path / "inputs", reference)
    with gzip.open(fixture.stream, "rt") as stream:
        frozen_pages = [json.loads(line) for line in stream]
    with fitz.open(fixture.pdf) as original:
        images = [page.get_pixmap().tobytes("png") for page in original]
    fixture.pdf.unlink()
    with fitz.open() as scanned:
        for raster in images:
            page = scanned.new_page()
            page.insert_image(page.rect, stream=raster)
        scanned.save(fixture.pdf)
    checksum = sha256(fixture.pdf)
    with gzip.open(fixture.stream, "wt") as stream:
        for page in frozen_pages:
            page.update(source_pdf_checksum=checksum, extraction_type="ocr")
            stream.write(json.dumps(page) + "\n")
    for task in fixture.tasks:
        task.update(source_pdf_checksum=checksum, page_stream_sha256=sha256(fixture.stream))
    path = fixture.release / "page_review_tasks.csv"
    path.unlink()
    write_csv(path, fixture.tasks, list(fixture.tasks[0]))
    fixture.refresh_release()
    release = TaskRelease.load(fixture.release, fixture.vocabulary, reference)
    reference.materialize(fixture.release, tmp_path / "packet", start_rank=1, count=2)
    prepare_bridge(reference, release, tmp_path / "packet", tmp_path / "evidence")
    output = tmp_path / "layout"
    result = prepare_layout(reference, tmp_path / "evidence", output)
    assert result["native_cells"] == 0
    assert result["geometry_status_counts"] == {"UNRESOLVED": 6}
    for page in read_jsonl(output / "page_layout.jsonl"):
        assert page["routing"] == "OCR_REQUIRED"
        assert page["frozen_representation"]["raw_text"]
        assert page["backend_representation"]["raw_text"] == ""
    assert verify_layout(output, recheck_inputs=True)["status"] == "PASS"


# Check that incomplete evidence retains all original hits.
def test_incomplete_evidence_retains_all_original_hits(tmp_path, reference):
    _, evidence = evidence_fixture(tmp_path, reference, missing_pdf=True)
    result = prepare_layout(reference, evidence, tmp_path / "layout")
    assert result["status"] == "LAYOUT_INPUT_INCOMPLETE"
    assert result["geometry_status_counts"] == {"UNRESOLVED": 6}
    assert result["pending_dispositions"] == 6
    assert verify_layout(tmp_path / "layout")["status"] == "PASS"


# Check that layout cli is usable.
def test_layout_cli_is_usable(tmp_path, reference, capsys):
    _, evidence = evidence_fixture(tmp_path, reference)
    output = tmp_path / "layout"
    assert (
        main(
            [
                "enrich-layout",
                "--reference-project",
                str(reference.root),
                "--evidence-run",
                str(evidence),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["original_hits"] == 6
    assert main(["verify-layout", "--run", str(output), "--recheck-inputs"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


# Catch altered layout meaning even when the changed file has a newly recorded checksum.
@pytest.mark.parametrize("change", ["dropped_word", "changed_hit_geometry"])
def test_layout_verification_detects_rehashed_semantic_changes(tmp_path, reference, change):
    _, evidence = evidence_fixture(tmp_path, reference)
    output = tmp_path / "layout"
    prepare_layout(reference, evidence, output)
    if change == "dropped_word":
        path = output / "page_layout.jsonl"
        rows = read_jsonl(path)
        rows[0]["predictions"]["assignments"].pop()
    else:
        path = output / "hit_geometry.jsonl"
        rows = read_jsonl(path)
        box = rows[0]["geometry"][0]["fragments"][0]["box"]
        box["x0"] += 5
        box["x1"] += 5
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    manifest_path = output / "layout_manifest.json"
    manifest = read_json(manifest_path)
    manifest["files_sha256"][path.name] = sha256(path)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify_layout(output, recheck_inputs=True)


# Check that malformed model page preserves other pages and original hit geometry.
def test_malformed_model_page_preserves_other_pages_and_original_hit_geometry(tmp_path, reference):
    _, evidence = evidence_fixture(tmp_path, reference)
    manifest = model_export(tmp_path, evidence)
    raw_path = tmp_path / "docling.json"
    raw = read_json(raw_path)
    raw["body"] = {"children": ["invalid node"]}
    raw_path.write_text(json.dumps(raw))
    value = read_json(manifest)
    value["pages"][0]["document_json_sha256"] = sha256(raw_path)
    manifest.write_text(json.dumps(value))
    output = tmp_path / "layout"
    result = prepare_layout(reference, evidence, output, docling_manifest=manifest)
    assert result["status"] == "LAYOUT_INPUT_INCOMPLETE"
    assert result["page_status_counts"] == {"BACKEND_READY": 2, "LAYOUT_INPUT_INCOMPLETE": 1}
    assert result["cells_pending_layout"] == 22
    assert result["geometry_status_counts"] == {"EXACT": 6}
    assert result["pending_dispositions"] == 6
    assert verify_layout(output, recheck_inputs=True)["status"] == "PASS"
