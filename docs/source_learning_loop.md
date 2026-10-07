# Source-reviewed learning loop

The current Phase 13 protocol is [evaluation_design_phase13.md](evaluation_design_phase13.md).
Historical VALIDATION results below are exposed development diagnostics, including
the original 15 courses. They do not establish untouched generalization. Existing
partition tags remain for reproducibility; new outputs identify their diagnostic
scope explicitly. The reserved cohort remains sealed until all 18 phases are
complete and the parser is frozen; a freeze alone does not authorize opening it.

We now have software that can show **where the parser fails**, train a small
experiment from existing source reviews, and prepare the next pages to inspect.
The parser is still weak. Measuring that weakness is the first useful result.

The latest 0.7 increment adds source-span transition checks and competing
boundary proposals. Reviewed extraction remains 25/52 complete core courses;
VALIDATION remains 0/15. Blank sequence annotation packets are ready, but there
are no adjudicated sequence labels and no trained CRF. See
[the transition/boundary implementation and current measurement](sequence_boundaries_v3.md).

## Our quality goal

The versioned project targets are in
[`config/parser_quality_targets_v1.json`](../config/parser_quality_targets_v1.json).
They are proposed research-use requirements, not current performance or an industry
standard. We aim for at least 99% course precision and 95% complete-course recall
on independently source-reviewed, held-out institution families. A complete course
must have the source-observable printed identifier/title, its complete owned
description and boundaries/continuations, with printed credits/prerequisites kept
separate. Finding a header alone does not count. Abstentions and parser failures
count as misses when the source supports a complete answer.

For the frozen keyword queue, the separate targets are 99.5% precision and 95%
recall for COURSE_OWNED proposals with the correct owner and title/description
field. Every field must be source-traceable and every original hit must remain
accounted for. Source-insufficient cases are reported separately, never converted
to negatives. No target permits model predictions to write authoritative reviews.

Our first milestone is 50 diverse source-reviewed page windows, followed by at
least 90% course precision and 80% complete-course recall on a separate development
validation institution cohort. The final independent benchmark starts with at
least 1,000 source-complete courses across 20 institution families; its size must
grow if uncertainty prevents establishing the targets. Report every supported
catalog category separately. Random sampling beyond keyword pages is necessary
for catalog-wide recall; active-learning/challenge scores are separate diagnostics.

## What the first real pilot found

The completed pilot is under `artifacts/source-learning-v4`; its final viewer is
in `evaluation-final/index.html`. Its 20 reviewed central pages
contain 52 independent course headers. The baseline produced 605 header candidates;
48 can be uniquely aligned to reviewed course headers, but none of those became a
matched course record. Four true headers were missed. This points to entry
classification, spurious header candidates and page structure as urgent work.

The entry-ranking experiment fitted 471 source-aligned candidate examples from
tuning institutions and measured 132 from separate development-validation
institutions. At its fixed 0.5 threshold it found all 13 labeled validation positives
but also produced 61 false positives. That is about 18% precision **among the scored
candidate examples**, not course-parser accuracy. It is an experimental review aid;
we have not enabled it to attach descriptions or decide keyword ownership.

These answers reuse existing source reviews.
Their reviewer and second-review/adjudication fields are retained. They have not
been independently recertified as human gold. These are exposed DEVELOPMENT
diagnostics, not evidence of performance on unseen universities.

## Measured contextual increment (2026-10-06)

The paired release is `artifacts/contextual-parser-v1/release`. Open
`comparison/index.html` for the comparison and links to the source images.
Both versions were scored with the same v2 scorer, the same 20 complete reviewed
page inventories and the same 52 course answers. Eight pending pages were not
used as negatives. Predictions were sealed before answers were imported.

| Result | Structural baseline | Contextual assembly |
| --- | ---: | ---: |
| Course proposals uniquely aligned to reviewed courses | 0 / 52 | 47 / 52 |
| Exact owned descriptions | 0 | 31 |
| Exact blocks with confirmed extent and continuation | 0 | 33 |
| Complete code, title, description and boundaries jointly | 0 / 52 | 12 / 52 (23.1%) |
| Complete entries including source-resolved credits/prerequisites | 0 / 38 | 5 / 38 |
| False course proposals | 0 | 0 |
| Ambiguous course-proposal alignments | 0 | 1 |

