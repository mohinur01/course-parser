# Turn extracted PDF text into proposed course records using nearby text, typography and page
# layout. A candidate is a possible course heading; it becomes a record only after the parser
# checks its context and the text that belongs to it.
"""Source-preserving contextual course assembly over the reference v3 contracts.

Rules use page structure and printed text, never keywords, institutions or review
answers. Typography is supporting evidence; explicit reference containers win
over repeated header shapes. Every stage still emits review-required proposals.
"""

from __future__ import annotations

import hashlib
import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from .historical_headers import INLINE_NUMERIC_PAIR_TITLE, PAIR_SIGNAL, extended_numeric_probes
from .io import contained_file, read_json
from .learning_layout import repair_parallel_prose, repair_source_rows

VERSION = "source-owned-course-assembly-v8"
_REQUIREMENT = re.compile(
    r"^(?:.{0,65}\b(?:requirements?|curriculum|electives?|plan of study|"
    r"recommended courses|suggested program|courses required)\b.{0,65}|"
    r"(?:choose|select|complete|take)\b.{0,100}|(?:first|second|third|fourth) year|"
    r"(?:fall|spring|summer) semester)[\s:.-]*$",
    re.I,
)
_NONCOURSE = re.compile(r"^(?:index|table of contents|faculty|directory|bibliography)\b", re.I)
_INVENTORY = re.compile(
    r"\b(?:course descriptions?|courses of (?:instruction|study)|course offerings?|"
    r"description of courses|courses offered)\b",
    re.I,
)
_PREREQ = re.compile(r"\b(?:pre[- ]?requisites?|co[- ]?requisites?|prereqs?|pr\.)\s*[:–-]\s*", re.I)
_ADMIN = re.compile(
    r"^(?:pass/no pass|semesters? offered|terms? offered|offered|typically offered|"
    r"lec\b|lab\b|lecture hours|laboratory hours|grading|course fees?|"
    r"\[?(?:CSU|UC)\]?\s*$|staff\b|profs?\.|mr\.|mrs\.|dr\.|alternate years|"
    r"lecture,\s*(?:demonstration|laboratory)|may be taken concurrently\b|"
    r"required of students\b|years,?\s*\d{4})",
    re.I,
)
_CREDIT = re.compile(
    r"\b(?:credits?|units?)\s*:?\s*\d+(?:\.\d+)?(?:\s*(?:[-–]|to|or)\s*\d+)?\b|"
    r"\b\d+(?:\.\d+)?(?:\s*(?:[-–]|to|or)\s*\d+)?\s*(?:semester\s+hours?|credit\s+hours?|"
    r"credits?|units?|cr\.)",
    re.I,
)
_ARRANGED_CREDIT = re.compile(r"\bcredits? as arranged\.", re.I)
_NO_PREVIOUS = re.compile(r"\bNo previous\s+knowledge\b[^.!?]*?\brequired\.", re.I)
_BARE_CREDIT = re.compile(r"\(\s*(?:\d{1,2}(?:\.\d+)?|\.\d+)(?:\s*[-–]\s*\d{1,2})?\s*\)\s*$")
_CREDIT_ONLY = re.compile(r"(?:\d+(?:\.\d+)?\s*|(?:cr|hrs?|credits?|units?)\.?\s*)+", re.I)
_SENTENCE_META = re.compile(
    r"(?:\bPrerequisites?\s*:|\bCorequisites?\s*:|(?<=[.!?])\s+Offered\b|"
    r"\b(?:One|Two|Three|Four|Five|Six) hours?\.|\b(?:Mr|Mrs|Dr|Prof)\.|"
    r"\bStaff\.|\bAlternate years\b)"
)
_RANGE = re.compile(
    r"^\s*(?P<code>\*?\d{1,3}(?:(?:\s*,\s*|\s*[-–]\s*|\s+through\s+)\d{1,3})+\.?)(?:\s+|$)"
)
_MODERN_COMPOUND = re.compile(r"^\s*\*?[A-Z]{2,8}[ -]?\d+(?:[-–]\d+)+(?:[A-Z])?(?=\s|[.:]|$)")
_CAPS_TITLE = re.compile(
    r"(?P<title>[A-Z][A-Z0-9'’/&.,()-]*(?:\s+[A-Z][A-Z0-9'’/&.,()-]*)*)\s+(?=[A-Z][a-z])"
)
_DESCRIPTION_START = re.compile(
    r"^(?:this\b|the\b|a\b|an\b|students?\b|study\b|studies\b|introduction\b|"
    r"introduces\b|focuses\b|covers\b|using\b|use\b|application\b|methods\b|"
    r"investigations\b|survey\b|reading\b|selected\b|selections\b|derivation\b)",
    re.I,
)
_DEGREE_REFERENCE = re.compile(r"^(?:B\.(?:A|S|Sc)\.|M\.(?:A|S|Sc)\.|Ph\.D\.)", re.I)
_DISTRIBUTION_REQUIREMENT = re.compile(
    r"^\s*\d+\s+hours?\s+(?:of\s+)?(?:humanities|social sciences?|natural sciences?|"
    r"arts and sciences|junior[-– ]senior)\b",
    re.I,
)
_NUMBERED_LIST_SECTION = re.compile(r"^[IVXLCDM]{1,8}\.\s+[A-Z]", re.I)
_INLINE_REFERENCE_CODES = re.compile(r"\b[A-Z]{2,8}[ -]?\d{1,4}[A-Z]?\b")


# A short display label can supply context without a layout heading role.
def display_container_label(text):
    """A short display label can supply context without a layout heading role.

    Ordinary lower-case prose fragments mentioning curriculum or faculty cannot
    change ownership for the rest of the column.
    """
    return bool(
        text
        and len(text.split()) <= 10
        and re.match(r"[A-Z]", text)
        and not re.search(r"[.!?,;]$|\b(?:and|or|of|for|to|in|with)$", text, re.I)
    )


# Apply proposed model updates and validate the complete resulting object again.
def replace_validated(value, **changes):
    return type(value).model_validate({**value.model_dump(mode="python"), **changes})


# Record the native typography of a line so headings can be compared with nearby body text.
@dataclass(frozen=True)
class LineStyle:
    bold: bool = False
    italic: bool = False
    size: float = 0.0
    # Exact character offset in the native line, never inferred from OCR or
    # whitespace-normalized text. None means the native runs do not align.
    bold_prefix_end: int | None = None


# Find where a source-supported bold prefix ends in the original line text.
def native_bold_prefix_end(line):
    spans = line.get("spans", [])
    raw = line.get("text", "")
    if not raw or "".join(s.get("text", "") for s in spans) != raw:
        return None
    end = 0
    emphasized = False
    for span in spans:
        text = span.get("text", "")
        if text.strip() and not int(span.get("flags", 0)) & 16:
            break
        emphasized |= bool(text.strip())
        end += len(text)
    return len(raw[:end].rstrip()) if emphasized else None


# Read only checksum-verified geometry in the already verified packet.
def packet_styles(packet: Path, layouts: tuple) -> dict[str, LineStyle]:
    """Read only checksum-verified geometry in the already verified packet."""
    reference = read_json(packet / "source_reference.json")
    result = {}
    for layout in layouts:
        asset = next(
            x for x in reference["page_artifacts"] if x["physical_page"] == layout.physical_page
        )
        recorded = Path(asset["embedded_geometry_path"])
        path = contained_file(packet, recorded.name)
        geometry = read_json(path)
        regions = {r.region_id: r for r in layout.regions}
        for line in geometry.get("lines", []):
            spans = [x for x in line.get("spans", []) if x.get("text", "").strip()]
            flags = [int(x.get("flags", 0)) for x in spans]
            key = f"{layout.dataset_page_id}:p{layout.physical_page}:{line['line_id']}"
            result[key] = LineStyle(
                bold=bool(flags) and bool(flags[0] & 16),
                italic=bool(flags) and all(x & 2 for x in flags),
                size=max((float(x.get("size", 0)) for x in spans), default=0),
                bold_prefix_end=native_bold_prefix_end(line)
                if key in regions and regions[key].raw_text == line.get("text")
                else None,
            )
    return result


