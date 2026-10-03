# Photo Cull System Specification

This document is the behavioral contract for Photo Cull: what the program must do, and
why. For the user guide, read `readme.md`. For every option, default, and output column,
read `docs/reference.md`.

Each section opens with a short summary of the contract, then lists the rules. Where a
rule's reason is not obvious, a **Why** note follows it. History of earlier schemas and
versions appears only as brief background.

## 1. Processing contract

Photo Cull recursively discovers supported images, evaluates one configurable primary
image per same-stem asset family, ranks all successful images, selects one winner per
burst, and optionally exports a portfolio subset. Sources are read-only.

### Supported formats

| Type | Formats |
| --- | --- |
| RAW | ARW, CR2, CR3, DNG, NEF, ORF, RAF, RW2, PEF, SRW, SR2, 3FR, ERF, IIQ, KDC, MOS, MRW, X3F |
| Standard | JPEG, PNG, WebP, TIFF, BMP |

Extension matching ignores letter case. Actual RAW decoding remains subject to the
bundled LibRaw build.

### Discovery rules

1. Discovery order is deterministic.
2. Symbolic-link directories are not followed, and symbolic-link files are not
   evaluated.
3. Generated output is never rediscovered as source input. This covers every directory
   named `.photo-cull`, the configured output root, and timestamped `run_` and legacy
   `picks_` directories.
4. AppleDouble companions, whose names begin with `._`, are excluded.
5. Operating-system directories that hold deleted or derived copies are excluded:
   `.Trashes`, `.Trash`, `.Spotlight-V100`, `.fseventsd`, `.TemporaryItems`, `@eaDir`,
   `$RECYCLE.BIN`, `System Volume Information`, and `lost+found`. Name matching ignores
   letter case.

### Discovery outcomes

| Situation | Result |
| --- | --- |
| A directory cannot be read | Reported as a `discovery` failure rather than silently omitted |
| A discovery error leaves no readable supported image | The run is audited as failed and exits with code 1 |
| A readable collection contains no supported images | A successful no-op |

## 2. Metadata and ingestion

Every evaluated image receives an aware capture time with a recorded source, and one
reduced, correctly oriented decode that every metric shares.

### Metadata backends

The preferred `auto` backend uses one bulk ExifTool process when `exiftool` is available.
Otherwise it uses the Pillow fallback. ExifTool reads:

- capture, create, and modify time, with subseconds and UTC offset
- camera identity (model and serial number)
- image or sequence number
- available AF-point descriptions

Rules for the ExifTool call:

1. Its output is decoded as UTF-8 rather than in the console locale.
2. It carries a deadline that scales with the collection size: 60 seconds plus 0.05
   seconds per file.
3. If `auto` selected ExifTool and the bulk call fails, the run continues with embedded
   metadata, records a `metadata_bulk` failure, and does not cache the new evaluations
   from that run. An explicit `--metadata-backend exiftool` fails instead.

**Why:** A Windows code page would mangle a non-ASCII path and lose its metadata. The
deadline stops a stalled mount from hanging the run, while still allowing a large
collection to take minutes.

The fallback reads standard and nested EXIF IFDs from the same Pillow or RAW-preview
decode used for scoring.

### Timestamps

Both backends try the original capture time first, then the creation time, then the
metadata modification time. The resulting labels are:

| Case | Time used | `timezone_source` |
| --- | --- | --- |
| The time carries a UTC offset | The embedded offset | `embedded_offset` |
| The time has no offset | `--assume-timezone` when supplied, otherwise the current system-local offset | `system_local_assumption` |
| The capture time is missing or malformed | The timezone-aware filesystem modification time, labeled `filesystem_mtime` | `filesystem` |

`timestamp_source` names the field that supplied the time, such as
`exiftool:DateTimeOriginal`, or `filesystem_mtime` for the last case.

### Decoding

1. JPEG decoding uses Pillow draft scaling where available, with an aspect-correct box.
2. RAW files use an embedded preview when possible, and half-size demosaicing with camera
   white balance otherwise. Embedded RAW previews receive the same draft scaling.
3. EXIF orientation is applied before all metrics.
4. Images are reduced to a maximum dimension of 1024 pixels.

**Why the box keeps the aspect ratio:** Pillow selects the DCT scale from
`min(width // box[0], height // box[1])`. A square box therefore measures the short side
and yields one step less reduction than the longest-side target; on a 3:2 frame it selects
none. Embedded RAW previews are frequently full sensor resolution, so they need the same
draft.