The conservative identification precision is 47/48 (97.9%); this measures whether
the proposal corresponds to an independent course, not whether all its fields
are correct. All 15 reviewed zero-course pages still produced zero courses.
Four of five courses reviewed as having no explicit credits were located, but
none passed the complete-content check. All 28 proposal windows completed without
a stage failure. The test suite has 92 passing tests, including 11 contextual
whole-entry and measurement regressions; Ruff check and formatting passed.

TUNING recovered 36/37 courses and 12/37 complete core entries. VALIDATION recovered
11/15 courses but **0/15 complete core entries**. That shows the rules do not yet
transfer reliably. The milestone is not met. In particular, fragmented/superscript
titles, multi-part identifiers, OCR reading order, administrative prose and
source-confirmed metadata absence still need work. Strict source comparisons
retain these failures instead of hiding them with fuzzy matching.

No neural training occurred in this increment, and the original 18 keyword-hit
decisions remain pending. The authoritative review ledger and frozen vocabulary
were not changed. The release manifests retain source/code hashes and the
`verification_and_strata.json` file records integrity checks and the hard-negative
and creditless-course diagnostics. A final independent benchmark is still needed.

## The next small task

Open the eight pending keyword windows listed first in `review-queue/review_queue.jsonl`.
Each has its central image, neighboring images, frozen text, original keyword IDs
and offsets. Review the central image first and inventory **every independent course**,
including courses without a keyword. A degree requirement or elective list is not
an independent course description.

For each course, preserve its printed code/title and separate description, credits
and prerequisites. Record source evidence, boundaries, continuation and text that
must not attach. If the visible window does not establish the end, retain unresolved
status and obtain more context before claiming completeness. Do not manufacture an
absent description from a parser failure.

`pilot/page_review_template.jsonl` contains valid blank PageReview records;
`pilot/course_truth_template.jsonl` is deliberately empty. The full PageReview and
CourseTruth JSON schemas are in `pilot/review_schema.json`. These templates contain
no inferred answers. Fill source evidence with `MANUAL_SOURCE_REVIEW`, the exact
source document ID/checksum, physical page, source quote and image geometry where
available. Mark a complete inventory only after inspecting all source-visible
courses; a zero-course inventory must also be source reviewed.

The software validates and imports these records into a **new evaluation output**.
It does not submit them to the reference project's review ledger. Every original
keyword hit still needs its own COURSE_OWNED, NONCOURSE or SOURCE_INPUT_INSUFFICIENT
disposition through the existing direct-source-review workflow. Missing evidence
must retain SOURCE_EVIDENCE_INCOMPLETE in that workflow.

## Reproduce a release

Run from the Course Parser workspace with the existing reference project's Python:

```sh
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src
export COURSE_PARSER_REFERENCE_PROJECT=/Users/mohinuryusufjanova/Documents/University_Course_Catalog_Scraper
export COURSE_PARSER_PYTHON="$COURSE_PARSER_REFERENCE_PROJECT/.venv/bin/python"

"$COURSE_PARSER_PYTHON" scripts/run_learning_pilot.py \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --partition-registry config/source_learning_partitions_v1.json \
  --output artifacts/my-new-learning-release
```

The output must be a new directory. Reusing a release path is refused. Preserve the
partition registry when adding pages so a tuning institution cannot move into
validation. The source-review snapshot, identity overlay, DEVELOPMENT document
inventory, exclusion metadata and vocabulary are pinned in `learning.py`; their
hashes are recorded. A new corpus or review snapshot needs an explicit new protocol.

Individual commands are also available:

```sh
"$COURSE_PARSER_PYTHON" -m course_parser verify-learning-pilot \
  --run artifacts/my-new-learning-release/pilot

"$COURSE_PARSER_PYTHON" -m course_parser run-learning-baseline \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --pilot artifacts/my-new-learning-release/pilot \
  --output artifacts/my-next-parser-proposals

"$COURSE_PARSER_PYTHON" -m course_parser score-learning \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --pilot artifacts/my-new-learning-release/pilot \
  --predictions artifacts/my-next-parser-proposals \
  --page-reviews /absolute/path/to/completed-page-reviews.jsonl \
  --course-truth /absolute/path/to/source-reviewed-courses.jsonl \
  --output artifacts/my-next-evaluation

"$COURSE_PARSER_PYTHON" -m course_parser train-entry-ranker \
  --evaluation artifacts/my-next-evaluation \
  --output artifacts/my-next-entry-experiment
```

