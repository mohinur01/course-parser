# Sort possible missed-course flags so reviewers can inspect the strongest evidence first.
# Group duplicate flags while retaining every original observation; a high rank never creates
# a course.
"""Explainable review ordering and lossless duplicate grouping; never extraction.

Points and tiers are provisional review heuristics, not calibrated probabilities.
All physical flags and original source wording survive grouping. No score or
group can create a course, change ownership, or supply a training label.
"""

from __future__ import annotations

import copy
import hashlib
import math
from collections import defaultdict

VERSION = "source-omission-ranking-v1"
WEIGHTS = {
    "COURSE_LIKE_IDENTIFIER": 4,
    "MATCHING_MODEL_SUPPORTED_ENTRY_PATTERN": 3,
    "BOUNDED_DESCRIPTION_STRUCTURE": 3,
    "EXPLICIT_PRINTED_CREDIT_VALUE": 2,
    "COURSE_DESCRIPTION_SECTION": 2,
    "MATCHING_OR_DISTINCTIVE_TYPOGRAPHY": 1,
    "REQUIREMENT_OR_REFERENCE_CONTEXT": -4,
    "ADMINISTRATIVE_CONTEXT": -4,
    "TABLE_OF_CONTENTS_CONTEXT": -3,
    "ORGANIZATION_OR_PERSON_NAME": -3,
    "ISOLATED_DISPLAY_HEADING": -2,
}


# Explain the review priority of a possible omission without turning it into an accepted
# course.
def score_flag(flag):
    signals = set(flag["signals"])
    cues = set(flag["corroborating_cues"])
    applied = []
    for condition, name in (
        (bool(signals & {"MODERN_IDENTIFIER", "HISTORICAL_IDENTIFIER"}), "COURSE_LIKE_IDENTIFIER"),
        ("MODEL_SUPPORTED_ENTRY_PATTERN" in cues, "MATCHING_MODEL_SUPPORTED_ENTRY_PATTERN"),
        (
            bool(cues & {"FOLLOWING_DESCRIPTION_STRUCTURE", "INLINE_TITLE_PROSE_TRANSITION"}),
            "BOUNDED_DESCRIPTION_STRUCTURE",
        ),
        ("EXPLICIT_PRINTED_CREDIT_VALUE" in cues, "EXPLICIT_PRINTED_CREDIT_VALUE"),
        ("COURSE_SECTION_CONTEXT" in cues, "COURSE_DESCRIPTION_SECTION"),
        (
            bool(cues & {"DISTINCTIVE_TYPOGRAPHY", "MATCHING_ENTRY_TYPOGRAPHY"}),
            "MATCHING_OR_DISTINCTIVE_TYPOGRAPHY",
        ),
        (
            bool(cues & {"REQUIREMENT_LIST_CONTEXT", "PARSER_REFERENCE_OR_NONCOURSE"}),
            "REQUIREMENT_OR_REFERENCE_CONTEXT",
        ),
        ("ADMINISTRATIVE_CONTEXT" in cues, "ADMINISTRATIVE_CONTEXT"),
        ("TABLE_OF_CONTENTS_CONTEXT" in cues, "TABLE_OF_CONTENTS_CONTEXT"),
        ("ORGANIZATION_OR_PERSON_NAME" in cues, "ORGANIZATION_OR_PERSON_NAME"),
        ("ISOLATED_DISPLAY_HEADING" in cues, "ISOLATED_DISPLAY_HEADING"),
    ):
        if condition:
            applied.append({"reason_code": name, "points": WEIGHTS[name]})
    score = sum(p["points"] for p in applied)
    return {
        **copy.deepcopy(flag),
        "omission_score": score,
        "priority_tier": "HIGH" if score >= 8 else "MEDIUM" if score >= 5 else "LOW",
        "priority_reasons": applied,
        "priority_version": VERSION,
        "score_is_probability": False,
        "score_uses_review_answers": False,
    }


# Normalize spacing for duplicate detection while retaining the original text separately.
def normalized_source_text(raw):
    # Whitespace/case only. No stemming, fuzzy similarity or OCR corrections.
    return " ".join(raw.split()).casefold()


# Intersection over the smaller positive box, in a shared source frame.
def overlap_fraction(a, b):
    """Intersection over the smaller positive box, in a shared source frame."""
    # Duplicate grouping needs valid boxes in the same original-page coordinate system.
    if any(len(box) != 4 or not all(math.isfinite(v) for v in box) for box in (a, b)):
        raise ValueError("deduplication needs finite canonical source boxes")
    aa, ba = (a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])
    if a[2] <= a[0] or a[3] <= a[1] or b[2] <= b[0] or b[3] <= b[1]:
        return 0.0
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return area / min(aa, ba)


# Group flags only when every member agrees in source text and location, avoiding chains that
# join distinct entries.
def group_duplicate_flags(flags, *, minimum_overlap=0.85):
    """Complete-link grouping prevents chains bridging distinct printed entries.

    Group only the same PDF, physical page, frame and whitespace/case-normalized
    text. Different locations, documents and OCR transcriptions remain separate.
    Even grouped aliases retain every full input flag and its provenance.
    """
    # Minimum overlap must be in (0, 1].
    if not 0 < minimum_overlap <= 1:
        raise ValueError("minimum overlap must be in (0, 1]")
    # Reject duplicate omission flag IDs.
    if len({f["flag_id"] for f in flags}) != len(flags):
        raise ValueError("duplicate omission flag IDs")
    buckets = defaultdict(list)
    for flag in flags:
        key = (
            flag["source_pdf_checksum"],
            flag["physical_page"],
            flag.get("geometry_frame", "PDF_POINTS_CANONICAL"),
            normalized_source_text(flag["raw_text"]),
        )
        overlap_fraction(flag["geometry"], flag["geometry"])
        buckets[key].append(flag)
    result = []
    for values in buckets.values():
        # Keep duplicate groups separate unless every member is compatible; a chain of
        # pairwise overlaps is not enough.
        groups = []
        for flag in sorted(values, key=lambda f: f["flag_id"]):
            group = next(
                (
                    g
                    for g in groups
                    if all(
                        overlap_fraction(flag["geometry"], p["geometry"]) >= minimum_overlap
                        for p in g
                    )
                ),
                None,
            )
            if group is None:
                groups.append([flag])
            else:
                group.append(flag)
        for group in groups:
            # No cue unions: an alias cannot borrow another observation's cues to manufacture
            # a higher score. Select the strongest intact flag.
            representative = min(group, key=priority_sort_key)
            ids = sorted(f["flag_id"] for f in group)
            result.append(
                {
                    **copy.deepcopy(representative),
                    "review_group_id": "omission-group-"
                    + hashlib.sha256("\n".join(ids).encode()).hexdigest()[:24],
                    "member_flag_ids": ids,
                    "member_flags": copy.deepcopy(sorted(group, key=lambda f: f["flag_id"])),
                    "duplicate_count": len(group) - 1,
                    "deduplication_rule": "SAME_SOURCE_PAGE_TEXT_AND_ALL_PAIR_BOX_OVERLAP",
                    "minimum_box_overlap": minimum_overlap,
                    "course_truth_assigned": False,
                }
            )
    return sorted(result, key=priority_sort_key)


# Create a deterministic sort key for the omission-review priorities.
def priority_sort_key(flag):
    return (
        {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(flag.get("priority_tier"), 3),
        -flag.get("omission_score", 0),
        flag["source_pdf_checksum"],
        flag["physical_page"],
        flag["geometry"][1],
        flag["flag_id"],
    )