## 3. Metrics and scoring

Each image receives focus, exposure, and neural measurements. The composite score is the
aesthetic score multiplied by six preset-weighted factors, and every candidate also gets
a plain-language reason.

### Focus

1. The grayscale preview receives one Laplacian transform.
2. The result is divided into a 16×16 grid. Remainder pixels are retained by array
   splitting: tile boundaries match `numpy.array_split`.
3. Tile variances are read from a summed-area table and clamped at zero.
4. Focus energy is the mean variance of the sharpest 3% of tiles.
5. When a tile would be smaller than 8 pixels on either side, focus energy is the
   variance of the whole Laplacian image.

**Why:** The summed-area table makes cost independent of tile count. Cancellation can
drive an almost-flat tile slightly negative, hence the clamp.

Because raw focus values depend on scene detail, each image also receives an empirical
session percentile `P_focus` (tied values share their average rank). Two focus factors
follow:

```text
F_absolute = focus_floor + (1 - focus_floor) × P_focus    (preset-specific moderate gate)
F_relative = Focus / max(Focus in burst)
```

`F_relative` is 1.0 when the largest focus in the burst is zero, and under `--no-group`.

### Exposure

Clipping is measured per channel on the 8-bit rendered preview, not on a luminance
conversion. For channels `C`:

```text
P_white = fraction(any C >= 254)
P_black = fraction(all C <= 1)
M_exposure = max(0.4, 1 - 5 × max(0, P_white - 0.02))
             × max(0.6, 1 - 2 × max(0, P_black - 0.05))
```

**Why:** Highlights use any channel because a saturated channel has lost detail
regardless of luminance: BGR (50, 100, 255) converts to mid grey, so a luminance
measurement reported no clipping for a sunset whose red channel was fully clipped.
Shadows require every channel, because a single channel at zero is ordinary in a
saturated colour.

The 0.02 and 0.05 thresholds are carried over from the luminance measurement. They are
uncalibrated against per-channel fractions, which are larger for the same photograph.

This describes preview clipping and is not a claim about recoverable RAW latitude.

### Neural metrics

- MUSIQ supplies a no-reference technical-quality score on a 0-100 scale. The composite
  divides it by 100 and clamps it to `[0, 1]`.
- Pinned CLIP ViT-L/14 embeddings feed the verified LAION aesthetic head.
- Genre presets optionally compare normalized image embeddings with positive and negative
  subject-integrity prompts. Every preset defining prompts must also carry a non-zero
  `subject_weight`; otherwise the run pays for the comparison and discards it.
- Portrait mode uses OpenCV face and eye cascades to emit an advisory eye factor and
  warning:

| Detection | Eye factor | Warning |
| --- | ---: | --- |
| No face | 1.0 (neutral) | `No face detected` |
| A face with fewer than two eyes | 0.7 | `Possible closed/obscured eyes` |
| Otherwise | 1.0 | None |

**Why a miss is neutral:** The frontal cascade misses profiles, hats, and backlight, so a
miss describes the detector rather than the photograph. A detected face with too few eyes
is a statement about the photograph, so it does reduce the factor.

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

1. Preset weights are defined in `scoring.py`, included in `run.json`, and deliberately
   treated as heuristics requiring validation against human feedback.
2. `score_multipliers` returns the six weighted terms after the aesthetic base. The
   composite multiplies them left to right, so the stored `*_multiplier` columns
   reproduce `composite_score` exactly.
3. Explanations add no cache field and change no per-image metric, so they need no
   algorithm-version increment.

### Score reasons

After ranking, `describe_score_reasons` writes one `score_reason` per candidate by joining
these clauses with `; `:

1. **Burst result**, omitted under `--no-group`. A one-frame burst is `single shot`. The
   winner names the second frame; any other frame names the winner. Each names the score
   gap and the comparison factor with the largest ratio. Equal scores state that path
   order decides.
2. **Standing among all candidates** for aesthetic, sharpness (raw focus), and technical
   quality (MUSIQ). The strongest measurement is named at 25 percent or better, and the
   weakest remaining measurement at 25 percent or worse.
3. **Penalties.** Each exposure, eye, or subject multiplier at or below 0.95 is named with
   its percentage loss. A non-empty eye warning with no penalty, such as
   `No face detected`, is named without a loss.

