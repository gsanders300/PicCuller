# Review: Scoring Weights, Calibration, and Flag Documentation

Date: 2026-09-09. Baseline: `uv run pytest -q` -> 153 passed, 1 skipped (the documented
case-insensitive-filesystem skip), clean worktree, commit `3978782`.

Scope: three questions, in order.

1. How the composite-score coefficients were chosen, and whether there is a better way.
2. What the calibration process actually is.
3. Where the command-line documentation is incomplete.

## Applied changes (2026-09-09)

Sections 3.4 and 2.3 were applied after this review was written. The rest of the document
describes the state before them. Test suite went from 153 passed / 1 skipped to **172 passed
/ 1 skipped**, with Ruff clean, `git diff --check` clean, and both console entry points
working. No `EVALUATION_ALGORITHM_VERSION` or `CACHE_SCHEMA_VERSION` bump was needed: no
per-image metric and no stored cache field changed, so existing caches stay valid.

| Item | Change | Verified by |
| --- | --- | --- |
| 3.1 | `help=` on the thirteen bare options in `build_parser`. All 24 options now describe themselves. | `photo-cull --help`, plus a test asserting every action carries non-empty help. |
| 3.2 | New `### Scoring presets in detail` in `readme.md`: a table of what each preset adds and what switching to it costs, then one subsection per preset with the subject prompts quoted verbatim, the intent behind the numbers, and the portrait re-decode. Cross-referenced from the command reference and the cache section. | Tests assert every preset has a section and every prompt appears verbatim. |
| 3.3 | `readme.md:494` corrected from an eye factor of 0.85 to the 0.7/1.0 the code returns. | The claim contradicted `advanced_analysis.py:62`, `Specifications.md:94`, and `readme.md:486`, and `tests/test_advanced_analysis.py:22` already pinned 1.0. |
| 3.4 | `tests/test_documentation.py`: help coverage, a section per preset, prompts verbatim, the published weights table against `scoring.py`, and eye-factor values against what the cascade can return. | Each guard was checked against the drift it targets, not assumed. Two of the first four probes did not fail; one probe was invalid, and the other found a real bug in the test (splitting the README on periods hid `0.85`, because a decimal contains a period). Rewritten to scan paragraphs. |
| 2.3 | `generate_contact_sheet` takes burst groups instead of a flat winners list. A multi-frame burst renders as its own block with the pick marked and the frames it beat marked `Lost`; consecutive single-frame groups share one grid, so a `--no-group` run keeps its dense layout. New `cull._review_groups` builds the groups. | 13 unit tests plus a pipeline test over three real frames that group into one burst. Reverting to winners-only fails 9 unit tests and the integration test. |
| 2.3a | `--contact-sheet` stays a cap on thumbnails, not bursts, so page decode cost is unchanged. Bursts are added whole in selection order, the walk stops at the first burst that does not fit rather than skipping it for a smaller one, and a burst larger than the whole cap is truncated so a positive cap never yields an empty page. `run.json` gains `contact_sheet_bursts`. | Tests cover the cap, the stop-rather-than-skip rule, the truncation floor, and a forced keep not duplicating its own burst. |

Docs updated in the same change, per `AGENTS.md:200`: `readme.md` (the `--contact-sheet`
row, `Use the review page`, the `review.html` section, and the `run.json` counts) and
`Specifications.md` section 8.

Still open from this review, in priority order: 2.4 (the feedback template pre-fills the
program's own picks as `keep`), 2.5 and 2.6 (the validator's two metrics), 2.7 (no fitting
step, no held-out split, no per-factor attribution), and all of section 1's Tier 0 to Tier 2
work, which needs labels that the changed review page can now actually collect.

## Provenance key

- **Measured** means a number produced by running code on this machine during this review.
- **Read** means taken directly from source or git history.
- **Assumed** means an illustrative band I supplied, not a statistic from your photographs.
  Every assumed band is labeled at the point of use.

---

# 1. The coefficients

## 1.1 How they were chosen: by judgment, and the repo says so

Git history answers this cleanly.

