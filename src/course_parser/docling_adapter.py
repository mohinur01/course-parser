# Translate Docling exports into the page-layout format used by this project. Preserve the
# original page coordinates and keep uncertain reading order explicit.
"""Import Docling JSON as page-specific proposals, preserving source and grid provenance."""

from __future__ import annotations

import math

from .layout_models import (
    Box,
    PagePredictions,
    Region,
    Table,
    TableCell,
    assign_cells,
    intersection_fraction,
)


# Validate an integer engine value instead of silently accepting malformed input.
def _integer(value, name):
    # Reject values that are not integers, including booleans.
    if type(value) is not int:
        raise ValueError(f"Docling {name} is not an integer")
    return value


# Select the requested physical page from the external document export.
def document_page(raw: dict, number: int) -> dict:
    # Docling document must be a JSON object.
    if not isinstance(raw, dict):
        raise ValueError("Docling document must be a JSON object")
    pages = raw.get("pages") or {}
    if isinstance(pages, dict):
        page = pages.get(str(number), pages.get(number))
    elif isinstance(pages, list):
        candidates = [page for page in pages if page.get("page_no") == number]
        page = candidates[0] if len(candidates) == 1 else None
    else:
        raise ValueError("Docling pages must be a mapping or a list")
    # Stop if the Docling export does not identify the requested page.
    if (
        not isinstance(page, dict)
        or not page
        or (page.get("page_no") is not None and page["page_no"] != number)
    ):
        raise ValueError("Docling export does not identify the requested page")
    return page


# Convert the external box into the verified upright page coordinate system.
def convert_box(raw: dict, width: float, height: float, target_width: float, target_height: float):
    # Stop if the Docling region has no bounding box.
    if not isinstance(raw, dict):
        raise ValueError("Docling region has no bounding box")
    left, right, top, bottom = (float(raw[key]) for key in ("l", "r", "t", "b"))
    # Coordinates must be finite numbers so they can be mapped back to the source.
    if not all(math.isfinite(value) for value in (left, right, top, bottom)):
        raise ValueError("Docling coordinates are not finite")
    origin = str(raw.get("coord_origin", "")).upper().split(".")[-1]
    if origin == "BOTTOMLEFT":
        top, bottom = height - top, height - bottom
    # Require a supported coordinate origin so boxes can be mapped to the source page.
    elif origin != "TOPLEFT":
        raise ValueError("Docling coordinate origin is missing or unsupported")
    x0, x1 = sorted((left, right))
    y0, y1 = sorted((top, bottom))
    # Stop if the Docling bounding box extends outside the identified page.
    if not (0 <= x0 < x1 <= width + 0.01 and 0 <= y0 < y1 <= height + 0.01):
        raise ValueError("Docling bounding box extends outside the identified page")
    return Box(
        x0=x0 * target_width / width,
        y0=y0 * target_height / height,
        x1=min(x1, width) * target_width / width,
        y1=min(y1, height) * target_height / height,
    )


# Index exported document items by their stable references.
def _items(raw):
    items = {}
    for collection in ("texts", "tables", "pictures", "groups", "key_value_items", "form_items"):
        for index, item in enumerate(raw.get(collection) or []):
            # Docling item must be a JSON object.
            if not isinstance(item, dict):
                raise ValueError("Docling item must be a JSON object")
            reference = f"#/{collection}/{index}"
            # Stop if the Docling item self-reference differs from its collection position.
            if item.get("self_ref", reference) != reference:
                raise ValueError("Docling item self-reference differs from its collection position")
            items[reference] = item
    return items


# Read the exported document tree while retaining missing or contradictory reading order.
def _tree_order(root, items):
    order, warnings, seen, active = [], [], set(), set()

    # Visit the linked export items without revisiting cycles or inventing missing children.
    def visit(node):
        # Docling tree node must be a JSON object.
        if not isinstance(node, dict):
            raise ValueError("Docling tree node must be a JSON object")
        reference = node.get("$ref")
        if reference is not None:
            if reference in active:
                warnings.append("DOCLING_TREE_CYCLE")
                return
            if reference in seen:
                warnings.append("DOCLING_TREE_REPEATED_REFERENCE")
                return
            if reference not in items:
                warnings.append("DOCLING_TREE_DANGLING_REFERENCE")
                return
            seen.add(reference)
            active.add(reference)
            order.append(reference)
            visit(items[reference])
            active.remove(reference)
        else:
            for child in node.get("children") or []:
                visit(child)

    visit(root or {})
    return order, warnings


