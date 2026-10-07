# Source ownership transitions and competing boundaries

## Phase 11: annotation readiness (0.18.0)

CRF training remains deferred. The historical and refreshed TUNING packets both
contain 18 windows / 6,412 units, with zero adjudicated sequence labels. One
window has no extracted units and remains pending source preparation. These
counts are source evidence, not training targets or proof of generalization.

The v2 annotation audit blocks reserved input/output paths and symlink aliases.
It checks duplicate identities, complete character coverage, frozen central
page/source packet identity, anchored owners, terminal boundaries and ownership
closure. An owner cannot reopen after EXIT/NEXT; a window cannot end at SAME/NEXT.
UNCERTAIN boundaries cannot be adjudicated gold. SOURCE_INSUFFICIENT windows
may be reviewed but are excluded as entire windows from training eligibility.
Pending labels, including parser-filled suggestions, receive no training credit.

Fresh `page_orders` have `order_confirmed: false`. Layout COMPLETE is only
`proposal_order_usable`; neither that flag nor the historical confirmed flag
substitutes for a human image check. For ADJUDICATED records, two distinct declared
human identities must provide a matching `source_order_review`:

```json
{
  "status": "CONFIRMED_AS_EXPORTED",
  "source_order_sha256": "digest from the first-batch plan or source_order_digest(original)",
  "reviewer": "same name as the HUMAN reviewer record",
  "adjudicator": "same name as the HUMAN adjudicator record"
}
```

The digest binds ordered frozen node IDs, physical pages, raw text and evidence
plus the PDF checksum. It is unchanged when a reviewer splits mixed-field nodes.
If exported order is incorrect, preserve that finding and version the source
packet before annotation. Do not edit frozen `page_orders` in a review copy.
This checks declared provenance; it does not authenticate human identities.