The initial commit (`268d14b`, `scoring.py`) had no presets at all. It had one formula and
two hard-coded constants: `ABSOLUTE_FOCUS_FLOOR = 0.5` and `relative_focus ** 1.5`, with
every other factor entering at implicit weight 1.0. `ConversationLog.md:23` records that
formula as a design decision reached in conversation:

```text
Composite = Aesthetic x (Focus / MaxFocus)^1.5 x NormalizedMUSIQ x ExposurePenalty
```

The four presets arrived later (`7849edf`) as a fan-out of that single formula. The
`balanced` profile is not a new set of values, it is the original constants given a name:
floor 0.5, relative exponent 1.5, every weight 1.0. The three genre profiles are
hand-perturbations around that origin.

No labeled data was involved at any point, and the project states this in six places rather
than hiding it: `scoring.py:56`, `AGENTS.md:140`, `readme.md:1200`,
`PotentialEnhancements.md:9` and `:31-33`, `Specifications.md:113`, and
`docs/how-a-photograph-is-judged.md:255-257`. That last one is the most direct: "these
weights were chosen by judgment, not derived from data."

So the honest answer to "how did you decide" is that nobody decided them empirically, and
the codebase is unusually forthright about it. The interesting question is not whether the
numbers are calibrated (they are not, by admission), but whether the *parameterization*
is one that calibration could ever fix. Three structural findings follow.

## 1.2 Two knobs control one thing, and they can silently cancel

`absolute_focus_floor` and `absolute_focus_weight` both govern the same quantity: how much
absolute sharpness is allowed to penalize a photograph. The floor sets the range of
`F_absolute`, and the weight sets the exponent applied to it. The observable consequence is
one number, the worst-case multiplier `floor ^ weight`.

Measured, from the constants in `scoring.py:28-59`:

| Preset | Floor | Weight | Worst-case absolute-focus multiplier |
| --- | ---: | ---: | ---: |
| `landscape` | 0.45 | 1.2 | **0.386** |
| `balanced` | 0.50 | 1.0 | 0.500 |
| `wildlife` | 0.55 | 0.8 | 0.618 |
| `portrait` | 0.60 | 0.7 | **0.699** |

The ordering is coherent and matches stated intent: landscape punishes absolute softness
hardest, portrait most forgives it. But note that `wildlife` and `portrait` each moved
*both* knobs in the same direction. Wildlife raised the floor (less range) and lowered the
weight (less range). Neither change is visible in isolation, and the combined effect is
larger than either digit suggests. A reviewer reading `absolute_focus_weight=0.8` would
reasonably guess a 20 percent softening; the actual softening relative to balanced is 24
percent of the available penalty range.

This is a parameterization problem, not a value problem. Collapsing the pair to a single
`absolute_focus_penalty` in [0, 1] (the worst-case multiplier itself) would make each
preset's intent legible and would remove a redundant dimension from any future fit.

## 1.3 The presets tune the small terms and leave the big ones alone

Because the composite is a product, taking logs makes every weight a linear coefficient:

```text
ln(composite) = ln(aesthetic) + sum_i  w_i * ln(f_i)
```

A term's influence on the ranking is therefore `w_i * (spread of ln f_i)`, not `w_i` alone.
A large weight on a factor that never varies changes nothing; a weight of 1.0 on a factor
that swings widely dominates the result.

Below, the per-term log-range under **assumed** plausible bands. The bands are mine
(relative focus 0.5 to 1.0, MUSIQ/100 0.35 to 0.75, exposure 0.6 to 1.0, eye 0.7 to 1.0,
subject 0.45 to 0.65); the arithmetic and the preset constants are read from source.
Section 1.5 explains how to replace my bands with real ones from your own data.

| Preset | abs focus | rel focus | MUSIQ | exposure | eye | subject |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `balanced` | 0.693 | **1.040** | 0.762 | 0.511 | 0.000 | 0.000 |
| `wildlife` | 0.478 | **1.248** | 0.762 | 0.409 | 0.000 | 0.074 |
| `portrait` | 0.358 | **1.109** | 0.762 | 0.409 | 0.125 | 0.055 |
| `landscape` | **0.958** | 0.832 | 0.838 | 0.613 | 0.000 | 0.055 |

