# Course Parser

Phase 18 adds mandatory source-only HIGH/MEDIUM/LOW proposal routing and separate
review queues. All records still require human publication review. All 469 cached
records are preserved; zero reviewed false headers remains unchanged, with five
ambiguous alignments and one contaminated description still explicit. Bands are
uncalibrated and no extraction gain is claimed. The full suite passes 564 tests;
the permanent gate passes 154. The reserved cohort remains sealed and the parser
has not been frozen by this phase.
See the [Phase 18 report](artifacts/precision-phase18-v1/release-v1/index.html)
and [precision policy and measurement limits](docs/precision_phase18.md).

Phase 17 adds mandatory whole-page accounting for every extracted native cell and
newline row, including unowned and unresolved text. The permitted cache audit
retained 25,086 rows and 581,776 characters across 68 exposed windows; all 469
cached course records remain unchanged. Unresolved rows and empty inventories
stay visible. The full suite passes 520 tests; the permanent gate passes 110.
This strengthens retention checks without claiming extraction accuracy gains.
The reserved cohort remains sealed.
See the [Phase 17 report](artifacts/page-accounting-phase17-v1/release-v4/index.html)
and [accounting contract](docs/page_accounting_phase17.md).

Phase 16 makes the permanent regression gate explicit: 82 checks cover all twelve
suggested behaviors plus exposure and annotation safeguards. Seventeen permitted
source fixtures are pinned; source replays protect 76 existing recovered gold-ID
observations rather than completion counts alone. Missing obligations and skipped
checks fail the complete gate. Parser and strict scoring code are unchanged, and
the reserved cohort remains sealed.
See the [Phase 16 report](artifacts/regression-phase16-v1/release-v1/index.html)
and [regression commands and maintenance policy](docs/permanent_regressions_phase16.md).

Phase 15 adds a reproducible mixed annotation queue for TUNING. Its first batch
contains four failure windows, three style-coverage windows and three random
windows: 3,362 source units across seven institution families. All targets remain
blank. The 40/30/30 allocation is configurable and is a starting policy, not a
proven optimum. Source images and the existing human-review audit support the
next annotation cycle; no model is trained or accuracy gain claimed. The reserved
cohort remains sealed.
See the [Phase 15 annotation batch](artifacts/active-annotation-phase15-v1/release-v2/index.html)
and [selection policy](docs/active_annotations_phase15.md).

Phase 14 adds institution-family diagnostics and fresh entry-ranker folds using
TUNING only. Seven families have candidate labels; five additional TUNING families
are explicitly skipped. On 702 candidates, ranker F1 is 53.2% versus 90.2% for
existing decisions, with 131 false positives versus zero. The ranker remains
experimental. Cached strict course scores stay **66/128** core and **13/97** all
fields. These exposed diagnostics do not establish untouched generalization;
the reserved cohort remains sealed.
See the [Phase 14 report](artifacts/institution-out-phase14-v1/release-v3/index.html)
and [institution-family evaluation](docs/institution_out_phase14.md).

Phase 13 makes legacy VALIDATION explicitly an exposed diagnostic in new metadata
audits and ranker output. It adds transitive checks for institution aliases,
declared catalog families, shared PDFs and overlapping context windows; the
scoring CLI now checks development metadata before answer import. Existing
scores and partition assignments remain unchanged. The reserved cohort stays
sealed; no new extraction or generalization gain is claimed.
See the [Phase 13 report](artifacts/evaluation-design-phase13-v1/release-v1/index.html)
and [evaluation design](docs/evaluation_design_phase13.md).

Phase 12 prepares a narrow field-role experiment with shared source words, relative
geometry, native typography and page images. Its three prospective arms compare a
simple classifier with LayoutLMv3 with and without images. Training and inference
remain deferred: the six prepared TUNING windows have zero adjudicated labels.
No extraction gain is claimed, and no learned proposals enter the parser.
See the [Phase 12 report](artifacts/layoutlm-readiness-phase12-v1/release-v3/index.html)
and [field experiment contract](docs/field_experiment_phase12.md).

Phase 11 keeps CRF training deferred and strengthens sequence annotation integrity.
Both TUNING packets contain 18 windows / 6,412 source units and zero adjudicated
labels. A six-window source-review queue, human order checks, uncertainty and
source-insufficiency gates are implemented. No extraction gain is claimed.
See the [Phase 11 report](artifacts/crf-readiness-phase11-v1/release-v4/index.html)
and [annotation contract](docs/sequence_boundaries_v3.md).

