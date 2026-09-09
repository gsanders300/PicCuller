# Enhancement Status and Future Work

This file tracks the original enhancement ideas without duplicating the system
specification.

| Area | Current implementation | Remaining work |
| --- | --- | --- |
| Focus verification | Genre profiles weight focus with per-preset floors and exponents. ExifTool records available AF descriptions in `evaluation.csv`; no scoring path consumes them yet. | Add tested, vendor-specific coordinate mappings before using AF points as a scoring region. |
| Subject integrity | Wildlife, portrait, and landscape presets use two-prompt CLIP integrity scores, each with a non-zero weight. All three weights are uncalibrated. | Calibrate the three weights against labeled feedback. Evaluate YOLO-World or segmentation only if that shows CLIP prompts are insufficient. |
| Metadata tagging | Optional 3–5 star XMP ratings are written beside exported copies only, matched to one asset family and written as exactly one rating per sidecar. | Validate round trips with each target version of Lightroom, Capture One, and darktable. |
| Ingestion throughput | Single-pass decode in the evaluation phase, aspect-correct JPEG draft scaling (also applied to RAW previews), summed-area tile variance, bounded threaded decode, CLIP batches, CUDA AMP, batch splitting, and device fallback. `run.json` records per-stage seconds and counts. | The review page re-decodes each thumbnail, so a cache-only run still decodes; share thumbnails across runs. Consider a dedicated MUSIQ batch strategy only if native-aspect evaluation can be preserved. Use `stage_seconds` to compare real sessions. |
| Facial and eye checks | Portrait preset uses bundled OpenCV face/eye cascades and exposes warnings and raw counts. An absent detection is neutral, so a cascade miss does not penalize the photograph. | Compare MediaPipe or a modern landmark model using labeled portrait sessions before adding its dependency. |
| Model customization | `--aesthetic-head` loads a compatible personal head; feedback files and `photo-cull-validate` measure ranking agreement. | Add an explicitly versioned training command after enough personal keep/reject labels exist. |

Exposure clipping is now measured per channel, so a saturated single channel is no
longer hidden by a luminance conversion. The 2% and 5% penalty thresholds were carried
over unchanged and are uncalibrated against the larger per-channel fractions.

## Candidate follow-ups

- Camera-specific clock-offset calibration for multi-camera shoots.
- Color-managed preview conversion, and per-channel clipping shown per channel rather
  than as one combined fraction.
- Optional duplicate detection beyond the temporal burst window.
- A local service UI if the static contact sheet becomes limiting.
- Signed model manifests or vendored model artifacts for offline deployments. SHA-256
  verification is mandatory today; nothing is cryptographically signed or vendored.
- Take `--preset` out of the cache signature so preset comparison stops costing a full
  evaluation pass per preset.
- A portable cache key: the primary key is an absolute resolved path, so remounting a
  collection at another drive letter or volume name discards every row.
