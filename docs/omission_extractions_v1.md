# Possible omissions and separate crop extractions

Version 0.8 adds an independent recall audit after course assembly. The audit
raises source-review questions; it never adds accepted courses, changes ownership,
fills keyword dispositions, or converts agreement into training labels.

## Source signals

`omission_signals.py` scans every native line on each central page in the verified
pilot, including pages where the header detector proposed nothing. It reads the
frozen geometry rather than filtering through the main header detector. Signals
include modern codes, historical numbered/ranged/letter/Roman identifiers, title
headers without subject codes, distinctive typography, paragraph gaps, nearby
description structure, inline capitalized title-to-prose transitions,
repeated entry shapes, explicit course-section context,
and whole unowned narrative blocks. No geology keyword, department restriction,
or explicit credit requirement is used.

Typography alone is insufficient: faculty headings and page furniture have it
too. Corroboration is required. A possible description beyond the next identifier
does not support the earlier header. Credit/administrative labels are excluded
from the title-only path. Nearby requirement, index, faculty, major and facilities
context is preserved and lowers priority. Those rules are fallible review aids,
not probabilities or proof of an independent course. References remain useful
hard negatives; the audit is intentionally capable of questioning an entry-stage
rejection when independent source cues conflict with it.

Each flag identifies an unproposed header, a header withheld by the entry stage,
a possible header swallowed inside another owned block, or unowned narrative.
It retains exact line text, source geometry, nearby lines, source/PDF/image/text
checksums, node and candidate IDs, competing owners, learning partition and blank
review status. Flags outside the bounded crop sample still have full-page evidence.
Unresolved source order remains an explicit cue.

## Separate extraction records

`crop_extractions.py` renders diagnostic crops from the checksum-verified source
PDF at 300 dpi by default. Crops include neighboring prose for review and **do not
assert course boundaries**. Each crop preserves:

- The requested upright clipping box, actual raster size and integer pixel origin.
- DPI and the crop-pixel → upright-page-point affine transform.
- Upright-page → unrotated MuPDF coordinates, including page rotation.
- Unrotated MuPDF → PDF user space, including origin/y-axis and CropBox effects.
- The composed crop-pixel → PDF-user-space transform and image/source hashes.
- An explicit identity preprocessing contract: no resize, rotation or unwarping.

The renderer's integer origin matters: a fractional requested box is not the
exact pixel-grid origin. Transform validation checks extents, invertibility and
composition. Regression tests cover 0/90/180/270-degree pages and offset CropBoxes.
Unknown preprocessing transforms are rejected, not guessed.

The frozen full-page source-text JSON is copied unchanged beside its original
page image. It is separate from the newly extracted native PDF page text, which
is also archived unchanged. A native crop retains source character offsets and
exact word/line slices. Its display string is an explicitly recorded newline join
of those slices. It is not asserted to align with the frozen searchable stream.
Intersecting words that cross a crop edge produce a review flag.

Tesseract outputs for PSM 6 (one text block) and PSM 11 (sparse text) occupy separate
directories with original TXT/TSV bytes, version, settings, input hashes, logs,
recognition scores and mapped polygons. These are **two configurations of the
same engine**, not independent models. No confidence threshold deletes words.

Optional `crop_ppstructure.py` runs a separate PP-StructureV3 pipeline. This crop
configuration uses PP-DocLayout_plus-L, PP-OCRv5_mobile_det and
en_PP-OCRv4_mobile_rec, four CPU threads, recognition threshold zero, and no
orientation, unwarping, table, formula, chart or seal inference. These settings
are recorded beside each export. The full raw JSON, parsed crop-pixel output,
independent OCR text and page/PDF polygons remain separate from native/Tesseract
records. The existing PP adapter rejects unknown preprocessing frames. Failed
initialization, inference or parsing remains an explicit failed extraction with
any available raw export retained. The crop viewer is a diagnostic OCR experiment,
not the previous full-page reading-order benchmark.

## Disagreements and review

`extraction_disagreements` verifies each input binding and raw artifact, rechecks
native source slices, rederives Tesseract word geometry, and reparses successful
PP exports. Pairwise comparison records exact agreement, whitespace-only
differences, substantive text differences, or non-comparable failures.
Whitespace normalization is a diagnostic only; source outputs remain unchanged.
Case, punctuation, hyphens and OCR characters are never corrected.

