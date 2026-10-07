# Compare reading-order relationships over the same original text pieces. Report reversals and
# missing evidence without pretending an uncertain set of links is a complete reading
# sequence.
"""Compare reading-order relations over identical immutable native source cells.

External order is a second opinion. Reversal, partial coverage and uncertain
relations stay separate; agreement does not certify source correctness. Review
routing never substitutes another engine's text or changes course ownership.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from itertools import combinations

VERSION = "source-reading-order-audit-v1"


# Create a stable checksum of structured data for later identity checks.
def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# Reachability through CONFIRMED relations; unresolved edges are barriers.
def _relations(proposal, ids):
    """Reachability through CONFIRMED relations; unresolved edges are barriers."""
    adjacency = {nid: set() for nid in ids}
    uncertain = []
    duplicates = Counter()
    for edge in proposal.reading_order:
        a, b = edge.source_region_id, edge.target_region_id
        # Reading-order links may refer only to the original body text being compared.
        if a not in ids or b not in ids:
            raise ValueError("reading-order relation refers outside the native body cells")
        duplicates[a, b] += 1
        # Reject duplicate reading-order relation.
        if duplicates[a, b] > 1:
            raise ValueError("duplicate reading-order relation")
        if str(edge.status) == "CONFIRMED":
            adjacency[a].add(b)
        else:
            uncertain.append(
                {
                    "source_region_id": a,
                    "target_region_id": b,
                    "status": str(edge.status),
                    "reason": edge.reason,
                }
            )
    reach = {}
    for nid in ids:
        visited, pending = set(), list(adjacency[nid])
        # Follow the confirmed ordering links, checking that none leads back to the
        # text piece where we started.
        while pending:
            target = pending.pop()
            # Reject reading-order links that form a cycle rather than a usable sequence.
            if target == nid:
                raise ValueError("cyclic confirmed reading order")
            if target not in visited:
                visited.add(target)
                pending.extend(adjacency[target] - visited)
        reach[nid] = visited
    return reach, uncertain


# Count comparable source-pair reversals without inventing a full sequence.
def compare_order_relations(native, alternative):
    """Count comparable source-pair reversals without inventing a full sequence."""
    identity = (
        "dataset_page_id",
        "source_document_id",
        "source_pdf_checksum",
        "physical_page",
        "page_width",
        "page_height",
    )
    # Compare ordering opinions only when they describe the same page and coordinate system.
    if any(getattr(native, name) != getattr(alternative, name) for name in identity):
        raise ValueError(
            "reading-order opinions identify different source pages or coordinate frames"
        )
    source = {r.region_id: r for r in native.regions}
    other = {r.region_id: r for r in alternative.regions}
    # Reject duplicate source region identities.
    if len(source) != len(native.regions) or len(other) != len(alternative.regions):
        raise ValueError("duplicate source region identities")
    # Reading-order audit requires identical native cells, text, roles and anchors.
    if source != other:
        raise ValueError(
            "reading-order audit requires identical native cells, text, roles and anchors"
        )
    body = {nid: r for nid, r in source.items() if str(r.role) != "PAGE_FURNITURE"}
    ids = set(body)
    left, lu = _relations(native, ids)
    right, ru = _relations(alternative, ids)
    counts, reversals = Counter(), []

    # Record a supported reading-order relationship between two original original text pieces.
    def relation(reach, a, b):
        return 1 if b in reach[a] else -1 if a in reach[b] else 0

    for a, b in combinations(sorted(ids), 2):
        nr, er = relation(left, a, b), relation(right, a, b)
        if nr and er:
            counts["comparable_pairs"] += 1
            if nr == er:
                counts["agreement_pairs"] += 1
            else:
                counts["reversed_pairs"] += 1
                reversals.append(
                    {
                        "native_before": a if nr == 1 else b,
                        "native_after": b if nr == 1 else a,
                        "alternative_before": a if er == 1 else b,
                        "alternative_after": b if er == 1 else a,
                    }
                )
        elif nr:
            counts["alternative_unresolved_pairs"] += 1
        elif er:
            counts["native_unresolved_pairs"] += 1
        else:
            counts["both_unresolved_pairs"] += 1
    for key in (
        "comparable_pairs",
        "agreement_pairs",
        "reversed_pairs",
        "alternative_unresolved_pairs",
        "native_unresolved_pairs",
        "both_unresolved_pairs",
    ):
        counts.setdefault(key, 0)
    affected = {nid for r in reversals for nid in (r["native_before"], r["native_after"])}
    flags = []
    if reversals:
        flags.append("REVIEW_ORDER_DISAGREEMENT")
    if counts["alternative_unresolved_pairs"] or ru or str(alternative.status) != "COMPLETE":
        flags.append("REVIEW_EXTERNAL_ORDER_COVERAGE")
    if (
        counts["native_unresolved_pairs"]
        or counts["both_unresolved_pairs"]
        or lu
        or str(native.status) != "COMPLETE"
    ):
        flags.append("REVIEW_NATIVE_ORDER_COVERAGE")
    decision = "NO_BODY_SOURCE" if not body else "REVIEW_REQUIRED" if flags else "ORDER_AGREEMENT"
    return {
        "version": VERSION,
        "dataset_page_id": native.dataset_page_id,
        "source_document_id": native.source_document_id,
        "source_pdf_checksum": native.source_pdf_checksum,
        "physical_page": native.physical_page,
        "native_engine": native.engine,
        "alternative_engine": alternative.engine,
        "alternative_engine_version": alternative.engine_version,
        "native_status": str(native.status),
        "alternative_status": str(alternative.status),
        "decision": decision,
        "review_flags": flags,
        "counts": dict(counts),
        "pair_denominator": len(ids) * (len(ids) - 1) // 2,
        "native_body_cells": len(body),
        "reversed_pair_fraction_conditional": counts["reversed_pairs"] / counts["comparable_pairs"]
        if counts["comparable_pairs"]
        else None,
        "reversals": reversals,
        "affected_source_regions": [body[nid].model_dump(mode="json") for nid in sorted(affected)],
        "native_unresolved_edges": lu,
        "alternative_unresolved_edges": ru,
        "native_order_sha256": _digest([e.model_dump(mode="json") for e in native.reading_order]),
        "alternative_order_sha256": _digest(
            [e.model_dump(mode="json") for e in alternative.reading_order]
        ),
        "source_cells_sha256": _digest(
            [source[nid].model_dump(mode="json") for nid in sorted(source)]
        ),
        "source_truth_read": False,
        "native_order_replaced": False,
        "course_ownership_assigned": False,
        "text_replaced": False,
        "score_note": (
            "Pair reversal fraction is conditional on relations both opinions resolve; "
            "coverage is reported separately. Engine agreement is not truth."
        ),
    }
