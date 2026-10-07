# Distinguish a table layout from the meaning of its contents. Keep requirement grids and
# uncertain rows from being mistaken for independent course descriptions.
"""Separate observed grid structure from its proposed semantic interpretation.

The layout engine's cells are evidence, not proof of a course inventory. A
requirements heading defeats repeated codes, titles and credit columns. An
inventory row needs its own narrative, an explicit inventory container, and
compatible peers. Unknown grids remain review proposals.
"""

from __future__ import annotations

import re
from collections import defaultdict

FIELD_LABEL = re.compile(
    r"^(?:course(?:\s+(?:code|number|title|description))?|code|number|title|"
    r"description|credits?|units?|hours?|semester|term|requirements?)\s*[:.]?$",
    re.I,
)
REQUIREMENT = re.compile(
    r"\b(?:requirements?|curriculum|electives?|plan of study|minimum credits|"
    r"required courses|choose|select|first year|second year|third year|fourth year|"
    r"fall semester|spring semester)\b",
    re.I,
)
INVENTORY = re.compile(
    r"\b(?:course descriptions?|courses of instruction|course offerings?|"
    r"description of courses|courses offered)\b",
    re.I,
)


# A coordinated field-label row vetoes a page-wide prose reinterpretation.
def declared_grid(regions: list[dict]) -> bool:
    """A coordinated field-label row vetoes a page-wide prose reinterpretation."""
    rows = defaultdict(list)
    for r in regions:
        if r.get("table_id") and r.get("row_id"):
            rows[(r["table_id"], r["row_id"])].append(r)
    return any(
        len(
            {
                x["raw_text"].strip().casefold().rstrip(":.")
                for x in row
                if FIELD_LABEL.fullmatch(x["raw_text"].strip())
            }
        )
        >= 2
        for row in rows.values()
    )


# Check that a proposed layout keeps every original text piece exactly once and leaves its
# text unchanged.
def layout_conservation(source, interpreted) -> dict:
    """Fail closed on lost/duplicated cells or modified source quotations."""
    original = {r.region_id: r.model_dump(mode="json") for r in source.regions}
    adopted = {r.region_id: r.model_dump(mode="json") for r in interpreted.regions}
    # A layout interpretation must preserve the original text pieces without adding or losing
    # any.
    if original.keys() != adopted.keys():
        raise ValueError("layout interpretation lost or introduced source cells")
    for rid, region in original.items():
        # Stop if the layout interpretation changed original text piece evidence.
        if any(
            region[k] != adopted[rid][k]
            for k in ("raw_text", "geometry", "evidence", "source_order")
        ):
            raise ValueError("layout interpretation changed source cell evidence")
    changes = [
        {
            "region_id": rid,
            "observed": {k: r[k] for k in ("role", "table_id", "row_id", "cell_id", "column_id")},
            "interpreted": {
                k: adopted[rid][k] for k in ("role", "table_id", "row_id", "cell_id", "column_id")
            },
        }
        for rid, r in original.items()
        if any(
            r[k] != adopted[rid][k] for k in ("role", "table_id", "row_id", "cell_id", "column_id")
        )
    ]
    return {
        "physical_page": source.physical_page,
        "status": "PASS",
        "source_cells": len(original),
        "interpreted_cells": len(adopted),
        "source_grid_preserved_in": "source_layouts",
        "interpretation_changes": changes,
        "source_reading_order_preserved": True,
        "reading_order_changed": source.reading_order != interpreted.reading_order,
    }


# Bind semantic proposals to complete observed row membership, never a join.
def table_contexts(layouts) -> dict[str, dict]:
    """Bind semantic proposals to complete observed row membership, never a join."""
    result = {}
    for layout in layouts:
        raw = layout.model_dump(mode="json")
        rows = defaultdict(list)
        for region in raw["regions"]:
            if region["role"] == "TABLE_CELL" and region["table_id"] and region["row_id"]:
                rows[(region["table_id"], region["row_id"])].append(region)
        for (tid, rowid), row in rows.items():
            x0 = min(r["geometry"]["x0"] for r in row)
            x1 = max(r["geometry"]["x1"] for r in row)
            y0 = min(r["geometry"]["y0"] for r in row)
            cues = []
            for r in raw["regions"]:
                text, g = r["raw_text"].strip(), r["geometry"]
                if (
                    r["role"] == "PAGE_FURNITURE"
                    or len(text.split()) > 16
                    or g["y1"] > y0
                    or g["x1"] <= x0
                    or g["x0"] >= x1
                ):
                    continue
                kind = (
                    "REQUIREMENTS"
                    if REQUIREMENT.search(text)
                    else ("COURSE_INVENTORY" if INVENTORY.search(text) else None)
                )
                if kind:
                    cues.append((g["y1"], kind, r["region_id"]))
            cue = max(cues, default=None)
            narrative = [
                r["region_id"]
                for r in row
                if len(re.findall(r"\b[^\W\d_]+\b", r["raw_text"])) >= 8
                and re.search(r"[.!?](?:\s|$)", r["raw_text"])
                and not FIELD_LABEL.fullmatch(r["raw_text"].strip())
            ]
            kind = cue[1] if cue else "UNRESOLVED_GRID"
            info = {
                "table_id": tid,
                "row_id": rowid,
                "row_node_ids": [
                    r["region_id"] for r in sorted(row, key=lambda r: r["geometry"]["x0"])
                ],
                "context_node_id": cue[2] if cue else None,
                "container": kind,
                "narrative_node_ids": narrative,
                "status": "REVIEW_REQUIRED",
            }
            for r in row:
                result[r["region_id"]] = info
    return result