A record with no clause reads `no standout strength or penalty`.

Details of the burst clause:

| Item | Rule |
| --- | --- |
| Score gap | A percentage of the higher score: `less than 1%`, a whole percent, `more than 99%`, or `100%` when the lower score is zero |
| Comparison factors | Aesthetic, sharpness (absolute times relative focus multiplier), technical quality, exposure, eye check, and subject check |

Details of the standing clause: the top percentage is the ceiling of 100 times the share
of candidates at or above the value. The bottom percentage uses the share at or below.

## 4. Burst grouping and ranks

Consecutive frames join a burst when they are close in time, come from the same camera,
and look alike. Three separate ranks are then assigned.

### Ordering

Records are sorted by aware capture time, then sequence number (numeric values before
text), then normalized source path.

### Join rules

Each frame is compared with the previous frame in the current burst. The two may join
when all of the following hold:

1. Their time gap is not negative and is at most `--burst-window` (default 2 seconds).
2. The total cluster duration, measured from the burst's first frame, remains within
   `--max-burst-duration` (default 10 seconds).
3. Known camera identities do not conflict. When both frames have serial numbers, they
   must match. Otherwise, camera models must not conflict when both are known.
4. The pHash Hamming distance is at most `--phash-threshold` (default 8), or the
   normalized CLIP similarity is at least `--sim-threshold` (default 0.88).

Otherwise the frame starts a new burst.

Configuration limits: `--burst-window` is not negative, `--max-burst-duration` is at least
`--burst-window`, `--phash-threshold` is not negative, and `--sim-threshold` is from 0
through 1.

Because only adjacent frames are compared, a frame from a second camera that falls between
two frames of one burst splits that burst.

`--no-group` puts every record in its own burst, numbered in timestamp order. The session
focus factor still affects the composite.

### Ranks

| Rank | Compares |
| --- | --- |
| `global_quality_rank` | All successful candidates |
| `burst_rank` | Candidates in one burst. Rank 1 is the burst winner. |
| `selection_rank` | Burst winners only. The value is empty for a non-winner. |

The CSV keeps these three ranks distinct. Rank 1 is the best rank. Ties use normalized
source paths for deterministic ordering.

## 5. Throughput and device behavior

Memory use stays bounded, decoding overlaps inference, and an accelerator failure
degrades first to smaller batches and then to the CPU.

### Processing flow

1. Image decoding and CPU metrics use a bounded thread pool (`--workers`, default the
   smaller of 4 and the CPU count).
2. Each bounded group of `--batch-size` images (default 8) is passed to CLIP as a
   mini-batch. The next group decodes while the current group runs inference, so
   effective decode concurrency is the smaller of `--workers` and `--batch-size`.
3. MUSIQ remains per-image.
4. CUDA inference uses automatic mixed precision by default. `--no-mixed-precision`
   disables it. It is never applied on CPU or MPS.

**Why MUSIQ stays per-image:** Batching would need padding or resizing that distorts the
native aspect ratio.

### Failure handling

| Failure | Response |
| --- | --- |
| Out-of-memory error in a multi-image CLIP batch | Split the batch recursively |
| Single-image out-of-memory error, or an unsupported MPS operation | Move all models to CPU and retry |
| Accelerator failure during model initialization under `--device auto` | Retry on CPU with mixed precision off, and record a `model_device_fallback` failure |
| Supported accelerator failure during preset-refresh initialization or cached-embedding scoring | Retry on CPU |

Preset refresh loads only the CLIP text tower.

### Primary images

Same-stem RAW/JPEG pairs default to one RAW primary. This avoids duplicate inference
while retaining all family files for export. `--primary jpeg` and `--primary all`
override that behavior.

| `--primary` | Evaluates |
| --- | --- |
| `raw` (default) | One RAW file when the family has one, otherwise one standard file |
| `jpeg` | One standard file when the family has one, otherwise one RAW file |
| `all` | Every supported image file |

When a family has several files of the preferred type, the first name in case-insensitive
order is chosen.

## 6. Cache, audit, and failure behavior

Completed evaluations are durable and resumable, every run leaves an audit, and every
failure is visible.

### Evaluation cache

1. Each successful evaluation is committed immediately to a WAL-mode SQLite database. The
   cache stores no executable pickle data.