Native/Tesseract and Tesseract/Tesseract word cells also receive reciprocal
geometric overlap matching (at least 0.5 intersection fraction, 0.1 tie margin).
Unmatched or ambiguous coverage is routed to review. PP recognition lines are
not treated as word boxes. Geometry agreement does not prove correct reading order
or course ownership. No merged consensus text is generated.

`omission_review.py` puts flags and their crop disagreements in one local inbox
with exact evidence links and blank dispositions. Inspect the central source
page first; use neighboring pages for continuations. Record an omitted course's
printed identifier/title and owned extent, or mark the item noncourse, already
covered, extraction error, or source insufficient. This inbox is separate from
the authoritative hit ledger. No external messages are sent. Source-insufficient
pages and producer failures remain visible instead of becoming negative labels.

Only TUNING flags are selected for crops, using deterministic round robin across
signal families and source pages. Source-only flags are sealed before any answer
comparison. Protected cohorts and answer files are not opened. Exposed DEVELOPMENT
validation pages are not fresh independent tests. Pending labels cannot train a
model or establish omission precision/recall.

## Run and verify

Use a new output directory; all stages refuse overwriting existing artifacts.
The existing reference-project Python environment can run native/Tesseract stages:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m course_parser build-omission-review \
  --pilot artifacts/source-learning-v4/pilot \
  --predictions artifacts/sequence-boundaries-v3/release/predictions \
  --output artifacts/my-omission-review-new --crop-limit 12 --prepare-only
```

If the optional Paddle environment and model cache are available, add independent
crop OCR before sealing the inbox. Otherwise finalize with native/Tesseract outputs:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv-engines/bin/python \
  -m course_parser run-crop-ppstructure --run artifacts/my-omission-review-new \
  --limit 4 --device cpu

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m course_parser \
  finalize-omission-review --run artifacts/my-omission-review-new

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m course_parser \
  verify-omission-review --run artifacts/my-omission-review-new
```

`build-omission-review` without `--prepare-only` builds and seals native/Tesseract
review evidence in one command. `--tesseract` can specify an executable.
Missing Tesseract is a recorded failure, not a fabricated empty OCR result.
Paddle is optional; model initialization may need downloads when weights are not
cached. Use a workspace model-cache path in restricted environments. A sealed
review run cannot accept additional PP extractions; prepare a fresh run instead.

The final pilot run is `artifacts/omission-extractions-v1/release-v4`. The 28 audited
windows yield 50 pending flags and 12 materialized TUNING crops. There are 40
separate extraction records: 12 native, 24 Tesseract and 4 PP-Structure. PP ran
successfully on all four crops; its raw records were copied byte-for-byte into
the final bundle after matching identical crop images and transform hashes.
All twelve crops have text differences to inspect. Eleven also have native words
intersecting a crop edge, so differences cannot automatically be called OCR errors.
Earlier trials
are retained separately and are not the final deliverable. Queue counts do not
measure extraction improvement. The previous course predictions are reused
unchanged; their measured core extraction remains 25/52, all-field 10/38, and
DEVELOPMENT validation 0/15. All 18 original pilot keyword hits remain pending.

An additional inspection of the old TUNING error report found an alignment issue:
“204 EPIC POETS” is already proposed, but a second header candidate shares “204.”
The mutually unique alignment rule labels the gold header missed. This case is
not a newly recovered course and was not used to revise the reported extraction
scores. The source-only audit skips headers already attached to course blocks;
evaluation-alignment conflicts need their own review rather than omission credit.

## Documentation and coding practices used

- [PyMuPDF Page coordinates, rotation and pixmap clipping](https://pymupdf.readthedocs.io/en/latest/page.html),
  [Pixmap raster origin](https://pymupdf.readthedocs.io/en/latest/pixmap.html), and
  [coordinate transformations](https://pymupdf.readthedocs.io/en/latest/app3.html):
  preserve and validate coordinate chains instead of inferring crop offsets.
- [Tesseract command-line and TSV output](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html):
  compare recorded segmentation settings and retain word confidence/geometry.
- [PP-StructureV3 pipeline](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html):
  keep layout/OCR outputs and preprocessing provenance as separate evidence.
- [GROBID principles](https://grobid.readthedocs.io/en/latest/Principles/):
  staged source features and context support task-specific interpretation.

The implementation extends the existing source-preserving contracts rather than
copying upstream model code or claiming university-course accuracy from document
benchmarks. There are 35 new regressions (153 total), including real local
Tesseract execution, blank-answer integration, failures, tampering, and spatial
ambiguity. Ruff checks and formatting pass.
