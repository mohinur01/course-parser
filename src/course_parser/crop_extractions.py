# Cut out small regions of a PDF page for closer inspection and OCR (reading text from an
# image). Keep each reading separately and record how image coordinates map back to the
# original PDF.
"""Immutable crop evidence, explicit coordinate frames and separate OCR outputs."""

from __future__ import annotations

import csv
import io
import math
import shutil
import subprocess
from pathlib import Path
from typing import Literal

import fitz
from pydantic import Field, model_validator

from .io import read_json, sha256, write_json
from .layout_models import Box, StrictModel
from .pdf_backend import PyMuPDFPageBackend

Matrix = tuple[float, float, float, float, float, float]


# Keep the crop's source identity, image size and coordinate conversions together so every OCR
# location can be traced back to the PDF.
class CropFrame(StrictModel):
    schema_version: Literal["source-crop-frame-v1"] = "source-crop-frame-v1"
    source_pdf_checksum: str
    physical_page: int = Field(ge=1)
    page_width: float = Field(gt=0)
    page_height: float = Field(gt=0)
    rotation_degrees: Literal[0, 90, 180, 270]
    requested_upright_box: Box
    dpi: int = Field(ge=72, le=600)
    pixel_origin: tuple[int, int]
    pixel_width: int = Field(gt=0)
    pixel_height: int = Field(gt=0)
    crop_image_sha256: str
    crop_pixels_to_upright_points: Matrix
    upright_points_to_unrotated_mupdf: Matrix
    unrotated_mupdf_to_pdf_user_space: Matrix
    crop_pixels_to_pdf_user_space: Matrix
    preprocessing: Literal["IDENTITY_NO_RESIZE_ROTATION_OR_UNWARP"] = (
        "IDENTITY_NO_RESIZE_ROTATION_OR_UNWARP"
    )

    # Verify every coordinate conversion between the crop image and the original PDF.
    @model_validator(mode="after")
    def check_transform_chain(self):
        matrices = (
            self.crop_pixels_to_upright_points,
            self.upright_points_to_unrotated_mupdf,
            self.unrotated_mupdf_to_pdf_user_space,
            self.crop_pixels_to_pdf_user_space,
        )
        # Coordinates must be finite numbers so they can be mapped back to the source.
        if any(not all(math.isfinite(x) for x in matrix) for matrix in matrices):
            raise ValueError("crop transform has nonfinite coordinates")
        scale = 72 / self.dpi
        expected = (scale, 0, 0, scale, self.pixel_origin[0] * scale, self.pixel_origin[1] * scale)
        # The crop conversion must include its actual pixel origin and rendering resolution.
        if any(abs(a - b) > 1e-6 for a, b in zip(expected, matrices[0], strict=True)):
            raise ValueError("crop transform ignores integer pixel origin or render scale")
        # Check that the combined conversion is the same as applying each recorded coordinate
        # conversion in order.
        composed = fitz.Matrix(matrices[0]) * fitz.Matrix(matrices[1]) * fitz.Matrix(matrices[2])
        # Stop if the crop transform composition changed.
        if any(abs(a - b) > 1e-5 for a, b in zip(composed, matrices[3], strict=True)):
            raise ValueError("crop transform composition changed")
        for matrix in matrices:
            a, b, c, d, _, _ = matrix
            # Stop if the crop transform is not invertible.
            if abs(a * d - b * c) < 1e-10:
                raise ValueError("crop transform is not invertible")
        box = self.requested_upright_box
        # Keep the requested crop inside the upright page.
        if box.x1 > self.page_width or box.y1 > self.page_height:
            raise ValueError("crop lies outside upright page")
        # PDF coordinates and raster pixels use different units. Verify the integer pixel area
        # produced at the recorded resolution.
        expected_pixels = (
            fitz.Rect(box.x0, box.y0, box.x1, box.y1) * fitz.Matrix(self.dpi / 72, self.dpi / 72)
        ).irect
        # The crop image dimensions must match the recorded clipping box.
        if (
            expected_pixels.x0,
            expected_pixels.y0,
            expected_pixels.width,
            expected_pixels.height,
        ) != (
            *self.pixel_origin,
            self.pixel_width,
            self.pixel_height,
        ):
            raise ValueError("crop raster extent does not match recorded clipping box")
        return self

    # Convert polygon coordinates through the recorded transform chain.
    def map_polygon(self, polygon, *, pdf_user_space=False):
        # Reject invalid crop polygon.
        if not polygon or any(len(p) != 2 or not all(math.isfinite(x) for x in p) for p in polygon):
            raise ValueError("invalid crop polygon")
        # Reject a path or coordinate that falls outside its allowed source boundary.
        if any(
            not (0 <= x <= self.pixel_width and 0 <= y <= self.pixel_height) for x, y in polygon
        ):
            raise ValueError("OCR coordinates escape the bound crop image")
        # Use the requested destination coordinate system while keeping the original crop
        # location available.
        matrix = (
            self.crop_pixels_to_pdf_user_space
            if pdf_user_space
            else self.crop_pixels_to_upright_points
        )
        return [list(fitz.Point(p) * fitz.Matrix(matrix)) for p in polygon]