# Preserve native original text pieces while proposing the page roles and order used by course
# assembly.
def contextual_layout(proposal, layout_module, models, *, ordering="legacy", styles=None):
    proposal = repair_parallel_prose(proposal, layout_module, models, fragmented=True)
    patterns = importlib.import_module("catalog_mining.course_extraction_v3.header_patterns")
    styles = styles or {}
    header_sizes = [
        styles.get(r.region_id, LineStyle()).size
        for r in proposal.regions
        if patterns.parse_header_lexeme(r.raw_text)
        and styles.get(r.region_id, LineStyle()).size > 0
    ]
    page_sizes = [
        styles.get(r.region_id, LineStyle()).size
        for r in proposal.regions
        if len(re.findall(r"[A-Za-z]+", r.raw_text)) >= 4
        and styles.get(r.region_id, LineStyle()).size > 0
    ]
    typical_size = max(median(header_sizes or [0]), median(page_sizes or [0]))
    regions = []
    for region in proposal.regions:
        text = region.raw_text.strip()
        # A bold course code or metadata label is not a new document section.
        is_header = (
            patterns.parse_header_lexeme(text) is not None
            or _RANGE.match(text)
            or _MODERN_COMPOUND.match(text)
        )
        metadata = _PREREQ.match(text) or _ADMIN.match(text) or _CREDIT.fullmatch(text)
        explicit_section = (
            (text.isupper() and len(re.findall(r"[A-Z]", text)) >= 3)
            or _INVENTORY.search(text)
            or (
                len(text.split()) <= 10
                and not re.search(r"[.!?]$", text)
                and _REQUIREMENT.fullmatch(text)
            )
            or _NONCOURSE.match(text)
            or (
                len(header_sizes) >= 3
                and 2 <= len(text.split()) <= 8
                and _title_like(text)
                and re.match(r"[A-Z]", text)
                and not _DESCRIPTION_START.match(text)
                and not re.search(r"[,;\u00ad-]$", text)
                and styles.get(region.region_id, LineStyle()).size >= typical_size * 1.2
            )
        )
        if region.role == models.LayoutNodeRole.SECTION_HEADING and (
            is_header or metadata or not explicit_section
        ):
            region = replace_validated(region, role=models.LayoutNodeRole.LINE)
        elif region.role == models.LayoutNodeRole.PAGE_FURNITURE and (
            text.isdigit()
            or (ordering == "source_rows" and _CREDIT_ONLY.fullmatch(text.strip(" .")))
        ):
            peers = [
                r
                for r in proposal.regions
                if patterns.parse_header_lexeme(r.raw_text)
                and abs(r.geometry.y0 - region.geometry.y0)
                < max(3, region.geometry.y1 - region.geometry.y0)
                and 0 <= region.geometry.x0 - r.geometry.x1 <= (proposal.page_width or 0) * 0.25
            ]
            if peers:
                region = replace_validated(
                    region, role=models.LayoutNodeRole.LINE, column_id=peers[0].column_id
                )
        elif (
            text.startswith("Advisory:")
            and region.geometry.x1 - region.geometry.x0 > (proposal.page_width or 0) * 0.65
        ):
            region = replace_validated(region, role=models.LayoutNodeRole.SECTION_HEADING)
        regions.append(region)
    result = replace_validated(
        proposal, regions=tuple(regions), engine_version=proposal.engine_version + "+contextual-v1"
    )
    return repair_source_rows(result, layout_module) if ordering == "source_rows" else result


# Return the text location as a bounding box, combining boxes when needed.
def box(node):
    boxes = [x.geometry for x in node.evidence if x.geometry is not None]
    if not boxes:
        return None
    return (
        min(x.x0 for x in boxes),
        min(x.y0 for x in boxes),
        max(x.x1 for x in boxes),
        max(x.y1 for x in boxes),
    )


# Look up nearby text, column positions and section context in the proposed reading order.
class PageFlow:
    # Store the inputs and helpers this object uses in its later methods.
    def __init__(self, page, models):
        self.page, self.models = page, models
        self.nodes = {x.node_id: x for x in page.nodes}
        self.next, self.previous, self.columns = {}, {}, {}
        adjacency = {x.node_id: set() for x in page.nodes}
        for edge in page.edges:
            if edge.status != models.ResolutionStatus.CONFIRMED:
                continue
            if edge.kind == models.EdgeKind.NEXT_IN_READING_ORDER:
                self.next[edge.source_node_id] = edge.target_node_id
                self.previous[edge.target_node_id] = edge.source_node_id
            if edge.kind == models.EdgeKind.SAME_COLUMN:
                adjacency[edge.source_node_id].add(edge.target_node_id)
                adjacency[edge.target_node_id].add(edge.source_node_id)
        for start in adjacency:
            if start in self.columns or not adjacency[start]:
                continue
            pending, group = [start], set()
            # Gather connected text pieces into the same column group.
            while pending:
                current = pending.pop()
                if current not in group:
                    group.add(current)
                    pending.extend(adjacency[current] - group)
            for current in group:
                self.columns[current] = frozenset(group)
        starts = sorted(
            set(self.nodes) - set(self.previous),
            key=lambda x: (box(self.nodes[x]) or (0, 0, 0, 0))[1],
        )
        self.order = {}
        for start in starts:
            current = start
            # Follow each reading stream in order, stopping before revisiting a text piece.
            while current and current not in self.order:
                self.order[current] = len(self.order)
                current = self.next.get(current)

    # Check whether the source locations belong to the same verified column.
    def same_column(self, a, b):
        return not self.columns or self.columns.get(a) == self.columns.get(b) is not None

    # Recognize course-code mentions inside metadata so they cannot become new course entries.
    def metadata_reference(self, candidate, styles):
        node = self.nodes[candidate.code_slice.node_id]
        if _ADMIN.match(node.raw_text) or re.match(r"^\d+\s*(?:cr\.|hrs?\.)", node.raw_text, re.I):
            return True
        current = node.node_id
        for _ in range(8):
            current = self.previous.get(current)
            if current is None or not self.same_column(node.node_id, current):
                break
            preceding = self.nodes[current]
            if _PREREQ.search(preceding.raw_text):
                return (
                    styles.get(node.node_id, LineStyle()).italic
                    or not candidate.title_raw
                    or bool(re.match(r"^(?:or|and|with|through)\b", candidate.title_raw, re.I))
                    or (
                        str(candidate.identifier_kind).startswith("HISTORICAL")
                        and not _historical_title_like(candidate.title_raw)
                    )
                )
            if preceding.role == self.models.LayoutNodeRole.SECTION_HEADING:
                break
            if (
                re.match(r"^\*?[A-Z]{2,8}\s*\d", preceding.raw_text)
                and styles.get(preceding.node_id, LineStyle()).bold
            ):
                break
        return False

    # Nearest explicit structural container; department headings can inherit it.
    def context(self, candidate):
        """Nearest explicit structural container; department headings can inherit it."""
        current = candidate.code_slice.node_id
        for _ in range(150):
            current = self.previous.get(current)
            if current is None:
                break
            node = self.nodes[current]
            text = node.raw_text.strip()
            if len(text) > 150:
                continue
            heading_like = node.role == self.models.LayoutNodeRole.SECTION_HEADING or (
                display_container_label(text)
            )
            if _NONCOURSE.match(text) and heading_like:
                return "NONCOURSE", node
            if heading_like:
                if _REQUIREMENT.fullmatch(text):
                    return "REFERENCE", node
                if _INVENTORY.search(text):
                    return "INVENTORY", node
        return None, None

    # Return the ordered original text pieces that belong to this page-body context.
    def body(self, candidate, candidates):
        starts = {
            x.code_slice.node_id
            for x in candidates
            if x.candidate_id != candidate.candidate_id
            and x.status == self.models.HeaderStatus.SUPPORTED
            and x.title_raw
            and not _PREREQ.match(self.nodes[x.code_slice.node_id].raw_text)
        }
        current = max(candidate.node_ids, key=lambda x: self.order.get(x, -1))
        result = []
        for _ in range(180):
            following = self.next.get(current)
            if (
                not following
                or following in starts
                or not self.same_column(candidate.code_slice.node_id, following)
            ):
                break
            node = self.nodes[following]
            if node.role in {
                self.models.LayoutNodeRole.SECTION_HEADING,
                self.models.LayoutNodeRole.PAGE_FURNITURE,
            }:
                break
            result.append(node)
            current = following
        return result


# Check whether the visible text has a plausible course-title shape.
def _title_like(text):
    words = text.split()
    return (
        bool(words)
        and len(words) <= 16
        and not re.search(r"[.!?]\s*$", text)
        and not _PREREQ.search(text)
        and not _ADMIN.match(text)
        and not re.match(r"^(?:this|the|students?|focuses|covers|introduces)\b", text, re.I)
        and not _CREDIT.fullmatch(text)
    )


# Display-title evidence can coexist with mixed case after a colon.
def _historical_title_like(text):
    """Display-title evidence can coexist with mixed case after a colon.

    This is a supporting cue only. Repeated independent entry structure and
    owned narrative are still required by the entry classifier.
    """
    prefix, separator, _ = (text or "").partition(":")
    return bool(
        text
        and _title_like(text)
        and not _NUMBERED_LIST_SECTION.match(text)
        and (
            text.isupper()
            or (separator and prefix.isupper() and len(re.findall(r"[A-Z]", prefix)) >= 3)
        )
    )