2. The lookup identity contains the collection-relative source path, size, nanosecond
   modification time, model identities, algorithm version, image size, metadata backend,
   and assumed timezone.
3. The preset is deliberately excluded from that identity. So are burst, selection,
   diversity, feedback, contact-sheet, and export settings, which only regroup, select,
   display, or export stored evaluations.
4. Storage is split in two. `evaluations` holds the sixteen preset-independent metrics.
   `preset_evaluations` holds the five preset-dependent ones (subject integrity plus the
   four portrait face and eye values), keyed by preset. One evaluation therefore serves
   every preset.
5. Paths are keyed relative to the input root and stored POSIX-separated. The absolute
   path is retained as a diagnostic column only.
6. Supported older schemas migrate to the current schema 5 when the cache opens. An
   unsupported schema produces a clear error before anything is written.

**Why relative paths:** An absolute key discarded every row whenever a collection was
remounted at a different volume name or drive letter, which is review priority 2 (loss of
resumable results). The relative key survives that, and survives moving between platforms.

One `--output-dir` shared by several collections can in principle collide on an identical
relative path. Size and nanosecond modification time must also match, and the default
per-collection cache location avoids the case.

Background: schema 5 introduced the relative key. Schema 4 introduced the two-table split;
schema 3 stored a complete duplicate per preset.

### Preset refresh

A base hit with a missing preset row enters the `preset_refresh` phase rather than a full
evaluation:

| Preset | Work needed |
| --- | --- |
| `balanced` | No model, because it defines no prompts and its subject score is the constant 1.0 |
| `wildlife`, `landscape` | Only the CLIP text tower compared with the stored embedding: no decode, no MUSIQ, and no image-tower inference |
| `portrait` | A full evaluation again, because face and eye detection needs decoded pixels |

Refreshed scores are stored, so returning to a preset already used is an ordinary cache
hit.

### Keeping and discarding evaluations

- A new evaluation's preset-independent and preset-dependent metrics are written in one
  transaction, so a failed write stores neither.
- A failed cache write does not discard the evaluation. The record is kept for ranking,
  reporting, and export, and the fault is recorded as a `cache_write` failure.
- The only condition that discards a completed evaluation is a changed file fingerprint,
  recorded as a `checkpoint` failure. A file that changed mid-read has an invalid record.

### Run audit

1. Every run directory is created before model initialization and contains an atomically
   updated `run.json`.
2. `run.json` records phase and status, timings, versions, settings, counts, model
   revisions and checksums, and output paths. The final status is `completed`, `failed`,
   or `interrupted`.
3. Ctrl+C marks the run `interrupted`; cached successes remain resumable.
4. Alongside wall-clock phase timings, `run.json` records `stage_seconds` and
   `stage_counts`: aggregate worker time and image count for `decode`, `cpu_metrics`,
   `phash`, `musiq`, `clip`, `portrait`, `preset_refresh`, and `thumbnail_decode`.
5. A fully cached run records no stages, because it evaluates nothing.
6. Time spent waiting at the prompt is recorded as the `awaiting_selection` phase.

**Why stage times:** Phase timings overlap, because decoding runs while inference uses the
previous batch, so they cannot be compared directly. Stage times are the values a
throughput comparison uses. The separate `awaiting_selection` phase keeps operator idle
time from being billed to ranking.

### Failure records

Per-file failures reach `failures.csv` immediately. The `stage` column uses these values:

| Stage | Meaning |
| --- | --- |
| `discovery` | A directory could not be read |
| `cache_lookup` | A file could not be fingerprinted or looked up in the cache |
| `metadata_bulk` | The ExifTool bulk call failed under `auto` |
| `model_device_fallback` | An accelerator failed and the work moved to CPU |
| `decode` | The image could not be decoded or measured |
| `portrait_analysis` | Face and eye detection failed; the image continues with neutral values |
| `musiq`, `clip` | Model inference failed for the image |
| `checkpoint` | The file changed during evaluation, so its result is discarded |
| `cache_write` | The result could not be cached; it still ranks and exports |
| `contact_sheet` | A review thumbnail failed |
| `xmp` | An XMP rating write failed |

A fatal pipeline error is recorded with the current phase name as its stage.

Rows are appended and flushed rather than rewritten.