Phase 7 adds source-bound scores and review alternatives to the existing
boundary competition. Prerequisites remain owned course metadata; gaps and
column changes cannot establish an endpoint alone. Conflicting verified extents
still require review because numerical score margins are not calibrated.
Measured complete core courses remain **35/52** and **66/128** on the original
and expanded exposed DEVELOPMENT pilots, with zero measured false proposals.
This is a diagnostic improvement, with no demonstrated extraction gain.
See the [Phase 7 report](artifacts/boundary-phase7-v1/index.html) and
[boundary implementation notes](docs/sequence_boundaries_v3.md).

The current phase workflow is **development-only**. The user instruction dated
2026-10-06 keeps the reserved institution-held-out cohort sealed until **all 18
phases are complete and the parser is frozen**. Do not inspect, open, score,
summarize, or use its source material or review answers. This replaces the earlier
opening gate; an earlier parser freeze alone does not permit access. The active
contract is [development_phase_execution_v1.json](config/development_phase_execution_v1.json).
Phase work may use TUNING, DEVELOPMENT, existing exposed diagnostics, regression
cases and permitted source material. Historical report instructions that suggest
opening the reserved cohort are superseded by this contract.

Phase 2 adds exact field scores alongside the unchanged strict completion score.
It reports end-to-end field delivery, conditional scores, pooled and per-field
averages, explicit source-unresolved counts, and a separate required-continuation
measure. Missing records remain in delivery denominators; unadjudicated start/end
anchors receive no invented points. Run `python -m course_parser.field_scoring
--evaluation <approved_exposed_evaluation> --output <new_output_directory>`
with `PYTHONPATH=src`. Only registered, checksum-pinned exposed evaluations are
accepted. The aggregate Phase 2 builder preserves its historical parser-freeze
requirement; the Phase 3 report below covers the current implementation.

Phase 3 improves ownership using demonstrated source failures. A split title
claim that overlaps another supported inline header is withheld for review.
Embedded comma-separated code lists inherit their printed body-text list
introducer and remain references. Source-span field relationships are exported
with existing course IDs, exact text, evidence and field statuses; double
ownership and source mutations still fail verification.

Version 0.11 recovers five additional complete core courses on the expanded
exposed sample: **36/128 → 41/128**. All-field completion improves **10/97 → 13/97**;
known false proposals remain zero and failed source windows fall from two to
zero. The original sample remains **35/52**, and ambiguous proposals on the
expanded sample increase from two to four as a recovered page exposes joint
headings. The gain comes from one TUNING page and does not establish transfer to
unseen institutions. See [the Phase 3 assessment and measured results](artifacts/ownership-phase3-v1/index.html).
Reproduce proposals and scoring with `scripts/measure_phase3_ownership.py`; its
input pilots are explicitly restricted to the two exposed DEVELOPMENT samples.
Use `scripts/build_phase3_ownership_report.py` for the accepted result and source
review inbox. The reserved cohort remains sealed under the 18-phase contract.

The first development milestone is implemented: a source-evidence bridge between
the frozen physical keyword-hit queue and the existing course-extraction v3 data
contracts. A second stage now adds PDF text geometry and imports Docling layout
proposals into those verified page windows. Version 0.3 adds PP-StructureV3
layout/OCR imports, review routing, engine comparisons, and an optional neural
producer. Version 0.4 connects the existing structural course parser to a real
DEVELOPMENT pilot, source-review diagnostics, a supervised entry-ranking
experiment, and the next review queue. Course ownership decisions remain separate.
Version 0.5 adds contextual course assembly and a paired complete-entry evaluation:
column flow, source typography, repeated entry patterns, split titles, inline
descriptions, metadata separation and evidenced endpoints. Its records remain
review-required. Use `scripts/compare_contextual_parser.py` to reproduce the
before/after measurement against the same verified pilot and source answers.

Version 0.6 improves source ownership inside those contracts: it recovers
same-row credits between code and title, keeps contact hours and administrative
text separate from descriptions, decodes fragmented prerequisite labels, and
rejects double ownership or changed source text. It rebuilds reading order after
role repair using overlapping printed rows. The contextual path now defaults to
this `source_rows` order; `--ordering-engine legacy` isolates ownership changes.
Actual Docling and PP layout/XY-cut outputs can be projected onto the same native
cells for a controlled ordering comparison. See [the implementation and measured
comparison](docs/ownership_order_v2.md).

Version 0.7 adds explicit source-span transition constraints, competing
stop/continue proposals, ambiguity withholding and blank sequence annotation
packets for future CRF experiments. The same reviewed cohort remains at 25/52
complete core courses and 10/38 complete all-field entries. See the
[implementation, measurement and CRF prerequisites](docs/sequence_boundaries_v3.md).
Run `scripts/compare_sequence_constraints.py` for the paired source viewer.