# Render the requested PDF region and save the coordinate conversions needed to trace it back
# to the page.
def render_crop(
    pdf_path: Path, checksum: str, physical_page: int, box: Box, output: Path, *, dpi=300
):
    # Reject this input if its file fingerprint no longer matches the recorded version.
    if sha256(pdf_path) != checksum:
        raise ValueError("crop source PDF checksum changed")
    output.mkdir(parents=True, exist_ok=False)
    with fitz.open(pdf_path) as pdf:
        # The requested physical page must exist in the source PDF.
        if not 1 <= physical_page <= len(pdf):
            raise ValueError("crop physical page outside source PDF")
        page = pdf[physical_page - 1]
        rect = fitz.Rect(box.x0, box.y0, box.x1, box.y1)
        # Keep the crop inside the source page's recorded bounds.
        if not page.rect.contains(rect):
            raise ValueError("crop lies outside source page")
        pix = page.get_pixmap(dpi=dpi, clip=rect, colorspace=fitz.csRGB, alpha=False)
        pix.save(output / "crop.png")
        matrix = fitz.Matrix(72 / dpi, 0, 0, 72 / dpi, pix.x * 72 / dpi, pix.y * 72 / dpi)
        inverse_pdf = ~page.transformation_matrix
        frame = CropFrame(
            source_pdf_checksum=checksum,
            physical_page=physical_page,
            page_width=page.rect.width,
            page_height=page.rect.height,
            rotation_degrees=page.rotation,
            requested_upright_box=box,
            dpi=dpi,
            pixel_origin=(pix.x, pix.y),
            pixel_width=pix.width,
            pixel_height=pix.height,
            crop_image_sha256=sha256(output / "crop.png"),
            crop_pixels_to_upright_points=tuple(matrix),
            upright_points_to_unrotated_mupdf=tuple(page.derotation_matrix),
            unrotated_mupdf_to_pdf_user_space=tuple(inverse_pdf),
            crop_pixels_to_pdf_user_space=tuple(matrix * page.derotation_matrix * inverse_pdf),
        )
        native, native_error = None, None
        try:
            native = PyMuPDFPageBackend().extract(page, f"crop:p{physical_page}")
        except (ValueError, RuntimeError) as exc:
            native_error = f"{type(exc).__name__}: {exc}"
    # Stop if the crop source PDF changed during rendering.
    if sha256(pdf_path) != checksum:
        raise ValueError("crop source PDF changed during rendering")
    write_json(output / "transform.json", frame.model_dump(mode="json"))
    native_dir = output / "native"
    native_dir.mkdir()
    if native is None:
        write_json(
            native_dir / "extraction.json",
            {
                "engine": "PYMUPDF_NATIVE",
                "engine_version": fitz.VersionBind,
                "status": "FAILED",
                "input_crop_sha256": frame.crop_image_sha256,
                "transform_sha256": sha256(output / "transform.json"),
                "raw_text": None,
                "raw_text_sha256": None,
                "lines": [],
                "review_flags": [],
                "error": native_error,
                "authoritative_text_replaced": False,
            },
        )
        return frame
    (native_dir / "source_page_raw.txt").write_text(native.raw_text, encoding="utf-8")
    cells = [
        c
        for c in native.cells
        if fitz.Rect(c.box.x0, c.box.y0, c.box.x1, c.box.y1).intersects(rect)
    ]
    grouped = {}
    for cell in cells:
        grouped.setdefault(cell.line_id, []).append(cell)
    lines, flags = [], []
    for line_id, group in grouped.items():
        start, end = min(c.span.start for c in group), max(c.span.end for c in group)
        bounds = fitz.Rect(group[0].box.x0, group[0].box.y0, group[0].box.x1, group[0].box.y1)
        for cell in group[1:]:
            bounds |= fitz.Rect(cell.box.x0, cell.box.y0, cell.box.x1, cell.box.y1)
        if not rect.contains(bounds):
            flags.append("NATIVE_TEXT_INTERSECTS_CROP_EDGE")
        lines.append(
            {
                "line_id": line_id,
                "text": native.raw_text[start:end],
                "source_page_span": {"start": start, "end": end},
                "upright_box": list(bounds),
                "source_cells": [c.model_dump(mode="json") for c in group],
            }
        )
    # Only an explicit display join; every source substring and the complete native page bytes
    # remain available. No claim of frozen-stream alignment.
    raw = "\n".join(entry["text"] for entry in lines)
    (native_dir / "raw.txt").write_text(raw, encoding="utf-8")
    write_json(
        native_dir / "extraction.json",
        {
            "engine": "PYMUPDF_NATIVE",
            "engine_version": fitz.VersionBind,
            "status": "EXTRACTED",
            "input_crop_sha256": frame.crop_image_sha256,
            "transform_sha256": sha256(output / "transform.json"),
            "raw_text_sha256": sha256(native_dir / "raw.txt"),
            "raw_text": raw,
            "lines": lines,
            "settings": {
                "native_clip_selection": "INTERSECTING_CELLS",
                "display_join": "NEWLINE_BETWEEN_EXACT_SOURCE_SLICES",
            },
            "source_page_raw_sha256": sha256(native_dir / "source_page_raw.txt"),
            "review_flags": sorted(set(flags)),
            "authoritative_text_replaced": False,
        },
    )
    return frame


