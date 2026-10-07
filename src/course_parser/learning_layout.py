# Repair proposed reading order when parallel prose columns were mistaken for a table. Reorder
# the original text pieces while keeping every piece and its wording intact.
"""Experimental repair of parallel prose mistaken for table rows.

Geometry alone cannot prove a logical table. Persistent gutters and substantial
prose on both sides provide an alternative column-order proposal. Compact tables
and uncertain layouts retain their original proposal. All source cells survive.
"""

from __future__ import annotations

import statistics

from .table_structure import declared_grid


# Order fragments on overlapping printed rows before sorting by x.
def baseline_rows(regions):
    """Order fragments on overlapping printed rows before sorting by x.

    OCR boxes can have different heights on the same baseline. A small bottom-y
    difference alone wrongly moves credits before a taller course code. Require
    vertical overlap, center proximity and disjoint horizontal extents instead.
    Original source cells and their quoted text remain separate.
    """
    rows = []
    for region in sorted(
        regions,
        key=lambda x: (
            (x["geometry"]["y0"] + x["geometry"]["y1"]) / 2,
            x["source_order"],
        ),
    ):
        g = region["geometry"]
        candidates = []
        for index, row in enumerate(rows[-3:]):
            boxes = [x["geometry"] for x in row]
            h = statistics.median(b["y1"] - b["y0"] for b in boxes)
            center = statistics.median((b["y0"] + b["y1"]) / 2 for b in boxes)
            delta = abs((g["y0"] + g["y1"]) / 2 - center)
            overlap = min(g["y1"], max(b["y1"] for b in boxes)) - max(
                g["y0"], min(b["y0"] for b in boxes)
            )
            horizontal = all(g["x1"] <= b["x0"] + 0.5 or g["x0"] >= b["x1"] - 0.5 for b in boxes)
            if horizontal and overlap > 0 and delta <= max(h, g["y1"] - g["y0"]) * 0.55:
                candidates.append((delta, len(rows) - min(3, len(rows)) + index))
        if candidates:
            rows[min(candidates)[1]].append(region)
        else:
            rows.append([region])
    return [r for row in rows for r in sorted(row, key=lambda x: x["geometry"]["x0"])]


# Rebuild the complete stream after role repair, using existing columns.
def repair_source_rows(proposal, layout_module):
    """Rebuild the complete stream after role repair, using existing columns."""
    raw = proposal.model_dump(mode="json")
    body = [r for r in raw["regions"] if r["role"] != "PAGE_FURNITURE"]
    if any(r["role"] == "TABLE_CELL" for r in body):
        return proposal  # Preserve genuine/uncertain table semantics.
    columns = {r["column_id"] for r in body if r["column_id"]}
    if not columns:
        return proposal
    spanning = sorted(
        [r for r in body if r["column_id"] is None], key=lambda r: r["geometry"]["y0"]
    )
    ordered, used = [], set()

    # Add the current source group to the ordered output without changing its text.
    def emit(top, bottom):
        for column in sorted(
            columns, key=lambda c: min(r["geometry"]["x0"] for r in body if r["column_id"] == c)
        ):
            members = [
                r
                for r in body
                if r["column_id"] == column
                and top <= (r["geometry"]["y0"] + r["geometry"]["y1"]) / 2 < bottom
            ]
            ordered.extend(baseline_rows(members))
            used.update(r["region_id"] for r in members)

    top = float("-inf")
    for r in spanning:
        middle = (r["geometry"]["y0"] + r["geometry"]["y1"]) / 2
        emit(top, middle)
        ordered.append(r)
        used.add(r["region_id"])
        top = middle
    emit(top, float("inf"))
    # Reordering source rows must preserve every source region exactly once.
    if len(ordered) != len(body) or used != {r["region_id"] for r in body}:
        raise ValueError("source row ordering lost or duplicated a source region")
    raw["reading_order"] = [
        dict(
            source_region_id=a["region_id"],
            target_region_id=b["region_id"],
            status="CONFIRMED",
            reason="SOURCE_COLUMNS_AND_OVERLAPPING_BASELINE_ROWS_V2",
            confidence=None,
        )
        for a, b in zip(ordered, ordered[1:], strict=False)
    ]
    raw["engine_version"] += "+source-rows-v2"
    return layout_module.PageLayoutProposal.model_validate(raw)