# Find possible course headings using both printed text and the surrounding page context.
class ContextualHeaderDetector:
    version = VERSION + "-headers"

    # Store the inputs and helpers this object uses in its later methods.
    def __init__(self, models, styles):
        self.models, self.styles = models, styles
        self.ownership_conflicts = []
        self.page_pattern_proposals = []
        module = importlib.import_module("catalog_mining.course_extraction_v3.header_detection")
        self.base = module.GrammarHeaderDetector()

    # Look for possible course headings using their printed text and nearby page context.
    def detect(self, graph, profile):
        m = self.models
        # Start with the reference detector's headings, then inspect their local context and
        # exact printed title spans.
        candidates = list(self.base.detect(graph, profile))
        pages = {p.physical_page: PageFlow(p, m) for p in graph.pages}
        by_node = {c.code_slice.node_id: c for c in candidates}
        # Compound historical identifiers are one printed entry, not two courses.
        for page in graph.pages:
            for node in page.nodes:
                match = _RANGE.match(node.raw_text)
                if not match:
                    continue
                code_start, code_end = match.span("code")
                remainder = node.raw_text[match.end() :]
                title_node = node
                if not remainder.strip():
                    title_node = pages[page.physical_page].nodes.get(
                        pages[page.physical_page].next.get(node.node_id)
                    )
                    if title_node is None:
                        continue
                    remainder = title_node.raw_text
                title_match = _CAPS_TITLE.match(remainder)
                if title_match is None:
                    title_match = re.fullmatch(
                        r"(?P<title>[A-Z][A-Z0-9'’/&.,():-]*(?:\s+[A-Z][A-Z0-9'’/&.,():-]*)*)\s*",
                        remainder.lstrip(),
                    )
                if not title_match:
                    continue
                title_start = (match.end() if title_node is node else 0) + (
                    len(remainder) - len(remainder.lstrip())
                )
                title_end = title_start + title_match.end("title")
                prototype = by_node.get(node.node_id)
                if prototype is None:
                    cid = (
                        "header-"
                        + hashlib.sha256(
                            (graph.identity.source_document_id + ":" + node.node_id).encode()
                        ).hexdigest()[:20]
                    )
                    prototype = m.HeaderCandidate(
                        candidate_id=cid,
                        physical_page=page.physical_page,
                        node_ids=(node.node_id,),
                        code_node_ids=(node.node_id,),
                        status=m.HeaderStatus.UNRESOLVED,
                        identifier_kind=m.HeaderIdentifierKind.HISTORICAL_NUMERIC,
                        arrangement=m.HeaderArrangement.INLINE,
                        code_raw=node.raw_text[code_start:code_end],
                        code_slice=m.NodeTextSlice(
                            node_id=node.node_id,
                            text_span=m.TextSpan(start=code_start, end=code_end),
                            observed_text=node.raw_text[code_start:code_end],
                        ),
                        evidence=node.evidence,
                    )
                    candidates.append(prototype)
                updated = self._header(
                    prototype,
                    node,
                    code_start,
                    code_end,
                    [(title_node, title_start, title_end)],
                    historical=True,
                )
                candidates[candidates.index(prototype)] = updated
                by_node[node.node_id] = updated
        result = []
        for candidate in candidates:
            if candidate.status == m.HeaderStatus.REJECTED or (
                candidate.identifier_kind == m.HeaderIdentifierKind.OCR_UNCERTAIN_MODERN
                and not _RANGE.match(
                    pages[candidate.physical_page].nodes[candidate.code_slice.node_id].raw_text
                )
            ):
                result.append(candidate)
                continue
            flow = pages[candidate.physical_page]
            node = flow.nodes[candidate.code_slice.node_id]
            if _CREDIT.fullmatch(node.raw_text.strip(" .()")) or _DISTRIBUTION_REQUIREMENT.match(
                node.raw_text
            ):
                result.append(
                    replace_validated(
                        candidate,
                        status=m.HeaderStatus.REJECTED,
                        contradictions=tuple(
                            dict.fromkeys(
                                [*candidate.contradictions, "PRINTED_HOURS_METADATA_NOT_HEADER"]
                            )
                        ),
                    )
                )
                continue
            start, end = candidate.code_slice.text_span.start, candidate.code_slice.text_span.end
            historical = str(candidate.identifier_kind).startswith("HISTORICAL")
            compound = _MODERN_COMPOUND.match(node.raw_text)
            if compound and candidate.identifier_kind == m.HeaderIdentifierKind.MODERN_SUBJECT_CODE:
                start, end = compound.span()
                start += len(node.raw_text[start:end]) - len(node.raw_text[start:end].lstrip())
            if start > 0 and node.raw_text[start - 1 : start] == "*":
                start -= 1
            if node.raw_text[end : end + 1] == "." and (
                node.raw_text[end + 1 : end + 2].isspace() or end + 1 == len(node.raw_text)
            ):
                end += 1
            title_slices = [
                (flow.nodes[s.node_id], s.text_span.start, s.text_span.end)
                for s in candidate.title_slices
            ]
            if title_slices and all(
                _CREDIT_ONLY.fullmatch(n.raw_text[a:b].strip(" .()")) for n, a, b in title_slices
            ):
                title_slices = []
            # Small-cap titles followed by ordinary prose share a single line.
            tail_start = end
            # Skip spacing and punctuation before the rest of the heading.
            while tail_start < len(node.raw_text) and node.raw_text[tail_start] in " \t.:;–—-":
                tail_start += 1
            if compound:
                title_slices = [
                    (n, max(a, tail_start) if n.node_id == node.node_id else a, b)
                    for n, a, b in title_slices
                    if n.node_id != node.node_id or b > tail_start
                ]
            pair_title = (
                INLINE_NUMERIC_PAIR_TITLE.match(node.raw_text[tail_start:])
                if candidate.identifier_kind == m.HeaderIdentifierKind.HISTORICAL_NUMERIC
                else None
            )
            match = pair_title or _CAPS_TITLE.match(node.raw_text[tail_start:])
            if match:
                title_slices = [(node, tail_start, tail_start + match.end("title"))]
                if pair_title:
                    candidate = replace_validated(
                        candidate,
                        structural_signals=tuple(
                            dict.fromkeys([*candidate.structural_signals, PAIR_SIGNAL])
                        ),
                    )
            elif historical:
                caps = re.fullmatch(
                    r"[A-Z][A-Z0-9'’/&.,()-]*(?:\s+[A-Z][A-Z0-9'’/&.,()-]*)*\s*",
                    node.raw_text[tail_start:],
                )
                if caps:
                    title_slices = [(node, tail_start, len(node.raw_text))]
                elif not title_slices and re.match(r"[A-Z][A-Z]+\s+", node.raw_text[tail_start:]):
                    prefix = re.match(r"[A-Z][A-Z]+", node.raw_text[tail_start:])
                    title_slices = [(node, tail_start, tail_start + prefix.end())]
            if not title_slices:
                following = flow.next.get(node.node_id)
                for _ in range(6):
                    next_node = flow.nodes.get(following)
                    if next_node is None or not flow.same_column(node.node_id, next_node.node_id):
                        break
                    raw = next_node.raw_text.strip()
                    if (
                        _CREDIT_ONLY.fullmatch(raw.strip(" .()"))
                        or re.fullmatch(
                            r"(?:[\d\s.,]+|(?:cr|hr|hrs)\.?[\d\s.,]*(?:(?:cr|hr|hrs)\.?[\d\s.,]*)*)",
                            raw,
                            re.I,
                        )
                        or _CREDIT.fullmatch(raw)
                    ):
                        following = flow.next.get(next_node.node_id)
                        continue
                    if _title_like(raw):
                        a, b = box(node), box(next_node)
                        if a and b and b[1] - a[3] <= max(a[3] - a[1], b[3] - b[1]) * 3:
                            title_slices = [(next_node, 0, len(next_node.raw_text))]
                    break
            if title_slices:
                last, _, _ = title_slices[-1]
                following = flow.nodes.get(flow.next.get(last.node_id))
                for _ in range(3):
                    if following is None or not flow.same_column(node.node_id, following.node_id):
                        break
                    ordinal = bool(
                        re.fullmatch(r"(?:st|nd|rd|th)(?:\s+[A-Z][a-z]+)?", following.raw_text)
                    ) and bool(re.search(r"\d$", last.raw_text))
                    a, b = box(last), box(following)
                    if ordinal and a and b and abs(a[1] - b[1]) < a[3] - a[1]:
                        title_slices.append((following, 0, len(following.raw_text)))
                        last = following
                        following = flow.nodes.get(flow.next.get(last.node_id))
                    else:
                        break
                if (
                    following
                    and following.node_id not in by_node
                    and flow.same_column(node.node_id, following.node_id)
                    and (
                        _title_like(following.raw_text)
                        or (
                            self.styles.get(following.node_id, LineStyle()).bold
                            and len(following.raw_text.split()) <= 16
                            and not _PREREQ.search(following.raw_text)
                            and not _ADMIN.match(following.raw_text)
                        )
                    )
                ):
                    a, b = box(last), box(following)
                    header_box = box(node)
                    if (
                        a
                        and b
                        and header_box
                        and b[1] - a[3] <= max(a[3] - a[1], header_box[3] - header_box[1]) * 0.8
                        and b[0] >= header_box[0] - 3
                    ):
                        style = self.styles.get(following.node_id, LineStyle())
                        wraps = (
                            style.bold
                            or (last.raw_text.isupper() and following.raw_text.isupper())
                            or re.search(
                                r"\b(?:and|or|of|for|to|in|laboratory|thinking)\s*$",
                                last.raw_text,
                                re.I,
                            )
                            or bool(
                                re.fullmatch(r"(?:st|nd|rd|th)(?:\s+[A-Z][a-z]+)?", last.raw_text)
                            )
                        )
                        if wraps:
                            title_slices.append((following, 0, len(following.raw_text)))
                result.append(
                    self._header(candidate, node, start, end, title_slices, historical=historical)
                )
            else:
                result.append(candidate)
        from .page_patterns import apply_local_numeric_patterns

        result = extended_numeric_probes(self, graph, pages, result, _CAPS_TITLE)
        # Use distinct neighboring entries as supporting evidence for weaker numeric headings.
        result, self.page_pattern_proposals = apply_local_numeric_patterns(
            self, graph, pages, result
        )
        result = self._constrain_title_ownership(graph, pages, result)
        return tuple(sorted(result, key=lambda x: (x.physical_page, x.candidate_id)))

    # A split title cannot borrow another supported inline header's code.
    def _constrain_title_ownership(self, graph, pages, candidates):
        """A split title cannot borrow another supported inline header's code.

        Evaluate the complete proposal set together, preserving both proposals
        and exact spans for review. Same-node compound/shared headings are not
        split-title conflicts. No institution names or absolute geometry enter
        this decision; the peer must be in the same confirmed column.
        """
        m = self.models
        self.ownership_conflicts = []
        inline_by_node = {}
        # Find supported inline headings before checking whether a wrapped title borrowed
        # another course's text.
        for peer in candidates:
            code = peer.code_slice
            node = pages[peer.physical_page].nodes[code.node_id]
            if (
                peer.status == m.HeaderStatus.SUPPORTED
                and not node.raw_text[: code.text_span.start].strip(" \t*")
                and any(
                    part.node_id == code.node_id
                    and part.text_span.start >= code.text_span.end
                    and part.observed_text.strip()
                    for part in peer.title_slices
                )
            ):
                inline_by_node.setdefault(code.node_id, []).append(peer)
        result = []
        for candidate in candidates:
            conflicts = []
            flow = pages[candidate.physical_page]
            if candidate.status == m.HeaderStatus.SUPPORTED:
                for title in candidate.title_slices:
                    if title.node_id == candidate.code_slice.node_id:
                        continue
                    for peer in inline_by_node.get(title.node_id, []):
                        code = peer.code_slice
                        if (
                            peer.physical_page == candidate.physical_page
                            and flow.same_column(candidate.code_slice.node_id, code.node_id)
                            and max(title.text_span.start, code.text_span.start)
                            < min(title.text_span.end, code.text_span.end)
                        ):
                            conflicts.append(peer.candidate_id)
                            self.ownership_conflicts.append(
                                {
                                    "status": "WITHHELD_FOR_REVIEW",
                                    "reason_code": "TITLE_OVERLAPS_COMPETING_HEADER",
                                    "candidate_id": candidate.candidate_id,
                                    "competing_candidate_id": peer.candidate_id,
                                    "source_document_id": graph.identity.source_document_id,
                                    "source_pdf_checksum": graph.identity.source_pdf_checksum,
                                    "physical_page": candidate.physical_page,
                                    "title_claim": title.model_dump(mode="json"),
                                    "competing_code_claim": code.model_dump(mode="json"),
                                }
                            )
            if conflicts:
                candidate = replace_validated(
                    candidate,
                    status=m.HeaderStatus.UNRESOLVED,
                    contradictions=tuple(
                        dict.fromkeys(
                            [*candidate.contradictions, "TITLE_OVERLAPS_COMPETING_HEADER"]
                        )
                    ),
                )
            result.append(candidate)
        return result

    # Build a heading candidate from the exact code and title positions, retaining the clues
    # that support or contradict it.
    def _header(self, candidate, node, start, end, parts, *, historical):
        m = self.models
        slices = tuple(
            m.NodeTextSlice(
                node_id=n.node_id,
                text_span=m.TextSpan(start=a, end=b),
                observed_text=n.raw_text[a:b],
            )
            for n, a, b in parts
        )
        title = "\n".join(x.observed_text for x in slices)
        reasons = [
            x
            for x in candidate.contradictions
            if x not in {"TITLE_NOT_STRUCTURALLY_RECOVERED", "INLINE_REMAINDER_NOT_TITLE_LIKE"}
        ]
        field_module = importlib.import_module("catalog_mining.course_extraction_v3.field_parser")
        only_credit = True
        for part in slices:
            source = next(n for n, _, _ in parts if n.node_id == part.node_id)
            trailing = field_module._trailing_credit(
                source.raw_text, part.text_span.start, part.text_span.end
            )
            if trailing is None or trailing[0] > part.text_span.start:
                only_credit = False
        if only_credit:
            reasons.append("TITLE_CONTAINS_ONLY_CREDIT_METADATA")
        if historical and (
            _historical_title_like(title) or _CAPS_TITLE.match(node.raw_text[end:].lstrip(" .:;-"))
        ):
            # Entry classification still requires repeated bounded prose/context.
            reasons = [x for x in reasons if not x.startswith("HISTORICAL_")]
        if _RANGE.match(node.raw_text):
            reasons = [x for x in reasons if x != "OCR_IDENTIFIER_UNCERTAIN"]
        return replace_validated(
            candidate,
            node_ids=tuple(dict.fromkeys([node.node_id, *[n.node_id for n, _, _ in parts]])),
            code_raw=node.raw_text[start:end],
            code_slice=m.NodeTextSlice(
                node_id=node.node_id,
                text_span=m.TextSpan(start=start, end=end),
                observed_text=node.raw_text[start:end],
            ),
            title_raw=title,
            title_slices=slices,
            title_node_ids=tuple(dict.fromkeys(x.node_id for x in slices)),
            arrangement=m.HeaderArrangement.TABLE_ROW
            if candidate.arrangement == m.HeaderArrangement.TABLE_ROW
            else m.HeaderArrangement.INLINE
            if all(n.node_id == node.node_id for n, _, _ in parts)
            else m.HeaderArrangement.SPLIT_READING_ORDER,
            status=m.HeaderStatus.UNRESOLVED if reasons else m.HeaderStatus.SUPPORTED,
            contradictions=tuple(reasons),
            warnings=tuple(x for x in candidate.warnings if x.code in reasons),
            structural_signals=tuple(
                dict.fromkeys([*candidate.structural_signals, "SOURCE_TITLE_ASSEMBLY"])
            ),
            evidence=tuple(
                dict.fromkeys([*candidate.evidence, *[a for n, _, _ in parts for a in n.evidence]])
            ),
            identifier_kind=m.HeaderIdentifierKind.HISTORICAL_NUMERIC
            if _RANGE.match(node.raw_text)
            else candidate.identifier_kind,
        )


