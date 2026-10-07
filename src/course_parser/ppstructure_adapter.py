# Translate optional PP-Structure layout and OCR exports into project data structures. Keep
# OCR readings separate from original PDF text and record uncertain matches.
"""Source-bound PP-StructureV3 proposals, with independent OCR evidence.

The documented parsing list is already in reading order. block_order is only
a display index and is legitimately null on some elements. No model text here
is a replacement for frozen text, a new keyword hit, or a course decision.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field

from .layout_models import (
    Box,
    CellAssignment,
    PagePredictions,
    Region,
    Span,
    StrictModel,
    TextCell,
    assign_cells,
)

POLICY = {
    "recognition_review_threshold": 0.8,
    "comparison_minimum_iou": 0.5,
    "comparison_ambiguity_margin": 0.1,
}


# Keep one OCR detection and its geometry as separate image-derived evidence.
class OCRDetection(StrictModel):
    detection_id: str
    polygon: tuple[tuple[float, float], ...]
    box: Box
    detection_score: float | None = Field(default=None, ge=0, le=1)


# Keep an OCR line's text and location without substituting it for native PDF text.
class OCRLine(StrictModel):
    line_id: str
    text: str
    span: Span | None
    polygon: tuple[tuple[float, float], ...]
    box: Box
    recognition_score: float = Field(ge=0, le=1)


# Group the OCR readings and their coordinate frame for one exported page.
class OCRRepresentation(StrictModel):
    representation_id: str
    channel: Literal["PP_STRUCTURE_OCR"] = "PP_STRUCTURE_OCR"
    raw_text: str
    lines: tuple[OCRLine, ...]
    detections: tuple[OCRDetection, ...]
    assignments: tuple[CellAssignment, ...]
    upstream_recognition_threshold: float | None = Field(default=None, ge=0, le=1)
    review_flags: tuple[str, ...]


# Describe a region sent to a specialized extraction tool, such as a table reader.
class SpecializedRoute(StrictModel):
    region_id: str
    label: str
    module_enabled: bool | None
    route: Literal[
        "TABLE_STRUCTURE_REVIEW_REQUIRED",
        "FORMULA_REVIEW_REQUIRED",
        "VISUAL_REGION_REVIEW_REQUIRED",
    ]


# Keep the imported PP-Structure layout, OCR and special-region suggestions for one page.
class PPStructurePage(StrictModel):
    layout: PagePredictions
    ocr: OCRRepresentation | None
    specialized_routes: tuple[SpecializedRoute, ...]
    input_image_width: int = Field(gt=0)
    input_image_height: int = Field(gt=0)
    model_settings: dict


# Validate a numeric engine value instead of silently accepting malformed input.
def _number(value):
    # PP-Structure coordinates must be finite numbers.
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("PP-Structure coordinates must be finite numbers")
    return float(value)


# Validate an engine polygon in its declared coordinate frame.
def _polygon(values, iw, ih, width, height):
    # PP-Structure OCR polygon must have four points.
    if not isinstance(values, list) or len(values) != 4:
        raise ValueError("PP-Structure OCR polygon must have four points")
    result = []
    for point in values:
        # Reject malformed PP-Structure OCR point.
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("malformed PP-Structure OCR point")
        x, y = map(_number, point)
        # Stop if the PP-Structure polygon extends beyond the bound image.
        if not 0 <= x <= iw or not 0 <= y <= ih:
            raise ValueError("PP-Structure polygon extends beyond the bound image")
        result.append((x * width / iw, y * height / ih))
    # Preserve the quadrilateral, and use its enclosing box for region assignment.
    area = (
        abs(
            sum(
                result[i][0] * result[(i + 1) % 4][1] - result[(i + 1) % 4][0] * result[i][1]
                for i in range(4)
            )
        )
        / 2
    )
    # An OCR word needs a usable box with positive area.
    if area <= 0:
        raise ValueError("PP-Structure OCR polygon is degenerate")
    xs, ys = zip(*result, strict=True)
    return tuple(result), Box(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


# Read the source box while keeping unavailable geometry explicit.
def _box(values, iw, ih, width, height):
    # Stop if the PP-Structure block has no rectangular image geometry.
    if not isinstance(values, list) or len(values) != 4:
        raise ValueError("PP-Structure block has no rectangular image geometry")
    x0, y0, x1, y1 = map(_number, values)
    # Stop if the PP-Structure block extends beyond the bound image.
    if not 0 <= x0 < x1 <= iw or not 0 <= y0 < y1 <= ih:
        raise ValueError("PP-Structure block extends beyond the bound image")
    return Box(x0=x0 * width / iw, y0=y0 * height / ih, x1=x1 * width / iw, y1=y1 * height / ih)


# Require the exported coordinate frame to match the verified original page image.
def _identity_frame(payload, iw, ih):
    # PP-Structure importer requires one rendered page image, not PDF input.
    if payload.get("page_index") is not None:
        raise ValueError("PP-Structure importer requires one rendered page image, not PDF input")
    for name, expected in (("width", iw), ("height", ih)):
        # The optional engine's output dimensions must match the checked page image.
        if name in payload and payload[name] != expected:
            raise ValueError("PP-Structure output dimensions differ from the verified image")
    settings = payload.get("model_settings")
    # Stop if the PP-Structure preprocessing settings are missing or unspecified.
    if not isinstance(settings, dict) or type(settings.get("use_doc_preprocessor")) is not bool:
        raise ValueError("PP-Structure preprocessing settings are missing or unspecified")
    prep = payload.get("doc_preprocessor_res")
    if settings["use_doc_preprocessor"] or prep is not None:
        # Require the preprocessing details needed to trace the result back to the source image.
        if not isinstance(prep, dict):
            raise ValueError("PP-Structure preprocessor provenance is unavailable")
        modules = prep.get("model_settings", {})
        # Stop if the PP-Structure transformed frame has no verified inverse mapping.
        if (
            modules.get("use_doc_unwarping") is not False
            or modules.get("use_doc_orientation_classify") is not False
            or prep.get("angle", -1) not in (-1, 0)
        ):
            raise ValueError("PP-Structure transformed frame has no verified inverse mapping")
    return settings


# Retain OCR readings and confidence as separate evidence instead of replacing frozen source
# text.
def _ocr(raw, key, iw, ih, width, height, regions):
    # PP-Structure overall OCR must be an object.
    if not isinstance(raw, dict):
        raise ValueError("PP-Structure overall OCR must be an object")
    texts, scores, polygons = (raw.get(name) for name in ("rec_texts", "rec_scores", "rec_polys"))
    # Stop if the PP-Structure recognition arrays have different lengths.
    if (
        not all(isinstance(v, list) for v in (texts, scores, polygons))
        or len(texts) != len(scores)
        or len(texts) != len(polygons)
    ):
        raise ValueError("PP-Structure recognition arrays have different lengths")
    raw_text = "\n".join(texts) if all(isinstance(t, str) for t in texts) else None
    # PP-Structure OCR text must be strings.
    if raw_text is None:
        raise ValueError("PP-Structure OCR text must be strings")
    lines, cells, flags, offset = [], [], [], 0
    for index, (text, score, polygon) in enumerate(zip(texts, scores, polygons, strict=True)):
        points, box = _polygon(polygon, iw, ih, width, height)
        identifier = f"{key}:pp:ocr:{index}"
        span = Span(start=offset, end=offset + len(text)) if text else None
        line = OCRLine(
            line_id=identifier,
            text=text,
            span=span,
            polygon=points,
            box=box,
            recognition_score=score,
        )
        lines.append(line)
        if span is not None:
            cells.append(
                TextCell(
                    cell_id=identifier,
                    line_id=identifier,
                    block_id=identifier,
                    text=text,
                    span=span,
                    box=box,
                    font_size=0,
                    font_name="",
                )
            )
        else:
            flags.append(f"EMPTY_RECOGNITION:{identifier}")
        if line.recognition_score < POLICY["recognition_review_threshold"]:
            flags.append(f"LOW_RECOGNITION_SCORE:{identifier}")
        offset += len(text) + 1
    # dt_* and rec_* are separate collections: filtering/re-recognition can change their
    # lengths. Never zip detection scores onto recognized lines by position.
    detected, detection_scores = raw.get("dt_polys", []), raw.get("dt_scores")
    # Stop if the PP-Structure detection arrays have different lengths.
    if not isinstance(detected, list) or (
        detection_scores is not None
        and (not isinstance(detection_scores, list) or len(detected) != len(detection_scores))
    ):
        raise ValueError("PP-Structure detection arrays have different lengths")
    detections = []
    for index, polygon in enumerate(detected):
        points, box = _polygon(polygon, iw, ih, width, height)
        detections.append(
            OCRDetection(
                detection_id=f"{key}:pp:det:{index}",
                polygon=points,
                box=box,
                detection_score=detection_scores[index] if detection_scores is not None else None,
            )
        )
    assignments = assign_cells(tuple(cells), regions)
    flags.extend(
        f"OCR_REGION_{a.status}:{a.cell_id}" for a in assignments if a.status != "ASSIGNED"
    )
    if not lines:
        flags.append("OCR_HAS_NO_RECOGNIZED_LINES")
    return OCRRepresentation(
        representation_id=f"{key}:pp:ocr",
        raw_text=raw_text,
        lines=tuple(lines),
        detections=tuple(detections),
        assignments=assignments,
        upstream_recognition_threshold=raw.get("text_rec_score_thresh"),
        review_flags=tuple(flags),
    )


# Read one PP-Structure page export and retain its layout and OCR as separate proposals.
def parse_ppstructure_page(
    raw, *, page_key, image_width, image_height, width, height, cells=(), engine_version
):
    payload = raw.get("res", raw)
    # Stop if the PP-Structure result is not an object.
    if not isinstance(payload, dict):
        raise ValueError("PP-Structure result is not an object")
    settings = _identity_frame(payload, image_width, image_height)
    blocks = payload.get("parsing_res_list")
    # Stop if the PP-Structure parsing_res_list is unavailable.
    if not isinstance(blocks, list):
        raise ValueError("PP-Structure parsing_res_list is unavailable")
    regions, routes, display_orders, warnings = [], [], [], []
    for index, block in enumerate(blocks):
        # Stop if the PP-Structure parsing block is not an object.
        if not isinstance(block, dict):
            raise ValueError("PP-Structure parsing block is not an object")
        identifier = block.get("block_id", index)
        # Stop if the PP-Structure block identifier is invalid.
        if type(identifier) is not int or identifier < 0:
            raise ValueError("PP-Structure block identifier is invalid")
        label, text = block.get("block_label"), block.get("block_content", "")
        # Stop if the PP-Structure block label or content is invalid.
        if not isinstance(label, str) or not label or not isinstance(text, str):
            raise ValueError("PP-Structure block label or content is invalid")
        region = Region(
            region_id=f"{page_key}:pp:block:{identifier}",
            label=label,
            predicted_text=text,
            origin="PP_STRUCTURE_V3",
            source_ref=f"parsing_res_list/{index}",
            box=_box(block.get("block_bbox"), image_width, image_height, width, height),
            furniture=label in {"header", "footer", "page_number", "header_image", "footer_image"},
        )
        regions.append(region)
        order = block.get("block_order")
        if order is not None:
            # Stop if the PP-Structure display order is invalid.
            if type(order) is not int or order < 0:
                raise ValueError("PP-Structure display order is invalid")
            display_orders.append(order)
        if label in {"table", "formula", "image", "figure", "chart", "seal"}:
            setting = {
                "table": "use_table_recognition",
                "formula": "use_formula_recognition",
                "chart": "use_chart_recognition",
                "seal": "use_seal_recognition",
            }.get(label)
            enabled = settings.get(setting) if setting else None
            # Stop if the PP-Structure specialized module setting is invalid.
            if enabled is not None and type(enabled) is not bool:
                raise ValueError("PP-Structure specialized module setting is invalid")
            routes.append(
                SpecializedRoute(
                    region_id=region.region_id,
                    label=label,
                    module_enabled=enabled,
                    route="TABLE_STRUCTURE_REVIEW_REQUIRED"
                    if label == "table"
                    else "FORMULA_REVIEW_REQUIRED"
                    if label == "formula"
                    else "VISUAL_REGION_REVIEW_REQUIRED",
                )
            )
    # Stop if the PP-Structure block identifiers repeat.
    if len({r.region_id for r in regions}) != len(regions):
        raise ValueError("PP-Structure block identifiers repeat")
    order_valid = display_orders == sorted(set(display_orders)) and bool(regions)
    if not order_valid:
        warnings.append(
            "PP_STRUCTURE_READING_ORDER_UNRESOLVED: contradictory display indices or no blocks"
        )
    if routes:
        warnings.append(
            "SPECIALIZED_OUTPUTS_RETAINED_IN_RAW_EXPORT; logical table grids are not imported"
        )
    regions = tuple(regions)
    ocr = (
        _ocr(
            payload["overall_ocr_res"], page_key, image_width, image_height, width, height, regions
        )
        if "overall_ocr_res" in payload
        else None
    )
    if ocr is None:
        warnings.append("PP_STRUCTURE_OCR_UNAVAILABLE")
    layout = PagePredictions(
        engine="PP_STRUCTURE_V3",
        engine_version=engine_version,
        regions=regions,
        reading_order=tuple(r.region_id for r in regions),
        reading_order_status="MODEL_PROPOSED" if order_valid else "UNRESOLVED",
        assignments=assign_cells(cells, regions),
        warnings=tuple(warnings),
    )
    return PPStructurePage(
        layout=layout,
        ocr=ocr,
        specialized_routes=tuple(routes),
        input_image_width=image_width,
        input_image_height=image_height,
        model_settings=settings,
    )


# Calculate box overlap to compare two observed regions.
def _iou(a, b):
    overlap = max(0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(0, min(a.y1, b.y1) - max(a.y0, b.y0))
    return overlap / (a.area + b.area - overlap)


# Translate an external region label into a proposed layout role.
def _role(region):
    if region.furniture:
        return "furniture"
    return {
        "text": "body",
        "paragraph": "body",
        "title": "heading",
        "document_title": "heading",
        "doc_title": "heading",
        "section_header": "heading",
        "paragraph_title": "heading",
        "par_title": "heading",
    }.get(region.label, region.label)


# Mutually unique region matches provide review cues, never truth votes.
def compare_layouts(primary, other):
    """Mutually unique region matches provide review cues, never truth votes."""
    if primary is None or primary.engine != "DOCLING":
        return {
            "status": "NOT_COMPARABLE",
            "matches": [],
            "review_flags": ["INDEPENDENT_DOCLING_PROPOSAL_UNAVAILABLE"],
        }
    scores = {
        (a.region_id, b.region_id): _iou(a.box, b.box)
        for a in primary.regions
        for b in other.regions
    }

    # Select a spatial match only when one candidate is uniquely supported.
    def unique_best(identifier, first):
        ranked = sorted(
            (
                (score, pair[1 if first else 0])
                for pair, score in scores.items()
                if pair[0 if first else 1] == identifier
            ),
            reverse=True,
        )
        if not ranked or ranked[0][0] < POLICY["comparison_minimum_iou"]:
            return None
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < POLICY["comparison_ambiguity_margin"]:
            return None
        return ranked[0][1]

    matches, flags = [], []
    by_other = {r.region_id: r for r in other.regions}
    for region in primary.regions:
        match = unique_best(region.region_id, True)
        if match is None or unique_best(match, False) != region.region_id:
            flags.append(f"DOCLING_REGION_UNALIGNED:{region.region_id}")
            continue
        matches.append(
            {
                "docling_region_id": region.region_id,
                "pp_region_id": match,
                "iou": scores[region.region_id, match],
            }
        )
        if _role(region) != _role(by_other[match]):
            flags.append(f"REGION_ROLE_DISAGREEMENT:{region.region_id}:{match}")
        if region.predicted_text != by_other[match].predicted_text:
            flags.append(f"REGION_TEXT_DIFFERENCE:{region.region_id}:{match}")
    mapped = {m["docling_region_id"]: m["pp_region_id"] for m in matches}
    flags.extend(
        f"PP_REGION_UNALIGNED:{r.region_id}"
        for r in other.regions
        if r.region_id not in mapped.values()
    )
    if (
        primary.reading_order_status != "MODEL_PROPOSED"
        or other.reading_order_status != "MODEL_PROPOSED"
    ):
        flags.append("ENGINE_READING_ORDER_UNRESOLVED")
    else:
        positions = {r: i for i, r in enumerate(other.reading_order)}
        primary_regions = {r.region_id: r for r in primary.regions}
        aligned = [
            positions[mapped[r]]
            for r in primary.reading_order
            if r in mapped
            and not primary_regions[r].furniture
            and not by_other[mapped[r]].furniture
        ]
        if aligned != sorted(aligned):
            flags.append("ENGINE_READING_ORDER_DISAGREEMENT")
    return {
        "status": "REVIEW_REQUIRED" if flags else "MODEL_AGREEMENT_PROPOSED",
        "matches": matches,
        "review_flags": flags,
    }