# Identify persistent prose columns without treating geometry alone as proof of a table.
def prose_column_groups(
    regions: list[dict], width: float, height: float, *, fragmented=False
) -> list[list[dict]]:
    if declared_grid(regions):
        return []  # Mixed pages retain the local grid; do not flatten a real table.
    eligible = [
        x
        for x in regions
        if x["role"] != "PAGE_FURNITURE"
        and x["geometry"]["x1"] - x["geometry"]["x0"] < width * 0.65
    ]

    # Separate the original text pieces into the proposed groups used by the ordering repair.
    def partition(items, depth=0):
        if len(items) < 12 or depth >= 3:
            return [items] if items else []
        ordered = sorted(items, key=lambda x: (x["geometry"]["x0"] + x["geometry"]["x1"]) / 2)
        options = []
        for index in range(6, len(ordered) - 5):
            left, right = ordered[:index], ordered[index:]
            gap = min(x["geometry"]["x0"] for x in right) - max(x["geometry"]["x1"] for x in left)
            if gap < width * (0.0125 if fragmented else 0.025):
                continue
            extent = [
                (min(x["geometry"]["y0"] for x in side), max(x["geometry"]["y1"] for x in side))
                for side in (left, right)
            ]
            overlap = min(x[1] for x in extent) - max(x[0] for x in extent)
            if overlap >= height * 0.2:
                options.append((gap, index))
        if not options:
            return [items]
        _, index = max(options)
        return partition(ordered[:index], depth + 1) + partition(ordered[index:], depth + 1)

    groups = partition(eligible)
    if len(groups) < 2:
        return []
    # A code/title/credits table has compact columns; independent prose columns have repeated
    # long lines extending over a substantial vertical interval.
    for group in groups:
        prose = sum(len(x["raw_text"].split()) >= 5 for x in group)
        if len(group) < 8 or (
            prose < 8
            if fragmented
            else statistics.median(len(x["raw_text"].strip()) for x in group) < 20
        ):
            return []
        span = max(x["geometry"]["y1"] for x in group) - min(x["geometry"]["y0"] for x in group)
        if span < height * 0.25:
            return []
    return sorted(groups, key=lambda group: min(x["geometry"]["x0"] for x in group))


# Propose a parallel-prose reading order while preserving every original original text piece.
def repair_parallel_prose(proposal, layout_module, models, *, fragmented=False):
    raw = proposal.model_dump(mode="json")
    if not raw["page_width"] or not raw["page_height"]:
        return proposal
    groups = prose_column_groups(
        raw["regions"], raw["page_width"], raw["page_height"], fragmented=fragmented
    )
    if not groups or not any(x["role"] == "TABLE_CELL" for group in groups for x in group):
        return proposal
    assignment = {
        x["region_id"]: f"prose_column_{index}" for index, group in enumerate(groups) for x in group
    }
    for region in raw["regions"]:
        if region["region_id"] in assignment:
            region["column_id"] = assignment[region["region_id"]]
            if region["role"] == "TABLE_CELL":
                region.update(
                    role="LINE",
                    table_id=None,
                    row_id=None,
                    cell_id=None,
                    engine_label="parallel_prose_column_proposal",
                )
    # Emit columns within vertical zones separated by full-width elements.
    body = [x for x in raw["regions"] if x["role"] != "PAGE_FURNITURE"]
    spanning = sorted(
        [x for x in body if x["region_id"] not in assignment], key=lambda x: x["geometry"]["y0"]
    )
    ordered, used = [], set()

    # Add the current source group to the ordered output without changing its text.
    def emit(top, bottom):
        for index in range(len(groups)):
            members = [
                x
                for x in body
                if assignment.get(x["region_id"]) == f"prose_column_{index}"
                and top <= (x["geometry"]["y0"] + x["geometry"]["y1"]) / 2 < bottom
                and x["region_id"] not in used
            ]
            rows = []
            for member in sorted(
                members, key=lambda x: x["geometry"]["y1"] if fragmented else x["geometry"]["y0"]
            ):
                if (
                    fragmented
                    and rows
                    and abs(member["geometry"]["y1"] - rows[-1][0]["geometry"]["y1"])
                    < min(
                        member["geometry"]["y1"] - member["geometry"]["y0"],
                        rows[-1][0]["geometry"]["y1"] - rows[-1][0]["geometry"]["y0"],
                    )
                    * 0.45
                ):
                    rows[-1].append(member)
                else:
                    rows.append([member])
            for region in [
                x for row in rows for x in sorted(row, key=lambda x: x["geometry"]["x0"])
            ]:
                ordered.append(region)
                used.add(region["region_id"])

    top = float("-inf")
    for region in spanning:
        middle = (region["geometry"]["y0"] + region["geometry"]["y1"]) / 2
        emit(top, middle)
        ordered.append(region)
        used.add(region["region_id"])
        top = middle
    emit(top, float("inf"))
    if used != {x["region_id"] for x in body}:
        return proposal
    raw["reading_order"] = [
        {
            "source_region_id": a["region_id"],
            "target_region_id": b["region_id"],
            "status": str(models.ResolutionStatus.CONFIRMED),
            "reason": "EXPERIMENTAL_PERSISTENT_GUTTER_PROSE_COLUMN_ORDER",
            "confidence": None,
        }
        for a, b in zip(ordered, ordered[1:], strict=False)
    ]
    raw["status"] = "COMPLETE"
    raw["engine_version"] += "+parallel-prose-v1"
    raw["warnings"].append("EXPERIMENTAL_PARALLEL_PROSE_REPAIR_REQUIRES_SOURCE_REVIEW")
    return layout_module.PageLayoutProposal.model_validate(raw)