# Decide whether a possible heading starts a course, mentions another course or needs review.
class ContextualEntryClassifier:
    version = VERSION + "-entries"

    # Store the inputs and helpers this object uses in its later methods.
    def __init__(self, models, styles, *, table_contexts=None):
        self.models, self.styles = models, styles
        self.table_contexts = table_contexts or {}
        self.module = importlib.import_module(
            "catalog_mining.course_extraction_v3.entry_classifier"
        )
        self.base = self.module.StructuralEntryClassifier()

    # Decide whether each possible heading starts an independent course or belongs to a
    # reference, requirements list or unresolved case.
    def classify(self, graph, profile, candidates):
        m = self.models
        baseline = {x.candidate_id: x for x in self.base.classify(graph, profile, candidates)}
        result = []
        for page in graph.pages:
            flow = PageFlow(page, m)
            local = [c for c in candidates if c.physical_page == page.physical_page]
            detail = {c.candidate_id: self._narrative(c, flow, local) for c in local}
            for c in local:
                old = baseline[c.candidate_id]
                kind, cue = flow.context(c)
                source_header = flow.nodes[c.code_slice.node_id]
                if _DEGREE_REFERENCE.match(source_header.raw_text.strip()):
                    result.append(
                        self._decision(
                            c,
                            flow,
                            m.EntryStatus.COURSE_REFERENCE,
                            m.EntryContextKind.REFERENCE_CONTAINER,
                            "PRINTED_DEGREE_ABBREVIATION_REFERENCE",
                        )
                    )
                    continue
                embedded_list = self._embedded_reference_list(c, flow)
                if embedded_list:
                    result.append(
                        self._decision(
                            c,
                            flow,
                            m.EntryStatus.COURSE_REFERENCE,
                            m.EntryContextKind.REFERENCE_CONTAINER,
                            "SOURCE_EMBEDDED_COMMA_SEPARATED_CODE_LIST",
                            context=embedded_list,
                        )
                    )
                    continue
                group_requirement = self._group_requirement(c, flow, local)
                if group_requirement:
                    result.append(
                        self._decision(
                            c,
                            flow,
                            m.EntryStatus.COURSE_REFERENCE,
                            m.EntryContextKind.REFERENCE_CONTAINER,
                            "SOURCE_DISTRIBUTION_REQUIREMENT_LIST",
                            context=group_requirement,
                        )
                    )
                    continue
                grid = self.table_contexts.get(c.code_slice.node_id)
                if grid:
                    context = (
                        [flow.nodes[grid["context_node_id"]]] if grid["context_node_id"] else []
                    )
                    detail_nodes = [
                        flow.nodes[nid]
                        for nid in grid["narrative_node_ids"]
                        if nid not in c.node_ids
                    ]
                    peer_rows = {
                        self.table_contexts.get(peer.code_slice.node_id, {}).get("row_id")
                        for peer in local
                        if peer.candidate_id != c.candidate_id
                        and peer.status == m.HeaderStatus.SUPPORTED
                        and self.table_contexts.get(peer.code_slice.node_id, {}).get("table_id")
                        == grid["table_id"]
                        and self.table_contexts.get(peer.code_slice.node_id, {}).get(
                            "narrative_node_ids"
                        )
                    }
                    row_safe = set(c.node_ids).issubset(grid["row_node_ids"])
                    if grid["container"] == "REQUIREMENTS":
                        status, context_kind, reason = (
                            m.EntryStatus.COURSE_REFERENCE,
                            m.EntryContextKind.REFERENCE_CONTAINER,
                            "SOURCE_REQUIREMENTS_TABLE",
                        )
                    elif (
                        grid["container"] == "COURSE_INVENTORY"
                        and detail_nodes
                        and peer_rows - {grid["row_id"]}
                        and row_safe
                        and page.status == m.ResolutionStatus.CONFIRMED
                        and c.status == m.HeaderStatus.SUPPORTED
                    ):
                        status, context_kind, reason = (
                            m.EntryStatus.INDEPENDENT_COURSE,
                            m.EntryContextKind.COURSE_INVENTORY,
                            "SOURCE_INVENTORY_ROW_WITH_OWNED_NARRATIVE",
                        )
                    else:
                        status, context_kind, reason = (
                            m.EntryStatus.UNRESOLVED,
                            m.EntryContextKind.UNSCOPED,
                            "GRID_PURPOSE_OR_OWNED_ROW_UNRESOLVED",
                        )
                    decision = self._decision(
                        c, flow, status, context_kind, reason, context=context, detail=detail_nodes
                    )
                    if status == m.EntryStatus.UNRESOLVED:
                        decision = replace_validated(
                            decision, context_status=m.ResolutionStatus.UNRESOLVED
                        )
                    result.append(decision)
                    continue
                if flow.metadata_reference(c, self.styles):
                    result.append(
                        self._decision(
                            c,
                            flow,
                            m.EntryStatus.COURSE_REFERENCE,
                            m.EntryContextKind.REFERENCE_CONTAINER,
                            "SOURCE_PREREQUISITE_OR_HOURS_REFERENCE",
                        )
                    )
                    continue
                if kind in {"REFERENCE", "NONCOURSE"}:
                    result.append(
                        self._decision(
                            c,
                            flow,
                            m.EntryStatus.COURSE_REFERENCE
                            if kind == "REFERENCE"
                            else m.EntryStatus.NONCOURSE,
                            m.EntryContextKind.REFERENCE_CONTAINER
                            if kind == "REFERENCE"
                            else m.EntryContextKind.NONCOURSE_CONTAINER,
                            "EXPLICIT_REFERENCE_OR_NONCOURSE_CONTAINER",
                            context=[cue],
                        )
                    )
                    continue
                if (
                    page.status != m.ResolutionStatus.CONFIRMED
                    or c.status != m.HeaderStatus.SUPPORTED
                    or c.arrangement == m.HeaderArrangement.TABLE_ROW
                ):
                    result.append(old)
                    continue
                # The reference classifier also reads short body sentences as container
                # labels. Only a corroborated local source pattern can override that specific
                # false cue; real headings win.
                body_noncourse_cue = (
                    "LOCAL_PAGE_NUMERIC_PATTERN" in c.structural_signals
                    and old.status == m.EntryStatus.NONCOURSE
                    and "SOURCE_NONCOURSE_CONTAINER" in old.reason_codes
                    and old.context_node_ids
                    and all(
                        flow.nodes[nid].role == m.LayoutNodeRole.LINE
                        and re.search(r"[.!?]$", flow.nodes[nid].raw_text.strip())
                        for nid in old.context_node_ids
                    )
                )
                if old.status in {m.EntryStatus.NONCOURSE, m.EntryStatus.COURSE_REFERENCE} and not (
                    body_noncourse_cue
                ):
                    result.append(old)
                    continue
                prose = detail[c.candidate_id]
                peers = []
                for peer in local:
                    if (
                        peer.candidate_id == c.candidate_id
                        or peer.status != m.HeaderStatus.SUPPORTED
                        or not detail[peer.candidate_id]
                    ):
                        continue
                    if not flow.same_column(c.code_slice.node_id, peer.code_slice.node_id):
                        continue
                    a, b = (
                        box(flow.nodes[c.code_slice.node_id]),
                        box(flow.nodes[peer.code_slice.node_id]),
                    )
                    if a and b and abs(a[0] - b[0]) > max(a[3] - a[1], b[3] - b[1]) * 2:
                        continue
                    between = [
                        n
                        for n in page.nodes
                        if min(
                            flow.order.get(c.code_slice.node_id, 0),
                            flow.order.get(peer.code_slice.node_id, 0),
                        )
                        < flow.order.get(n.node_id, -1)
                        < max(
                            flow.order.get(c.code_slice.node_id, 0),
                            flow.order.get(peer.code_slice.node_id, 0),
                        )
                        and n.role == m.LayoutNodeRole.SECTION_HEADING
                    ]
                    if not between and flow.context(peer)[0] not in {"REFERENCE", "NONCOURSE"}:
                        peers.append(peer.candidate_id)
                header = flow.nodes[c.code_slice.node_id]
                style = self.styles.get(header.node_id, LineStyle())
                emphasis = style.bold or bool(c.title_raw and c.title_raw.isupper())
                historical_valid = not str(c.identifier_kind).startswith("HISTORICAL") or bool(
                    _historical_title_like(c.title_raw)
                    or "LOCAL_PAGE_NUMERIC_PATTERN" in c.structural_signals
                )
                if not historical_valid:
                    result.append(old)
                    continue
                metadata = [n for n in flow.body(c, local) if _PREREQ.search(n.raw_text)]
                if not prose and emphasis and peers and metadata:
                    prose = metadata
                if prose and (peers or (kind == "INVENTORY" and emphasis)):
                    result.append(
                        self._decision(
                            c,
                            flow,
                            m.EntryStatus.INDEPENDENT_COURSE,
                            m.EntryContextKind.COURSE_INVENTORY
                            if kind == "INVENTORY"
                            else m.EntryContextKind.LOCAL_COURSE_DETAILS,
                            "REPEATED_SOURCE_HEADER_AND_OWNED_PROSE",
                            context=[cue] if cue else [],
                            detail=prose,
                            peers=peers,
                            signals=[
                                "BOUNDED_NARRATIVE_WITHOUT_REQUIRED_CREDITS",
                                "TYPOGRAPHIC_HEADER_EMPHASIS",
                            ]
                            if emphasis
                            else ["BOUNDED_NARRATIVE_WITHOUT_REQUIRED_CREDITS"],
                        )
                    )
                else:
                    result.append(old)
        return tuple(sorted(result, key=lambda x: x.candidate_id))

    # Check for description-like text within the bounded source neighborhood.
    def _narrative(self, c, flow, local):
        body = flow.body(c, local)
        inline = flow.nodes[c.code_slice.node_id].raw_text
        end = max(
            (s.text_span.end for s in c.title_slices if s.node_id == c.code_slice.node_id),
            default=len(inline),
        )
        texts = [
            inline[end:],
            *[
                n.raw_text
                for n in body
                if not _ADMIN.match(n.raw_text)
                and not _DISTRIBUTION_REQUIREMENT.match(n.raw_text)
                and not _PREREQ.match(n.raw_text)
                and not self.styles.get(n.node_id, LineStyle()).italic
            ],
        ]
        text = " ".join(texts)
        words = re.findall(r"\b[^\W\d_]+\b", text)
        return (
            body or [flow.nodes[c.code_slice.node_id]]
            if len(words) >= 4 and re.search(r"[.!?](?:\s|$)", text)
            else []
        )

    # Body-styled, comma-separated codes inherit a printed list introducer.
    def _embedded_reference_list(self, candidate, flow):
        """Body-styled, comma-separated codes inherit a printed list introducer.

        Repeated codes alone do not establish a reference list: a preceding
        body-text colon must introduce codes, with no intervening emphasized
        header or section. This covers lists embedded in an entry's prose while
        preserving unbolded independent courses and shared printed headings.
        """
        node = flow.nodes[candidate.code_slice.node_id]
        if self.styles.get(node.node_id, LineStyle()).bold or node.raw_text.isupper():
            return ()
        matches = list(_INLINE_REFERENCE_CODES.finditer(node.raw_text))
        if len(matches) < 2 or not any(
            "," in node.raw_text[a.end() : b.start()]
            for a, b in zip(matches, matches[1:], strict=False)
        ):
            return ()
        current, context = node.node_id, []
        for _ in range(8):
            current = flow.previous.get(current)
            if current is None or not flow.same_column(node.node_id, current):
                break
            preceding = flow.nodes[current]
            if (
                preceding.role == self.models.LayoutNodeRole.SECTION_HEADING
                or self.styles.get(current, LineStyle()).bold
            ):
                break
            context.append(preceding)
            code = _INLINE_REFERENCE_CODES.search(preceding.raw_text)
            colon = preceding.raw_text.rfind(":")
            if colon >= 0 and _INLINE_REFERENCE_CODES.search(preceding.raw_text, colon + 1):
                return tuple(reversed(context))
            if not code or "," not in preceding.raw_text:
                break
        return ()

    # Group-level distribution totals are not a final course description.
    def _group_requirement(self, c, flow, local):
        """Group-level distribution totals are not a final course description.

        Require a bounded, repeated title list and an explicit distribution
        total; never treat one printed course's own credit value as a list.
        """
        anchor = c.code_slice.node_id
        nodes = sorted(
            (n for n in flow.nodes.values() if flow.same_column(anchor, n.node_id)),
            key=lambda n: flow.order.get(n.node_id, -1),
        )
        index = next((i for i, n in enumerate(nodes) if n.node_id == anchor), None)
        if index is None:
            return []
        sections = [
            i for i, n in enumerate(nodes) if _NUMBERED_LIST_SECTION.match(n.raw_text.strip())
        ]
        start = max((i for i in sections if i < index), default=-1)
        end = min((i for i in sections if i > index), default=len(nodes))
        if start < 0:
            return []
        members = nodes[start + 1 : end]
        ids = {n.node_id for n in members}
        headers = [
            peer
            for peer in local
            if peer.code_slice.node_id in ids
            and peer.status == self.models.HeaderStatus.SUPPORTED
            and peer.title_raw
            and not _DEGREE_REFERENCE.match(flow.nodes[peer.code_slice.node_id].raw_text.strip())
        ]
        totals = [n for n in members if _DISTRIBUTION_REQUIREMENT.match(n.raw_text)]
        if len(headers) < 2 or not totals:
            return []
        header_ids = {nid for peer in headers for nid in peer.node_ids}
        narrative = [
            n
            for n in members
            if n.node_id not in header_ids
            and n not in totals
            and not _ADMIN.match(n.raw_text)
            and not _DEGREE_REFERENCE.match(n.raw_text.strip())
            and len(n.raw_text.split()) >= 8
            and re.search(r"[.!?](?:\s|$)", n.raw_text)
        ]
        return [] if narrative else [nodes[start], *totals]

    # Create the candidate's decision record with the supporting text and reasons for its
    # status.
    def _decision(
        self, c, flow, status, context_kind, reason, *, context=(), detail=(), peers=(), signals=()
    ):
        m = self.models
        nodes = list(
            dict.fromkeys(
                [*c.node_ids, *[n.node_id for n in context], *[n.node_id for n in detail]]
            )
        )
        anchors = []
        for nid in nodes:
            for anchor in flow.nodes[nid].evidence:
                if anchor not in anchors:
                    anchors.append(anchor)
        return m.EntryDecision(
            candidate_id=c.candidate_id,
            status=status,
            context_kind=context_kind,
            context_status=m.ResolutionStatus.CONFIRMED,
            reason_codes=(reason,),
            supporting_node_ids=tuple(nodes),
            context_node_ids=tuple(n.node_id for n in context),
            detail_node_ids=tuple(n.node_id for n in detail),
            peer_candidate_ids=tuple(peers),
            structural_signals=tuple([*c.structural_signals, *signals]),
            evidence=tuple(anchors),
            classifier_version=self.version,
        )