**Why:** A run against an unsupported RAW format fails every image, and a full rewrite per
failure is quadratic. The file therefore trades whole-file atomic replacement for
append-with-flush; every other report keeps atomic replacement.

### Evaluation report

1. `evaluation.csv` is written atomically and retains full-precision metrics. Display
   rounding occurs only in the terminal and the review page.
2. It is written once before the export prompt, so a completed evaluation pass survives
   an interrupt or a rejected selection value.
3. It is rewritten after selection with the `portfolio_selected` column populated.

## 7. Terminal interface

Photo Cull uses a streaming Rich interface. It does not use an alternate screen, mouse
input, menus, or cursor navigation. Output remains in the terminal history.

### 7.1 Terminal modes

Display mode and selection-input mode are independent.

| Concern | Condition | Behavior |
| --- | --- | --- |
| Rich display | The console is a terminal and `--plain` is absent | Use color, animated phase status, and live evaluation progress |
| Static display | `--plain` is set or the console is not a terminal | Use static phase messages and one durable progress line per evaluation batch |
| Interactive selection | Standard input and the console are terminals, and `--select` is absent | Ask for an export count |
| Non-interactive selection | `--select` is present or either stream is not a terminal | Use `--select`; request zero automatic selections when it is absent |

For example, redirecting standard input while leaving output attached to a terminal keeps
the Rich display but disables the prompt. `--plain` in a normal terminal disables Rich
animation but does not disable the prompt.

`--plain` changes presentation only. It does not change discovery, evaluation, ranking,
selection, reporting, export, exit codes, or selection-input mode.

### 7.2 Output sequence

The interface presents information in this order:

1. Validate arguments and configuration. A malformed `--select` value fails before
   discovery and before a run directory exists.
2. Show the source scan status. A readable collection with no supported images prints a
   message and exits successfully without a run directory. A discovery error that leaves
   no readable supported image creates a failed audit and exits with code 1 when the
   output root is writable.
3. Show the run-directory path immediately after the audit is created.
4. Report preset-refresh scoring and cache reuse when cached evaluations exist.
5. When uncached files need ExifTool, show the metadata status. A metadata fallback
   produces an explanatory warning.
6. Show the environment panel. It contains the current compute mode, source path, output
   root, primary-image count, metadata backend, preset, and burst-grouping state.
7. Show model-loading status when uncached work exists. Direct weight downloads show the
   cache destination, percentage or byte count, and total size when the server supplies
   it. A device fallback produces an explanatory warning.
8. Show evaluation progress. In Rich display mode, show a spinner, bar, completed and
   total counts, percentage, and estimated time remaining. In static display mode, write
   one line per completed batch with processed and successful counts, percentage, rate,
   and ETA.
9. Show at most ten burst winners in the `Top Burst Winners` table. The columns are
   selection rank, path relative to the source root, composite score, and score reason.
   Long paths and reasons wrap rather than truncate. After the table, show the shown,
   winner, and candidate counts and a one-line statement of how the score is formed.
10. Write `evaluation.csv` before any interactive selection prompt. An interrupt or a bad
    count at the prompt therefore does not discard completed metrics.
11. Prompt for a count only in interactive selection mode. When the feedback file has
    keeps, first state their number and that they count toward the requested number.
12. Generate the feedback and review artifacts, then export. If the selection is empty,
    state that no photos were exported. Otherwise, show the `Exported Photos` table:
    selection order, relative path, score, and `burst winner #N` or
    `marked keep in feedback`, at most 20 rows, with a count of any further rows. When
    `--diversity` is above zero, add a note that diversity can replace a winner with a
    lower-ranked one.
13. When the run completes, show the run-directory path and one line for each principal
    output: `evaluation.csv`, `review.html` when generated, `picks/` with selected-image
    and copied-file counts when not empty, and `failures.csv` when a failure occurred.

Display rules:

- Status animations must not erase prior warnings or results.
- Dynamic paths, filenames, and exception messages are escaped before Rich interprets
  markup.
- Terminal score values can be rounded for display; report values retain full precision.

### 7.3 Selection input

Selection input is case-insensitive and ignores surrounding space.

| Input | Result |
| --- | --- |
| Enter, `none`, `skip`, or `0` | Request zero automatic selections; feedback keeps still apply |
| `all` | Set the requested count to the burst-winner count; feedback still applies |
| Integer from 1 through the winner count | Request that many selection slots |
| Malformed explicit `--select` value | Fail during initial configuration validation |
| Malformed interactive value | Show the error and prompt again |
| Explicit integer larger than the winner count | Fail after ranking, when the available count is known |
| Interactive integer larger than the winner count | Show the valid range and prompt again |

