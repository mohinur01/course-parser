# Look for text that may indicate a missed course, including pages where the parser found no
# headings. Produce review questions, not additional accepted course records.
"""Independent, source-only recall audit. A flag is never a course decision."""

from __future__ import annotations

import hashlib
import re
from statistics import median

from .omission_ranking import score_flag

VERSION = "source-omission-signals-v2"
MODERN = re.compile(r"^\s*[A-Z]{2,8}\s*[-.]?\s*\d{2,4}[A-Z]?\b")
HISTORICAL = re.compile(
    r"^\s*(?:\d{1,5}(?:\.\d{1,5})?[a-z]?"
    r"(?:\s*[,–-]\s*\d{1,5}(?:\.\d{1,5})?[a-z]?)*[.):]?|"
    r"[IVXLCDM]{1,8}[.)]|[A-Z][.)])(?:\s+(?=[A-Za-z])|\s*$)"
)
INVENTORY = re.compile(
    r"\b(?:course descriptions?|courses of (?:instruction|study)|"
    r"description of courses|course offerings?|courses offered)\b",
    re.I,
)
REFERENCE = re.compile(
    r"\b(?:requirements?|curriculum|electives?|plan of study|index|"
    r"table of contents|faculty|directory|major|facilities)\b|^(?:choose|select|complete)\b",
    re.I,
)
NARRATIVE = re.compile(
    r"^(?:this|the|a|an|study|studies|students?|introduction|introduces|"
    r"covers|survey|methods|application|principles|investigation|fundamental|"
    r"topics|examination|explores|analysis|laboratory|lectures|continuation|designed)\b",
    re.I,
)
CREDIT = re.compile(r"\b(?:credits?|units?|semester hours?|prerequisites?)\b", re.I)
METADATA = re.compile(
    r"^(?:\[?(?:CSU|UC)\]?\s*$|(?:one|two|three|four|five|six|\d+)\s+"
    r"(?:hours?|credits?|units?)\b|(?:mr|mrs|ms|dr|prof)\.|staff\b|"
    r"prerequisites?\b|corequisites?\b|offered\b|semesters?\b|lecture hours?\b|"
    r"credits?\b|units?\b|credit hours?\b|lec\b|lab\b|pass\s*/\s*no\s*pass\b)",
    re.I,
)
CAPS_PREFIX = re.compile(r"^[A-Z][A-Z'’/&.,()-]*(?:\s+[A-Z][A-Z0-9'’/&.,()-]*)*\s+(?=[A-Z][a-z])")
ADMINISTRATIVE = re.compile(
    r"\b(?:administrative organization|university administration|accreditation|"
    r"rights and privacy act|enrollment policies|academic (?:standing|loads)|"
    r"equal opportunity|admissions? policies|ineligibility)\b",
    re.I,
)
ORGANIZATION = re.compile(r"\b(?:inc\.|corporation|bureau of|& sons\b)|^[A-Z]\.\s+[A-Z]\.\s+", re.I)
EXPLICIT_CREDIT = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:[-–]\s*\d+\s*)?(?:credits?|credit hours?|units?)\b|"
    r"\b(?:credits?|credit hours?|units?)\s*:\s*\d+(?:\.\d+)?",
    re.I,
)
HOURS_METADATA = re.compile(r"^\s*\d+\s+(?:[Cc][Il]\.|[Ll]ab\.|Q\.H\.)")


# Identify the visible identifier pattern without treating it as an admitted course.
def _identifier_family(text):
    if MODERN.match(text):
        return "MODERN"
    match = HISTORICAL.match(text)
    if match and not (METADATA.match(text) or HOURS_METADATA.match(text)):
        code = match.group().strip(" .():")
        return "NUMERIC" if code[:1].isdigit() else "ROMAN_OR_LETTERED"
    return None


