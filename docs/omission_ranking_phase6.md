# Phase 6: omission questions, priority and provenance

The audit runs after a frozen extraction release. It proposes source-review
questions, never course records. An omitted-course answer must come from direct
source review; a point total or agreement between extraction engines cannot supply
that answer. The frozen vocabulary and original hit ledger are unchanged.

## What changed

`omission_signals.py` now exports source-derived reasons for each flag. It keeps
modern, historical numeric/decimal, Roman/lettered, title-only and uncovered-prose
paths. A separate number-only header requires a nearby title on the same row,
using distances relative to line height. This handles split native PDF text while
rejecting isolated credit/page numbers. Lookahead and indentation are relative to
local text size, and description support stops before the next header. Historical
headers with no printed description can still raise a question when at least two
neighboring model-supported course entries share their identifier family and style.

Native text remains unchanged. The audit may inspect row fragments together for
prose structure; it does not replace text, reconstruct course fields or repair OCR.
Model-supported peers are corroboration rather than human-confirmed courses.
Context must be a short display-like heading in the same geometric lane; an
ordinary sentence mentioning a major or requirement does not set section context.

`omission_ranking.py` scores those explicit reasons once per signal family:

| Review evidence | Points |
| --- | ---: |
| Course-like identifier | +4 |
| At least two matching model-supported neighboring entries | +3 |
| Bounded following prose or inline title/prose transition | +3 |
| Explicit printed numeric credit value | +2 |
| Course-description section | +2 |
| Distinctive or matching entry typography | +1 |
| Requirement/reference or existing parser noncourse/reference decision | −4 |
| Administrative context | −4 |
| Table of contents/index context | −3 |
| Organization/person-name pattern | −3 |
| Isolated title-only display heading | −2 |

HIGH means at least 8 points, MEDIUM 5–7, LOW below 5. These point values and
cutoffs are **provisional review heuristics, not trained or calibrated
probabilities**. Repeated numeric strings alone and vague credit/prerequisite
mentions earn no points. Creditless courses remain eligible for audit questions.
Every tier stays in the inbox. The bounded crop sample balances signal families
and pages within TUNING and uses scores as a ranking signal. It reads no answers.

## Lossless grouping

Group only flags with the same PDF checksum, physical page, coordinate frame and
whitespace/case-normalized source text. Every pair in a group must overlap by at
least 85% of the smaller box. Complete-link grouping prevents a chain of partly
overlapping boxes from bridging separate printed entries. Different locations,
documents, pages, punctuation or OCR transcriptions remain separate.

The representative keeps its own score; it cannot borrow another alias's cues.
`omission_flags.jsonl` keeps every physical observation.
`omission_review_groups.jsonl` keeps complete member records and IDs.
`review_queue.jsonl` contains one blank decision per group, plus explicit source
failure tasks. No original source ID or evidence is deleted. The repeated
“Conservation of Natural Resources” observations on the permitted sample are at
distinct positions, so they remain separate. This sample has no actual duplicate
observations to merge; the implementation is protected by regression tests.

## Reproduce the permitted comparison

From the workspace, with its Python environment and `PYTHONPATH=src`:

```sh
python scripts/measure_phase6_omissions.py --output artifacts/omission-phase6-v1/new-replay
python scripts/build_phase6_omission_report.py --run artifacts/omission-phase6-v1/new-replay
```

The measurement accepts only the explicitly named original and expanded exposed
pilots and their approved Phase 5 evaluations. It verifies inputs, leaves
extraction unchanged, and seals **both audit runs before reading either existing
answer subset**. Course coverage uses unique overlaps with reviewed header code or
title boxes. Alignment ambiguity stays separate. These counts diagnose audit
coverage, not independent precision, corpus-wide recall or generalization.
The original sample is included in the expanded one.

The accepted run and counts are linked from
[the Phase 6 report](../artifacts/omission-phase6-v1/index.html).
The source-review inbox retains separate native/Tesseract outputs and crop
transforms. Its answers stay blank; later source review can provide legitimate
TUNING labels for evaluating ranking quality.

The strict extraction scorer and extraction modules remain unchanged. Complete
core extraction stays 35/52 original and 66/128 expanded; all-field extraction
stays 10/38 and 13/97. No omission flag is counted as an extracted course.

## Evaluation boundary

Use only TUNING, DEVELOPMENT, existing exposed diagnostics, regressions and
permitted source material. Reserved-cohort inputs and outputs are rejected before
input lookup. The reserved 34-window institution-held-out cohort, source evidence,
identities and answers remain sealed until **all 18 phases are complete and the
parser is frozen**. Do not open it to tune scores or choose cutoffs.

## Relevant implementation references

The location-plus-text principle is consistent with
[pdfplumber's duplicate-character handling](https://github.com/jsvine/pdfplumber#extracting-text).
Our grouping operates on audit observations and retains originals.
[Precision and recall definitions](https://scikit-learn.org/stable/modules/model_evaluation.html#precision-recall-f-measure-metrics)
require labeled outcomes. No cited library supplies evidence that these particular
point values will improve course extraction; the source-based comparisons provide
local evidence for audit coverage only.
