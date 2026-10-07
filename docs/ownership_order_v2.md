# Source ownership and ordering, version 0.6

This increment improves extraction within the existing v3 source contracts.
It does not change the frozen vocabulary, authoritative review ledger or scorer.

## What changed and why

The source-reviewed biology page demonstrated two failures. Credits next to a
code were omitted when a title followed below it: the reference builder began
its traversal after the last title node. A small credit cell was restored from
page furniture after reading order had already been constructed, leaving that
cell disconnected. Different-height OCR boxes also sorted credit fragments
before their course code.

`OwnedCourseBlockBuilder` fills a code-to-title gap only along a connected,
same-column path of numeric/unit fragments overlapping the printed code row.
Other headers, unknown prose or an unresolved path prevent that attachment.
The source remains in exact, ordered block segments. An ownership audit rejects
source mutation and a source node claimed by two courses, including after
continuation resolution. It does not change boundaries to make a score pass.

Field decoding considers the complete header metadata row before assigning
fragments. `3 cr. 4 hr.` yields printed credits separately from contact hours.
Bounded eligibility sentences and administrative prose spanning fragments stay
outside descriptions. A printed `Pre` plus soft hyphen followed by `requisite:`
can label prerequisites across nodes while every source character stays in its
original allocation. Empty fields remain `NOT_RECOVERED`; the parser cannot
manufacture source-reviewed absence. Existing requirement-container rejection
and support for courses without explicit credits remain in place.

`source_rows` reconstructs the full stream after role repair. It orders columns
within spanning-element zones and then clusters fragments by vertical overlap,
center proximity and disjoint horizontal extent. This addresses mixed OCR box
heights without combining source strings or correcting characters. Genuine or
uncertain table cells retain their table layout. The original experimental
prose-column-only path remains available; its previous zero gain is not counted
as a new improvement.

## Lessons applied from the libraries