# Check that the crop image and its recorded PDF coordinates still match the saved source.
def verify_crop(root: Path) -> CropFrame:
    frame = CropFrame.model_validate(read_json(root / "transform.json"))
    image = root / "crop.png"
    pix = fitz.Pixmap(str(image))
    # Stop if the crop image checksum or dimensions changed.
    if sha256(image) != frame.crop_image_sha256 or (pix.width, pix.height) != (
        frame.pixel_width,
        frame.pixel_height,
    ):
        raise ValueError("crop image checksum or dimensions changed")
    return frame


# Read Tesseract's tab-separated word output and map valid word boxes back to the PDF.
def parse_tesseract_tsv(raw: str, frame: CropFrame) -> list[dict]:
    rows = csv.DictReader(io.StringIO(raw), delimiter="\t", quoting=csv.QUOTE_NONE)
    required = {
        "level",
        "page_num",
        "block_num",
        "par_num",
        "line_num",
        "word_num",
        "left",
        "top",
        "width",
        "height",
        "conf",
        "text",
    }
    # Reject unsupported Tesseract TSV schema.
    if not rows.fieldnames or set(rows.fieldnames) != required:
        raise ValueError("unsupported Tesseract TSV schema")
    words = []
    for row in rows:
        # Reject malformed Tesseract TSV row.
        if None in row or any(v is None for v in row.values()):
            raise ValueError("malformed Tesseract TSV row")
        if int(row["level"]) != 5:
            continue
        x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        score = float(row["conf"])
        # Reject invalid Tesseract word geometry or confidence.
        if w <= 0 or h <= 0 or not math.isfinite(score) or not -1 <= score <= 100:
            raise ValueError("invalid Tesseract word geometry or confidence")
        polygon = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
        mapped = frame.map_polygon(polygon)
        words.append(
            {
                "word_id": ":".join(
                    row[k] for k in ("page_num", "block_num", "par_num", "line_num", "word_num")
                ),
                "text": row["text"],
                "recognition_score": score,
                "crop_polygon": polygon,
                "upright_polygon": mapped,
                "pdf_user_space_polygon": frame.map_polygon(polygon, pdf_user_space=True),
            }
        )
    return words


