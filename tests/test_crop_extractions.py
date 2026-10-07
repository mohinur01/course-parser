# Tests for rendering PDF crops, preserving OCR readings and mapping crop coordinates back to
# the PDF.
"""Round-trip geometry and immutable, independent extraction regressions."""

import copy
import shutil

import fitz
import pytest

from course_parser.crop_extractions import (
    CropFrame,
    align_word_geometry,
    extraction_disagreements,
    parse_tesseract_tsv,
    render_crop,
    run_tesseract,
    verify_crop,
)
from course_parser.io import read_json, sha256, write_json
from course_parser.layout_models import Box


# Build a synthetic crop record with its original PDF bounds and source identity.
def crop(tmp_path, rotation=0):
    source = tmp_path / "source.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=200, height=300)
        page.insert_text((30, 60), "17. Mineralogy", fontsize=13)
        page.insert_text((30, 85), "Study of minerals.", fontsize=12)
        page.set_rotation(rotation)
        pdf.save(source)
    root = tmp_path / "crop"
    frame = render_crop(
        source, sha256(source), 1, Box(x0=10.25, y0=20.5, x1=190.3, y1=195.7), root, dpi=144
    )
    return root, frame, source


# Map crop coordinates to the PDF and back, checking that the saved image covers the exact
# requested pixels.
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_crop_transform_roundtrip_and_exact_render_pixels(tmp_path, rotation):
    root, frame, source = crop(tmp_path, rotation)
    assert verify_crop(root) == frame
    assert frame.pixel_origin == (20, 41)  # fractional requested box rounded by renderer
    point = fitz.Point(31.4, 60.2)
    matrix = fitz.Matrix(frame.crop_pixels_to_pdf_user_space)
    roundtrip = point * matrix * ~matrix
    assert roundtrip.x == pytest.approx(point.x, abs=1e-4)
    assert roundtrip.y == pytest.approx(point.y, abs=1e-4)
    with fitz.open(source) as pdf:
        actual = fitz.Pixmap(str(root / "crop.png"))
        box = frame.requested_upright_box
        expected = pdf[0].get_pixmap(
            dpi=144,
            clip=fitz.Rect(box.x0, box.y0, box.x1, box.y1),
            colorspace=fitz.csRGB,
            alpha=False,
        )
        assert actual.samples == expected.samples
        mapped = fitz.Point(frame.map_polygon([[10, 20]])[0])
        assert mapped.x == pytest.approx(15)
        assert mapped.y == pytest.approx(30.5)
        # Explicit PDF user-space chain includes both page rotation and the PDF-to-MuPDF
        # origin/y-axis conversion.
        pdf_point = fitz.Point(frame.map_polygon([[10, 20]], pdf_user_space=True)[0])
        assert pdf_point * pdf[0].transformation_matrix * pdf[0].rotation_matrix == mapped


# Check that native source substrings and each cell remain exact.
def test_native_source_substrings_and_each_cell_remain_exact(tmp_path):
    root, _, _ = crop(tmp_path)
    native = read_json(root / "native/extraction.json")
    source_raw = (root / "native/source_page_raw.txt").read_text()
    for line in native["lines"]:
        span = line["source_page_span"]
        assert line["text"] == source_raw[span["start"] : span["end"]]
        for cell in line["source_cells"]:
            assert cell["text"] == source_raw[cell["span"]["start"] : cell["span"]["end"]]
    assert native["authoritative_text_replaced"] is False


# Check that wrong source checksum and outside crop are rejected.
def test_wrong_source_checksum_and_outside_crop_are_rejected(tmp_path):
    _, _, source = crop(tmp_path)
    with pytest.raises(ValueError, match="checksum"):
        render_crop(source, "wrong", 1, Box(x0=1, y0=1, x1=20, y1=20), tmp_path / "wrong")
    with pytest.raises(ValueError, match="outside"):
        render_crop(
            source, sha256(source), 1, Box(x0=1, y0=1, x1=250, y1=350), tmp_path / "outside"
        )


