# Cells, row ownership and expanded source review — version 0.9

This increment reduces demonstrated false course ownership. It does not improve
complete-course recall. All extractions remain review proposals.

## Implementation and relevant sources

Docling's [layout postprocessor](https://github.com/docling-project/docling/blob/main/docling/utils/layout_postprocessor.py)
separates text cells from region hypotheses, assigns cells, retains orphan text,
and distinguishes deterministic assembly order from later semantic reading order.
Our application is to preserve the original engine layout in `source_layouts`
and the adopted interpretation separately in `layouts`. Every changed cell has
an explicit interpretation record. Text, evidence, geometry, source order and
region membership must be conserved. The original table, row, cell, column and
reading-order hypotheses remain inspectable even when a working interpretation
uses prose columns. These engine hypotheses are not independently verified
physical table structure.

PP-StructureV3 [documents separate table classification, structure recognition
and cell detection](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html).
Its [layout parsing pipeline](https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/inference/pipelines/layout_parsing/pipeline_v2.py)
keeps table processing distinct from other layout processing. Our application
is a separate, source-bound row-purpose stage (`table_structure.py`), followed
by entry classification and ownership. This increment adds no neural table model
and does not assume that an engine's table label establishes a course inventory.

GROBID's [cascade architecture](https://grobid.readthedocs.io/en/latest/Principles/)
provides the relevant separation between container segmentation, entity detection
and detailed fields. Our cascade distinguishes requirements, inventory rows and
unresolved grids before ownership. No CRF is trained without suitable annotations.

Rules now require:

- An inventory table has an explicit local inventory container, a supported
  header contained within one row, its own narrative cell and compatible narrative
  rows. The complete source row remains bounded separately from adjacent rows.
  Printed credits are not mandatory. Unknown grids remain unresolved.
- A local requirements container defeats repeated course codes, titles, credit
  numbers and prose-shaped cells. Requirement rows become references.
- Coordinated, distinct field labels veto the broad prose-column repair. Merely
  repeating the word “course” in fragmented paragraphs is insufficient. Compact
  grids retain their rows; persistent prose gutters still have a separate column
  interpretation. Mixed or uncertain tables are not globally flattened.
- A bounded numbered course list with multiple headers and a group distribution
  total, such as “12 hours social sciences, 9 hours junior-senior level,” is a
  reference list when it lacks independent course narrative. That total cannot
  supply the last listed course's description.
- Printed degree abbreviations such as `B.A./B.S.` cannot establish independent
  historical courses.

Table inference is deliberately conservative. A row's bare number is preserved
but does not automatically become credits without evidence for that field.
Sparse inventories without explicit containers or descriptions may need review.
Conservation checks reject lost cells, duplicated membership or altered quotes;
the ownership audit still rejects source nodes owned by multiple courses.

## Review selection and source regressions

`prepare-learning-pilot --expand-review` retains the original 28 windows and
expands the cohort to 68 windows:

| Stratum | Windows |
| --- | ---: |
| Existing source-reviewed inventories | 50 |
| Pending keyword windows | 8 |
| Pending independent random windows | 10 |

The 50 reviewed windows include additional random, structural-challenge and
noncourse-control pages. Review availability conditions that diagnostic sample;
it is not a corpus-wide random accuracy estimate. There are 25 random-role
windows overall, including the ten pending random pages. All 18 old original
hits remain accounted for; the expanded random sample adds one hit, for 19
pending original-hit decisions. Vocabulary remains 33 concepts and 68 forms.

`build-expanded-review` maintains separate random, active TUNING and sealed
VALIDATION lanes, with deterministic family round robin within each lane.
Native omission signals include historical identifiers, uncoded headers and
uncovered prose. Other source signals cover grids, alternative reading order,
parser failure and ambiguous field paths. Unique paths do not raise ambiguity
flags. Signals never remove or rerank away the independent random stratum,
manufacture labels, or open answer files. All review decisions remain blank.

Institution/alias families and source PDFs retain their partition assignments.
CALIBRATION, TEST and locked evaluation remain excluded. VALIDATION reviews are
not training material. The exposed DEVELOPMENT validation result below is not
an independent final test.