Custom answer files are imported as a complete supplied subset; include the existing
selected-page reviews you intend to keep scoring alongside new reviews. Both review
and truth files are required. Unreviewed pages remain pending. TUNING examples fit
weights; VALIDATION examples are measured only. Never train on the protected
CALIBRATION/TEST pools or the earlier locked pilot.

To compare parser versions, use identical pilot and answer-key files for both runs:

```sh
"$COURSE_PARSER_PYTHON" -m course_parser compare-learning \
  --before artifacts/my-old-evaluation \
  --after artifacts/my-next-evaluation \
  --output artifacts/my-paired-comparison
```

The optional `--repair-prose-columns` baseline flag is an experiment. It changed
layout proposals on this pilot without improving course recall. Comparison results
are diagnostic and never automatically approve a production change.

## Contextual course assembly (version 0.5)

The next increment is documented in [Source ownership and ordering, version
0.6](ownership_order_v2.md), including the controlled real-engine comparison.
The original version 0.5 measurements below remain historical; current contextual
runs default to `source_rows` ordering.

The opt-in `--contextual-course-assembly` path improves whole entries. It reads
checksum-verified text geometry and printed font flags, uses persistent gutters
to separate prose columns, and preserves every original source fragment. Compact
tables retain their original layout. Code and metadata typography no longer
automatically create section boundaries. Repeated supported headers with their
own bounded prose can establish independent courses without requiring credits.
An explicit requirement/reference container takes priority over repeated shapes.

Header assembly handles split titles, credit fragments between codes and titles,
small-cap titles followed by prose on the same line, and literal compound
historical identifiers. Prerequisite references cannot create new courses.
Fields partition every character of the owned source into code, title, description,
credits, prerequisites, other metadata or separators. Contact-hour and offering
continuations stay separate from descriptions. A page-window edge remains
unresolved unless the adjacent verified page establishes a structural endpoint.
The existing resolver handles evidenced cross-column and cross-page continuation.

These changes adapt the staged layout/postprocessing pattern in
[Docling's layout postprocessor](https://github.com/docling-project/docling/blob/main/docling/utils/layout_postprocessor.py)
and the separate region/ordering/assembly responsibilities in
[PaddleX's layout parsing pipeline](https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/inference/pipelines/layout_parsing/pipeline_v2.py).
The joint grouped-record evaluation is informed by
[DocILE](https://github.com/rossumai/docile): a found header alone cannot make a
record correct. These are domain-specific deterministic adaptations; no Docling,
Paddle, LayoutLM or other neural model was trained or run for this increment.

The scorer now reports complete content (code, title, description, confirmed exact
block, page extent and continuation jointly) and complete all-fields records
(also credits and prerequisites). It keeps exact raw source comparisons modulo
whitespace. Empty unrecovered metadata never earns source-confirmed absence.
Missing courses and failed reviewed pages remain in the eligible denominator.
Conservative course precision includes ambiguous proposals in its denominator.
The comparison refuses different answer keys, pilots, partitions or scoring code.

Reproduce this increment with a fresh output path:

```sh
"$COURSE_PARSER_PYTHON" scripts/compare_contextual_parser.py \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --pilot artifacts/source-learning-v4/pilot \
  --baseline artifacts/source-learning-v4/baseline \
  --output artifacts/my-contextual-comparison
```

This seals predictions without reading answers, then scores both versions with
the same scorer and writes an HTML comparison linking to real source images,
proposed fields and remaining errors. `score-learning --partition TUNING` imports
only tuning answers during rule development. VALIDATION is scored after the rules
and tests are frozen. Both partitions remain exposed development diagnostics.

## What to improve next

First inspect the withheld true headers beside the false header candidates on
tuning pages. Add source-grounded features that distinguish a course's own prose
from requirements lists and neighboring columns. Confirm header/code/title
assembly before training a model to attach a description. Then test complete block
boundaries and continuation, followed by field extraction and exact keyword
ownership. Improve one stage at a time, save a new version, and compare it against
the same source-reviewed answers.

More labels alone will not fix text from the wrong column or a damaged PDF encoding.
Those failures should route to the existing Docling/PP-Structure interfaces or a
verified OCR run. Preserve the frozen text and original hits separately. Neural
training and whole-corpus deployment remain future work after the source reviews
and component measurements justify them.
