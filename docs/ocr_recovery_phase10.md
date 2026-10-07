# Targeted OCR evidence recovery

Phase 10 adds a source-quality gate to the existing bounded omission review.
An omitted course proposal is not itself proof that OCR is needed. Course-section,
header, title, ownership and boundary errors can occur with fully readable text.
The parser continues to use native source rows and the frozen keyword matcher.

The gate inspects the checksum-bound native crop before OCR:

| Source evidence | Action |
| --- | --- |
| Native extraction failed, native text absent, replacement/control/private-use characters | Bounded `TARGET_OCR` probe |
| Native cells intersect crop edge | `REVIEW_CROP_BOUNDS` first |
| Native line crosses unambiguous inferred prose columns | `REVIEW_COLUMN_CROP` before OCR on column-local crops |
| The crossing occurs on a page with table cells | `REVIEW_TABLE_STRUCTURE` |
| Readable native text and an omission flag | `REVIEW_STRUCTURE_ONLY` |
| Readable native text without an omission flag | `KEEP_NATIVE` |

These signals are routing aids, not correctness labels or calibrated probabilities.
Empty native text may mean a blank page rather than missing printed text. A probe
cannot turn absence of OCR output into a negative course label. Private-use glyphs
may be deliberate font encodings; an OCR proposal requires visual source review.
Short text, unusual spelling, historical numbering, missing credits, and ASCII
confusions such as `GE0L` are not automatically treated as damaged characters.
No dictionary, stemming, synonym expansion or character correction is applied.

Existing native and OCR records, source slices, TXT/TSV/JSON bytes, settings,
recognition scores, crop images and coordinate chains remain separate. Routing
and reconciliation are additional artifacts and never rewrite an extraction.
New gated preparations run Tesseract PSM 6/11 only for eligible crops. Optional
PP also honors that selection and does not initialize its models when no crop is
eligible. Earlier explicit bounded OCR experiments remain readable unchanged.

Reconciliation groups exact/whitespace-equivalent competing readings for review,
preserving each raw string. Two Tesseract segmentation settings count as one
engine family. Two different OCR families supporting an alternative produce
`REVIEW_CORROBORATED_ALTERNATIVE`, not an accepted correction. Readable native
text remains primary when OCR disagrees. When native text is absent and OCR
returns text, `REVIEW_RECOVERED_TEXT` keeps the native record empty and requires
source review before any future promotion. Clip uncertainty, model failures,
unmatched geometry and low recognition scores remain visible. No merged consensus,
new course, keyword disposition, training label or automatic replacement is made.

Run the regular review producer into a new directory:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m course_parser build-omission-review \
  --pilot artifacts/source-learning-v4/pilot \
  --predictions artifacts/reading-order-phase9-v1/candidate-v2/original-predictions \
  --output artifacts/my-new-gated-review --crop-limit 12
```

For the fixed permitted Phase 10 measurement:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/measure_phase10_ocr.py \
  --reference-project /Users/mohinuryusufjanova/Documents/University_Course_Catalog_Scraper \
  --output artifacts/my-new-phase10-measurement
```

Both exposed prediction runs and source-only OCR diagnostics are sealed before
strict scoring. The reserved 34-window institution-held-out cohort and answers
remain sealed until all 18 phases are complete and the parser is frozen.

The final comparison is in [the Phase 10 report](../artifacts/ocr-recovery-phase10-v1/index.html).
The original 12 TUNING crops are a retrospective routing sample, not an independent
test set. Their preserved OCR disagreement does not establish OCR recovery accuracy.
Image-only and blank-page regressions separately test evidence recovery and the
source-insufficient outcome. Course extraction accuracy is reported independently.

This follows Tesseract's advice to select segmentation for small regions and its
warning that table text needs layout analysis. PSM 11 does not establish reading
order, so its text is never promoted as an ordered course description.
[Tesseract image quality and segmentation](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html),
[TXT/TSV command-line outputs](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html).