[Docling's postprocessor](https://github.com/docling-project/docling/blob/main/docling/utils/layout_postprocessor.py)
separates layout clusters from source cells and handles unassigned cells
explicitly. Our bridge assigns each native cell to a unique model region using
geometric containment, retains ambiguous/orphan cells and marks their order
unresolved. Model text never replaces native source text. Native heading roles
can disagree with an engine's page-furniture label; those cells survive and the
disagreement is reported. Missing table-grid information is separate from
whether the document tree supplies a valid region order.

[PP-Structure's pipeline code](https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/inference/pipelines/layout_parsing/pipeline_v2.py)
separates layout detection, OCR, block assembly and ordering. For this controlled
experiment we run its actual detector and installed, version-pinned XY-cut code
with verified native text. This isolates ordering from OCR quality and expense.
Both external paths use the common native roles, cells, within-region row sort,
course classifier, block builder and field decoder. These are hybrid pipelines,
not an end-to-end competition between the libraries.

## Fair comparison and limits

The paired diagnostic uses the same 20 reviewed DEVELOPMENT windows, 52 courses,
source hashes, answers and unchanged v2 scorer. Each external engine processes
the same central and adjacent pages: 60 pages. The remaining eight pilot windows
have pending hit reviews and are excluded from the course accuracy denominator.
External-engine exports are intentionally limited to the reviewed cohort; their
missing exports on those eight unreviewed windows are explicit stage failures,
not source negatives or measured extraction failures.

All predictions are sealed before the comparison imports answers. Parser rules
were developed using TUNING evidence. VALIDATION is reported after freezing the
rules; its previously exposed status prevents an independent accuracy claim.
Protected CALIBRATION/TEST and locked pilot answers are not opened. Existing
source reviews are not a substitute for independent human certification.

Complete core content jointly requires exact printed code, title, description,
confirmed exact owned block, page extent and continuation. Complete all fields
also requires correct credits and prerequisites. Missing courses, abstentions,
unconfirmed boundaries and unrecovered absence cannot earn correctness. Matching
remains exact modulo the scorer's existing whitespace policy; source OCR errors
and differences in printed characters remain failures.

Docling 2.102.2 uses its default Heron layout model on original PDF windows, with
OCR, table extraction and remote services disabled. PP uses the frozen 100 dpi
page images, the recorded detector configuration and PaddleX 3.5.2's official
XY-cut. PP full OCR attempts were interrupted after their recognition stage
took about 35 seconds per page; their partial outputs are excluded. The default
PP-DocLayout_plus-L detector and the documented PP-DocLayout-S variant are tested
separately using native text. Library versions, model weights, source algorithms
and producer hashes are retained in the engine artifacts.

## Measured result

On the identical 20 reviewed pages and 52 courses:

| Pipeline | Located | Complete core | Complete all fields |
| --- | ---: | ---: | ---: |
| Previous contextual parser | 47/52 | 12/52 | 5/38 |
| Improved ownership, previous order | 47/52 | 22/52 | 9/38 |
| Improved ownership, source rows | 47/52 | 25/52 | 10/38 |
| Docling region order with common native rows | 30/52 | 9/52 | 0/38 |
| PP default detector and official XY-cut with native rows | 5/52 | 0/52 | 0/38 |
| PP small detector and official XY-cut with native rows | 0/52 | 0/52 | 0/38 |

The ownership change adds ten jointly correct core records; ordering adds three
more. Complete core recall rises from 23.1% to 48.1%, while conservative
identification precision stays 47/48 = 97.9%: zero known false-course proposals
and one ambiguous proposal. Exact descriptions rise 31 → 34 and confirmed exact
blocks 33 → 44. Fifteen reviewed negative pages still produce no courses.

All gains occur in TUNING: 25/37 complete core records. VALIDATION remains 0/15,
with 11 courses located. The milestone has not been achieved. In particular,
OCR character differences, fragmented titles, source-confirmed absence and
broader ownership/boundary generalization still need work.

Both external engine families executed all 60 reviewed source pages with no
producer failures. Their integrations leave uncovered/ambiguous native cells:
Docling has 21 partial page layouts, default PP 46 and small PP 53. The existing
conservative contract then prevents complete extraction on those layouts.
These measurements show the configured integrations are not ready to replace
the native path; they do not establish universal library accuracy. The small PP
detector, for example, found only a footer and a header on the reviewed biology
page. Its zero course output is not evidence of perfect rejection.

The complete comparison and source viewers are in
`artifacts/ownership-order-v2/release-full/`. The separate
`verification_and_strata.json` records artifact checks, negative/creditless
coverage, pending original hits and the external runtime provenance hashes.
The full test suite has 100 passing tests; Ruff passes. These checks establish
regression coverage and source integrity, not production accuracy.

## Reproduce

Use the reference project's Python for parser evaluation and an isolated engine
environment for Docling/Paddle. The renderer must be pinned to the source pilot's
PyMuPDF 1.27.2.3; an initial 1.28.2 installation failed pixel verification and was
replaced. Source verification remains enforced.

```sh
PYTHONPATH=src "$COURSE_PARSER_PYTHON" -m course_parser run-learning-baseline \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --pilot artifacts/source-learning-v4/pilot \
  --output artifacts/my-owned-source-rows \
  --contextual-course-assembly --ordering-engine source_rows

# Run in the isolated optional engine environment; use fresh output paths.
PYTHONPATH=src .venv-engines/bin/python scripts/export_docling_pilot.py \
  --pilot artifacts/source-learning-v4/pilot --output artifacts/my-docling-exports
PYTHONPATH=src .venv-engines/bin/python scripts/export_pp_layout_order_pilot.py \
  --pilot artifacts/source-learning-v4/pilot --output artifacts/my-pp-exports

PYTHONPATH=src "$COURSE_PARSER_PYTHON" scripts/compare_ownership_order.py \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --pilot artifacts/source-learning-v4/pilot \
  --before artifacts/contextual-parser-v1/release/predictions \
  --docling-exports artifacts/my-docling-exports \
  --ppstructure-exports artifacts/my-pp-exports \
  --output artifacts/my-ownership-order-comparison
```

Every producer refuses to overwrite a run. The comparison checks identical
pilot/answer/scorer hashes, keeps failures in scored denominators and provides
source-image viewers for every page. Engine installation and model execution
are optional; the default contextual parser needs neither neural runtime.

The next extraction work must target the remaining source-backed field and
boundary failures, followed by a new independent sample. Adding provenance IDs
or passing synthetic regressions alone cannot establish the research targets.
