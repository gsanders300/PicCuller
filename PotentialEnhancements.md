# Enhancement Status and Future Work

This file tracks the original enhancement ideas: what exists today, what remains, and what
blocks it. It does not repeat the system specification.

## Planned work

This order was chosen on 2026-11-17. Photo Cull is used as a first-pass filter: it cuts a
shoot down and collapses bursts, and the final choice happens in Explorer, Finder, or
Lightroom. So the measure that matters most is how rarely it drops a photo you'd keep.

1. **Tuning screen.** Done in 1.2.0: counting modes, counts and percentages, weights, burst
   looseness, and the `show` preview. See [Tuning screen](docs/reference.md#tuning-screen).
2. **Speed baseline.** Next. Time one real shoot on the current code before step 4 changes
   anything. On the computer you cull with:
   1. Update: `git pull`, then `uv sync --locked`.
   2. Run a typical shoot from scratch: `uv run photo-cull <shoot folder> --refresh-cache`.
      It evaluates every photo again, so it takes as long as a first run.
   3. Use the tuning screen as usual: for example `10%`, then `show`, then Enter on an empty
      line. See [A typical session](readme.md#a-typical-session).
   4. Keep that run's `run.json`, in `<shoot folder>/.photo-cull/run_.../`. Its
      `stage_seconds` and `stage_counts` are the baseline.

   [Measure a run's speed](docs/reference.md#measure-a-runs-speed) explains how to compare
   two runs.
3. **Decisions from the file manager.** Read your pruning of `picks/` back as keep and
   reject decisions, so every culled shoot adds feedback without the review page:
   - A photo left in `picks/` is a keep, and a deleted one is a reject.
   - `picks/_alternates/` holds burst runners-up within 10% of their winner, outside your
     count. Moving one into `picks/` records that the wrong frame won its burst.
   - `picks/_audit/` holds about 20 random photos from below the cut, spread across score
     bands, outside your count. Moving one into `picks/` records a missed keeper, which is
     the only way to measure how many keepers the cut drops.
   - A folder deleted without being opened counts as unreviewed, not rejected.
4. **Faster evaluation with unchanged scores.**
   - Prefetch decodes by file, so `--workers` helps above `--batch-size`.
   - Run MUSIQ on several photos of identical size at once, if `pyiqa` supports it.
   - Run face and eye checks on the decode threads.

   Half-precision MUSIQ stays out unless timing shows MUSIQ still dominates. Each change is
   checked against the step 2 baseline.
5. **New measurements, in one cache change.**
   - Face and eye checks on every photo, so the `eyes` weight works without
     `--preset portrait`.
   - Sharpness measured around the autofocus point, for Sony cameras only, when ExifTool
     reads one. Other cameras are unchanged.
   - Look-alike groups: burst winners that are nearly identical and within 5 minutes of
     each other collapse to the best one. The `bursts` setting adjusts both.

   Every shoot is evaluated again, once.
6. **Weights fitted to your decisions.** `photo-cull-validate` fits the weights to the
   decisions from step 3, tests them by leaving out one shoot at a time, and offers them in
   the tuning screen as `start fitted`. It needs at least 3 shoots and 30 missed keepers or
   wrong winners. A personal aesthetic model waits for about 2,000 decisions.

## Focus verification

- **Today:** Genre profiles weight focus with per-preset floors and exponents. ExifTool
  records available AF descriptions (text such as `Center`) in `evaluation.csv`. No scoring
  path uses them, and no numeric `(X, Y)` coordinate is parsed.
- **Remaining work:** Add tested, vendor-specific coordinate mappings before AF points are
  used as a scoring region.
- **Blocker:** MakerNote coordinate systems differ per vendor and per body, and mapping them
  onto a downscaled preview is untested. Scoring the wrong region is worse than the generic
  grid.
- **Not needed:** A `PyExifTool` dependency. The bulk `exiftool` call already reads these
  tags.

## Subject integrity

- **Today:** The wildlife, portrait, and landscape presets use two-prompt CLIP integrity
  scores, each with a non-zero weight. All three weights are uncalibrated.
- **Remaining work:** Calibrate the three weights against labeled feedback. Evaluate
  YOLO-World or segmentation only if that shows the CLIP prompts are insufficient.

## Metadata tagging

- **Today:** Optional 3–5 star XMP ratings are written beside exported copies only. Each is
  matched to one asset family and written as exactly one rating per sidecar, using the
  standard library XML writer.
- **Remaining work:** Validate round trips with each target version of Lightroom,
  Capture One, and darktable.
- **Refused by design:** **Writing sidecars next to the originals, so picks appear in a
  catalogue without copying files, is refused by design, not unbuilt.** It would breach the
  read-only source invariant. It needs an explicit opt-in flag and a conscious decision, not
  a quiet extension. A `pyexiv2` dependency would only be worth considering as part of that
  decision.

## Ingestion throughput

- **Today:**
  - single-pass decode in the evaluation phase
  - aspect-correct JPEG draft scaling, also applied to RAW previews
  - summed-area tile variance
  - bounded threaded decode
  - CLIP batches, CUDA AMP, batch splitting, and device fallback
  - per-stage seconds and counts in `run.json`
  - an evaluation reused, not repeated, when `--preset` changes
  - review thumbnails shared across runs, so a fully cached run decodes nothing
- **Known limit:** Decode prefetch is expressed in batches, not files. The drain loop
  resolves every future in the current batch before it submits the next, so effective decode
  concurrency is `min(--workers, --batch-size)`. Measured peak concurrent decodes with
  `--batch-size 8`:

  | `--workers` | Peak concurrent decodes |
  | ---: | ---: |
  | 4 | 4 |
  | 8 | 8 |
  | 16 | 8 |
  | 32 | 8 |

- **Remaining work:**
  - Express the prefetch window in files, so `--workers` is not inert above `--batch-size`.
  - Consider a dedicated MUSIQ batch strategy only if native-aspect evaluation can be
    preserved.
  - Use `stage_seconds` to compare real sessions.

## Exposure clipping

- **Today:** Clipping is measured per channel, so a saturated single channel is no longer
  hidden by a luminance conversion.
- **Caveat:** The 2% and 5% penalty thresholds were carried over unchanged. They are
  uncalibrated against the larger per-channel fractions.

## Facial and eye checks

- **Today:** The portrait preset uses bundled OpenCV face and eye cascades, and exposes
  warnings and raw counts. An absent detection is neutral, so a cascade miss does not
  penalize the photograph.
- **Remaining work:** Compare MediaPipe or a modern landmark model on labeled portrait
  sessions before adding its dependency.

## Model customization

- **Today:** `--aesthetic-head` loads a compatible personal head. Feedback files and
  `photo-cull-validate` measure ranking agreement.
- **Remaining work:** Add an explicitly versioned training command once enough personal
  keep/reject labels exist.

## Terminal usability

- **Today:** The streaming Rich interface:
  - separates display and input modes
  - reports static progress and direct model downloads
  - prints the run path early
  - explains each winner's score in plain words
  - lists every exported photo with the reason it was chosen
  - re-prompts bad input
  - handles discovery interrupts
  - writes metrics before the tuning screen
  - tunes the selection after evaluation: counting mode, count or percentage, weights,
    diversity, and burst looseness, with a file-manager preview of the cut
- **Remaining work:** Integrate Hugging Face download output with the line-oriented
  reporter. Add operating-system-backed pseudo-terminal coverage.
- **Known limit:** Active Pillow or LibRaw calls cannot be cancelled safely. Queued decodes
  are cancelled.

## What actually blocks most of this

Four of the areas above are waiting on the same thing: focus verification, subject
integrity, facial and eye checks, and model customization. The missing piece is not a
dependency or an algorithm. It is a body of real keep and reject decisions over your own
photographs.

Without labels, none of the open questions can be answered:

- Does an open-vocabulary detector such as YOLO-World beat the two-prompt CLIP score?
- Do MediaPipe landmarks beat the OpenCV cascades?
- Does AF-targeted sharpness beat the generic grid?
- What should a fine-tuned aesthetic head optimize for?

Adding any of those dependencies first would be adding weight on faith.

The same labels are needed to calibrate the values that are uncalibrated by admission: the
three subject weights, the per-preset focus and exposure weights, and the 2% and 5%
exposure-clipping thresholds.

Collecting them needs no new code:

1. Review a real shoot in `review.html`, or edit the run's own `feedback.csv`.
2. Download the feedback CSV from the page, if you used it.
3. Run `photo-cull-validate` against that run's `evaluation.csv`.

That one dataset unlocks four areas at once, and shows which are worth the dependency.

## Candidate follow-ups

- Camera-specific clock-offset calibration for multi-camera shoots.
- Color-managed preview conversion, with per-channel clipping shown per channel rather than
  as one combined fraction.
- Optional duplicate detection beyond the temporal burst window.
- A local service UI, if the static contact sheet becomes limiting.
- Signed model manifests or vendored model artifacts for offline deployments. SHA-256
  verification is mandatory today; nothing is cryptographically signed or vendored.