# Check the surrounding source display and section context of a possible header.
def _display_context(line):
    text = line["text"].strip()
    if len(text.split()) > 16 or len(text) > 150 or not _title_shape(text.rstrip(":")):
        return None
    if INVENTORY.search(text):
        return "INVENTORY"
    if re.search(r"\b(?:table of contents|index)\b", text, re.I):
        return "CONTENTS"
    if ADMINISTRATIVE.search(text):
        return "ADMINISTRATIVE"
    if REFERENCE.search(text):
        return "REFERENCE"
    return None


# Read the source box while keeping unavailable geometry explicit.
def _box(line):
    return line["bbox_canonical_pdf_points"]


# Check whether the source locations belong to the same verified column.
def _same_column(a, b):
    x0, _, x1, _ = _box(a)
    y0, _, y1, _ = _box(b)
    return max(0, min(x1, y1) - max(x0, y0)) >= 0.5 * min(x1 - x0, y1 - y0)


# Collect native text-style evidence while keeping missing style information explicit.
def _style(line):
    spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
    return (
        any(int(s.get("flags", 0)) & 16 for s in spans),
        any(int(s.get("flags", 0)) & 2 for s in spans),
        max((float(s.get("size", 0)) for s in spans), default=0),
    )


# Check the source text for a plausible title shape without making a course decision.
def _title_shape(text):
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", text)
    return (
        1 <= len(words) <= 16
        and len(text) <= 160
        and sum(w[0].isupper() for w in words) >= max(1, len(words) * 0.55)
        and not text.rstrip().endswith(("?", ":"))
    )


# A separate title fragment adjacent to a code, using relative geometry.
def _row_title(line, lines):
    """A separate title fragment adjacent to a code, using relative geometry.

    This is support for an audit question, not title assembly. Credit columns
    and page numbers without adjacent titles cannot seed it.
    """
    box = _box(line)
    height = max(box[3] - box[1], 1e-6)
    right = []
    for entry in lines:
        other = _box(entry)
        shared_y = max(0, min(box[3], other[3]) - max(box[1], other[1]))
        text = entry["text"].strip()
        if (
            entry is not line
            and 0 <= other[0] - box[2] <= 2 * height
            and shared_y >= 0.5 * min(height, other[3] - other[1])
            and _title_shape(text)
            and re.match(r"[A-Za-z]", text)
            and len(re.findall(r"[A-Za-z]", text)) >= 3
            and not (
                METADATA.match(text)
                or HOURS_METADATA.match(text)
                or _display_context(entry)
                or CREDIT.search(text)
            )
        ):
            right.append(entry)
    return min(right, key=lambda p: _box(p)[0]) if right else None


# Find possible missed header text for the independent omission-review audit.
def _potential_header(line, lines):
    if not _identifier_family(line["text"]):
        return False
    match = HISTORICAL.match(line["text"])
    code_only = match and not line["text"][match.end() :].strip()
    return not code_only or _row_title(line, lines) is not None


# Test native row fragments together; keep the original lines separately.
def _bounded_narrative(lines):
    """Test native row fragments together; keep the original lines separately."""
    rows = []
    for line in sorted(lines, key=lambda p: (_box(p)[1], _box(p)[0])):
        box = _box(line)
        row = next(
            (
                r
                for r in rows
                if any(
                    max(0, min(box[3], _box(p)[3]) - max(box[1], _box(p)[1]))
                    >= 0.5 * min(box[3] - box[1], _box(p)[3] - _box(p)[1])
                    for p in r
                )
            ),
            None,
        )
        if row is None:
            rows.append([line])
        else:
            row.append(line)
    for row in rows:
        ordered = sorted(row, key=lambda p: _box(p)[0])
        text = " ".join(p["text"].strip() for p in ordered)
        if len(text.split()) >= 7 and NARRATIVE.match(text):
            return ordered[0]
    return None