Version 0.8 adds an independent omission audit using historical identifiers,
headers without subject codes, source typography/context, repeated entry shapes,
and uncovered prose. It preserves frozen text, native crop slices, separate OCR
outputs and explicit crop-to-page/PDF transforms, then routes disagreements to a
blank local source-review inbox. See [the commands and evidence contracts](docs/omission_extractions_v1.md).
This diagnostic stage does not automatically accept courses or change measured
extraction accuracy.

Version 0.9 preserves original cells, rows and engine layout hypotheses alongside
their adopted interpretation; separates inventory rows, requirements and
unresolved grids; and prevents group distribution totals and degree abbreviations
from establishing course ownership. It expands the pilot to 50 reviewed pages
plus 18 pending windows while retaining random samples and protected partitions.
On the same expanded reviewed cohort, known false courses fall from 2 to 0;
complete core extraction remains 26/128. Six actual catalog regressions now
exercise source evidence, ownership and boundaries. See
[implementation, review selection and paired measurement](docs/table_ownership_review_v1.md).

The existing project remains the reference implementation. This workspace loads
its matcher, typed evidence models, and page renderer through a small adapter;
it does not modify that project or its review ledger.

## What works now

- Validate the complete frozen release before selecting a batch: artifact hashes,
  unique task ranks, source identities, original hit IDs, hit-to-task membership,
  source spans, and blank disposition templates.
- Validate the authoritative vocabulary against the existing v2 matcher:
  33 concepts and 68 written forms, including its normalization and overlap policy.
- Render a bounded rank interval with the existing renderer, or connect an
  existing rendered packet.
- Verify the PDF and frozen text-stream checksums. Verify each packet image by
  comparing its decoded pixels with a fresh render of the verified PDF.
- Export typed v3 `PageSource` and `EvidenceAnchor` records. Each anchor's exact
  text must equal the corresponding slice of the original frozen page text.
- Preserve overlapping hits, original spans, IDs, source checksum, and spelling
  labels. Semicolon-joined approved labels remain one physical hit; the exact
  source quote is stored separately.
- Keep missing, damaged, or inconsistent evidence visible as
  `SOURCE_EVIDENCE_INCOMPLETE`, with every original hit still accounted for.
- Produce a static page viewer, coverage counts, a blank disposition template,
  and a manifest of input, output, reference-code, and software hashes.
- Verify exported artifacts and their internal relationships independently.

`SOURCE_EVIDENCE_READY` means the evidence bridge's checks passed. It says nothing
about whether the keyword belongs to an independent course. Every hit remains
`PENDING_SOURCE_REVIEW`, with a blank decision and no proposed owner.

## Docling concepts now applied

The first increment adopted a staged, source-preserving architecture, typed page
evidence, and explicit failure accounting. It did not run Docling or analyze page
structure. Version 0.2 adds the following concrete applications of the technical
report's processing pipeline, PDF-backend boundary, and typed assembly design:

| Docling lesson | Implementation | Effect on our parser |
| --- | --- | --- |
| Keep PDF parsing separate from model processing | `pdf_backend.py`: `PageBackend`, native character/word cells and font information | Later layout engines share source geometry instead of replacing the source text |
| Preserve text cells and coordinates alongside page images | Upright coordinate normalization, including 0/90/180/270-degree rotation | Original hits can receive exact geometric evidence when frozen/native text agrees |
| Apply page models before document assembly | `layout_stage.py`: backend, optional Docling import, assignments, typed output and QC | Source failures and model failures remain explicit per page |
| Group PDF text cells into predicted regions | Overlap-based assignments with separate assigned, ambiguous and orphan results | Unassigned words and competing regions cannot silently disappear |
| Represent reading order explicitly | `docling_adapter.py`: page-specific provenance and recursive body/group traversal | Multi-page exports stay separated; missing, cyclic or incomplete order stays unresolved |
| Keep table structure separate from text recovery | Logical rows/columns/spans, native-word-to-cell assignments, invalid-grid checks | A table can retain empty positions, spanning headers, and unassigned words |
| Assemble a typed intermediate document representation | Frozen Pydantic page predictions, source cells, tables, and separate hit geometry | Later entry/boundary/field stages receive auditable structure rather than flattened Markdown |

These are adaptations for the course parser, not a copy of Docling's entire
pipeline. Our cell-assignment thresholds are explicit development policy: at
least 50% of a word cell must overlap a region; near-tied competing overlaps stay
ambiguous. They need evaluation against reviewed development examples.