For scale, a LAION aesthetic score spanning 4.5 to 7.0 (**assumed**) contributes a
log-range of 0.442.

Three things fall out of this table.

**The MUSIQ term is probably larger than the aesthetic base.** 0.762 against 0.442. The
documentation describes the aesthetic score as the base and everything else as bounded
gates that only pull down (`readme.md:509-510`,
`docs/how-a-photograph-is-judged.md:242-248`). That is true of the *form* but, under these
bands, misleading about the *effect*: the technical-quality model is doing more of the
ranking work than the aesthetic model. Whether that is desirable is a taste question, but
it should be a decision rather than a side effect, and `musiq_weight` moves only from 1.0
to 1.1 across all four presets, so no preset expresses a view on it.

**The knobs that got the most design attention have the least leverage.** `eye_weight`
(0.35) contributes 0.125 and `subject_weight` (0.15 to 0.2) contributes 0.055 to 0.074.
Together they are a fifth of the relative-focus term. The subject-integrity path costs a
CLIP text-tower pass, a cache table, a `preset_refresh` phase, and a documented invariant
(`Specifications.md:91-92`), for what is currently a few percent of score movement.

**The relative-focus exponent is the highest-leverage preset knob**, in three of four
presets. That matters for section 2, because it is also the one knob the labeling UI cannot
collect evidence for.

## 1.4 Subject integrity is a one-dimensional linear probe with an unexamined temperature

`advanced_analysis.subject_integrity_scores` computes, for unit-normalized embeddings:

```text
p = softmax(10 * [img.t_pos, img.t_neg])[0] = sigmoid(10 * img.(t_pos - t_neg))
```

So the score is a monotone function of the projection of the image embedding onto one fixed
direction, `t_pos - t_neg`. The two-prompt framing is presentation; mechanically it is a
single linear probe whose direction is set by prose and whose temperature is hard-coded.

Two measurements about that.

**The temperature is 10x below the model's own.** I read the trained `logit_scale` straight
out of the pinned checkpoint (`model.safetensors`, revision `32bd6428...`): it is
4.605170, and `exp(4.605170) = 100.0` exactly. `advanced_analysis.py:78` uses `10.0`. That
choice flattens the resulting probability by an order of magnitude toward 0.5. It may well
be the right call (CLIP's trained scale is calibrated for retrieval over a large candidate
set, not for a two-way contrast), but nothing records it as a decision, no test pins it, and
it is a free parameter with more leverage than `subject_weight` itself.

**The two prompts point in nearly the same direction.** Measured with the real CLIP text
tower on the pinned revision:

| Preset | cos(positive, negative) | max achievable \|gap\| |
| --- | ---: | ---: |
| `wildlife` | **0.859** | 0.532 |
| `landscape` | 0.773 | 0.674 |
| `portrait` | 0.729 | 0.737 |

The max-gap column is the hard bound `||t_pos - t_neg||`, reached only by an image
embedding perfectly aligned with the prompt difference, which never happens; real gaps are
far smaller. The point is the first column: the wildlife pair shares 86 percent of its
direction, so almost everything CLIP sees in the photograph is common to both prompts and
cancels. The prompts are competing over a small residual.

Rewriting the prompts to be more opposed would help. Learning the direction from labels
would help more, and section 1.5 explains why that costs nothing extra.

## 1.5 A better way, in three tiers

### Tier 0: rank the knobs by leverage. No labels needed. Do this first.

Every column the calculation needs is already in `evaluation.csv` at full precision
(`AGENTS.md:209`). For any run you have already done:

```python
import numpy as np, pandas as pd
d = pd.read_csv("evaluation.csv")
terms = {
    "aesthetic":      np.log(d.aesthetic_score.clip(lower=1e-6)),
    "absolute_focus": np.log(d.absolute_focus_factor),
    "relative_focus": np.log(d.relative_focus_factor),
    "musiq":          np.log((d.musiq_score / 100).clip(1e-6, 1)),
    "exposure":       np.log(d.exposure_penalty.clip(lower=1e-6)),
    "eye":            np.log(d.eye_factor.clip(lower=1e-6)),
    "subject":        np.log(d.subject_integrity.clip(lower=1e-6)),
}
print(pd.Series({k: v.std() for k, v in terms.items()}).sort_values(ascending=False))
```

Multiply each standard deviation by that term's weight and you have the real version of the
table in section 1.3, computed on your photographs instead of my assumed bands. This tells
you which weights are worth calibrating before you spend a weekend labeling. My prediction,
stated in advance so it can be wrong: MUSIQ and relative focus will dominate, exposure will
be near-constant on a well-exposed collection, and subject integrity will be nearly a
constant offset rather than a discriminator.

Also check for degenerate terms. If `exposure_penalty` is 1.0 for 98 percent of rows, its
weight is tuning noise, and the 2 percent and 5 percent thresholds
(`PotentialEnhancements.md:15-17`) matter more than `exposure_weight` does.

### Tier 1: fit the weights by pairwise logistic regression

The multiplicative form is a gift here. Because `ln(composite)` is linear in the weights,
fitting them to preference data is a convex problem with a closed-form gradient, not a
search over a grid.

For an ordered pair where you preferred image *i* over image *j*, a Bradley-Terry model
gives:

```text
P(i > j) = sigmoid( ln C_i - ln C_j )
         = sigmoid( d_aesthetic + sum_k  w_k * d_k )
```

where each `d_k = ln f_k(i) - ln f_k(j)`. That is ordinary logistic regression on
difference features, with no intercept, and `w` is the coefficient vector. Six or seven
parameters. A few hundred pairs is plenty. Constrain `w >= 0` so a factor cannot invert into
a reward, and you get standard errors for free, which is the genuinely useful output: you
will learn which weights are statistically indistinguishable from zero.

**Where the pairs come from matters more than the fitting.** Use within-burst pairs. Every
burst you review yields "I kept frame 4" plus n-1 losers, and inside a burst the scene,
light, lens, and subject are held constant, so the comparison isolates exactly the
differences the weights are supposed to arbitrate. Across-burst pairs (a great landscape
against a bad indoor snapshot) are easy, plentiful, and nearly uninformative: any sane
weight vector gets them right. See section 2.3 for why the current review page cannot
produce within-burst pairs, which is the single blocking defect in the calibration loop.

The focus floor is the one parameter that is *not* linear in log space, because it sits
inside the logarithm. Handle it with a small outer grid (three or four values), refitting
the linear weights at each. That is cheap; see section 2.2 for why re-scoring costs
seconds.

On per-preset fitting: with roughly seven parameters per preset and four presets, fitting
each independently on a few hundred labels will overfit. Fit one shared vector on all
labels first, then introduce a genre deviation only where a likelihood-ratio test on that
genre's own labels justifies it. Most presets will probably not earn a deviation, and that
is a finding, not a failure.

### Tier 2: replace the hand-written prompts with a learned probe

Given the Tier 1 labels, the same embeddings that are already sitting in the cache
(`evaluation_cache`, 768 floats per image) support a logistic regression that learns the
best discriminating direction directly, instead of inheriting whatever direction two
English sentences happened to produce. Inference cost is identical: one dot product. The
prompts, the temperature, and the `cos(pos, neg) = 0.859` problem in section 1.4 all
disappear together.

This is close to what `PotentialEnhancements.md:13` already contemplates under "model
customization", and `--aesthetic-head` already provides the loading mechanism. The new part
is the framing: within-burst pairs make the training set nearly free, and the same labels
serve Tier 1 and Tier 2 at once.

### What not to do

Do not add YOLO-World, MediaPipe, or autofocus-coordinate mapping first.
`PotentialEnhancements.md:19-37` is right that all of them are gated on the same missing
labels, and Tier 0 will very likely show that the terms they would improve are not where
the leverage is.