# Allocate bounded printed metadata across source nodes without rewriting.
def _inline_metadata_overrides(text, models):
    """Allocate bounded printed metadata across source nodes without rewriting.

    Named credit amounts exclude the introductory label, which remains in
    OTHER_METADATA. All returned spans refer to the original joined block.
    """
    other = models.FieldAllocationKind.OTHER_METADATA
    overrides = []
    for pattern in (
        r"\(\s*(?:Offered|Typically offered)\b[^()]*\)",
        r"\b(?:The\s+)?course may be (?:taken|repeated)\b[^.!?]*(?:[.!?]|$)",
    ):
        overrides.extend((match, other) for match in re.finditer(pattern, text, re.I))
    credit = re.compile(
        r"(?P<label>\bCredits?\s+(?:may\s+)?(?:vary|range)\s+from\s+)"
        r"(?P<amount>\d+(?:\.\d+)?(?:\s*(?:[-–]|to|or)\s*\d+(?:\.\d+)?)?\s*"
        r"(?:semester\s+hours?|credit\s+hours?|credits?|units?)\.)",
        re.I,
    )
    for match in credit.finditer(text):
        # Match objects on the full source preserve exact block-relative offsets.
        label = re.compile(re.escape(match.group("label"))).match(text, match.start("label"))
        amount = re.compile(re.escape(match.group("amount"))).match(text, match.start("amount"))
        overrides.extend(((label, other), (amount, models.FieldAllocationKind.CREDITS)))
    return overrides


