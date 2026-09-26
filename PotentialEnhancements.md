# Enhancement Status and Future Work

This file tracks the original enhancement ideas without duplicating the system
specification.

| Area | Current implementation | Remaining work |
| --- | --- | --- |
| Focus verification | Genre profiles weight focus with per-preset floors and exponents. ExifTool records available AF descriptions (text such as `Center`) in `evaluation.csv`; no scoring path consumes them, and no numeric `(X, Y)` coordinate is parsed. | Add tested, vendor-specific coordinate mappings before using AF points as a scoring region. The blocker is that MakerNote coordinate systems differ per vendor and per body, and mapping them onto a downscaled preview is untested; scoring the wrong region is worse than the generic grid. A `PyExifTool` dependency is not required: the bulk `exiftool` call already reads these tags. |
| Subject integrity | Wildlife, portrait, and landscape presets use two-prompt CLIP integrity scores, each with a non-zero weight. All three weights are uncalibrated. | Calibrate the three weights against labeled feedback. Evaluate YOLO-World or segmentation only if that shows CLIP prompts are insufficient. |
| Metadata tagging | Optional 3–5 star XMP ratings are written beside exported copies only, matched to one asset family and written as exactly one rating per sidecar, using the standard library XML writer. | Validate round trips with each target version of Lightroom, Capture One, and darktable. **Writing sidecars next to the originals, so picks appear in a catalogue without copying files, is refused by design, not unbuilt.** It would breach the read-only source invariant, so it needs an explicit opt-in flag and a conscious decision, not a quiet extension. A `pyexiv2` dependency would only be worth considering as part of that decision. |
| Ingestion throughput | Single-pass decode in the evaluation phase, aspect-correct JPEG draft scaling (also applied to RAW previews), summed-area tile variance, bounded threaded decode, CLIP batches, CUDA AMP, batch splitting, and device fallback. `run.json` records per-stage seconds and counts. Changing `--preset` reuses the evaluation instead of repeating it, and review thumbnails are shared across runs, so a fully cached run decodes nothing. | Decode prefetch is expressed in batches, not files: the drain loop resolves every future in the current batch before submitting the next, so effective decode concurrency is `min(--workers, --batch-size)`. Measured peak concurrent decodes with `--batch-size 8`: 4 workers gives 4, 8 gives 8, 16 gives 8, 32 gives 8. Express the prefetch window in files so `--workers` is not inert above `--batch-size`. Consider a dedicated MUSIQ batch strategy only if native-aspect evaluation can be preserved. Use `stage_seconds` to compare real sessions. |
| Facial and eye checks | Portrait preset uses bundled OpenCV face/eye cascades and exposes warnings and raw counts. An absent detection is neutral, so a cascade miss does not penalize the photograph. | Compare MediaPipe or a modern landmark model using labeled portrait sessions before adding its dependency. |
| Model customization | `--aesthetic-head` loads a compatible personal head; feedback files and `photo-cull-validate` measure ranking agreement. | Add an explicitly versioned training command after enough personal keep/reject labels exist. |
| Terminal usability | The streaming Rich interface separates display and input modes, reports static progress and direct model downloads, prints the run path early, explains each winner's score in plain words, lists every exported photo with the reason it was chosen, re-prompts bad input, handles discovery interrupts, and writes metrics before the selection prompt. | Integrate Hugging Face download output with the line-oriented reporter and add operating-system-backed pseudo-terminal coverage. Active Pillow or LibRaw calls cannot be cancelled safely; queued decodes are cancelled. |

Exposure clipping is now measured per channel, so a saturated single channel is no
longer hidden by a luminance conversion. The 2% and 5% penalty thresholds were carried
over unchanged and are uncalibrated against the larger per-channel fractions.

## What actually blocks most of this

Four of the six areas above (focus verification, subject integrity, facial and eye checks,
and model customization) are waiting on the same thing, and it is not a dependency or an
algorithm. It is a body of real keep and reject decisions over your own photographs.

Without labels there is no way to answer any of the open questions: whether an
open-vocabulary detector such as YOLO-World beats the two-prompt CLIP score, whether
MediaPipe landmarks beat the OpenCV cascades, whether AF-targeted sharpness beats the
generic grid, or what a fine-tuned aesthetic head should optimize for. Adding any of those
dependencies first would be adding weight on faith.

The same labels are also the prerequisite for calibrating the values that are currently
uncalibrated by admission: the three subject weights, the per-preset focus and exposure
weights, and the 2% and 5% exposure-clipping thresholds.

Collecting them needs no new code. Review a real shoot in `review.html`, download the
feedback CSV, and run `photo-cull-validate` against that run's `evaluation.csv`. That one
dataset unlocks four areas at once and says which are worth the dependency.

## Candidate follow-ups

- Camera-specific clock-offset calibration for multi-camera shoots.
- Color-managed preview conversion, and per-channel clipping shown per channel rather
  than as one combined fraction.
- Optional duplicate detection beyond the temporal burst window.
- A local service UI if the static contact sheet becomes limiting.
- Signed model manifests or vendored model artifacts for offline deployments. SHA-256
  verification is mandatory today; nothing is cryptographically signed or vendored.