---

# 2. The calibration process

## 2.1 What exists

The loop is:

1. **Produce** a run. `evaluation.csv` gets 34 columns at full precision, plus a
   `feedback.csv` template containing every successful candidate, plus `review.html`.
2. **Label.** Click Keep or Reject in `review.html`, then Download feedback.csv. Or edit the
   generated template by hand.
3. **Measure.** `photo-cull-validate evaluation.csv feedback.csv` emits eight fields, of
   which two are the real metrics: `precision_at_keep_count` and `pairwise_accuracy`.
4. **Change.** Hand-edit the weights in `scoring.py`.
5. **Repeat** from step 1.

That is a complete and honest measurement harness. Labels go in, agreement numbers come
out, the numbers are not gamed, and nothing pretends to be validated that is not
(`AGENTS.md:259-270` is an explicit list of what has not been checked).

## 2.2 One property that makes calibration far cheaper than the docs let on

The composite score is computed after the cache, from cached metrics. Preset weights are not
part of the cache identity, and neither is any hand edit you make to `scoring.py`. So step 4
followed by step 1 is a cache hit: on an already-evaluated collection, testing a new weight
vector costs seconds, not a full decode plus MUSIQ plus CLIP pass.

The README explains this for `--preset` switching (`readme.md:883-898`) but never connects
it to weight tuning, which is the use that matters. A weight sweep over hundreds of
candidate vectors is entirely practical today. This is the strongest existing foundation for
calibration in the project, and it is undocumented as such.

## 2.3 The blocking defect: the labeling UI cannot see burst losers

`cull.py:622-629` builds `candidate_pool` from the burst **winners**, plus any images the
feedback file already forces in. `cull.py:655` then slices that pool to
`--contact-sheet` entries, default 100, and that slice is what `review.html` renders.

Consequence: **the only point-and-click labeling interface in the project can never show
you a frame that lost its burst.** It shows at most 100 burst winners.

That is precisely the wrong half of the data. Section 1.3 measured the relative-focus
exponent as the highest-leverage preset knob in three of four presets, and section 1.5
argued that within-burst pairs are the only pairs that isolate the hard decision. Both need
burst losers. The review page structurally cannot produce them.

`feedback.csv` *is* complete (`cull.py:648` passes all records), so the labels are reachable
by hand-editing a CSV of every photograph in the shoot with no thumbnails attached. Nobody
is going to do that for a thousand frames. In practice the labeling UI defines what gets
labeled.

The fix is small and does not disturb the ranking path: add burst siblings of each displayed
winner to the review page, grouped visually, so a click can say "not this one, that one".
That single change turns the review page from a validation tool into a training-data
generator, and it is the highest-value item in this document.

## 2.4 The feedback template pre-agrees with the program

`portfolio.write_feedback_template` writes `decision=keep` for exactly the images the
program selected (`portfolio.py:195`), which `readme.md:618` states as a plain fact with no
warning attached.

For its intended use, carrying a decision forward into the next run, that is correct and
convenient. For validation it is contaminating. A user who starts from the template and
edits a handful of rows returns a label set that agrees with the ranking by construction,
and both `precision_at_keep_count` and `pairwise_accuracy` are inflated by an amount nobody
can estimate afterward.

`review.html`, by contrast, starts empty and records only decisions you actually made
(`readme.md:586-588`). It is the correct source for validation labels, and the docs should
say which file is for which purpose.

## 2.5 `precision_at_keep_count` is not comparable across sessions

`validation.py:31-41` ranks **all** rows by composite score, takes the top K where K is the
number of keeps, and reports what fraction of keeps land inside it. Unlabeled rows compete
for those slots.

So if you label 20 keeps out of 1,200 photographs and the program's top 20 contains 15
images you simply never looked at, precision reads 0.25 even when the ranking is perfect.
The metric is only meaningful under dense labeling of the top of the ranking, and its value
depends on your labeling coverage as much as on the ranking. Two sessions labeled at
different densities cannot be compared, and the README's instruction to "use results from
multiple representative sessions" (`readme.md:974`) does not mention this.

