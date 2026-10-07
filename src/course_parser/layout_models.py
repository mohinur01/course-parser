# Define the data structures used to describe a PDF page: text pieces, bounding boxes,
# proposed regions and table cells. Validate their coordinates and relationships when they are
# created.
"""Typed page predictions; model text and original source text remain distinct."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Base class for checked, immutable data records. Unexpected fields and invalid numbers are
# rejected.
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


# A rectangle around text or another page region, measured in page coordinates.
class Box(StrictModel):
    x0: float = Field(ge=0)
    y0: float = Field(ge=0)
    x1: float = Field(gt=0)
    y1: float = Field(gt=0)

    # Reject geometry that has no positive width or height.
    @model_validator(mode="after")
    def positive_area(self):
        # Stop if the bounding box has no positive area.
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("bounding box has no positive area")
        return self

    # Calculate the area of the validated box.
    @property
    def area(self):
        return (self.x1 - self.x0) * (self.y1 - self.y0)


# The start and end character positions of a piece of original text.
class Span(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    # Require coordinate values to follow the expected order.
    @model_validator(mode="after")
    def ordered(self):
        # Stop if the text span is empty or reversed.
        if self.end <= self.start:
            raise ValueError("text span is empty or reversed")
        return self


# One original PDF text piece, with its text, location and font information.
class TextCell(StrictModel):
    cell_id: str
    line_id: str
    block_id: str
    text: str
    span: Span
    box: Box
    font_size: float = Field(ge=0)
    font_name: str


# A proposed page region, such as a paragraph or heading, and the tool that proposed it.
class Region(StrictModel):
    region_id: str
    label: str
    box: Box
    predicted_text: str
    origin: Literal["PDF_BACKEND", "DOCLING", "PP_STRUCTURE_V3"]
    source_ref: str
    furniture: bool = False


# Record where a text piece belongs, or leave its placement ambiguous or unassigned.
class CellAssignment(StrictModel):
    cell_id: str
    status: Literal["ASSIGNED", "AMBIGUOUS", "ORPHAN"]
    region_id: str | None = None
    candidates: tuple[str, ...] = ()


# A proposed table cell, including the rows and columns it covers.
class TableCell(StrictModel):
    cell_id: str
    row_start: int = Field(ge=0)
    row_end: int = Field(gt=0)
    col_start: int = Field(ge=0)
    col_end: int = Field(gt=0)
    predicted_text: str
    box: Box | None = None
    column_header: bool = False
    row_header: bool = False

    # Check that a table cell covers at least one row and one column.
    @model_validator(mode="after")
    def valid_span(self):
        # Stop if the table cell has an empty logical span.
        if self.row_end <= self.row_start or self.col_end <= self.col_start:
            raise ValueError("table cell has an empty logical span")
        return self


# A proposed table grid and its cells, checked for overlap and out-of-bounds positions.
class Table(StrictModel):
    table_id: str
    region_id: str
    rows: int = Field(ge=0)
    columns: int = Field(ge=0)
    cells: tuple[TableCell, ...]
    word_assignments: tuple[CellAssignment, ...] = ()

    # Check that table cells stay within the declared grid and never occupy the same grid
    # position.
    @model_validator(mode="after")
    def valid_grid(self):
        # Track every occupied grid position, including positions covered by a merged cell.
        occupied = set()
        # Stop if the table logical cell identifiers repeat.
        if len({cell.cell_id for cell in self.cells}) != len(self.cells):
            raise ValueError("table logical cell identifiers repeat")
        for cell in self.cells:
            # Stop if the table cell extends beyond the declared grid.
            if cell.row_end > self.rows or cell.col_end > self.columns:
                raise ValueError("table cell extends beyond the declared grid")
            for row in range(cell.row_start, cell.row_end):
                for col in range(cell.col_start, cell.col_end):
                    # Stop if the table logical cells overlap.
                    if (row, col) in occupied:
                        raise ValueError("table logical cells overlap")
                    occupied.add((row, col))
        return self


# Keep one tool's proposed regions, reading order and tables together for a page.
class PagePredictions(StrictModel):
    engine: Literal["PDF_BACKEND", "DOCLING", "PP_STRUCTURE_V3"]
    engine_version: str
    regions: tuple[Region, ...]
    reading_order: tuple[str, ...]
    reading_order_status: Literal["MODEL_PROPOSED", "UNRESOLVED"]
    assignments: tuple[CellAssignment, ...]
    tables: tuple[Table, ...] = ()
    warnings: tuple[str, ...] = ()


# Measure the portion of a source box covered by another region.
def intersection_fraction(cell: Box, region: Box) -> float:
    width = max(0.0, min(cell.x1, region.x1) - max(cell.x0, region.x0))
    height = max(0.0, min(cell.y1, region.y1) - max(cell.y0, region.y0))
    return width * height / cell.area


# Retain every cell; overlapping model proposals cannot silently steal text.
def assign_cells(cells: tuple[TextCell, ...], regions: tuple[Region, ...]):
    """Retain every cell; overlapping model proposals cannot silently steal text."""
    assignments = []
    for cell in cells:
        # Compare how much of this text box each proposed region covers, rather than choosing
        # a region by list order.
        scores = sorted(
            ((intersection_fraction(cell.box, region.box), region.region_id) for region in regions),
            reverse=True,
        )
        candidates = tuple(identifier for score, identifier in scores if score >= 0.5)
        # Keep text that no region covers instead of dropping it from the page.
        if not candidates:
            value = CellAssignment(cell_id=cell.cell_id, status="ORPHAN")
        # If the two best placements are too close, retain both possibilities and mark the
        # assignment ambiguous.
        elif len(candidates) > 1 and scores[0][0] - scores[1][0] < 0.1:
            value = CellAssignment(cell_id=cell.cell_id, status="AMBIGUOUS", candidates=candidates)
        else:
            value = CellAssignment(
                cell_id=cell.cell_id,
                status="ASSIGNED",
                region_id=candidates[0],
                candidates=candidates,
            )
        assignments.append(value)
    return tuple(assignments)
