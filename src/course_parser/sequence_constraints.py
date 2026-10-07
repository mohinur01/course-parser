# Check whether a proposed sequence of course fields makes sense. Keep alternative
# interpretations when the evidence ties, while preserving the original text and its field
# assignments.
"""Source-span sequence constraints, with bounded competing decoding paths.

Scores are deterministic rule weights, not probabilities or a trained CRF.
The transition function is shared by decoding and annotation validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

VERSION = "source-field-transitions-v1"
HEADER = {"CODE", "TITLE"}
CONTENT = {"DESCRIPTION", "PREREQUISITES"}
ROLES = HEADER | CONTENT | {"CREDITS", "OTHER_METADATA", "SEPARATOR"}
_PREREQ = re.compile(
    r"(?:pre[\s\u00ad-]*requisites?|co[- ]?requisites?|prereqs?|pr\.)\s*[:–-]|"
    r"\bNo previous\s+knowledge\b",
    re.I,
)
_CREDIT = re.compile(r"\b(?:credits?|units?|cr\.)", re.I)
_NARRATIVE = re.compile(
    r"^(?:this|the|a|an|students?|study|studies|introduction|introduces|focuses|covers|"
    r"using|use|application|methods|investigations|survey|reading|selected|selections|"
    r"derivation)\b",
    re.I,
)


# Describe the source clues available for assigning one text span to a course field.
@dataclass(frozen=True)
class SpanObservation:
    allocation_id: str
    proposed_role: str
    header_anchor: bool = False
    credit_marker: bool = False
    prerequisite_marker: bool = False
    narrative_restart: bool = False
    alternatives: tuple[str, ...] = ()


# Remember whether body text has started and which field came last while checking a sequence.
@dataclass(frozen=True)
class SequenceState:
    body_started: bool = False
    last_role: str | None = None


# Check whether the next field is allowed here and return either the updated state or the
# reason it is rejected.
def transition(state: SequenceState, role: str, span: SpanObservation):
    """Return a new state or a named hard violation; field order stays flexible."""
    if role not in ROLES:
        return None, "UNKNOWN_ROLE"
    if role == "SEPARATOR":
        return state, None
    # Codes and titles can start an entry, but must not restart a heading after its body text
    # has begun.
    if role in HEADER:
        if not span.header_anchor:
            return None, "HEADER_REQUIRES_EXACT_SOURCE_ANCHOR"
        if state.body_started:
            return None, "HEADER_CANNOT_RESTART_INSIDE_OWNED_BODY"
    # A numeric-looking fragment is not a credit value without a printed credit cue.
    if role == "CREDITS" and not span.credit_marker:
        return None, "CREDITS_REQUIRE_PRINTED_UNIT_OR_HEADER_ROW"
    # Prerequisite text needs either a printed label or a continuation of an already
    # identified prerequisite field.
    if role == "PREREQUISITES" and not (
        span.prerequisite_marker or state.last_role == "PREREQUISITES"
    ):
        return None, "PREREQUISITES_REQUIRE_LABEL_OR_CONTINUATION"
    # Returning from prerequisites to the description needs a visible narrative restart.
    if role == "DESCRIPTION" and state.last_role == "PREREQUISITES" and not span.narrative_restart:
        return None, "DESCRIPTION_RESTART_REQUIRES_SOURCE_CUE"
    return SequenceState(state.body_started or role in CONTENT, role), None


# Keep the best legal field sequences for each state, retaining alternatives when their scores
# tie.
def decode_spans(observations: tuple[SpanObservation, ...], *, k: int = 2) -> dict:
    """Exact top-k dynamic programming per state; retain ties instead of guessing.

    Keeping k paths *per state*, rather than one global beam, avoids discarding
    a prefix that is the only one with a legal later transition.
    """
    # Competing decoding requires at least two paths.
    if k < 2:
        raise ValueError("competing decoding requires at least two paths")
    # Keep alternatives for each state so a currently weaker path can survive if it is the
    # only legal path later.
    paths = {SequenceState(): [(0, ())]}
    rejected = set()
    for index, span in enumerate(observations):
        following = {}
        roles = tuple(dict.fromkeys((span.proposed_role, *span.alternatives)))
        for state, prefixes in paths.items():
            for role in roles:
                new_state, violation = transition(state, role, span)
                if violation:
                    rejected.add((index, role, violation))
                    continue
                for score, labels in prefixes:
                    # Ambiguous source spans deliberately receive tied emissions.
                    following.setdefault(new_state, []).append((score + 1, (*labels, role)))
        paths = {
            state: sorted(set(values), key=lambda x: (-x[0], x[1]))[:k]
            for state, values in following.items()
        }
        if not paths:
            break
    ranked = sorted(
        {p for values in paths.values() for p in values if len(p[1]) == len(observations)},
        key=lambda x: (-x[0], x[1]),
    )[:k]
    ambiguous = bool(len(ranked) > 1 and ranked[0][0] == ranked[1][0])
    affected = set()
    # Union over emitted alternatives, not just the first two tied paths. Three or more
    # independent ambiguities must not hide a later uncertain field.
    for span in observations:
        if span.alternatives:
            affected.update((span.proposed_role, *span.alternatives))
    return {
        "version": VERSION,
        "status": "NO_LEGAL_PATH" if not ranked else "AMBIGUOUS" if ambiguous else "UNIQUE",
        "score_kind": "UNTRAINED_RULE_WEIGHT_NOT_PROBABILITY",
        "paths": [{"score": score, "roles": list(roles)} for score, roles in ranked],
        "selected_roles": None if ambiguous or not ranked else list(ranked[0][1]),
        "affected_roles": sorted(affected) if ambiguous else [],
        "rejected_transitions": [
            {"span_index": i, "role": role, "reason": why} for i, role, why in sorted(rejected)
        ],
    }


# Build source-cued observations and preserve an auditable lossless partition.
def constrain_allocations(candidate, block, allocations, styles, models):
    """Build source-cued observations and preserve an auditable lossless partition."""
    from .contextual import LineStyle, box

    fixed = [candidate.code_slice, *candidate.title_slices]
    code_box = box(block.segments[0])
    observations, previous = [], None
    metadata_label = False
    for item in allocations:
        role = str(item.kind)
        text = item.raw_text.strip()
        if not text:
            role = "SEPARATOR"
        exact_header = any(
            s.node_id == item.node_id
            and s.text_span.start <= item.text_span.start < item.text_span.end <= s.text_span.end
            for s in fixed
        )
        geometry = box(block.segments[item.segment_index])
        same_header_row = bool(
            geometry and code_box and min(geometry[3], code_box[3]) > max(geometry[1], code_box[1])
        )
        paragraph_restart = False
        if previous and previous.node_id != item.node_id:
            prior = box(block.segments[previous.segment_index])
            paragraph_restart = bool(
                prior and geometry and geometry[1] - prior[3] > (prior[3] - prior[1]) * 0.8
            )
        restart = bool(_NARRATIVE.match(text)) or paragraph_restart
        prefix = re.search(r"\bNo previous\s*$", text, re.I)
        prereq_marker = (
            bool(_PREREQ.search(item.segment_raw_text)) or metadata_label or bool(prefix)
        )
        alternatives = ()
        if role == "PREREQUISITES" and previous and str(previous.kind) == "PREREQUISITES":
            if restart and not prereq_marker:
                alternatives = ("DESCRIPTION",)
        if role == "DESCRIPTION" and previous and str(previous.kind) == "OTHER_METADATA":
            prior_style = styles.get(previous.node_id, LineStyle())
            if (
                prior_style.italic
                and not restart
                and not styles.get(item.node_id, LineStyle()).italic
            ):
                alternatives = ("OTHER_METADATA",)
        observations.append(
            SpanObservation(
                allocation_id=item.allocation_id,
                proposed_role=role,
                header_anchor=exact_header,
                credit_marker=bool(_CREDIT.search(item.segment_raw_text))
                or same_header_row
                or (role == "CREDITS" and exact_header),
                prerequisite_marker=prereq_marker,
                narrative_restart=restart,
                alternatives=alternatives,
            )
        )
        if role != "SEPARATOR":
            if role == "OTHER_METADATA":
                metadata_label = bool(_PREREQ.search(text)) or bool(
                    re.search(r"\bPre[\u00ad-]?\s*$|^requisites?\s*:", text, re.I)
                )
            elif role != "PREREQUISITES":
                metadata_label = False
            previous = item
    report = decode_spans(tuple(observations))
    report.update(
        candidate_id=candidate.candidate_id,
        block_id=block.block_id,
        spans=[
            {
                "allocation_id": a.allocation_id,
                "node_id": a.node_id,
                "text_span": a.text_span.model_dump(mode="json"),
                "proposed_role": str(a.kind),
            }
            for a in allocations
        ],
    )
    if report["status"] == "NO_LEGAL_PATH":
        report["affected_roles"] = sorted(
            {str(a.kind) for a in allocations} - HEADER - {"SEPARATOR", "OTHER_METADATA"}
        )
    selected = report["selected_roles"]
    if selected is not None:
        from .contextual import replace_validated

        allocations = tuple(
            replace_validated(a, kind=models.FieldAllocationKind(role))
            for a, role in zip(allocations, selected, strict=True)
        )
    # On ambiguity retain proposed span labels for audit, but withhold affected attached
    # fields. These labels must never be treated as training answers.
    return allocations, report