# Reject a crop whose image or coordinate conversion has changed.
def test_transform_and_image_tampering_rejected(tmp_path):
    root, frame, _ = crop(tmp_path)
    changed = frame.model_dump(mode="json")
    changed["crop_pixels_to_upright_points"][4] += 0.25
    with pytest.raises(ValueError, match="origin"):
        CropFrame.model_validate(changed)
    changed = frame.model_dump(mode="json")
    changed["crop_pixels_to_pdf_user_space"][5] += 1
    with pytest.raises(ValueError, match="composition"):
        CropFrame.model_validate(changed)
    (root / "crop.png").write_bytes((root / "crop.png").read_bytes() + b"changed")
    with pytest.raises(ValueError, match="checksum"):
        verify_crop(root)


# Build a small synthetic word-level OCR table for the extraction parser.
def tsv(text="Mineral0gy", left=10):
    return (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        + f"5\t1\t1\t1\t1\t1\t{left}\t20\t40\t12\t45.7\t{text}\n"
    )


# Check that OCR characters are not corrected and polygons are transformed.
def test_ocr_characters_are_not_corrected_and_polygons_are_transformed(tmp_path):
    _, frame, _ = crop(tmp_path)
    words = parse_tesseract_tsv(tsv(), frame)
    assert words[0]["text"] == "Mineral0gy"
    assert words[0]["recognition_score"] == 45.7
    assert words[0]["upright_polygon"][0] == [15, 30.5]
    with pytest.raises(ValueError, match="escape"):
        parse_tesseract_tsv(tsv(left=frame.pixel_width), frame)


# Copy a crop reading and alter its text to simulate a second engine response.
def other_extraction(root, name, text, *, engine="TESSERACT"):
    native = read_json(root / "native/extraction.json")
    record = copy.deepcopy(native)
    record.update(engine=engine, raw_text=text, raw_text_sha256=None, lines=[], review_flags=[])
    record.pop("source_page_raw_sha256", None)
    directory = root / name
    directory.mkdir()
    (directory / "raw.txt").write_text(text)
    record["raw_text_sha256"] = sha256(directory / "raw.txt")
    write_json(directory / "extraction.json", record)
    return record


# Check that disagreements preserve all outputs and queue same engine modes.
def test_disagreements_preserve_all_outputs_and_queue_same_engine_modes(tmp_path):
    root, _, _ = crop(tmp_path)
    native = read_json(root / "native/extraction.json")
    other_extraction(root, "tesseract_psm6", native["raw_text"] + "\n")
    other_extraction(root, "tesseract_psm11", "17. Mineral0gy\nStudy of minerals.")
    before = {str(p): sha256(p) for p in root.rglob("*") if p.is_file()}
    result = extraction_disagreements(root)
    assert {p["status"] for p in result["pairs"]} == {
        "TEXT_DISAGREEMENT",
        "WHITESPACE_ONLY_DIFFERENCE",
    }
    assert any(not p["independent_engines"] for p in result["pairs"])
    assert result["review_status"] == "PENDING_SOURCE_REVIEW"
    assert result["consensus_text_created"] is False
    assert result["course_truth_assigned"] is False
    assert before == {str(p): sha256(p) for p in root.rglob("*") if p.is_file()}


# Check that exact agreement does not assign course truth.
def test_exact_agreement_does_not_assign_course_truth(tmp_path):
    root, _, _ = crop(tmp_path)
    other_extraction(root, "second", read_json(root / "native/extraction.json")["raw_text"])
    result = extraction_disagreements(root)
    assert result["pairs"][0]["status"] == "EXACT_TEXT_AGREEMENT"
    assert result["course_truth_assigned"] is False


# Check that failure is retained as review event not empty agreement.
def test_failure_is_retained_as_review_event_not_empty_agreement(tmp_path):
    root, _, _ = crop(tmp_path)
    record = run_tesseract(root, psm=6, executable="course-parser-no-such-ocr")
    assert record["status"] == "FAILED"
    assert record["raw_text"] is None
    comparison = extraction_disagreements(root)
    assert comparison["pairs"][0]["status"] == "NOT_COMPARABLE"
    assert any(e["kind"] == "EXTRACTION_FAILED" for e in comparison["review_events"])