# Run Tesseract on a verified crop and save its text, word locations and execution result.
def run_tesseract(root: Path, *, psm: int, executable="tesseract", timeout=90) -> dict:
    # Use only the three crop-reading modes supported by this workflow.
    if psm not in (3, 6, 11):
        raise ValueError("supported crop segmentation modes are 3, 6 and 11")
    frame = verify_crop(root)
    directory = root / f"tesseract_psm{psm}"
    directory.mkdir(exist_ok=False)
    version, error, words, raw, status = None, None, [], None, "FAILED"
    binary = shutil.which(executable)
    try:
        # Stop if the tesseract executable is unavailable.
        if not binary:
            raise RuntimeError("Tesseract executable is unavailable")
        version = (
            subprocess.run([binary, "--version"], check=True, capture_output=True, timeout=10)
            .stdout.decode("utf-8")
            .splitlines()[0]
        )
        result = subprocess.run(
            [
                binary,
                str((root / "crop.png").resolve()),
                str((directory / "raw").resolve()),
                "-l",
                "eng",
                "--psm",
                str(psm),
                "txt",
                "tsv",
            ],
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        (directory / "stdout.log").write_bytes(result.stdout)
        (directory / "stderr.log").write_bytes(result.stderr)
        # Treat a failed OCR command as a failed reading, not as usable text.
        if result.returncode:
            raise RuntimeError(f"Tesseract exited with code {result.returncode}")
        raw = (directory / "raw.txt").read_bytes().decode("utf-8")
        words = parse_tesseract_tsv((directory / "raw.tsv").read_bytes().decode("utf-8"), frame)
        status = "EXTRACTED"
    # Handle oserror, value error, runtime error, subprocess's subprocess error by keeping the
    # failure explicit or using the documented fallback.
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    verify_crop(root)
    record = {
        "engine": "TESSERACT",
        "engine_version": version,
        "status": status,
        "settings": {"language": "eng", "psm": psm, "preprocessing": frame.preprocessing},
        "input_crop_sha256": frame.crop_image_sha256,
        "transform_sha256": sha256(root / "transform.json"),
        "raw_text": raw,
        "raw_text_sha256": sha256(directory / "raw.txt")
        if (directory / "raw.txt").exists()
        else None,
        "raw_tsv_sha256": sha256(directory / "raw.tsv")
        if (directory / "raw.tsv").exists()
        else None,
        "words": words,
        "review_flags": ["LOW_OCR_CONFIDENCE"]
        if any(w["recognition_score"] < 80 for w in words)
        else [],
        "error": error,
        "authoritative_text_replaced": False,
    }
    write_json(directory / "extraction.json", record)
    return record


# Compare separate records, retaining formatting-only and failure cases.
def extraction_disagreements(root: Path) -> dict:
    """Compare separate records, retaining formatting-only and failure cases.

    Whitespace comparison is a diagnostic only. Punctuation, case, hyphens and
    OCR characters are never corrected. Equal outputs never become course truth.
    """
    frame = verify_crop(root)
    records = []
    for path in sorted(root.glob("*/extraction.json")):
        record = read_json(path)
        # OCR results must remain separate from the original source text.
        if record.get("authoritative_text_replaced") is not False:
            raise ValueError("extraction replacement of authoritative text is prohibited")
        # Stop if the extraction belongs to a different crop or transform.
        if record["input_crop_sha256"] != frame.crop_image_sha256 or record[
            "transform_sha256"
        ] != sha256(root / "transform.json"):
            raise ValueError("extraction belongs to a different crop or transform")
        if record["raw_text_sha256"]:
            # Stop if the extraction raw text changed.
            if (
                sha256(path.parent / "raw.txt") != record["raw_text_sha256"]
                or (path.parent / "raw.txt").read_bytes().decode("utf-8") != record["raw_text"]
            ):
                raise ValueError("extraction raw text changed")
        # Stop if the extraction TSV changed.
        if (
            record.get("raw_tsv_sha256")
            and sha256(path.parent / "raw.tsv") != record["raw_tsv_sha256"]
        ):
            raise ValueError("extraction TSV changed")
        if record.get("raw_tsv_sha256") and record["status"] == "EXTRACTED":
            expected = parse_tesseract_tsv(
                (path.parent / "raw.tsv").read_bytes().decode("utf-8"), frame
            )
            # Stop if the extraction OCR word geometry or text changed.
            if expected != record["words"]:
                raise ValueError("extraction OCR word geometry or text changed")
        if record.get("source_page_raw_sha256"):
            source_file = path.parent / "source_page_raw.txt"
            # Stop if the extraction native source page changed.
            if sha256(source_file) != record["source_page_raw_sha256"]:
                raise ValueError("extraction native source page changed")
            page_text = source_file.read_bytes().decode("utf-8")
            for line in record["lines"]:
                start, end = line["source_page_span"]["start"], line["source_page_span"]["end"]
                # Stop if the native crop source slice changed.
                if line["text"] != page_text[start:end]:
                    raise ValueError("native crop source slice changed")
                for cell in line["source_cells"]:
                    # Stop if the native crop cell source slice changed.
                    if cell["text"] != page_text[cell["span"]["start"] : cell["span"]["end"]]:
                        raise ValueError("native crop cell source slice changed")
        # Stop if the extraction engine export changed.
        if (
            record.get("raw_export_sha256")
            and sha256(path.parent / "raw.json") != record["raw_export_sha256"]
        ):
            raise ValueError("extraction engine export changed")
        if record["engine"] == "PP_STRUCTURE_V3" and record["status"] == "EXTRACTED":
            from .crop_ppstructure import decode_crop_export

            parsed, lines = decode_crop_export(
                read_json(path.parent / "raw.json"), frame, root.name, record["engine_version"]
            )
            # Stop if the PP-Structure crop output differs from its preserved export.
            if (
                parsed.ocr.raw_text != record["raw_text"]
                or lines != record["lines"]
                or list(parsed.ocr.review_flags) != record["review_flags"]
            ):
                raise ValueError("PP-Structure crop output differs from its preserved export")
        records.append((path.parent.name, record))
    pairs, review = [], []
    for i, (left_id, left) in enumerate(records):
        if left["status"] != "EXTRACTED":
            review.append(
                {"kind": "EXTRACTION_FAILED", "extraction_ids": [left_id], "error": left["error"]}
            )
        for right_id, right in records[i + 1 :]:
            if left["status"] != "EXTRACTED" or right["status"] != "EXTRACTED":
                status = "NOT_COMPARABLE"
            elif left["raw_text"] == right["raw_text"]:
                status = "EXACT_TEXT_AGREEMENT"
            elif " ".join(left["raw_text"].split()) == " ".join(right["raw_text"].split()):
                status = "WHITESPACE_ONLY_DIFFERENCE"
            else:
                status = "TEXT_DISAGREEMENT"
            pair = {
                "extraction_ids": [left_id, right_id],
                "status": status,
                "independent_engines": left["engine"] != right["engine"],
            }
            pairs.append(pair)
            if status in {"TEXT_DISAGREEMENT", "WHITESPACE_ONLY_DIFFERENCE"}:
                review.append({"kind": status, **pair})
            if left["status"] == right["status"] == "EXTRACTED":
                alignment = align_word_geometry(left, right)
                if alignment is not None:
                    pair["word_geometry_alignment"] = alignment
                    if (
                        alignment["unmatched_left"]
                        or alignment["unmatched_right"]
                        or alignment["ambiguous"]
                    ):
                        review.append(
                            {
                                "kind": "GEOMETRY_COVERAGE_DISAGREEMENT",
                                "extraction_ids": [left_id, right_id],
                            }
                        )
        for flag in left["review_flags"]:
            review.append({"kind": flag, "extraction_ids": [left_id]})
    if len(records) < 2:
        review.append(
            {"kind": "SECOND_EXTRACTION_MISSING", "extraction_ids": [r[0] for r in records]}
        )
    if records and not any(r[1]["status"] == "EXTRACTED" and r[1]["raw_text"] for r in records):
        review.append(
            {"kind": "SOURCE_INPUT_INSUFFICIENT", "extraction_ids": [r[0] for r in records]}
        )
    return {
        "pairs": pairs,
        "review_events": review,
        "review_status": "PENDING_SOURCE_REVIEW" if review else "NO_EXTRACTION_DISAGREEMENT",
        "course_truth_assigned": False,
        "consensus_text_created": False,
    }


# Reciprocal spatial word matches; no fuzzy matching or OCR repair.
def align_word_geometry(left, right):
    """Reciprocal spatial word matches; no fuzzy matching or OCR repair.

    PP-Structure recognition lines are not word cells, so those comparisons
    remain text/line evidence instead of a false word-level alignment.
    """

    # Map each OCR word back to its exact crop and original-page coordinate frame.
    def words(record):
        if record["engine"] == "PYMUPDF_NATIVE":
            return [
                {
                    "id": c["cell_id"],
                    "text": c["text"],
                    "box": [c["box"][k] for k in ("x0", "y0", "x1", "y1")],
                }
                for line in record["lines"]
                for c in line["source_cells"]
            ]
        if record["engine"] == "TESSERACT" and "words" in record:
            return [
                {
                    "id": w["word_id"],
                    "text": w["text"],
                    "box": [
                        min(p[0] for p in w["upright_polygon"]),
                        min(p[1] for p in w["upright_polygon"]),
                        max(p[0] for p in w["upright_polygon"]),
                        max(p[1] for p in w["upright_polygon"]),
                    ],
                }
                for w in record["words"]
            ]
        return None

    a, b = words(left), words(right)
    if a is None or b is None:
        return None

    # Measure overlap between two source locations without inventing missing geometry.
    def overlap(x, y):
        x0, y0, x1, y1 = x["box"]
        p0, q0, p1, q1 = y["box"]
        area = max(0, min(x1, p1) - max(x0, p0)) * max(0, min(y1, q1) - max(y0, q0))
        return area / min((x1 - x0) * (y1 - y0), (p1 - p0) * (q1 - q0))

    # Retain all spatially plausible matches so an ambiguous location is not silently chosen.
    def choices(source, target):
        result, ambiguous = {}, []
        for item in source:
            options = sorted(
                [(overlap(item, other), other["id"]) for other in target], reverse=True
            )
            if options and options[0][0] >= 0.5:
                if len(options) > 1 and options[0][0] - options[1][0] < 0.1:
                    ambiguous.append(item["id"])
                else:
                    result[item["id"]] = options[0][1]
        return result, ambiguous

    ab, aa = choices(a, b)
    ba, bb = choices(b, a)
    by_a, by_b = {w["id"]: w for w in a}, {w["id"]: w for w in b}
    matches = [
        {"left_id": x, "right_id": y, "exact_text_agreement": by_a[x]["text"] == by_b[y]["text"]}
        for x, y in ab.items()
        if ba.get(y) == x
    ]
    used_a, used_b = {m["left_id"] for m in matches}, {m["right_id"] for m in matches}
    return {
        "method": "RECIPROCAL_OVERLAP_GE_0.5_TIE_MARGIN_0.1",
        "matches": matches,
        "unmatched_left": sorted(set(by_a) - used_a),
        "unmatched_right": sorted(set(by_b) - used_b),
        "ambiguous": {"left": aa, "right": bb} if aa or bb else {},
        "text_corrected": False,
    }