Six checksum-bound TUNING regression cases replay original layouts, styles and
source-reviewed answers: Hartnell, Fitchburg, Vermont, a Mines mixed requirements
page, a Northeastern directory, and the Missouri-Kansas City distribution list.
Their answer basis is source-image review, not model predictions. Existing
capability floors make regressions visible without pretending every course on
those pages is already correct. Tests also cover creditless inventory rows,
long-prose tables, source conservation, requirement ownership, denominator
accounting and random-selection stability.

## Paired measurement

Old 0.8 code was copied before edits and used to predict the same expanded
68 windows. Old/new predictions were sealed before answer import. The same
scorer and the same source reviews evaluate both versions. The old alignment
policy is unchanged, including unresolved duplicate-code alignments.

On the **same 50 reviewed DEVELOPMENT pages**, with 128 source-reviewed courses:

| Measure | Before | After |
| --- | ---: | ---: |
| Matched course proposals | 51 | 51 |
| Central proposals on reviewed pages | 55 | 53 |
| Known false course proposals | 2 | 0 |
| Ambiguous course proposals | 2 | 2 |
| Conservative course precision | 92.7% | 96.2% |
| Complete code/title/description/block/page extent/continuation | 26/128 | 26/128 |
| Complete-course recall | 20.3% | 20.3% |
| Complete-course conservative precision | 47.3% | 49.1% |
| Confirmed exact owned extents | 45/128 | 45/128 |
| Exact descriptions, including misses in denominator | 36/128 | 36/128 |
| Complete all-field entries | 10/97 | 10/97 |
| Clean zero-course pages | 38/39 | 39/39 |
| Reviewed parser-failed pages retained in denominator | 1 | 1 |

The removed false courses are the list's `CJC 475P` and the spurious historical
`B` from `B.A./B.S.`. All other central proposals are unchanged. Across all 68
windows, both versions have two failed windows, one of which is unreviewed.
The duplicate-ownership failure remains explicit; it is not turned into a
negative or quietly discarded. The original 20 reviewed-page subset remains
25/52 complete core courses and 10/38 complete all-field entries.

TUNING is 26/113 complete core; exposed VALIDATION remains 0/15. These are reused
source reviews without new independent adjudication. No original-hit ownership
precision/recall can be claimed while the 19 dispositions are blank.

The new metrics distinguish complete-course precision, exact-extent precision
and recall, end-to-end description recall, missing/incorrect descriptions, and
false ownership on fully reviewed zero-course pages. All have explicit
denominators; pending pages are excluded, failed reviewed pages and missed
courses remain included. Results are also separated by review role.

## Artifacts and reproduction

The deliverable lives under `artifacts/table-ownership-v1/release/`:

- `measurement/`: paired evaluation, retained original cohort and source-cell viewer.
- `predictions/`: source-only 0.9 extraction proposals and conservation records.
- `parser-code/`: exact source snapshot for those sealed predictions.
- `review-selection-v2/`: final blank queue with correct UNIQUE/AMBIGUOUS path flags.
  The older queue in `measurement/review_selection` is retained as an initial
  artifact; it overflagged UNIQUE paths. This correction changes review signals
  only and does not change extraction, selection membership/order or scores.
- `final_checks.json`: output hashes, source retention, code fingerprints and
  accounting checks. `index.html` links the final results and queue.

Use the configured Python and `PYTHONPATH=src`:

```sh
python -m course_parser prepare-learning-pilot \
  --reference-project /Users/mohinuryusufjanova/Documents/University_Course_Catalog_Scraper \
  --partition-registry config/source_learning_partitions_v1.json \
  --expand-review --output <new-pilot>

python -m course_parser run-learning-baseline \
  --reference-project /Users/mohinuryusufjanova/Documents/University_Course_Catalog_Scraper \
  --pilot <pilot> --contextual-course-assembly --ordering-engine source_rows \
  --output <new-predictions>

python -m course_parser build-expanded-review \
  --pilot <pilot> --predictions <predictions> --output <new-queue>

python scripts/measure_table_ownership.py \
  --reference-project /Users/mohinuryusufjanova/Documents/University_Course_Catalog_Scraper \
  --pilot <same-pilot> --before <sealed-old-predictions> \
  --after <sealed-new-predictions> --original-pilot <retained-original-pilot> \
  --output <new-comparison>
```

Outputs refuse overwriting. The reference project, authoritative ledger and
protected evaluation are unchanged. This release improves false-ownership
rejection and the ability to find regressions; substantial header, description,
boundary and continuation work remains before the quality targets are met.
