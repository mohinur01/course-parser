# Phase 15: mixed annotation selection

The recommendation is appropriate as an annotation acquisition policy. Combining
known difficulties, coverage and random exploration avoids relying exclusively
on parser uncertainty. Active-learning research describes how uncertainty-only
queries can favor unrepresentative outliers and discusses representativeness
alongside informativeness ([Settles, 2009](https://burrsettles.com/pub/settles.activelearning_20090109.pdf)).
It does not establish that 40/30/30 is optimal for this parser. We implement that
ratio as a configurable starting allocation and prepare a small, reviewable batch.
Neither annotation savings nor extraction improvement has been measured yet.

## Selection and interpretation

The unit of work is a whole central/context source window, including negative
text, furniture and references. Candidate-only labeling would miss unproposed
headers and reward the parser's existing blind spots. The first batch has ten
windows: four from known failures or unresolved proposals, three for style
coverage and three randomly drawn. Budget rounding uses largest remainders and
preserves at least one slot per stream. Explicit shortfalls receive random
backfill where available; insufficient pools report unfilled slots.

Random slots are drawn first, uniformly without replacement from the sorted
eligible sampling frame, using seed 1501. Failure scores, coverage and institution
caps cannot condition this primary draw. Overlapping windows from the same PDF
are grouped transitively by shared context pages, with one stable representative
per group in each batch. Alternatives remain recorded and available in future
cycles. This is random exploration within an exposed, filtered development pool;
it cannot estimate corpus accuracy or untouched generalization.

Targeted selection caps each connected institution family at two windows per
batch. The failure stream uses capped question counts: incomplete core entries
and missed courses (weight 3 each), false proposals or contamination (3), ambiguous
proposals (2), unresolved entry proposals and source-order warnings (1 each).
Family spread precedes the score, then additional challenge coverage breaks ties.
These weights are heuristic priorities, not probabilities or proven root causes.
The unsuccessful Phase 14 ranker's confidence is excluded.

Style descriptors combine proposed identifier families, table/prose layout and
inferred columns. They are coarse source/proposal descriptors, not independently
verified catalog-template classes. Style selection first considers audited target
annotation coverage, then current-batch coverage, family spread and audited family
coverage. Source-inventory review coverage is a separate tie-break proxy. All
audited target coverage is currently zero; reviewed course inventories do not
supply sequence-field targets. Scores and coarse descriptors may be wrong.

The review checklist includes misleading references, historical identifiers,
long descriptions, credit absence, tables, continuation, damaged OCR, multiple
columns and title-only headers. Missing proposed credits are a question, not proof
of a no-credit course. Long proposed blocks use the permitted pool's upper
quartile as a review question, not a description label. Title-only headers and
actual continuation still require whole-window human inspection; selection does
not guarantee every challenge occurs in the first batch.

## Inputs, targets and isolation

Only registered, checksum-pinned exposed DEVELOPMENT evaluations are accepted.
The bound metadata audit runs before cached metrics, proposals or source templates
are decoded. New annotations are selected from TUNING only; legacy exposed
diagnostics cannot enter the draw. Cached proposal and template manifests must
declare source-truth access false and match the same bound pilot. Frozen templates
must cover exactly its TUNING inventory, carry matching identities and contain
blank labels, owners, boundaries and transition cues.

The batch copies exact frozen source units and order digests, plus checksum-bound
central/context page images. Images must remain inside the bound pilot, match its
packet identities and cover the frozen context. The pilot and selected metadata
hashes are checked again before preparing images. No PDF is reopened, no OCR run,
no parser rerun and no course rescore occurs. Protected path guards also run
before source reads. The reserved 34-window cohort and its review answers remain
sealed until all 18 phases are complete and the parser is frozen.

Planning creates no training labels. Two distinct human source reviews, source
order confirmation, complete character spans, ownership and transition checks
remain necessary under the existing sequence annotation audit. Source-insufficient
windows enter a preparation backlog. Rerunning with audited reviews excludes
completed target windows and updates actual annotation coverage. Future selection
may use these development annotations; independent evaluation remains separate.

## First batch and validation

The source export contains 50 TUNING windows, 15,538 source units and zero labels.
Three windows lack source units on one or more context pages and remain in the
preparation backlog. The remaining 47 windows form 47 nonoverlapping sampling
units. The primary random-slot marginal inclusion probability is 3/47 in this
declared frame. With the fixed policy, ten selected windows span seven institution
families, 3,362 source units and 30 existing page images. There are no stream
shortfalls or backfills. All field, owner, boundary and transition targets remain
blank. All 50 source templates remain pending rather than training eligible.

The accepted batch is
[release-v2](../artifacts/active-annotation-phase15-v1/release-v2/index.html).
Its plan records every selected reason, frame, overlap group, coverage count,
seed and allocation; the manifest binds inputs, implementation and copied images.
The acceptance check verifies exact blank-template copying, deterministic
selection, image/context identity, family caps and unchanged extraction/scoring
modules. Regression tests cover random independence, input order, overlap,
coverage priority, explicit shortages, protected-role refusal and pre-read guards.

Cached strict performance remains 66/128 complete core courses and 13/97 complete
all-field courses on the expanded exposed diagnostic sample. This phase improves
the annotation workflow and makes no extraction accuracy claim.

## Reproduction

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m course_parser prepare-annotation-batch \
  --pilot artifacts/table-ownership-v1/expanded-pilot \
  --evaluation artifacts/ocr-recovery-phase10-v1/candidate-v2/expanded-evaluation \
  --templates artifacts/active-annotation-phase15-v1/tuning-templates \
  --output artifacts/active-annotation-phase15-v1/new-run \
  --budget 10 --shares 40 30 30 --seed 1501 --family-cap 2
```

The output must be new. Use `--reviewed` with a future complete audited TUNING
review file to update coverage. The batch's source-only `batch_template.jsonl`
contains annotation slots; it is not a reviewed target file. Keep annotation
edits in a separate review copy and validate them through the existing audit.