# Split the text belonging to a course into code, title, description and metadata fields.
class ContextualCourseFieldParser:
    version = VERSION + "-fields"

    # Store the inputs and helpers this object uses in its later methods.
    def __init__(self, models, styles, *, transition_constraints=True):
        self.models, self.styles = models, styles
        self.transition_constraints = transition_constraints
        self.sequence_proposals = []
        self.module = importlib.import_module("catalog_mining.course_extraction_v3.field_parser")

    # Allocate the owned source text to course fields without dropping or borrowing text.
    def parse(self, graph, profile, candidates, blocks):
        self.sequence_proposals = []
        by_id = {c.candidate_id: c for c in candidates}
        return tuple(self._record(graph, by_id[b.candidate_id], b) for b in blocks)

    # Turn one course's text block into a record with separately identified fields and source
    # links.
    def _record(self, graph, candidate, block):
        m = self.models
        f = self.module
        # Reserve the exact code and title positions first. The remaining text can then be
        # split into description and metadata without overlap.
        fixed = {index: [] for index in range(len(block.segments))}
        indices = {segment.node_id: index for index, segment in enumerate(block.segments)}
        code = candidate.code_slice
        fixed[indices[code.node_id]].append(
            (code.text_span.start, code.text_span.end, m.FieldAllocationKind.CODE)
        )
        for part in candidate.title_slices:
            index = indices[part.node_id]
            start, end = part.text_span.start, part.text_span.end
            trailing = f._trailing_credit(block.segments[index].raw_text, start, end)
            if trailing is None:
                fixed[index].append((start, end, m.FieldAllocationKind.TITLE))
            else:
                title_end, credit_start, credit_end = trailing
                if title_end > start:
                    fixed[index].append((start, title_end, m.FieldAllocationKind.TITLE))
                fixed[index].append((credit_start, credit_end, m.FieldAllocationKind.CREDITS))
        if PAIR_SIGNAL in candidate.structural_signals:
            # Revalidate the exact printed delimiter from the adopted title span. Preserve its
            # bytes without guessing credit/contact meaning.
            part = next(p for p in candidate.title_slices if p.node_id == code.node_id)
            raw = block.segments[indices[code.node_id]].raw_text
            pair = INLINE_NUMERIC_PAIR_TITLE.match(raw, part.text_span.start)
            # Stop if the numeric-pair title delimiter differs from source spans.
            if pair is None or pair.end("title") != part.text_span.end:
                raise ValueError("numeric-pair title delimiter differs from source spans")
            start, end = pair.span("metadata")
            fixed[indices[code.node_id]].append((start, end, m.FieldAllocationKind.OTHER_METADATA))
        allocations = []
        state = None
        header_metadata = set()
        code_box = box(next(n for p in graph.pages for n in p.nodes if n.node_id == code.node_id))
        # Numeric/unit cells on the code's printed row jointly form header metadata.
        # Decode the complete row before classifying fragments such as a bare '3'.
        for index, segment in enumerate(block.segments):
            geometry = next((a.geometry for a in segment.evidence if a.geometry), None)
            if (
                geometry
                and code_box
                and segment.node_id not in candidate.node_ids
                and min(geometry.y1, code_box[3]) > max(geometry.y0, code_box[1])
                and _CREDIT_ONLY.fullmatch(segment.raw_text.strip(" .()"))
            ):
                header_metadata.add(index)
        metadata_text = " ".join(block.segments[i].raw_text for i in sorted(header_metadata))
        credit_row = bool(re.search(r"\b(?:cr\.|credits?|units?)\b|\bcr\.", metadata_text, re.I))
        # Recognize a bounded eligibility sentence across fragmented source lines.
        # This changes field ownership, never the source bytes or printed wording.
        role_overrides = [
            (match, m.FieldAllocationKind.PREREQUISITES)
            for match in _NO_PREVIOUS.finditer(block.raw_text)
        ]
        role_overrides.extend(
            (match, m.FieldAllocationKind.OTHER_METADATA)
            for match in re.finditer(r"\bLecture,\s*demonstration[^.!?]*\.", block.raw_text, re.I)
        )
        role_overrides.extend(_inline_metadata_overrides(block.raw_text, m))
        credit_stop = re.search(r"\b(?:cr\.|credits?|units?)", metadata_text, re.I)
        credit_offset = 0
        # Split header metadata across its original text fragments, keeping credit text
        # separate from other metadata.
        header_spans = {}
        for i in sorted(header_metadata):
            length = len(block.segments[i].raw_text)
            split = min(length, max(0, credit_stop.end() - credit_offset)) if credit_stop else 0
            header_spans[i] = [
                *([(0, split, m.FieldAllocationKind.CREDITS)] if credit_row and split else []),
                *(
                    [(split, length, m.FieldAllocationKind.OTHER_METADATA)]
                    if split < length
                    else []
                ),
            ]
            credit_offset += length + 1
        segment_offset = 0
        for index, segment in enumerate(block.segments):
            marked = list(fixed[index])
            for start, end, kind in list(marked):
                if kind == m.FieldAllocationKind.TITLE:
                    match = _BARE_CREDIT.search(segment.raw_text[start:end])
                    if match and (
                        candidate.identifier_kind == m.HeaderIdentifierKind.MODERN_SUBJECT_CODE
                        or "LOCAL_PAGE_NUMERIC_PATTERN" in candidate.structural_signals
                    ):
                        marked.remove((start, end, kind))
                        stop = start + match.start()
                        if stop > start:
                            marked.append((start, stop, kind))
                        marked.append((stop, end, m.FieldAllocationKind.CREDITS))
            occupied = sorted(marked)
            cursor = 0
            gaps = []
            for start, end, _ in occupied:
                if start > cursor:
                    gaps.append((cursor, start))
                cursor = end
            if cursor < len(segment.raw_text):
                gaps.append((cursor, len(segment.raw_text)))
            for start, end in gaps:
                if (
                    segment.node_id == code.node_id
                    and "LOCAL_PAGE_NUMERIC_PATTERN" in candidate.structural_signals
                    and start >= max(p.text_span.end for p in candidate.title_slices)
                ):
                    # Skip printed separators before assigning the remaining text to a field.
                    while start < end and segment.raw_text[start] in " \t.:;–—-":
                        start += 1
                raw = segment.raw_text[start:end]
                if not raw.strip(" \t\n.:;,–—-"):
                    continue
                spans, state = self._spans(
                    raw,
                    segment.node_id,
                    state,
                    historical=str(candidate.identifier_kind).startswith("HISTORICAL"),
                )
                if index in header_metadata:
                    spans = header_spans[index]
                    state = None
                # A line-broken printed label is decoded across source nodes; allocations
                # still retain the original hyphen and line break.
                split_label = re.search(r"\bPre[\u00ad-]?\s*$", raw, re.I)
                if split_label and index + 1 < len(block.segments):
                    if re.match(r"^requisites?\s*:", block.segments[index + 1].raw_text, re.I):
                        spans = [
                            *(
                                [(0, split_label.start(), m.FieldAllocationKind.DESCRIPTION)]
                                if split_label.start()
                                else []
                            ),
                            (split_label.start(), len(raw), m.FieldAllocationKind.OTHER_METADATA),
                        ]
                        state = "SPLIT_PREREQUISITE_LABEL"
                for match, override_kind in role_overrides:
                    a = max(0, match.start() - segment_offset - start)
                    b = min(len(raw), match.end() - segment_offset - start)
                    if a >= b:
                        continue
                    remaining = []
                    for sa, sb, kind in spans:
                        if sa < a:
                            remaining.append((sa, min(sb, a), kind))
                        if sb > b:
                            remaining.append((max(sa, b), sb, kind))
                    spans = [*remaining, (a, b, override_kind)]
                if (
                    raw.strip().isdigit()
                    and index + 1 < len(block.segments)
                    and re.match(r"^\s*hrs?\.", block.segments[index + 1].raw_text, re.I)
                ):
                    spans = [(0, len(raw), m.FieldAllocationKind.OTHER_METADATA)]
                    state = "CONTACT_HOURS"
                marked.extend((start + a, start + b, kind) for a, b, kind in spans)
            # Fill every gap between marked fields so no part of the original segment
            # disappears.
            allocations.extend(f._partition_segment(index, segment, marked))
            segment_offset += len(segment.raw_text) + 1
        allocations = tuple(allocations)
        f._validate_field_identity(candidate, allocations)
        sequence = None
        if self.transition_constraints:
            from .sequence_constraints import constrain_allocations

            allocations, sequence = constrain_allocations(
                candidate, block, allocations, self.styles, m
            )
            self.sequence_proposals.append(sequence)
        fields = {
            name: f._field(
                allocations,
                getattr(m.FieldAllocationKind, name.upper()),
                partial=block.boundary_status != m.BoundaryStatus.CONFIRMED
                if name == "description"
                else False,
                code=name == "code",
                allow_hyphen_join=name == "description",
            )
            for name in ("code", "title", "description", "credits", "prerequisites")
        }
        warnings = block.warnings
        # Leave affected fields ambiguous when more than one legal interpretation remains.
        if sequence and sequence["status"] != "UNIQUE":
            for name in ("description", "credits", "prerequisites"):
                if name.upper() in sequence["affected_roles"]:
                    fields[name] = m.CourseField(
                        raw=None,
                        normalized=None,
                        status=m.FieldStatus.AMBIGUOUS,
                        evidence=tuple(a for item in allocations for a in item.evidence),
                        reason="Competing source-span sequences require source adjudication.",
                    )
            warnings = (
                *warnings,
                m.ParserWarning(
                    code="FIELD_TRANSITION_" + sequence["status"],
                    stage="field_sequence",
                    message="Field ownership has no unique legal source-span sequence.",
                    severity=m.WarningSeverity.BLOCKING,
                    evidence=block.evidence,
                ),
            )
        if not fields["description"].raw:
            description_status = (
                m.DescriptionStatus.AMBIGUOUS_OWNERSHIP
                if fields["description"].status == m.FieldStatus.AMBIGUOUS
                else m.DescriptionStatus.NOT_RECOVERED
            )
        else:
            description_status = (
                m.DescriptionStatus.COMPLETE
                if block.boundary_status == m.BoundaryStatus.CONFIRMED
                else m.DescriptionStatus.PARTIAL
            )
        section, department = f._context_fields(graph, candidate, set(block.node_ids))
        return m.CourseRecord(
            parser_version=self.version,
            course_occurrence_id=f._occurrence_id(
                graph.identity.source_document_id, block.block_id
            ),
            source=graph.identity,
            physical_pages=block.physical_pages,
            printed_pages=tuple(
                dict.fromkeys(a.printed_page for a in block.evidence if a.printed_page is not None)
            ),
            header_candidate_id=candidate.candidate_id,
            block_id=block.block_id,
            entry_status=m.EntryStatus.INDEPENDENT_COURSE,
            boundary_status=block.boundary_status,
            continuation_status=block.continuation_status,
            publication_status=m.PublicationStatus.REVIEW_REQUIRED,
            department=department,
            section_context=section,
            raw_course_block=block.raw_text,
            normalized_course_block=block.normalized_text,
            block_normalization_steps=block.normalization_steps,
            block_evidence=block.evidence,
            warnings=warnings,
            field_allocations=allocations,
            description_status=description_status,
            **fields,
        )

    # Split the remaining text into field spans, using printed metadata labels and the
    # preceding field to interpret continuations.
    def _spans(self, text, node_id, state, *, historical=False):
        m = self.models
        left = len(text) - len(text.lstrip())
        right = len(text.rstrip())
        body = text[left:right]
        if not body:
            return [], state
        # A prerequisite label may be split across lines; continue recognizing the label
        # before assigning the following text.
        if state == "SPLIT_PREREQUISITE_LABEL":
            label = re.match(r"^requisites?\s*:\s*", body, re.I)
            if label:
                return [
                    (left, left + label.end(), m.FieldAllocationKind.OTHER_METADATA),
                    *(
                        [(left + label.end(), right, m.FieldAllocationKind.PREREQUISITES)]
                        if label.end() < len(body)
                        else []
                    ),
                ], "PREREQUISITES"
        special = _ARRANGED_CREDIT.search(body) or _NO_PREVIOUS.search(body)
        if special:
            kind = (
                m.FieldAllocationKind.CREDITS
                if _ARRANGED_CREDIT.fullmatch(special.group())
                else m.FieldAllocationKind.PREREQUISITES
            )
            spans = []
            if special.start():
                spans.append((left, left + special.start(), m.FieldAllocationKind.DESCRIPTION))
            spans.append((left + special.start(), left + special.end(), kind))
            if special.end() < len(body):
                spans.append((left + special.end(), right, m.FieldAllocationKind.OTHER_METADATA))
            return spans, None
        if state == "OTHER_METADATA" and re.match(r"^\d{4}[-–]\d{2,4}\.?$", body):
            return [(left, right, m.FieldAllocationKind.OTHER_METADATA)], None
        if body.lower().startswith("corequisite"):
            return [(left, right, m.FieldAllocationKind.OTHER_METADATA)], "OTHER_METADATA"
        if _ADMIN.match(body):
            return [
                (left, right, m.FieldAllocationKind.OTHER_METADATA)
            ], "OFFERED" if body.casefold().startswith("offered") else "OTHER_METADATA"
        if _CREDIT.fullmatch(body.strip(" .()")):
            return [(left, right, m.FieldAllocationKind.CREDITS)], None
        matches = list(_PREREQ.finditer(body))
        if matches:
            first = matches[0].start()
            spans = []
            if first:
                spans.append((left, left + first, m.FieldAllocationKind.DESCRIPTION))
            tail = body[first:]
            terminator = _SENTENCE_META.search(tail, matches[0].end() - first)
            stop = first + terminator.start() if terminator else len(body)
            label_end = matches[0].end() if historical else first
            if label_end > first:
                spans.append((left + first, left + label_end, m.FieldAllocationKind.OTHER_METADATA))
            if stop > label_end:
                spans.append((left + label_end, left + stop, m.FieldAllocationKind.PREREQUISITES))
            if stop < len(body):
                spans.append((left + stop, right, m.FieldAllocationKind.OTHER_METADATA))
            return spans, "PREREQUISITES" if stop == len(body) else None
        if state == "OFFERED" and re.match(
            r"^(?:fall|spring|summer|winter|every|alternate)\b", body, re.I
        ):
            return [(left, right, m.FieldAllocationKind.OTHER_METADATA)], None
        if state == "CONTACT_HOURS":
            return [(left, right, m.FieldAllocationKind.OTHER_METADATA)], None if re.search(
                r"[.!?]$", body
            ) else state
        if state == "OTHER_METADATA" and self.styles.get(node_id, LineStyle()).italic:
            return [(left, right, m.FieldAllocationKind.OTHER_METADATA)], state
        if state == "PREREQUISITES" and (
            self.styles.get(node_id, LineStyle()).italic or not _DESCRIPTION_START.match(body)
        ):
            metadata = _SENTENCE_META.search(body)
            if metadata:
                spans = []
                if metadata.start():
                    spans.append(
                        (left, left + metadata.start(), m.FieldAllocationKind.PREREQUISITES)
                    )
                spans.append((left + metadata.start(), right, m.FieldAllocationKind.OTHER_METADATA))
                return spans, None
            return [(left, right, m.FieldAllocationKind.PREREQUISITES)], state
        metadata = re.search(
            r"(?:\b\d+\s*hrs?\.(?:\s|$)|\bPrerequisites?\s*:|"
            r"\b(?:One|Two|Three|Four|Five|Six) hours?\.|\b(?:Mr|Mrs|Dr|Prof)\.|"
            r"\bStaff\.|\bAlternate years\b)",
            body,
        )
        if metadata:
            spans = []
            if metadata.start():
                spans.append((left, left + metadata.start(), m.FieldAllocationKind.DESCRIPTION))
            spans.append((left + metadata.start(), right, m.FieldAllocationKind.OTHER_METADATA))
            return spans, "CONTACT_HOURS" if re.match(
                r"\d+\s*hrs?\.", body[metadata.start() :], re.I
            ) else "OTHER_METADATA"
        return [(left, right, m.FieldAllocationKind.DESCRIPTION)], None