Related, and smaller: `_common_input_root` uses `os.path.commonpath` and raises a clear
error when rows do not share a root, so pooling two shoots works only when they sit under a
common directory, and never across volumes. Even when it works, pooling computes one global
precision rather than a mean of per-session precisions, which are different statistics.

## 2.6 `pairwise_accuracy` mixes the easy question with the hard one

`validation.py:42-47` forms the full cross product of keeps and rejects. That pools two very
different comparisons: "is this good photograph better than that bad one" (easy, and
dominated by the aesthetic and MUSIQ terms) with "is frame 4 better than frame 5 of the same
burst" (hard, and the reason the tool exists).

A weight vector can post 0.95 pairwise accuracy while being useless at burst selection,
because the easy pairs outnumber the hard ones. Stratify: report pairwise accuracy within
burst and across burst as two numbers. `burst_id` is already a column in `evaluation.csv`,
so this is a few lines in `validation.py`.

## 2.7 What is missing outright

- **No fitting step.** Step 4 of the loop is "open `scoring.py` in an editor". There is no
  sweep, no optimizer, and no way to score two weight vectors in one command, despite
  section 2.2 making that nearly free.
- **No held-out split.** Tuning seven parameters and reporting accuracy on the same labels
  will overfit, and nothing in the tool or docs mentions train/test separation.
- **No per-factor attribution.** `photo-cull-validate` reads only `composite_score` and
  `global_quality_rank`. It cannot say which factor caused a disagreement, cannot compare
  presets on one label set, and cannot report that a weight is doing nothing. Every column
  it would need is already in the file it opens.

## 2.8 Summary for question 2

The plumbing is complete and honest. The method is absent. Specifically: labels cannot be
collected where they matter (2.3), the convenient label source pre-agrees with the program
(2.4), the headline metric is not comparable across sessions (2.5), the second metric
answers the easy question (2.6), and there is no fitting step, no split, and no attribution
(2.7). Every one of those is fixable within the existing architecture, and 2.3 is the one
that gates the rest.

---

# 3. Command-line documentation

## 3.1 Thirteen of twenty-four flags have no help text

Verified by running `uv run photo-cull --help`. These options print their name and nothing
else:

`--preset`, `--burst-window`, `--max-burst-duration`, `--phash-threshold`,
`--sim-threshold`, `--no-group`, `--device`, `--batch-size`, `--workers`,
`--no-mixed-precision`, `--metadata-backend`, `--no-cache`, `--refresh-cache`.

`--preset` shows only the four choice names. Eleven options do carry `help=` strings, so the
gap is an inconsistency inside `build_parser` (`cull.py:1340-1392`) rather than a
convention.

This matters more than it looks, for two reasons. `readme.md:160-164` directs the user to
`--help` as the authoritative current option list, and `AGENTS.md:200` requires the README
and specification to be updated when an option changes but says nothing about argparse help,
so the gap has no rule keeping it closed.

The README tables (`readme.md:240-309`) are genuinely good. The terminal is not.

## 3.2 No document says what a preset does

The wildlife preset is a fair test case, and you named it correctly. It appears in the
user-facing documentation in exactly three places:

- `readme.md:252`, one command-reference row: "Select a scoring profile."
- `readme.md:515`, one row of digits in the weights table.
- `readme.md:182-187`, a four-flag example with no explanation.

Nothing in `readme.md`, `Specifications.md`, or `--help` tells a user any of the following,
all of which are real behavioral differences:

**It enables a CLIP subject-integrity comparison, and the prompts are invisible.** The two
prompts are the preset's actual behavior:

```text
positive: "a wildlife photograph with the complete animal clearly in frame"
negative: "a wildlife photograph where the animal is cut off or leaving the frame"
```

They live at `advanced_analysis.py:10-13` and appear in no user-facing document.
`docs/how-a-photograph-is-judged.md:172-173` paraphrases them as "roughly" that, which is
the closest anything comes. A user cannot judge whether a heuristic fits their shooting
without seeing the sentence it is built from, and they cannot report that it misfires
without knowing what it claims to test.