# Translate one Docling page export into checked regions, tables and reading-order
# information.
def parse_docling_page(
    raw: dict,
    *,
    page_no: int,
    page_key: str,
    width: float,
    height: float,
    cells=(),
    engine_version: str,
) -> PagePredictions:
    page = document_page(raw, page_no)
    size = page.get("size") or {}
    source_width, source_height = float(size["width"]), float(size["height"])
    # Stop if the Docling page dimensions do not match the verified upright image.
    if not all(
        math.isfinite(value) and value > 0 for value in (source_width, source_height)
    ) or not math.isclose(source_width / source_height, width / height, rel_tol=0.002):
        raise ValueError("Docling page dimensions do not match the verified upright image")
    items = _items(raw)
    body_order, warnings = _tree_order(raw.get("body"), items)
    furniture_order, furniture_warnings = _tree_order(raw.get("furniture"), items)
    warnings.extend(furniture_warnings)
    furniture_refs = set(furniture_order)
    regions, refs_to_regions, tables = [], {}, []

    # Return the text location as a bounding box, combining boxes when needed.
    def box(value):
        return convert_box(value, source_width, source_height, width, height)

    for reference, item in items.items():
        if reference.startswith("#/groups/"):
            continue
        label = str(item.get("label") or "unknown")
        text = str(item.get("orig") if item.get("orig") is not None else item.get("text") or "")
        for index, provenance in enumerate(item.get("prov") or []):
            if _integer(provenance["page_no"], "provenance page number") != page_no:
                continue
            identifier = f"{page_key}:docling:{reference.removeprefix('#/')}:prov:{index}"
            fragment = text
            if text and "charspan" in provenance:
                start, end = provenance["charspan"]
                start, end = _integer(start, "charspan start"), _integer(end, "charspan end")
                # The recorded character positions must fit inside the text returned by Docling.
                if not 0 <= start <= end <= len(text):
                    raise ValueError("Docling text provenance extends outside its model text")
                fragment = text[start:end]
            region = Region(
                region_id=identifier,
                label=label,
                box=box(provenance["bbox"]),
                predicted_text=fragment,
                origin="DOCLING",
                source_ref=reference,
                furniture=(
                    reference in furniture_refs
                    or label in {"page_header", "page_footer"}
                    or item.get("content_layer") == "furniture"
                ),
            )
            regions.append(region)
            refs_to_regions.setdefault(reference, []).append(identifier)
            if reference.startswith("#/tables/"):
                data = item.get("data") or {}
                rows = _integer(data.get("num_rows", 0), "table row count")
                columns = _integer(data.get("num_cols", 0), "table column count")
                # Stop if the Docling table dimensions are invalid or exceed the grid budget.
                if rows < 0 or columns < 0 or rows * columns > 1_000_000:
                    raise ValueError(
                        "Docling table dimensions are invalid or exceed the grid budget"
                    )
                table_cells = []
                for cell_index, cell in enumerate(data.get("table_cells") or []):
                    coordinates = {
                        name: _integer(cell[key], key)
                        for name, key in (
                            ("row_start", "start_row_offset_idx"),
                            ("row_end", "end_row_offset_idx"),
                            ("col_start", "start_col_offset_idx"),
                            ("col_end", "end_col_offset_idx"),
                        )
                    }
                    table_cells.append(
                        TableCell(
                            cell_id=f"{identifier}:cell:{cell_index}",
                            **coordinates,
                            predicted_text=str(cell.get("text") or ""),
                            box=box(cell["bbox"]) if cell.get("bbox") else None,
                            column_header=cell.get("column_header", False),
                            row_header=cell.get("row_header", False),
                        )
                    )
                cell_regions = tuple(
                    Region(
                        region_id=cell.cell_id,
                        label="table_cell",
                        box=cell.box,
                        predicted_text=cell.predicted_text,
                        origin="DOCLING",
                        source_ref=reference,
                    )
                    for cell in table_cells
                    if cell.box is not None
                )
                table_words = tuple(
                    cell for cell in cells if intersection_fraction(cell.box, region.box) >= 0.5
                )
                tables.append(
                    Table(
                        table_id=identifier,
                        region_id=identifier,
                        rows=rows,
                        columns=columns,
                        cells=tuple(table_cells),
                        word_assignments=assign_cells(table_words, cell_regions),
                    )
                )
    reading_order = tuple(
        identifier
        for reference in body_order
        for identifier in refs_to_regions.get(reference, [])
        if not next(region for region in regions if region.region_id == identifier).furniture
    )
    expected = {region.region_id for region in regions if not region.furniture}
    if set(reading_order) != expected:
        warnings.append("DOCLING_BODY_DOES_NOT_ORDER_EVERY_REGION")
    if not regions:
        warnings.append("DOCLING_PAGE_HAS_NO_REGIONS")
    if any(not cell_regions.cells for cell_regions in tables):
        warnings.append("DOCLING_TABLE_HAS_NO_LOGICAL_CELLS")
    return PagePredictions(
        engine="DOCLING",
        engine_version=engine_version,
        regions=tuple(regions),
        reading_order=reading_order,
        reading_order_status="MODEL_PROPOSED"
        if regions and not any(w.startswith(("DOCLING_TREE_", "DOCLING_BODY_")) for w in warnings)
        else "UNRESOLVED",
        assignments=assign_cells(cells, tuple(regions)),
        tables=tuple(tables),
        warnings=tuple(sorted(set(warnings))),
    )