The importer reads exported `DoclingDocument` JSON directly, so consuming existing
outputs does not require Docling's neural runtime. It selects the explicitly bound
page, preserves model text separately from PDF and frozen text, normalizes declared
coordinate origins, checks the page aspect ratio and bounds, follows nested group
references, separates page furniture, and preserves table grids.

There is no fresh Docling model inference in this increment. The Docling package
and weights are not installed in the Python environment used here. OCR routing is
recorded for pages without native cells; OCR is not executed. Neural layout/OCR
accuracy and course ownership accuracy remain unmeasured. Six existing pilot
exports were parsed successfully (180 regions, five tables), which is a schema
compatibility check. Their source bindings were not revalidated by that check.

## PP-StructureV3 concepts now applied

The reference project already had an external-engine runner and a basic adapter
for PP-Structure block boxes. The new workspace had modular stages but did not
consume PP-Structure outputs. Version 0.3 connects this engine to the verified
page windows without replacing the native or Docling channels.

| Lesson from the documentation/code | Concrete implementation | Benefit for course parsing |
| --- | --- | --- |
| Separate layout, OCR, and specialized modules | `ppstructure_adapter.py`: independent layout and OCR representations; table/formula/visual review routes | A detected table or formula cannot silently become ordinary course prose |
| Keep detection and recognition evidence separate | Original OCR quadrilaterals, detection scores, recognition scores and exact model-text spans | Filtering can leave different array lengths; the parser never misattaches detection confidence to recognized text |
| Preserve recognition uncertainty | Low recognition scores, empty lines, ambiguous region assignments and orphan lines remain explicit | Weak OCR becomes a review cue rather than disappearing or being automatically corrected |
| Recover multi-column reading order | Consume the documented ordered `parsing_res_list`; check display-index contradictions; accept legitimate null `block_order` values | The importer retains PP-Structure's order, including non-numbered elements, without inventing a new sequence |
| Track preprocessing coordinates | Bind input-image dimensions and checksums; reject rotation/unwarping without a verified inverse transform | Model boxes cannot be overlaid onto the wrong source frame |
| Reuse pipeline instances and isolate failures | `ppstructure_runner.py`: one neural pipeline per run, per-page success/failure output | A failed page remains visible in the subsequent import instead of stopping or vanishing from a batch |
| Retain independent signals | Mutual unambiguous region alignment, role/text differences and body-order conflicts between Docling and PP-Structure | Disagreement guides review; agreement remains a model proposal and never becomes course ownership truth |

PP-Structure's upstream PaddleX pipeline applies enhanced XY-cut to regions and
blocks. Our optional producer delegates that processing to PP-Structure, and our
importer preserves its proposed order. It does not independently reproduce the
neural models or claim that a generic document order determines course boundaries.
Furniture is excluded from the cross-engine body-order comparison.

The OCR review threshold of 0.8 and comparison IoU/margin thresholds of 0.5/0.1
are recorded development settings, not calibrated accuracy estimates. Raw model
JSON and specialized outputs are archived. PP table HTML/cell geometry is not yet
converted into the typed logical table grid: table regions are routed for review.
Docling's existing logical-grid importer remains available.

Source-bound imports and the optional neural producer are implemented, but
PaddleOCR, PaddleX and PaddlePaddle are absent from the environment used for this
increment. **No fresh PP-Structure neural inference was run.** The automated
checks use explicitly synthetic contract fixtures and test doubles. There is no
measured improvement in course ownership, OCR or layout accuracy yet.

Primary sources checked for this implementation:

- [PP-StructureV3 documentation](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html)
- [PaddleOCR's pipeline wrapper](https://github.com/PaddlePaddle/PaddleOCR/blob/main/paddleocr/_pipelines/pp_structurev3.py)
- [PaddleX layout pipeline and ordering](https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/inference/pipelines/layout_parsing/pipeline_v2.py)
- [PaddleX result serialization](https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/inference/pipelines/layout_parsing/result_v2.py)
- [PaddleX preprocessor provenance](https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/inference/pipelines/doc_preprocessor/result.py)

## Run in this workspace

The commands below reuse the existing project's Python environment. No model
downloads or installation are required. The package requires Python 3.11 or newer,
PyMuPDF, and Pydantic; development checks use pytest and Ruff. The reference
project is an explicit runtime dependency for preparation.

```sh
cd '/path/to/Course Parser'
export COURSE_PARSER_REFERENCE_PROJECT='/Users/mohinuryusufjanova/Documents/University_Course_Catalog_Scraper'
COURSE_PARSER_PYTHON="$COURSE_PARSER_REFERENCE_PROJECT/.venv/bin/python"
COURSE_PARSER_RELEASE="$COURSE_PARSER_REFERENCE_PROJECT/outputs/supervisor_keyword_v2_page_review_tasks_v1"
COURSE_PARSER_VOCABULARY="$COURSE_PARSER_REFERENCE_PROJECT/outputs/supervisor_keyword_v2/supervisor_keyword_v2.json"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/src"
```

Audit the real metadata release without opening source PDFs:

```sh
"$COURSE_PARSER_PYTHON" -m course_parser audit-release \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --task-release "$COURSE_PARSER_RELEASE" \
  --vocabulary "$COURSE_PARSER_VOCABULARY" \
  --output artifacts/release-audit-new
```

An audit has already passed for the local authoritative release: **84,008 page
tasks and 149,226 physical hits**, with all hits accounted for exactly once. This
audits the queue metadata, not the quality of all source pages or course decisions.

### Inspect the synthetic demo

```sh
"$COURSE_PARSER_PYTHON" scripts/build_demo.py \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --output artifacts/demo-new
"$COURSE_PARSER_PYTHON" -m course_parser verify-run \
  --run artifacts/demo-new/evidence --recheck-inputs
"$COURSE_PARSER_PYTHON" -m http.server 8770 --bind 127.0.0.1 \
  --directory artifacts/demo-new/evidence
```

Open `http://127.0.0.1:8770/`. The synthetic fixture contains three PDF pages,
two page tasks, and six original hits, including overlapping phrases and a degree
requirement that references a course. All six dispositions remain pending.
The demo exercises evidence preparation; it does not demonstrate course ownership
accuracy. A generated example is in `artifacts/demo-v2/evidence/index.html`.

### Connect a real rendered batch

The original PDFs and frozen page streams must be accessible to the machine
running this command. Copying only the rendered page images is insufficient for
the checksum and pixel-binding checks. If Wendian paths have been copied under a
different local root, supply both mapping arguments. Otherwise omit both.

```sh
"$COURSE_PARSER_PYTHON" -m course_parser bridge-batch \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --task-release "$COURSE_PARSER_RELEASE" \
  --vocabulary "$COURSE_PARSER_VOCABULARY" \
  --packet-dir /absolute/path/to/rendered_batch \
  --map-remote-root /scratch/mohinur_yusufjanova/catalog_rebuild_20260824 \
  --map-local-root /absolute/path/to/copied_wendian_tree \
  --output artifacts/real-bridge-new
```

To render and bridge in one command, use `build-batch` with the same reference,
release, vocabulary, and optional mapping arguments, replacing `--packet-dir`
with `--start-rank 1 --count 20`. Its output has `source_packet/` and `evidence/`
subdirectories. Use a fresh output directory for every run. Existing directories
are deliberately never overwritten.

Exit codes: `0` means the operation passed; `2` means a completed evidence bundle
contains source gaps; `1` means an invalid input or preparation failure. An
incomplete bundle can pass `verify-run`: verification confirms that its recorded
gaps and accounting are consistent, not that its evidence is ready.

`verify-run --recheck-inputs` also checks the original inputs and reference code.
Without that flag, it checks the exported bundle's hashes, scope, task/hit
relationships, image hashes, source anchors, and blank dispositions. Moving a
bundle preserves its static viewer; input rechecking needs the original paths.

### Add PDF geometry and Docling proposals

```sh
"$COURSE_PARSER_PYTHON" -m course_parser enrich-layout \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --evidence-run artifacts/demo-v2/evidence \
  --output artifacts/docling-concepts-native-new
"$COURSE_PARSER_PYTHON" -m course_parser verify-layout \
  --run artifacts/docling-concepts-native-new --recheck-inputs
```

The native path opens the checksum-bound PDFs recorded by evidence preparation,
captures words, characters, fonts and coordinates, and retains both frozen and
backend text representations. Exact hit projection requires equality of the
entire frozen and backend page text. If they differ, geometry remains unresolved;
the stage does not correct text or guess offsets. PDF extraction order remains
explicitly unresolved as logical reading order.

To import Docling proposals, add `--docling-manifest /absolute/path/to/docling-input.json`.
An import manifest has the following structure, with real IDs and hashes supplied
by the producer of the Docling run:

```json
{
  "schema_version": "course-parser-docling-input-v1",
  "coordinate_frame": "VERIFIED_UPRIGHT_PAGE",
  "pages": [{
    "page_key": "SOURCE_ID:PDF_SHA256:p2",
    "source_pdf_checksum": "PDF_SHA256",
    "image_sha256": "VERIFIED_IMAGE_SHA256",
    "docling_page_no": 1,
    "engine_version": "RECORDED_DOCLING_VERSION",
    "document_json_path": "exports/page-2.json",
    "document_json_sha256": "DOCLING_EXPORT_SHA256"
  }]
}
```

Paths to exports are relative to the manifest directory and must stay inside it.
`docling_page_no` refers to the page within the Docling export; it can differ from
the catalog's physical page number when conversion used a single rendered image.
The export's coordinate frame must represent the verified upright page image;
unrecorded deskewing or cropping cannot be silently treated as that frame.
Model text and labels remain proposals, and a model-proposed reading order is not
human-confirmed order or course ownership truth.

The layout bundle adds `page_layout.jsonl`, `hit_geometry.jsonl`, a byte-for-byte
copy of the original hit inventory, `layout_qc.json`, `layout_manifest.json`, and
an overlay viewer. Blue rectangles show layout regions; orange rectangles show
exact original hit geometry. Imported JSON is retained under `docling_exports/`.
`verify-layout --recheck-inputs` also re-extracts PDF cells and checks projected hit
coordinates against the original characters. A completed stage can still contain
unresolved reading order, OCR routing, or model gaps; consult its QC and page records.
The generated native geometry example is in
`artifacts/docling-concepts-native-final/index.html`. It contains verified PDF
geometry and original hit overlays; it does not contain fresh neural predictions.

To repeat the stored-export compatibility check:

```sh
"$COURSE_PARSER_PYTHON" scripts/check_docling_exports.py \
  --exports-dir "$COURSE_PARSER_REFERENCE_PROJECT/outputs/course_layout_pilot_v1/run_v2_upright" \
  --output artifacts/docling-export-compatibility-new.json
```

The implementation was informed by the attached *Docling Technical Report*,
sections 3.1–3.4, and the current primary code for
[layout post-processing](https://github.com/docling-project/docling/blob/main/docling/utils/layout_postprocessor.py),
[reading-order assembly](https://github.com/docling-project/docling/blob/main/docling/models/stages/reading_order/readingorder_model.py),
and the [PDF pipeline](https://github.com/docling-project/docling/blob/main/docling/pipeline/standard_pdf_pipeline.py).

## Output contract

| Artifact | Purpose |
| --- | --- |
| `page_evidence.jsonl` | Unique context pages, typed representations, verified image binding, gaps |
| `task_evidence.jsonl` | Original page tasks, central/context relationships, readiness |
| `hit_evidence.jsonl` | Original hits, frozen spans, exact quotes, v3 anchors, pending state |
| `hit_disposition_template.csv` | Blank worksheet keyed by original task and hit IDs |
| `coverage.json` | Selected task/hit/page counts, readiness counts, explicit scope |
| `release_audit.json` | Full frozen queue metadata validation summary |
| `run_manifest.json` | Version, renderer binding, input/code/output SHA256 hashes |
| `index.html`, `assets/` | Static source viewer and verified page images |

The exported worksheet is a development artifact. It is not a submission format
for the existing direct source-review ledger; that workflow has its own commands
and adjudication requirements.

The scope is `BOUNDED_FROZEN_HIT_REVIEW_CONTEXT`. These are page windows, not
complete `SourceDocument` inputs for the semantic parser. The bridge never calls
the course inference pipeline and never permits catalog-level negative inference.
The separate 1,399 web hits and the 600-page development evaluation workflow are
outside this queue.

The bridge accepts documented embedded-text and OCR channel labels. An unknown
channel remains unresolved. When frozen embedded text differs from a fresh native
extraction, it is retained as manifest-supplied text; it is not relabeled as a new
native extraction. Image verification uses the existing renderer's scale of 2.5.
Different rendering versions that change pixels can conservatively block a page.
No geometry is guessed from a lexical hit, and no OCR character correction is used.

### Add PP-Structure proposals

Use an existing exported JSON result with a source-binding manifest:

```sh
"$COURSE_PARSER_PYTHON" -m course_parser enrich-layout \
  --reference-project "$COURSE_PARSER_REFERENCE_PROJECT" \
  --evidence-run artifacts/demo-v2/evidence \
  --ppstructure-manifest /absolute/path/to/ppstructure-input.json \
  --output artifacts/ppstructure-layout-new
"$COURSE_PARSER_PYTHON" -m course_parser verify-layout \
  --run artifacts/ppstructure-layout-new --recheck-inputs
```

Add `--docling-manifest` to the same command to retain both engines and produce
their comparison. Blue rectangles show the native/Docling channel, green show
PP-Structure proposals, and orange show exact original keyword spans. Both
engines remain proposals; frozen hit identities, source spans, text and decisions
are preserved.

The PP-Structure manifest describes **single verified rendered images**, not
direct PDF inference. It accepts wrapped `{"res": ...}` and unwrapped exports:

```json
{
  "schema_version": "course-parser-ppstructure-input-v1",
  "coordinate_frame": "VERIFIED_UPRIGHT_IMAGE",
  "input_kind": "RENDERED_PAGE_IMAGE",
  "pages": [{
    "page_key": "SOURCE_ID:PDF_SHA256:p2",
    "source_pdf_checksum": "PDF_SHA256",
    "image_sha256": "VERIFIED_IMAGE_SHA256",
    "image_width": 1500,
    "image_height": 2000,
    "engine_version": "RECORDED_PADDLEOCR_VERSION",
    "status": "EXPORTED",
    "result_json_path": "results/page-2.json",
    "result_json_sha256": "RAW_RESULT_SHA256"
  }]
}
```

Supply the exact decoded image dimensions. Paths must be relative to the manifest
directory and remain inside it. Exported `model_settings.use_doc_preprocessor`
must be explicit. Applied document orientation classification or unwarping is
rejected until a verified inverse transformation is supported. Malformed model
outputs cause `INPUT_INCOMPLETE` on the affected PP channel; the native/Docling
evidence and every original hit remain available. `verify-layout` reparses the
archived raw PP export, checks derived OCR spans, scores, assignments and engine
comparisons, and verifies original hit accounting.

When a separate environment has compatible PaddleOCR/PaddleX/PaddlePaddle
installed, the optional producer creates that manifest:

```sh
python -m course_parser run-ppstructure \
  --evidence-run /absolute/path/to/verified/evidence \
  --limit 20 --device cpu --output /absolute/path/to/ppstructure-run-new
```

Run from the workspace with `PYTHONPATH` pointing to its `src` directory.
The producer can download upstream weights when constructing the pipeline; it
records package versions, requested module settings, raw JSON, image/source
bindings and per-page failures. It disables document rotation/unwarping and uses
a recognition threshold of 0.0 to retain weak recognized text for review. It
enables tables and disables formula, chart and seal inference for this catalog
configuration. It never writes human dispositions or generates new frozen hits.
Failed page entries also enter the manifest as `status: FAILED` with the same
source/image identity and an error reason, so import preserves attempted failures.
With an unavailable runtime, the command fails before creating an output bundle.

The synthetic integration example can be regenerated with
`scripts/build_ppstructure_demo.py`. It exercises the import and evidence
contracts; it is not a neural benchmark.

## Structure and checks

`release.py` validates the frozen inventory; `reference.py` isolates reuse of the
existing project; `bridge.py` prepares and verifies evidence; `report.py` creates
the viewer; `cli.py` supplies command-line operations. The synthetic
fixtures use the actual reference models, matcher, and renderer.

```sh
"$COURSE_PARSER_PYTHON" -m pytest -c pyproject.toml tests -q \
  --reference-project="$COURSE_PARSER_REFERENCE_PROJECT"
"$COURSE_PARSER_REFERENCE_PROJECT/.venv/bin/ruff" check src tests scripts
"$COURSE_PARSER_REFERENCE_PROJECT/.venv/bin/ruff" format --check src tests scripts
```

The tests cover overlapping hits, pending dispositions, typed source anchors,
missing and damaged images, incorrect image pixels, missing PDFs, changed source
streams, invalid original quotes, modified vocabulary, malformed inventories,
altered artifacts, path traversal, HTML escaping, CLI execution, and refusal to
overwrite. They also check semantic inconsistencies after artifact rehashing.

## Source-reviewed learning loop

The working loop is: **verified source windows → independent parser proposals →
source-reviewed answers → error report → supervised experiment → next review queue**.
See [the operational guide](docs/source_learning_loop.md) for the commands and
review requirements. The real pilot uses locally mirrored DEVELOPMENT PDFs, not
synthetic catalogs. It requires the existing reference project and its pinned
review/identity snapshots; those files are read only.

Twenty reviewed central pages form the diagnostic cohort. Only two reviewed
keyword-hit pages remain eligible after excluding locked institutions and their
aliases. Eight additional keyword-hit windows are therefore prepared with blank
answer keys. Together the 28 windows include ten hit windows, five originally
random pages, five structural-challenge pages and eight course-dense pages. The
originally random pages are independent of keywords, but conditioning on completed
reviews makes this a diagnostic sample rather than a corpus-wide accuracy sample.

The parser connects the existing v3 geometry layout, document grammar, header,
entry, block, continuation and field stages. It uses neighboring pages for structural
context, scores headers starting on each central page, and retains context courses
separately. A three-page window is never declared a complete catalog. Keywords are
matched late and separately in title and description. Original keyword IDs and
dispositions remain untouched.

The answer-key importer gates the shared snapshot by selected page ID before
decoding course-answer payloads. It validates typed review records, course inventory
counts, source identity and page extent. Unreviewed and source-insufficient pages
provide no negative labels. Fields unresolved in the source are excluded from field
accuracy denominators. Field scores are conditional on matched courses; page-level
recall separately counts courses the parser missed.

The experimental entry ranker is a transparent logistic classifier using structural
features. It fits only TUNING institutions, balances classes and institutions, and
measures separate DEVELOPMENT VALIDATION institutions. It does not change entry
decisions or generate authoritative course records. The partition registry preserves
the first pilot's assignments as the cohort grows; new families receive stable hash
assignments. Reused development validation is exposed and is not a final test.

An optional parallel-prose layout experiment addresses falsely inferred table rows
using persistent gutters. Its measured result did not improve course extraction on
this pilot, so it remains optional. More capable layout/OCR inference and stronger
entry features are future experiments to test against the same reviewed sources.
Subsequent versions have run actual Docling and PP layout/order inference on the
same reviewed pages; see [the engine comparison](docs/ownership_order_v2.md).
Separate native/Tesseract crop extraction and optional PP crop OCR are documented
in [the omission-review workflow](docs/omission_extractions_v1.md).

New OCR matches still require a separate reconciled hit release. Neither training
nor model agreement may fill the authoritative hit-disposition ledger.

The v0.10 source audit and paired comparison are in
[the validation-transfer report](artifacts/validation-transfer-v1/index.html).
All 15 former validation courses were inspected before changes; they now belong
to the exposed diagnostic set under
`config/validation_transfer_protocol_v1.json`. Complete content improved from
25/52 to 35/52, including 0/15 to 10/15 on that diagnostic subset. The disjoint
37-course tuning result stayed 25/37. The expanded result improved from 26/128
to 36/128; exact all-fields extraction did not improve. The scorer, answer keys
and frozen vocabulary were unchanged.

The reserved 34-window institution-held-out cohort remains sealed under
`config/development_phase_execution_v1.json`. Its sources, identities and review
answers must not be opened or used until all 18 development phases are complete
and the parser is frozen. The earlier freeze alone does not open this gate.

Phase 6 refines the independent omission audit with explainable review priorities,
relative source geometry and lossless duplicate grouping. All flags remain review
questions. See [the implementation guide](docs/omission_ranking_phase6.md) and
[the measured Phase 6 report](artifacts/omission-phase6-v1/index.html).

Phase 8 exposes continuation as a separate source-bound decision with
`CONTINUE`, `STOP` and `UNRESOLVED` outcomes, evidence scores, missing cues and
ownership blockers. Existing structural continuations remain enabled. The new
relative page-stream fallback remains review-only: no measured extraction gain
supports automatic attachment. OCR window coverage preserves each transform and
never joins text merely because crops overlap. See
[the Phase 8 comparison and diagnostic explorer](artifacts/continuation-phase8-v1/index.html).
Complete core extraction remains 35/52 on the original exposed cohort and 66/128
on the expanded cohort, which includes the original. All three centrally reviewed
courses requiring continuation are withheld or missed at the header/entry stage;
their continuation correctness cannot yet be scored. The reserved cohort remains
sealed.

Phase 9 keeps native `source_rows` as the primary contextual reading-order path.
Cached external layouts can supply separate source-bound second opinions through
`scripts/measure_phase9_order.py`. Reversed ordering relations and incomplete
coverage become distinct pending review questions. Unresolved edges never imply
a complete sequence, and alternate graphs cannot overwrite native text or course
ownership. See [the Phase 9 report](artifacts/reading-order-phase9-v1/index.html).
This adds review diagnostics; native course extraction remains 35/52 and 66/128.
The earlier 25/52, 9/52 and 0/52 engine comparison describes historical configured
integrations, not a current or universal engine benchmark.

Phase 10 adds source-quality routing to the bounded omission review. Readable
native text and a rejected header alone no longer trigger OCR. Clipped crops,
column crossings and tables require crop/structure review first; missing or
explicitly damaged native text can request a bounded OCR probe. Native and OCR
outputs remain separate. Competing readings require source review, and two
Tesseract settings count as one engine family. No automatic character correction
or keyword-ledger update is permitted. See [the recovery guide](docs/ocr_recovery_phase10.md)
and [the measured Phase 10 report](artifacts/ocr-recovery-phase10-v1/index.html).