# Use the reference continuation checks and close a course only when its ending is supported.
class ContextualContinuationResolver:
    """Retain the reference continuation algorithm; close only evidenced endpoints."""

    version = VERSION + "-boundaries"

    # Store the inputs and helpers this object uses in its later methods.
    def __init__(self, models, *, transition_constraints=True, styles=None):
        self.models = models
        self.styles = styles or {}
        self.transition_constraints = transition_constraints
        self.boundary_proposals = []
        self.continuation_proposals = []
        from .continuation import AssessedContinuationResolver

        self.base = AssessedContinuationResolver(models, self.styles)

    # Work out where each course's text ends, keeping uncertain endings for review.
    def resolve(self, graph, profile, candidates, decisions, blocks):
        self.boundary_proposals = []
        m = self.models
        resolved = self.base.resolve(graph, profile, candidates, decisions, blocks)
        pages = {p.physical_page: PageFlow(p, m) for p in graph.pages}
        entries = {c.code_slice.node_id: c for c in candidates}
        statuses = {d.candidate_id: d.status for d in decisions}
        result = []
        for block in resolved:
            if block.boundary_status != m.BoundaryStatus.UNRESOLVED or block.continuation_hops:
                result.append(block)
                continue
            flow = pages[block.physical_pages[-1]]
            boundary = [flow.nodes[x] for x in block.boundary_node_ids if x in flow.nodes]
            section = [n for n in boundary if n.role == m.LayoutNodeRole.SECTION_HEADING]
            if (
                block.boundary_kind == m.BoundaryKind.COLUMN_TRANSITION_UNRESOLVED
                and len(section) == 1
            ):
                result.append(
                    self._close(
                        block,
                        section,
                        m.BoundaryKind.SECTION_BOUNDARY,
                        "SOURCE_SECTION_AFTER_FINAL_COLUMN",
                    )
                )
                continue
            if block.boundary_kind != m.BoundaryKind.PAGE_END_UNRESOLVED:
                result.append(block)
                continue
            last = flow.nodes[block.end_node_id]
            trailing = [
                n
                for n in flow.nodes.values()
                if flow.order.get(n.node_id, -1) > flow.order.get(last.node_id, -1)
                and n.role != m.LayoutNodeRole.PAGE_FURNITURE
            ]
            neighbor = pages.get(flow.page.physical_page + 1)
            if (
                trailing
                or neighbor is None
                or flow.page.status != m.ResolutionStatus.CONFIRMED
                or neighbor.page.status != m.ResolutionStatus.CONFIRMED
            ):
                result.append(block)
                continue
            meaningful = sorted(
                (
                    n
                    for n in neighbor.nodes.values()
                    if n.role != m.LayoutNodeRole.PAGE_FURNITURE and n.raw_text.strip()
                ),
                key=lambda n: neighbor.order.get(n.node_id, 10**9),
            )
            first = meaningful[0] if meaningful else None
            entry = entries.get(first.node_id) if first else None
            finished = bool(re.search(r"[.!?\]\)]\s*$", last.raw_text))
            if (
                first
                and finished
                and entry
                and statuses[entry.candidate_id] == m.EntryStatus.INDEPENDENT_COURSE
            ):
                result.append(
                    self._close(
                        block,
                        [first],
                        m.BoundaryKind.NEXT_INDEPENDENT_HEADER,
                        "ADJACENT_PAGE_STARTS_WITH_VERIFIED_COURSE",
                    )
                )
            elif first and finished and first.role == m.LayoutNodeRole.SECTION_HEADING:
                result.append(
                    self._close(
                        block,
                        [first],
                        m.BoundaryKind.SECTION_BOUNDARY,
                        "ADJACENT_PAGE_STARTS_WITH_SOURCE_SECTION",
                    )
                )
            else:
                result.append(block)
        if self.transition_constraints:
            from .boundary_proposals import resolve_competing_boundaries

            result, self.boundary_proposals = resolve_competing_boundaries(
                graph, candidates, decisions, blocks, resolved, result, m, styles=self.styles
            )
        self.base.reconcile(result)
        self.continuation_proposals = self.base.reports
        return tuple(result)

    # Work out where each course's text ends, keeping uncertain endings for review.
    def _close(self, block, nodes, kind, reason):
        m = self.models
        evidence = tuple(dict.fromkeys(a for n in nodes for a in n.evidence))
        return replace_validated(
            block,
            boundary_status=m.BoundaryStatus.CONFIRMED,
            boundary_kind=kind,
            boundary_node_ids=tuple(n.node_id for n in nodes),
            boundary_reason_codes=(reason,),
            boundary_evidence=evidence,
            excluded_neighbor_evidence=tuple(
                dict.fromkeys([*block.excluded_neighbor_evidence, *evidence])
            ),
            unresolved_evidence=(),
            continuation_status=m.ContinuationStatus.NONE,
            continuation_reason_codes=(),
            continuation_evidence=(),
            warnings=(),
            builder_version=self.version,
        )