# Reject an extraction that points to a different input or changes its recorded text.
def test_extraction_input_binding_and_raw_text_tampering_rejected(tmp_path):
    root, _, _ = crop(tmp_path)
    other_extraction(root, "second", "changed")
    (root / "second/raw.txt").write_text("silently changed")
    with pytest.raises(ValueError, match="raw text changed"):
        extraction_disagreements(root)
    path = root / "second/extraction.json"
    record = read_json(path)
    record["input_crop_sha256"] = "different-image"
    path.write_text(__import__("json").dumps(record))
    with pytest.raises(ValueError, match="different crop"):
        extraction_disagreements(root)


# Check that actual tesseract retains txt tsv and independent settings.
@pytest.mark.skipif(shutil.which("tesseract") is None, reason="optional local OCR executable")
def test_actual_tesseract_retains_txt_tsv_and_independent_settings(tmp_path):
    root, _, _ = crop(tmp_path)
    record = run_tesseract(root, psm=6)
    assert record["status"] == "EXTRACTED"
    assert "Mineralogy" in record["raw_text"]
    assert record["words"]
    assert (root / "tesseract_psm6/raw.tsv").is_file()
    assert record["settings"]["psm"] == 6
    extraction_disagreements(root)


# Check that native failure preserves crop and is reviewable.
def test_native_failure_preserves_crop_and_is_reviewable(tmp_path, monkeypatch):
    from course_parser.pdf_backend import PyMuPDFPageBackend

    # Raise a deliberate error to test how the caller retains evidence after a failure.
    def fail(*args):
        raise ValueError("unreadable native character geometry")

    monkeypatch.setattr(PyMuPDFPageBackend, "extract", fail)
    root, frame, _ = crop(tmp_path)
    assert verify_crop(root) == frame
    record = read_json(root / "native/extraction.json")
    assert record["status"] == "FAILED"
    assert record["raw_text"] is None
    assert any(
        e["kind"] == "EXTRACTION_FAILED" for e in extraction_disagreements(root)["review_events"]
    )


# Check that spatial matching retains ambiguous words and missing coverage.
def test_spatial_matching_retains_ambiguous_words_and_missing_coverage():
    # Build a synthetic recognized word with the chosen box and confidence.
    def word(word_id, text, x0, x1):
        return {
            "word_id": word_id,
            "text": text,
            "upright_polygon": [[x0, 0], [x1, 0], [x1, 10], [x0, 10]],
        }

    left = {
        "engine": "TESSERACT",
        "words": [word("one", "Mineralogy", 0, 30), word("two", "Study", 40, 60)],
    }
    right = {
        "engine": "TESSERACT",
        "words": [word("a", "Mineral0gy", 0, 30), word("b", "Study", 40, 60)],
    }
    alignment = align_word_geometry(left, right)
    assert len(alignment["matches"]) == 2
    assert alignment["matches"][0]["exact_text_agreement"] is False
    right["words"].append(word("duplicate", "Mineralogy", 0, 30))
    alignment = align_word_geometry(left, right)
    assert alignment["ambiguous"]["left"] == ["one"]
    assert "one" in alignment["unmatched_left"]
    assert alignment["text_corrected"] is False


# Handle PDF crop-box offsets and rotated pages when mapping image coordinates back to the
# source.
def test_cropbox_offset_and_rotation_transform_chain(tmp_path):
    source = tmp_path / "offset.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=400, height=500)
        page.insert_text((110, 170), "Field Methods", fontsize=12)
        page.set_cropbox(fitz.Rect(70, 90, 370, 450))
        page.set_rotation(90)
        pdf.save(source)
    root = tmp_path / "offset-crop"
    frame = render_crop(
        source, sha256(source), 1, Box(x0=10.2, y0=20.4, x1=200.6, y1=250.8), root, dpi=150
    )
    with fitz.open(source) as pdf:
        point = fitz.Point(77, 104)
        mapped_pdf = point * fitz.Matrix(frame.crop_pixels_to_pdf_user_space)
        back = mapped_pdf * pdf[0].transformation_matrix * pdf[0].rotation_matrix
        expected = point * fitz.Matrix(frame.crop_pixels_to_upright_points)
        assert back.x == pytest.approx(expected.x, abs=1e-4)
        assert back.y == pytest.approx(expected.y, abs=1e-4)