**Its numbers encode an intent the table cannot express.** Wildlife carries the highest
relative-focus exponent of the four (1.8) and the second-most-forgiving absolute-focus
penalty (0.618 worst case, against balanced's 0.500). Read together that says: judge a frame
against its burst siblings, and forgive absolute softness, because a distant animal in poor
light is inherently soft and the real question is which frame of the burst caught it. That
is a coherent and defensible design. The weights table gives the digits and none of the
reasoning, so a user cannot tell whether the preset matches their situation.
`docs/how-a-photograph-is-judged.md:250-253` gets closest, in one sentence, for two presets.

**Its cost profile differs from the other presets.** Switching an evaluated collection to
`wildlife` needs only the CLIP text tower, so it decodes nothing, while switching to
`portrait` re-decodes every image for the face and eye cascades. That is a large practical
difference, and it is documented only in the cache section (`readme.md:888-894`), four
hundred lines from the preset documentation and not cross-referenced from it.

**The workflow example bundles unrelated flags.** `readme.md:185` shows
`--preset wildlife --diversity 0.25 --select 40 --write-xmp`, and nothing says that
`--diversity 0.25` is a personal preference unrelated to the preset. A reader copying that
line reasonably concludes diversity is part of wildlife culling.

The same three gaps apply to `landscape` and `portrait`. `balanced` additionally deserves one
sentence saying it defines no prompts and therefore loads no model on a preset switch
(`readme.md:888-889` says this; the preset documentation does not).

## 3.3 One factual error

`readme.md:494`:

> The portrait check gives an eye factor of 0.85 when it finds no face.

`advanced_analysis.py:62` returns **1.0**. Verified: the string `0.85` appears nowhere in
the Python source, `Specifications.md:94` says 1.0, and `readme.md:486-489`, eight lines
earlier in the same section, correctly explains that an absent detection is neutral and why.

So one line contradicts the code, the specification, and its own section. It looks like a
survivor of an earlier design in which a missing face was penalized, and it is the kind of
line a user would reasonably act on.

## 3.4 Suggested shape of the fix

Three changes, one commit, per `AGENTS.md:200`:

1. Add `help=` to the thirteen options in `build_parser` (`cull.py:1340-1392`). Keep each to
   one line and let the README hold the detail. For `--preset`, name what changes:
   "Scoring profile: weights, focus floor, and genre subject prompts."
2. Add a `### Scoring presets in detail` section to `readme.md`, after the weights table at
   line 517, with one short subsection per preset covering four things: what it changes,
   the subject prompts verbatim where they exist, the intent behind its numbers, and its
   cost on a preset switch. Cross-reference it from `readme.md:252` and from the cache
   section at `readme.md:883`.
3. Correct `readme.md:494` to 1.0, matching `advanced_analysis.py:62` and
   `Specifications.md:94`.

A fourth, optional: a test asserting every non-store-true option in `build_parser` has a
non-empty `help`, so the gap cannot silently reopen. `AGENTS.md:229` already lists CLI help
as a required check area.

---

# Coverage and limits

Source reading, git history, and local execution only. Executed here: the full test suite
(153 passed, 1 skipped), `photo-cull --help`, arithmetic over the `scoring.py` constants,
the CLIP text tower on the pinned revision for the prompt-pair cosines, and a direct read of
`logit_scale` from the pinned `model.safetensors`.

Not done, and material to how much weight sections 1.3 and 1.5 can bear: no real photograph
collection was evaluated, so every band marked **assumed** is illustrative. The leverage
ordering in section 1.3 is a prediction, and the Tier 0 snippet in section 1.5 exists
precisely so it can be checked against your own data and, if wrong, discarded. No labeled
keep/reject set was available, so no weight was fitted and no claim here is empirical about
ranking quality. Consistent with `AGENTS.md:259-270`, nothing in this document validates MPS
or CUDA sessions, autofocus mapping, or XMP round trips.
