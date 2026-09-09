# Photo Cull System Specification

## 1. Processing contract

Photo Cull recursively discovers supported images, evaluates one configurable primary
per same-stem asset family, ranks all successful images, selects one winner per burst,
and optionally exports a portfolio subset. Sources are read-only.

Supported RAW extensions include ARW, CR2, CR3, DNG, NEF, ORF, RAF, RW2, PEF, SRW,
SR2, 3FR, ERF, IIQ, KDC, MOS, MRW, and X3F. Standard formats include JPEG, PNG,
WebP, TIFF, and BMP. Actual RAW decoding remains subject to the bundled LibRaw build.

Generated `.photo-cull` and timestamped legacy output directories must never be
rediscovered as source input. Discovery order is deterministic and symbolic-link
directories are not followed.

## 2. Metadata and ingestion

The preferred `auto` metadata backend uses one bulk ExifTool process when `exiftool`
is available. It reads capture/create time, subseconds, UTC offset, camera identity,
image/sequence number, and available AF-point descriptions.

The fallback reads standard and nested EXIF IFDs from the same Pillow/RAW-preview
decode used for scoring. Missing offsets use `--assume-timezone` when supplied,
otherwise the current system-local offset, and are labeled
`system_local_assumption`. Missing or malformed capture time falls back to the
timezone-aware filesystem modification time and is labeled `filesystem_mtime`.

JPEG decoding uses Pillow draft scaling where available. EXIF orientation is applied
before all metrics. RAW files use an embedded preview when possible and half-size
demosaicing otherwise. Images are reduced to a maximum dimension of 1024 pixels.

## 3. Metrics and scoring

### Focus

The grayscale preview receives one Laplacian transform and is divided into a 16×16
grid. Focus energy is the mean variance of the sharpest 3% of tiles. Remainder pixels
are retained by array splitting.

Because raw focus values depend on scene detail, each image receives an empirical
session percentile `P_focus`. A preset-specific moderate absolute gate is:

```text
F_absolute = focus_floor + (1 - focus_floor) × P_focus
```

Within a burst, `F_relative = Focus / max(Focus in burst)`.

### Exposure

For 8-bit rendered luminance `Y`:

```text
P_white = fraction(Y >= 254)
P_black = fraction(Y <= 1)
M_exposure = max(0.4, 1 - 5 × max(0, P_white - 0.02))
             × max(0.6, 1 - 2 × max(0, P_black - 0.05))
```

This describes preview clipping and is not a claim about recoverable RAW latitude.

### Neural metrics

- MUSIQ supplies a no-reference technical-quality score normalized to `[0, 1]`.
- Pinned CLIP ViT-L/14 embeddings feed the verified LAION aesthetic head.
- Genre presets optionally compare normalized image embeddings with positive and
  negative subject-integrity prompts.
- Portrait mode uses OpenCV face/eye cascades to emit an advisory eye factor and warning.

### Composite

For scoring profile exponents `w` and relative focus exponent `r`:

```text
Composite = max(0, Aesthetic)
            × F_absolute ^ w_absolute_focus
            × F_relative ^ r
            × clip(MUSIQ / 100, 0, 1) ^ w_musiq
            × M_exposure ^ w_exposure
            × EyeFactor ^ w_eye
            × SubjectIntegrity ^ w_subject
```

Preset weights are defined in `scoring.py`, included in `run.json`, and deliberately
treated as heuristics requiring validation against human feedback.

## 4. Burst grouping and ranks

Records are sorted by aware capture time and normalized path. Two consecutive frames
may join when all of the following hold:

1. Their time gap is at most `--burst-window` (default 2 seconds).
2. The total cluster duration remains within `--max-burst-duration` (default 10 seconds).
3. Known camera identities do not conflict.
4. pHash Hamming distance is at most 8, or normalized CLIP similarity is at least 0.88.

The CSV distinguishes `global_quality_rank`, `burst_rank`, and `selection_rank`.
Ties use normalized source paths for deterministic ordering.

## 5. Throughput and device behavior

Image decoding and CPU metrics use a bounded thread pool. Each bounded group is passed
to CLIP as a mini-batch. CUDA inference uses automatic mixed precision by default.
On an out-of-memory error, CLIP batches split recursively; a single-image OOM or an
unsupported MPS operation moves all models to CPU and retries. MUSIQ remains per-image
to avoid padding or distorting aspect ratios.

Same-stem RAW/JPEG pairs default to one RAW primary, avoiding duplicate inference while
retaining all family files for export. `--primary jpeg` and `--primary all` override
that behavior.

## 6. Cache, audit, and failure behavior

Each successful evaluation is committed immediately to a WAL-mode SQLite database.
The lookup identity contains resolved path, size, nanosecond modification time, model
identities, algorithm version, image size, metadata backend, assumed timezone, and
preset. The cache stores no executable pickle data.

Every run directory is created before model initialization and contains an atomically
updated `run.json`. It records phase/status, timings, versions, settings, counts, model
revisions/checksums, and output paths. Per-file failures are immediately written to
`failures.csv`. Ctrl-C marks the run interrupted; cached successes remain resumable.

`evaluation.csv` is written atomically and retains full-precision metrics. Display
rounding occurs only in the terminal.

## 7. Terminal interface

The Rich interface is scrollback-safe rather than full-screen:

1. Scanning/model/metadata status indicators.
2. Environment panel with device, paths, candidate count, backend, preset, and grouping.
3. Evaluation progress with spinner, fraction, percentage, and ETA.
4. Top-candidate table containing selection/global ranks and principal metrics.
5. Interactive selection prompt only when stdin and the console are terminals.
6. Export confirmation panel.

`--plain` disables animation and color. `--select N|all|none` supports scripts and CI.
EOF is treated as `none`, and Ctrl-C exits with code 130 after updating the run audit.

## 8. Portfolio review and feedback

`review.html` contains local thumbnails, scores, keep/reject controls, and a browser-side
feedback CSV download. A later `--feedback` CSV pins keeps and removes rejects.

`--diversity` uses maximal marginal relevance over normalized CLIP embeddings to trade
off composite quality against similarity to already selected images. Zero is pure
quality ranking; one maximizes novelty.

`photo-cull-validate` compares feedback with an evaluation and reports precision at the
number of keeps, keep-versus-reject pairwise accuracy, and mean global ranks. Compatible
personal aesthetic-head weights can be supplied with `--aesthetic-head`; their hash is
recorded for reproducibility.

## 9. Export contract

Selections are planned completely before copying. The plan includes same-stem and
compound sidecars, deduplicates families, preserves relative paths, detects
case-insensitive collisions, rejects resolved sources outside the input root, and
refuses existing destinations. Each copy is published through a temporary file.

`export_manifest.csv` records source, destination, and size. `--write-xmp` creates or
updates ratings only inside `picks/`, never in the source tree.

## 10. Reproducibility and verification

- Direct dependency ranges are constrained and `uv.lock` is committed.
- CLIP is pinned to a Hugging Face revision.
- The aesthetic head uses a commit-pinned URL and mandatory SHA-256 verification.
- CI runs tests, Ruff, and a CLI smoke test on macOS and Windows.
- Tests cover file operations, cache migration/identity, timestamps, scoring, bursts,
  diversity, XMP, validation, audit output, and a mocked end-to-end pipeline.