Do not copy the screenshot's overlapping course/field labels literally into one
chain. Our owner and boundary relations encode course scope separately from
field roles. A future BIO field encoding would be derived from reviewed spans
inside owned courses, preserving character offsets; course markers must not be
inserted as invented source tokens. CRFsuite describes one label per sequence
item and learned state/transition features in its
[official manual](https://www.chokkan.org/software/crfsuite/manual.html).
Its [official Python-wrapper tutorial](https://sklearn-crfsuite.readthedocs.io/en/latest/tutorial.html)
illustrates labeled sequences and feature construction. These references support
the model form, not a claim that a CRF improves our catalogs.

The old exposed 15-course diagnosis contains one FIELD_SEQUENCE_AMBIGUITY,
three INLINE_METADATA_IN_DESCRIPTION and four PREREQUISITE_LABEL_CONVENTION
cases. Counts overlap and precede subsequent fixes. They motivate targeted
source annotation; they do not establish a dominant current transition problem.
Current field mismatches also include unresolved source absence. Identifier and
OCR errors require separate diagnosis. The hypothetical screenshot counts are
not our measured counts.

The first batch contains six complete TUNING windows, retaining a reviewed
zero-course central page and prioritizing different institution families and
field mismatches. Empty unit windows stay outside that initial annotation batch.
This is a small review queue, not a sufficient training dataset. Review central
and context pages, noncourse lists, references and furniture, not only positives.
No model targets are inferred from course-level answers.

The audit always returns `crf_experiment_ready: false`: it certifies integrity,
not sufficient data or an experiment's authorization. Before a future experiment,
we need reliable positive and hard-negative sequences, a reviewed rationale that
transition failures remain material, and a separate institution-grouped sequence
validation split from permitted material. Group overlapping PDF pages and catalog
families together; do not borrow the reserved 34-window cohort. Exclude institution
names, paths, keywords and answer-derived features. Compare complete-course
extraction, false ownership, contamination, boundary/continuation accuracy and
abstentions against the constrained baseline; token accuracy is auxiliary.

No CRF dependency was added and no model was trained or enabled. Extraction code
and the strict scorer are unchanged. Phase 10 accepted results are reused, not
rerun or rescored: original 35/52 core / 10/38 all fields; expanded 66/128 core /
13/97 all fields, zero measured false courses. Expanded includes original.
There is no extraction gain claimed for this annotation phase.

See [the Phase 11 report](../artifacts/crf-readiness-phase11-v1/release-v4/index.html).
Reproduce with a fresh output directory:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$COURSE_PARSER_PYTHON" \
  scripts/prepare_phase11_sequences.py \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --output artifacts/my-new-phase11-readiness
```


## Phase 7 update (0.14.0)

`boundary_scoring.py` adds source-bound ranking evidence to the existing
competition. Proposal origins are retained, but identical owned segments,
physical pages, continuation state and excluded targets count as one endpoint.
Changing the proving method alone cannot create an ownership conflict. Different
excluded targets remain distinct. Hard order, ownership and endpoint admission
checks run before acceptance; a high-scoring rejected proposal cannot win.

The provisional weights are capped by correlated evidence family: structure
(8), language (3), relative geometry (2), typography (1), field transition (4).
Printed terminal punctuation and lowercase adjacent-page text are weak cues,
not proof of completeness or continuation. Gaps and indentation use the page's
median non-furniture line height; font comparisons use ratios. Missing source
geometry or typography is recorded as unavailable. Cross-page indentation is
not compared without comparable column origins. No institution coordinates,
keyword signals, new model, or review-answer features are used.

Prerequisite markers produce DESCRIPTION stop hypotheses while remaining inside
the full course extent. Large relative gaps and column changes produce review
questions; they cannot truncate owned source text. Native reviewed metadata
cases and adversarial scale, gap, duplicate-proof and high-score rejection tests
exercise these distinctions.

The Phase 5 baseline has no conflicting admitted endpoints in either permitted
pilot. There is therefore no source-reviewed evidence to calibrate a numerical
acceptance margin between distinct verified extents. Such conflicts continue to
abstain, even with a large provisional score margin. Scores and margins are
diagnostic ranking values, not probabilities. This deliberately defers automatic
score-only conflict resolution until suitable permitted boundary annotations
exist; it does not claim that heuristics generalize better by construction.

Both source-only candidate runs were sealed before scoring. Strict code, title,
complete description and owned extent remain the main metric. Original complete
core courses remain **35/52**, all fields **10/38**; expanded remain **66/128**,
all fields **13/97**. Course records are identical to the accepted baseline
apart from version fields, with zero measured false proposals. Across all
windows/context pages, the new reports expose 82 original and 108 expanded
prerequisite field-stop questions. These counts are proposals, not new failures
or labels. The expanded pilot contains the original pilot.

Artifacts: `artifacts/boundary-phase7-v1/index.html`, source-bound JSONL,
paired evaluations, source-only freeze, verification and code diff. Reproduce
with `scripts/measure_phase7_boundaries.py` and
`scripts/build_phase7_boundary_report.py`, using fresh output directories and
the configured reference environment. Only the two registered exposed pilots
are accepted. The reserved 34-window cohort remains sealed until all 18 phases
are complete and the parser is frozen.

## Historical implementation (0.7)

Version 0.7 adds explicit admission checks to course field sequences and keeps
competing stop/continue proposals. It preserves the 0.6 source-row ordering and
typed evidence contracts. It has **not increased measured extraction recall**.
Its concrete benefit is rejecting unsupported transitions, withholding ambiguous
fields, and making the alternatives available for source review.

## Relevant documentation and code

GROBID separates coarse document segmentation from narrower field labeling, and
uses layout information with the source tokens. We adapt that staged design:
course-context/entry classification precedes ownership, boundaries precede field
attachment. This is an architectural adaptation, not use of GROBID's scientific
article labels or pretrained model. See [GROBID's architecture](https://grobid.readthedocs.io/en/latest/Principles/).

GROBID's [Segmentation.java](https://github.com/grobidOrg/grobid/blob/master/grobid-core/src/main/java/org/grobid/core/engines/Segmentation.java)
builds line features, calls a labeling interface, and assembles the resulting
document structure. Our separate decoding/assembly interfaces follow that
separation. The present decoder is an untrained finite-state algorithm.

The [GROBID annotation guidelines](https://github.com/grobidOrg/grobid/blob/master/doc/training/General-principles.md)
require manual correction of model tags while preserving the extracted text,
including noise. Our annotation packets preserve every source character and
start with blank labels. Our two-reviewer requirement is a project choice;
it is not a requirement copied from GROBID.

## Field ownership: check the sequence

`src/course_parser/sequence_constraints.py` contains a shared transition function
and top-k dynamic programming decoder. Its observations are exact allocation
spans from the existing parser, with source cues and explicit alternatives.

| Transition | Admission rule |
| --- | --- |
| Code/title in header | Must be inside the detected source slices; cannot restart after body content |
| Credits | Require a printed unit marker or a supported header-row/title allocation |
| First prerequisites span | Require a printed label or bounded eligibility cue |
| Prerequisite continuation | May continue the existing prerequisite state |
| Prerequisites → description | Require a narrative restart or paragraph spacing cue |
| Separators | Preserve the source partition; do not change semantic state |

This is not a universal fixed field order. Credits may precede the title, appear
after description, or be absent. Prerequisites may precede or follow description.
Credits and prerequisites remain unrecovered if absent from automated evidence;
manual source evidence is still required to claim `ABSENT_IN_SOURCE`.

The decoder retains two paths per state, rather than pruning to one global path.
This preserves a prefix that is needed for a legal later transition. Emissions
currently have equal untrained rule weights. These scores are **not probabilities**.
Paragraph/typography cues can expose competing description/prerequisite or
description/administrative interpretations. Tied paths cause the affected
attached fields to be `AMBIGUOUS`; no legal path causes conservative withholding.
All candidate allocations and the raw block remain available for inspection.

`ContextualCourseFieldParser` applies this before assembling attached fields. Its
`sequence_proposals` records source allocation IDs, spans, competing paths and
rejected transitions. Proposed labels retained for audit are never gold labels.
The existing lossless source partition and field identity checks still run.

## Boundaries: compare stop and continue

`src/course_parser/boundary_proposals.py` compares complete candidate blocks from
the same-page builder, existing geometric continuation resolver, and source
endpoint resolver. It also records provisional stop/continue interpretations at
the disputed target nodes, with exact evidence and admission reasons.

The reference continuation resolver retains its existing source/target geometry,
unique-target, column alignment and downstream-boundary checks. The added layer
checks page-order status, adjacent pages, column transition edges, existing
ownership, competing header decisions and the endpoint's semantic role.

A course reference cannot independently prove an endpoint. A section label
cannot prove an endpoint if source text lies between it and the last owned node.
An independent course or external section cannot be attached as continuation
content. Existing course owners cannot share a source node.

Equivalent proven extents count as one proposal. A unique admissible endpoint
wins. Different admissible extents leave the original conservative extent
unresolved with a blocking warning. Provisional continuations cannot attach text
until their source constraints have been proved. This is deliberately a bounded
proposal set from existing producers, not an exhaustive learned search over all
possible document boundaries.

The resolver exposes `boundary_proposals`; learning prediction artifacts now
include both boundary and field sequence proposals. The original course/block
IDs remain in use. The changes operate on ownership and attachment decisions.

## Same-cohort measurement

Release: `artifacts/sequence-boundaries-v3/release/`. Both versions use the same
frozen pilot, answer snapshots, reviewed partitions and unchanged scoring code.
New predictions were sealed before answer import. The prior prediction release
is `artifacts/ownership-order-v2/release-full/source_rows/predictions/`.

| Result on 20 reviewed central pages | 0.6 | 0.7 |
| --- | ---: | ---: |
| Aligned independent-course proposals | 47/52 | 47/52 |
| Complete code/title/description/boundaries | 25/52 | 25/52 |
| Complete entries including resolved credits/prerequisites | 10/38 | 10/38 |
| Confirmed exact owned blocks | 44 | 44 |
| Exact descriptions | 34 | 34 |
| Known false courses / ambiguous alignments | 0 / 1 | 0 / 1 |
| Failed reviewed pages | 0 | 0 |

TUNING remains 25/37 complete core courses; exposed VALIDATION remains **0/15**.
The 15 reviewed negative pages still produce no course proposals. Eight pending
central pages remain outside accuracy denominators, and the 18 original keyword
hit decisions remain pending. Keyword ownership accuracy is unmeasured.

Across 209 proposed courses including context pages, there are 200 unique field
paths, seven ambiguous sequences and two with no legal path. Of the 92 central
proposals including pending windows, 89 are unique, one ambiguous and two have
no legal path. These are **proposal counts**, not measured error counts.
The boundary layer admits 181 unique proven endpoints and retains 28 unresolved
extents; it found no competing admissible endpoint in this pilot. It proves no
recall gain. Adversarial regressions exercise the conflict behavior separately.

The suite has 118 passing tests. New cases cover field order/optional credits,
illegal restarts, competing decoding prefixes, ambiguous paragraph ownership,
skipped source content, conflicting endpoints, source mutation, incomplete
annotation coverage, reviewer provenance and partition leakage. Ruff check and
format checks pass. No reference code, vocabulary or review ledger was changed.

## Sequence annotations and the CRF decision

`src/course_parser/sequence_annotations.py` prepares blank TUNING packets and
audits manually labeled copies. The release contains **18 windows and 6,412
source units, zero adjudicated labels**. Units include course-shaped negatives,
page furniture and context pages, not only the keyword-bearing descriptions.

A reviewer may split a mixed-field source node into contiguous character spans.
Every span needs a semantic label, source owner where appropriate, transition
cue and boundary-after relation. Code/title/description/credits/prerequisites,
administrative text, separators, noncourse text, sections, furniture and source
insufficiency are distinct. `owner_start_node_id` points to the printed entry's
first header node; it is not a new extraction identifier.

The audit checks full source coverage, order, exact text/evidence, field
transitions, owner changes and declared distinct human reviewer/adjudicator
identities. It does not authenticate those identities. Pending labels never
count as training data. Unconfirmed page order cannot become confirmed merely
by editing an annotation flag: review and version the source order first.
If the owner begins outside the rendered window, materialize sufficient context
before claiming complete ownership. Source insufficiency stays separate from
noncourse negatives.

No CRF was installed or trained. Once suitable sequence labels exist:

1. Review whole windows, including requirements, references, creditless courses,
   historical numbering and continuations. Adjudicate uncertain boundaries.
2. Obtain a new institution-grouped sequence validation cohort; these exports
   contain TUNING only. Keep overlapping PDF pages and catalog families together.
3. Derive coarse course/reference/context sequences first, then finer field
   sequences inside owned courses. Mixed-field lines need span/token labels.
4. Test a small linear-chain CRF with geometry, spacing, font/style, lexical
   shapes and label cues. Exclude institution identity, source paths and the
   keyword targets as shortcuts. Preserve the hard transition constraints in
   decoding, and evaluate exceptions against source evidence.
5. Compare against the constrained baseline using complete-course recall,
   false ownership, contaminated descriptions, boundaries and abstentions.
   Token accuracy alone cannot justify adopting the CRF.

## Reproduce

Use a fresh output directory and the reference project's Python environment:

```sh
PYTHONPATH=src "$COURSE_PARSER_PYTHON" scripts/compare_sequence_constraints.py \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --pilot artifacts/source-learning-v4/pilot \
  --before artifacts/ownership-order-v2/release-full/source_rows/predictions \
  --output artifacts/my-sequence-comparison

PYTHONPATH=src "$COURSE_PARSER_PYTHON" -m course_parser export-sequence-templates \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --pilot artifacts/source-learning-v4/pilot \
  --predictions artifacts/my-sequence-comparison/predictions \
  --output artifacts/my-sequence-templates

PYTHONPATH=src "$COURSE_PARSER_PYTHON" -m course_parser audit-sequence-reviews \
  --templates artifacts/my-sequence-templates \
  --reviewed /absolute/path/to/manually_reviewed_sequences.jsonl \
  --output artifacts/my-sequence-readiness.json
```

The release root viewer links paired source images, source-bound proposals,
transition violations and annotation readiness. Hash verification binds the
prediction, evaluation and blank annotation artifacts. The broader quality goal
remains unmet; these changes make the remaining uncertainty explicit.
