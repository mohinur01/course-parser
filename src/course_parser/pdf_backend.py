# Extract text, character positions and typography from PDF pages. Keep coordinates and source
# text aligned so later stages can point back to the exact printed location.
"""PDF backend boundary: text cells plus geometry in the verified upright page frame."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import fitz

from .layout_models import Box, Region, Span, TextCell


# One extracted character and its position on the original PDF page.
@dataclass(frozen=True)
class Character:
    offset: int
    text: str
    line_id: str
    box: Box


# The text, geometry and character information returned for one PDF page.
@dataclass(frozen=True)
class BackendPage:
    raw_text: str
    cells: tuple[TextCell, ...]
    characters: tuple[Character, ...]
    regions: tuple[Region, ...]
    width: float
    height: float
    rotation: int
    rotation_matrix: tuple[float, ...]


# Describe the extraction interface that a PDF text backend must provide.
class PageBackend(Protocol):
    # Read the requested source text and geometry into the structures expected by the caller.
    def extract(self, page: fitz.Page, page_key: str) -> BackendPage: ...


# Combine source boxes into the smallest enclosing box.
def union_boxes(boxes) -> Box:
    values = list(boxes)
    return Box(
        x0=min(box.x0 for box in values),
        y0=min(box.y0 for box in values),
        x1=max(box.x1 for box in values),
        y1=max(box.y1 for box in values),
    )


# Extract page text and geometry with PyMuPDF while preserving their source locations.
class PyMuPDFPageBackend:
    # Read the requested source text and geometry into the structures expected by the caller.
    def extract(self, page: fitz.Page, page_key: str) -> BackendPage:
        # PyMuPDF text coordinates are unrotated; rendered page coordinates are rotated.
        matrix = page.rotation_matrix
        raw = page.get_text("rawdict", flags=fitz.TEXTFLAGS_TEXT, sort=False)
        text, cells, characters, regions = "", [], [], []
        for block_index, block in enumerate(raw["blocks"]):
            if block["type"] != 0:
                continue
            block_id = f"{page_key}:native:block:{block_index}"
            block_cells = []
            block_start = len(text)
            for line_index, line in enumerate(block["lines"]):
                line_id = f"{block_id}:line:{line_index}"
                pending = []

                # Store the current source word before starting the next word.
                def flush_word(pending, line_id, block_id, block_cells):
                    if not pending:
                        return
                    first, last = pending[0], pending[-1]
                    cell = TextCell(
                        cell_id=f"{line_id}:word:{first[0]}",
                        line_id=line_id,
                        block_id=block_id,
                        text="".join(value[1] for value in pending),
                        span=Span(start=first[0], end=last[0] + 1),
                        box=union_boxes(value[2] for value in pending),
                        font_size=first[3],
                        font_name=first[4],
                    )
                    cells.append(cell)
                    block_cells.append(cell)
                    pending.clear()

                for span in line["spans"]:
                    for char in span["chars"]:
                        transformed = (fitz.Rect(char["bbox"]) * matrix) & page.rect
                        # Each character needs a usable position on the page before we can
                        # highlight it.
                        if transformed.is_empty or transformed.is_infinite:
                            raise ValueError("native character has no visible page geometry")
                        box = Box(
                            x0=transformed.x0,
                            y0=transformed.y0,
                            x1=transformed.x1,
                            y1=transformed.y1,
                        )
                        for letter in char["c"]:
                            offset = len(text)
                            text += letter
                            characters.append(Character(offset, letter, line_id, box))
                            if letter.isspace():
                                flush_word(pending, line_id, block_id, block_cells)
                            else:
                                pending.append((offset, letter, box, span["size"], span["font"]))
                flush_word(pending, line_id, block_id, block_cells)
                text += "\n"
            if block_cells:
                regions.append(
                    Region(
                        region_id=block_id,
                        label="native_text_block",
                        origin="PDF_BACKEND",
                        source_ref=f"rawdict/blocks/{block_index}",
                        predicted_text=text[block_start:],
                        box=union_boxes(cell.box for cell in block_cells),
                    )
                )
        for cell in cells:
            # Stop if the native cell does not preserve the backend text span.
            if text[cell.span.start : cell.span.end] != cell.text:
                raise ValueError("native cell does not preserve the backend text span")
        return BackendPage(
            raw_text=text,
            cells=tuple(cells),
            characters=tuple(characters),
            regions=tuple(regions),
            width=page.rect.width,
            height=page.rect.height,
            rotation=page.rotation,
            rotation_matrix=tuple(matrix),
        )


# Project exact frozen spans only when the entire page representation agrees.
def project_frozen_hit(backend: BackendPage, frozen_text: str, start: int, end: int) -> list[dict]:
    """Project exact frozen spans only when the entire page representation agrees."""
    # Map character positions only when the saved and freshly extracted text agree.
    if backend.raw_text != frozen_text:
        raise ValueError("frozen/native text differs; no character-coordinate projection")
    # Stop if the frozen span is outside page text.
    if not 0 <= start < end <= len(frozen_text):
        raise ValueError("frozen span is outside page text")
    selected = [
        char
        for char in backend.characters
        if start <= char.offset < end and not char.text.isspace()
    ]
    # Stop if the hit span has no visible characters.
    if not selected:
        raise ValueError("hit span has no visible characters")
    by_line = {}
    for char in selected:
        by_line.setdefault(char.line_id, []).append(char)
    return [
        {
            "line_id": line_id,
            "box": union_boxes(char.box for char in chars).model_dump(),
            "span": {"start": chars[0].offset, "end": chars[-1].offset + 1},
        }
        for line_id, chars in by_line.items()
    ]