# Use native lines even when the parser proposed no headers at all.
def audit_page(geometry: dict, layout: dict, proposal: dict, binding: dict) -> list[dict]:
    """Use native lines even when the parser proposed no headers at all.

    Context/prose/style corroborate identifiers. No approved-keyword or modern
    subject-code requirement is imposed on typography and uncovered-prose paths.
    Negative context is retained explicitly and lowers review priority.
    """
    # Stop if the omission audit geometry belongs to another physical page.
    if geometry["physical_page"] != layout["physical_page"]:
        raise ValueError("omission audit geometry belongs to another physical page")
    # Stop if the omission audit source coordinate frame changed.
    if any(
        proposed is not None and abs(native - proposed) > 1e-5
        for native, proposed in (
            (geometry["canonical_width_points"], layout["page_width"]),
            (geometry["canonical_height_points"], layout["page_height"]),
        )
    ):
        raise ValueError("omission audit source coordinate frame changed")
    lines = [entry for entry in geometry.get("lines", []) if entry["text"].strip()]
    page = layout["physical_page"]
    prefix = f"{layout['dataset_page_id']}:p{page}:"
    sizes = [_style(entry)[2] for entry in lines if _style(entry)[2] > 0]
    body_size = median(sizes) if sizes else 0
    candidates = {}
    for candidate in proposal.get("candidates", []):
        for node in candidate["node_ids"]:
            candidates.setdefault(node, []).append(candidate)
    decisions = {d["candidate_id"]: d for d in proposal.get("decisions", [])}
    headers, owners = set(), {}
    for block in proposal.get("blocks", []):
        candidate = next(
            c for c in proposal["candidates"] if c["candidate_id"] == block["candidate_id"]
        )
        headers.update(candidate["node_ids"])
        for node in block["node_ids"]:
            owners.setdefault(node, []).append(block["block_id"])
    result = []
    # Inspect native text lines even when the parser produced no headings for this page.
    for line in lines:
        node = prefix + line["line_id"]
        if node in headers:
            continue
        raw, signals, cues = line["text"], [], []
        text = raw.strip()
        bb = _box(line)
        line_height = max(bb[3] - bb[1], 1e-6)
        historical_match = HISTORICAL.match(text)
        after_identifier = text[historical_match.end() :] if historical_match else text
        code_only = bool(historical_match and not after_identifier.strip())
        split_title = _row_title(line, lines) if code_only else None
        # The code box alone is too narrow to define a description lane.
        lane = (
            line
            if split_title is None
            else {
                **line,
                "bbox_canonical_pdf_points": [
                    bb[0],
                    bb[1],
                    _box(split_title)[2],
                    max(bb[3], _box(split_title)[3]),
                ],
            }
        )
        peers = sorted(
            [entry for entry in lines if entry is not line and _same_column(lane, entry)],
            key=lambda entry: (_box(entry)[1], entry["line_id"]),
        )
        below = [entry for entry in peers if bb[1] < _box(entry)[1] <= bb[3] + 7 * line_height]
        before = [entry for entry in peers if _box(entry)[3] <= bb[1] + 1]
        # Nearest explicit context in the same geometric column, not page-wide keyword
        # presence. An intervening requirements heading wins.
        context = next(
            (entry for entry in reversed(before) if _display_context(entry)),
            None,
        )
        context_kind = _display_context(context) if context else None
        inventory = context_kind == "INVENTORY"
        reference = context_kind in {"REFERENCE", "CONTENTS", "ADMINISTRATIVE"}
        # Do not borrow the description of a later entry to support this line.
        next_header_y = min(
            (_box(entry)[1] for entry in below if _potential_header(entry, lines)),
            default=float("inf"),
        )
        below = [entry for entry in below if _box(entry)[1] < next_header_y]
        narrative_start = _bounded_narrative(below)
        bold, italic, size = _style(line)
        typography = bold or italic or (body_size > 0 and size >= body_size * 1.15)
        gap = bool(before and bb[1] - _box(before[-1])[3] > line_height * 0.45)
        modern = bool(MODERN.match(text))
        caps_title = CAPS_PREFIX.match(after_identifier)
        inline_prose = bool(
            caps_title
            and len(after_identifier[caps_title.end() :].split()) >= 4
            and not METADATA.match(after_identifier[caps_title.end() :])
        )
        historical = bool(
            historical_match
            and (
                _title_shape(after_identifier)
                or typography
                and after_identifier.strip()
                or inline_prose
                or code_only
                and split_title
            )
            and not CREDIT.fullmatch(text[historical_match.end() :].rstrip(" ."))
            and not METADATA.match(text[historical_match.end() :])
            and not HOURS_METADATA.match(text)
        )
        peer_headers = [
            entry
            for entry in peers
            if _identifier_family(entry["text"]) and abs(_box(entry)[0] - bb[0]) < line_height * 1.2
        ]
        supported_peers = []
        for entry in peer_headers:
            peer_node = prefix + entry["line_id"]
            if _identifier_family(entry["text"]) != _identifier_family(text):
                continue
            if any(
                decisions.get(c["candidate_id"], {}).get("status") == "INDEPENDENT_COURSE"
                and c.get("status") == "SUPPORTED"
                for c in candidates.get(peer_node, [])
            ):
                if any(
                    _display_context(p) in {"REFERENCE", "CONTENTS", "ADMINISTRATIVE"}
                    for p in peers
                    if min(bb[1], _box(entry)[1]) < _box(p)[1] < max(bb[1], _box(entry)[1])
                ):
                    continue
                supported_peers.append(entry)
        title_only = (
            not modern
            and not historical
            and (_title_shape(text) or inline_prose)
            and not METADATA.match(text)
        )
        if modern:
            signals.append("MODERN_IDENTIFIER")
        if historical:
            signals.append("HISTORICAL_IDENTIFIER")
        if (
            title_only
            and (typography or gap)
            and not (INVENTORY.search(text) or REFERENCE.search(text))
        ):
            signals.append("HEADER_WITHOUT_SUBJECT_CODE")
        if typography:
            cues.append("DISTINCTIVE_TYPOGRAPHY")
        if gap:
            cues.append("PARAGRAPH_GAP")
        if inventory:
            cues.append("COURSE_SECTION_CONTEXT")
        if narrative_start:
            cues.append("FOLLOWING_DESCRIPTION_STRUCTURE")
        if inline_prose:
            cues.append("INLINE_TITLE_PROSE_TRANSITION")
        if len(peer_headers) >= 2:
            cues.append("REPEATED_ENTRY_PATTERN")
        if split_title:
            cues.append("ADJACENT_SAME_ROW_TITLE")
        if len(supported_peers) >= 2:
            cues.append("MODEL_SUPPORTED_ENTRY_PATTERN")
        if supported_peers and any(
            (bold, italic) == _style(p)[:2]
            and size > 0
            and _style(p)[2] > 0
            and 0.85 <= size / _style(p)[2] <= 1.15
            for p in supported_peers
        ):
            cues.append("MATCHING_ENTRY_TYPOGRAPHY")
        if CREDIT.search(text) or any(CREDIT.search(entry["text"]) for entry in below):
            cues.append("NEARBY_CREDIT_OR_PREREQUISITE")
        if EXPLICIT_CREDIT.search(" ".join([text, *[entry["text"] for entry in below]])):
            cues.append("EXPLICIT_PRINTED_CREDIT_VALUE")
        if context_kind == "REFERENCE" or _display_context(line) == "REFERENCE":
            cues.append("REQUIREMENT_LIST_CONTEXT")
        if context_kind == "CONTENTS" or _display_context(line) == "CONTENTS":
            cues.append("TABLE_OF_CONTENTS_CONTEXT")
        if context_kind == "ADMINISTRATIVE" or _display_context(line) == "ADMINISTRATIVE":
            cues.append("ADMINISTRATIVE_CONTEXT")
        if ORGANIZATION.search(text) and not modern:
            cues.append("ORGANIZATION_OR_PERSON_NAME")
        if candidates.get(node) and all(
            decisions.get(c["candidate_id"], {}).get("status") in {"COURSE_REFERENCE", "NONCOURSE"}
            for c in candidates[node]
        ):
            cues.append("PARSER_REFERENCE_OR_NONCOURSE")
        if title_only and not supported_peers:
            cues.append("ISOLATED_DISPLAY_HEADING")
        # Typography alone also describes repeated page headers and footers and faculty
        # headings.
        # Require a second signal family to route a likely omission for review.
        corroborated = bool(
            narrative_start
            or split_title
            and len(peer_headers) >= 2
            or historical
            and typography
            and len(supported_peers) >= 2
            or (inventory and (typography or gap or len(peer_headers) >= 2))
            or (typography and gap and len(peer_headers) >= 2)
            or (inline_prose and len(peer_headers) >= 2)
        )
        if title_only:
            corroborated = bool(
                narrative_start
                and (typography or gap)
                or inventory
                and typography
                and (gap or len(peer_headers) >= 2)
                or inline_prose
                and (inventory or len(peer_headers) >= 2)
            )
        if reference:
            # Keep genuine conflicts in the inbox, but a reference list with repeated codes
            # alone is not evidence of an omitted description.
            corroborated = bool(
                narrative_start and typography or inline_prose and len(peer_headers) >= 2
            )
        header_like = bool(signals and corroborated)
        # A whole unowned narrative block can reveal an omission even if the header text is
        # not recognized. Only its first native line is flagged.
        same_block = [entry for entry in lines if entry["block_index"] == line["block_index"]]
        first = line is min(same_block, key=lambda entry: entry["line_index"])
        uncovered = (
            inventory
            and first
            and not owners.get(node)
            and not candidates.get(node)
            and len(text.split()) >= 7
            and len(same_block) >= 3
            and all(not owners.get(prefix + entry["line_id"]) for entry in same_block)
        )
        if not header_like and not uncovered:
            continue
        if uncovered:
            signals.append("UNCOVERED_DESCRIPTION_BLOCK")
        if reference or REFERENCE.search(text):
            cues.append("REFERENCE_OR_NONCOURSE_CONTEXT")
        if owners.get(node):
            kind = "POSSIBLE_HEADER_INSIDE_OWNED_BODY"
        elif candidates.get(node):
            kind = "HEADER_WITHHELD_BY_ENTRY_STAGE"
        else:
            kind = "HEADER_NOT_PROPOSED" if header_like else "UNOWNED_NARRATIVE"
        if layout["status"] != "COMPLETE":
            cues.append("SOURCE_ORDER_UNRESOLVED")
        payload = f"{binding['source_pdf_checksum']}:{page}:{line['line_id']}:{VERSION}"
        result.append(
            {
                **binding,
                "flag_id": "omission-" + hashlib.sha256(payload.encode()).hexdigest()[:24],
                "physical_page": page,
                "node_id": node,
                "line_id": line["line_id"],
                "raw_text": raw,
                "geometry": bb,
                "geometry_frame": "PDF_POINTS_CANONICAL",
                "kind": kind,
                "signals": sorted(set(signals)),
                "corroborating_cues": sorted(set(cues)),
                "context_line": None
                if context is None
                else {
                    "line_id": context["line_id"],
                    "raw_text": context["text"],
                    "geometry": _box(context),
                },
                "nearby_source_lines": [
                    {
                        "line_id": entry["line_id"],
                        "raw_text": entry["text"],
                        "geometry": _box(entry),
                    }
                    for entry in below[:6]
                ],
                "competing_block_ids": owners.get(node, []),
                "model_supported_peer_nodes": [prefix + p["line_id"] for p in supported_peers],
                "existing_candidate_decisions": [
                    {
                        "candidate_id": c["candidate_id"],
                        "status": decisions.get(c["candidate_id"], {}).get("status"),
                    }
                    for c in candidates.get(node, [])
                ],
                "review_priority": "CONTEXT_CONFLICT"
                if reference or REFERENCE.search(text)
                else "POSSIBLE_OMISSION",
                "review_status": "PENDING_SOURCE_REVIEW",
                "course_truth_assigned": False,
            }
        )
    return [score_flag(flag) for flag in result]
