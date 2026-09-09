# Enhancement Status and Future Work

This file tracks the original enhancement ideas without duplicating the system
specification.

| Area | Current implementation | Remaining work |
| --- | --- | --- |
| Focus verification | ExifTool captures available AF descriptions, and genre profiles weight focus alongside subject integrity. | Add tested, vendor-specific coordinate mappings before using AF points as a scoring region. |
| Subject integrity | Wildlife, portrait, and landscape presets use two-prompt CLIP integrity scores. | Evaluate YOLO-World or segmentation only if labeled feedback shows that CLIP prompts are insufficient. |
| Metadata tagging | Optional 3–5 star XMP ratings are written beside exported copies only. | Validate round trips with each target version of Lightroom, Capture One, and darktable. |
| Ingestion throughput | Single-pass decode, Pillow JPEG draft scaling, bounded threaded decode, CLIP batches, CUDA AMP, batch splitting, and device fallback. | Benchmark real sessions; consider a dedicated MUSIQ batch strategy only if native-aspect evaluation can be preserved. |
| Facial and eye checks | Portrait preset uses bundled OpenCV face/eye cascades and exposes warnings and raw counts. | Compare MediaPipe or a modern landmark model using labeled portrait sessions before adding its dependency. |
| Model customization | `--aesthetic-head` loads a compatible personal head; feedback files and `photo-cull-validate` measure ranking agreement. | Add an explicitly versioned training command after enough personal keep/reject labels exist. |

## Candidate follow-ups

- Camera-specific clock-offset calibration for multi-camera shoots.
- Color-managed preview conversion and per-channel clipping diagnostics.
- Optional duplicate detection beyond the temporal burst window.
- A local service UI if the static contact sheet becomes limiting.
- Signed model manifests or vendored model artifacts for offline deployments.