Feedback keeps and rejects still apply after the count is read. Forced keeps can make the
selection larger than the requested count. EOF at the interactive prompt is equivalent to
`none`. A supplied `--select` value always bypasses the prompt.

### 7.4 Warnings, errors, and interruption

| Event | Behavior |
| --- | --- |
| Recoverable per-image failure | Print a `Warning:` line without a source location, write a `failures.csv` row, and let other images continue |
| Fallback | The warning names the failed subsystem and the fallback |
| Fatal failure | Print an `Error:` message and return a nonzero exit code |

Scripts must use the exit code and report files instead of parsing Rich formatting.

After the run audit exists, Ctrl+C:

1. marks the audit `interrupted`,
2. preserves completed cache records,
3. cancels queued decode work and waits only for already-running decodes, and
4. returns code 130.

The completed evaluation report also survives an interrupt at the selection prompt. An
interrupt during discovery returns code 130 without a traceback; discovery occurs before
the audit exists, so it cannot update `run.json`.

### 7.5 Current usability gaps

These gaps are not part of the implemented interface contract:

- Hugging Face controls the CLIP repository download display; it does not use Photo
  Cull's line-oriented direct-weight reporter.
- Ctrl+C cannot stop a decode that is already executing inside Pillow or LibRaw. It
  cancels work that has not started and waits for the active worker calls.
- Display modes, narrow width, literal markup, and discovery interruption have forced
  console or mocked tests, but not operating-system-backed pseudo-terminal tests.

Future terminal work must preserve non-interactive operation, the read-only source
invariant, deterministic results, and the scrollback-safe design.

### 7.6 Verification requirements

Automated terminal-interface tests must cover these cases:

1. `--help` and documented defaults agree with the parser.
2. Rich display, plain display, redirected input, and redirected output select the
   correct display and input modes.
3. Plain output contains no ANSI color or cursor-control sequences and includes durable
   per-batch evaluation progress.
4. Explicit `--select` values never prompt. Missing `--select` never reads input in
   non-interactive selection mode.
5. Enter, aliases, `all`, valid counts, malformed values, out-of-range counts, repeated
   prompts, and EOF have the results in section 7.3.
6. Ctrl+C during evaluation and at the prompt returns 130, preserves completed cache
   records, and records the final audit status.
7. Recoverable warnings remain visible while later progress and results continue.
8. Paths and filenames containing Rich markup characters display as literal text.
9. Long paths, long filenames, Unicode text, and narrow terminals do not crash or hide
   the final status and output path.

Tests that require terminal detection or interruption timing must use pseudo-terminals or
equivalent platform facilities. They must not use a real photo collection or download a
model.

## 8. Portfolio review and feedback

The selection combines burst winners, human feedback, and optional diversity. The review
page and the validator close the loop with human decisions.

### Selection rules

1. The candidate pool is every burst winner plus every feedback `keep` that is not a
   winner.
2. A `reject` removes a candidate. A later `--feedback` CSV pins keeps and removes
   rejects.
3. The target count is the requested count or the number of keeps, whichever is larger,
   limited to the number of eligible candidates. Keeps fill slots first, so they count
   toward the requested number and may exceed it.
4. With `--diversity` at zero, the remaining slots go to the highest composite scores.
   Other values use maximal marginal relevance, described below.
5. Diversity determines the selected set, not its order. The returned selection is always
   sorted by composite score, then by normalized path.

Because of rule 5, the in-memory `portfolio_selection_order` (not an `evaluation.csv`
column), the XMP rating bands, and the review page agree with measured quality. Feedback
keeps are forced into the selection but do not occupy the first position.

### Feedback files

- The `file_path` column is required. A `decision` is `keep`, `reject`, or empty, and
  ignores letter case and surrounding space.
- An invalid non-empty decision stops the run and names its row.
- A relative path resolves from the source root. When a path appears more than once, the
  last decision applies.

### Diversity

`--diversity` uses maximal marginal relevance over normalized CLIP embeddings to trade off
composite quality against similarity to already selected images. Zero is pure quality
ranking; one maximizes novelty. Each pick maximizes:

```text
objective  = (1 - diversity) × quality - diversity × similarity
quality    = (score - lowest eligible score) / (highest - lowest), or 1 when all are equal
similarity = max over already selected images of (cosine + 1) / 2
```

Ties resolve by higher quality, then normalized path, so they resolve identically at every
diversity value.

**Why it scales:** Similarity is held as a running per-candidate maximum against the
selected set and updated with one matrix-vector product per pick. Selection cost is
therefore proportional to picks times candidates rather than to the square of the picks.

### Review page

1. `review.html` contains local thumbnails, scores, score reasons, keep/reject controls,
   a full-screen viewer with zoom and keyboard marking, and a browser-side feedback CSV
   download.
2. It shows the first `--contact-sheet` candidates from the pool: burst winners in
   selection-rank order, then keeps that are not winners.
3. Each card is labelled with its selection rank, or `Feedback keep` for a forced keep
   that is not a burst winner. An `Exported` badge marks the current selection. The page
   is written after selection, so the badge is exact.
4. Thumbnails come from a shared content-addressed store at `OUTPUT_ROOT/thumbnails/`,
   keyed by collection-relative path, size, modification time, and review dimension
   (2048 pixels). They are published into each run directory as hard links, falling back
   to a copy.
5. The store is bounded to its 2000 most recent entries.

**Why a shared store:** A repeat run over unchanged photographs therefore decodes nothing.
Previously the review page re-decoded up to `--contact-sheet` sources on every run, which
on a fully cached run was the only decode remaining.

### Validation

`photo-cull-validate` compares feedback with an evaluation and reports precision at the
number of keeps, keep-versus-reject pairwise accuracy, and mean global ranks.

Compatible personal aesthetic-head weights can be supplied with `--aesthetic-head`. Their
hash is recorded for reproducibility, in `run.json` and in the cache identity.

## 9. Export contract

Selections are planned completely before copying, copied into a private staging
directory, and published only when every copy succeeds. An export never overwrites a file
and never writes into the source tree.

### Planning rules

Before the first copy, the plan:

1. includes same-stem and compound sidecars for each selected asset family,
2. deduplicates families,
3. preserves paths relative to the source root,
4. detects destination collisions without regard to letter case,
5. rejects resolved sources outside the input root,
6. refuses an existing export directory, and
7. checks that the destination has enough free space for the whole export.

### Copy rules

1. The complete export tree is copied into a private temporary directory. Each file is
   copied through a temporary name and renamed into place.
2. Photo Cull publishes that directory as `picks/` only after every copy succeeds, so a
   failed export does not expose a partial `picks/` tree.

### Export outputs

- `export_manifest.csv` records source, destination, and size for each file, in the
  columns `source`, `destination`, `source_size_bytes`, `destination_size_bytes`, and
  `kind` (`copied`, `copied_and_rated`, or `generated_xmp`).
- `--write-xmp` creates or updates ratings only inside `picks/`, never in the source tree.
  Each exported family receives one rating: 5 for the first 10 percent of the selection,
  4 for the next 30 percent, and 3 for the rest, with the `Green` label.

### XMP sidecar matching

Sidecar matching uses the same `logical_asset_stem` rule as discovery and export, so a
sidecar is claimed only for its own asset family:

| Sidecar | Matches `IMG_0001.ARW` |
| --- | --- |
| `IMG_0001.xmp` | Yes |
| `IMG_0001.ARW.xmp` | Yes |
| `IMG_0001.v2.xmp` | No |

When several sidecars match, the shortest name wins, then the normalized name, so the
choice is deterministic. The writer emits exactly one `xmp:Rating` and one `xmp:Label`,
removing any other attribute-form or child-element-form copy across every
`rdf:Description`, and preserves unrelated metadata.

## 10. Reproducibility and verification

- Direct dependency ranges are constrained and `uv.lock` is committed.
- CLIP is pinned to a Hugging Face revision.
- The MUSIQ weights and the default aesthetic head use commit-pinned URLs and mandatory
  SHA-256 verification.
- A custom aesthetic head loads with `weights_only=True`.
- CI runs a locked sync, tests, Ruff, and a CLI smoke test on macOS and Windows.
- Tests cover file operations, cache migration/identity, timestamps, scoring, bursts,
  diversity, XMP, validation, audit output, and a mocked end-to-end pipeline.
