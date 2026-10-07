# Measure how well extracted course text and fields match their source reviews. Keep missed
# courses, failed pages and uncertain alignments in the appropriate counts.
"""Strict source ownership diagnostics; no inferred keyword dispositions."""

from __future__ import annotations


# Calculate ownership precision and recall with explicit missing-course and failed-page
# counts.
def ownership_rates(counts: dict) -> dict:
    # Return a fraction when there is something to count; leave an empty denominator
    # undefined.
    def rate(numerator, denominator):
        return numerator / denominator if denominator else None

    return {
        "complete_course_precision_conservative": rate(
            counts["complete_content_correct"], counts["central_course_proposals"]
        ),
        "owned_extent_precision_conservative": rate(
            counts["confirmed_extent_exact_matched"], counts["central_course_proposals"]
        ),
        "owned_extent_recall": rate(
            counts["confirmed_extent_exact_matched"], counts["source_boundary_eligible"]
        ),
        "description_recall_end_to_end": rate(
            counts["description_exact_matched"], counts["source_description_eligible"]
        ),
        "description_missing_or_incorrect": (
            counts["source_description_eligible"] - counts["description_exact_matched"]
        ),
        "zero_course_pages": counts["zero_course_pages"],
        "false_proposals_on_zero_course_pages": counts["false_proposals_on_zero_course_pages"],
        "clean_zero_course_page_rate": rate(
            counts["clean_zero_course_pages"], counts["zero_course_pages"]
        ),
        "scope": "EXPOSED_SOURCE_REVIEWED_DEVELOPMENT",
        "note": (
            "Exact means whitespace-normalized source quotes, resolved fields, confirmed "
            "owned block, page extent and continuation. Misses and failed pages remain in "
            "recall denominators; unresolved alignments remain in conservative precision. "
            "No token ownership labels or original-hit dispositions are inferred."
        ),
    }
