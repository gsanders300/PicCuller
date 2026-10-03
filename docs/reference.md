# Photo Cull Reference

This is the complete reference for Photo Cull: every option, rule, score, output file, and
failure behavior. For a task-first introduction, start with the [user guide](../readme.md).
For the reasoning behind each step, read
[How a Photograph Is Judged](how-a-photograph-is-judged.md). For the formal contract that
the code must satisfy, read [Specifications.md](../Specifications.md).

## Contents

- [Terms](#terms)
- [System requirements](#system-requirements)
- [Command-line options](#command-line-options)
- [Processing pipeline](#processing-pipeline)
- [Discovery and file rules](#discovery-and-file-rules)
- [Scores](#scores)
- [Bursts and ranks](#bursts-and-ranks)
- [Selection, feedback, and ratings](#selection-feedback-and-ratings)
- [Terminal interface](#terminal-interface)
- [Review page](#review-page)
- [Output files](#output-files)
- [Evaluation cache](#evaluation-cache)
- [Interruption, recovery, and exit codes](#interruption-recovery-and-exit-codes)
- [Validate the ranking](#validate-the-ranking)
- [Performance](#performance)
- [Privacy and model integrity](#privacy-and-model-integrity)
- [Safety protections](#safety-protections)
- [Known limitations](#known-limitations)

## Terms

| Term | Meaning |
| --- | --- |
| Source directory | The folder that contains your photographs. Photo Cull scans all applicable subfolders. |
| Output root | The folder that holds the cache and every run directory. The default is `SOURCE_DIRECTORY/.photo-cull`. |
| Run directory | The output folder for one run, named `run_YYYYMMDD_HHMMSS_microseconds`. |
| Asset family | Files in one folder that share a logical base name. `IMG_0001.ARW`, `IMG_0001.JPG`, and `IMG_0001.ARW.xmp` are one family. |
| Primary image | The one file in a family that Photo Cull evaluates. |
| Candidate | A primary image that Photo Cull evaluated successfully. |
| Burst | A group of photos taken close together in time that also look alike. |
| Burst winner | The candidate with the best composite score in its burst. |
| Selection | The burst winners and forced keeps that Photo Cull exports. |
| Feedback file | A CSV file that marks images `keep` or `reject`. |

## System requirements

Supported targets:

- macOS 13 or later on Apple silicon
- Windows 10 or Windows 11 with an NVIDIA GPU
- CPU-only operation on macOS or Windows
- Python 3.12 (the project requires `>=3.12,<3.13`)
- `uv` for the Python environment and package lock

Continuous integration tests on macOS 14 and the latest Windows runner. Other systems can
work, but they are not tested.

For CUDA on Windows, the locked environment uses the PyTorch CUDA 12.4 wheels, so install a
compatible NVIDIA driver.

The `--device` option selects the compute device:

| Value | Behavior |
| --- | --- |
| `auto` | Use CUDA if available, then MPS, then the CPU. |
| `cuda` | Require a CUDA device. |
| `mps` | Require an Apple Metal Performance Shaders device. |
| `cpu` | Use the CPU. |

The first scored run needs an internet connection to download the pinned CLIP model, the
MUSIQ weights, and the aesthetic-head weights. Later runs use the local model cache.

ExifTool is optional but recommended. It gives the most reliable RAW timestamps, camera
identifiers, sequence numbers, and autofocus descriptions. Put the `exiftool` command on
`PATH`; the default `auto` metadata backend detects it.

## Command-line options

~~~text
uv run photo-cull [OPTIONS] SOURCE_DIRECTORY
~~~

`uv run photo-cull --help` shows the same options with their defaults.

### Source and output

| Option | Default | What it does |
| --- | --- | --- |
| `SOURCE_DIRECTORY` | Required | The folder that contains your photographs. |
| `--output-dir PATH` | `SOURCE_DIRECTORY/.photo-cull` | The output root. It cannot be the source directory or one of its parents. |
| `--primary raw\|jpeg\|all` | `raw` | Which file in each family to evaluate. See [Primary images](#primary-images). |

### Ranking and bursts

| Option | Default | What it does |
| --- | --- | --- |
| `--preset balanced\|landscape\|portrait\|wildlife` | `balanced` | The scoring profile. See [Scoring profiles](#scoring-profiles). |
| `--burst-window SECONDS` | `2.0` | Longest gap between two frames of one burst. Zero or more. |
| `--max-burst-duration SECONDS` | `10.0` | Longest time from the first to the last frame of a burst. Must be at least `--burst-window`. |
| `--phash-threshold INTEGER` | `8` | Largest perceptual-hash distance for two adjacent frames. Zero or more. |
| `--sim-threshold NUMBER` | `0.88` | Smallest CLIP similarity for two adjacent frames, from 0 to 1. |
| `--no-group` | Off | Put every candidate in its own burst. |

The perceptual-hash test and the CLIP-similarity test are alternatives: either one can
pass. The time test and the camera test must always pass. See [Burst rules](#burst-rules).

### Compute

| Option | Default | What it does |
| --- | --- | --- |
| `--device auto\|cpu\|cuda\|mps` | `auto` | The compute device. See [System requirements](#system-requirements). |
| `--batch-size INTEGER` | `8` | Photos per CLIP batch. Positive. |
| `--workers INTEGER` | The smaller of 4 and the CPU count | Image-decode threads. Positive. Values above `--batch-size` have no effect, because decoding is prefetched one batch at a time. |
| `--no-mixed-precision` | Off | Turn off CUDA automatic mixed precision. It does not affect CPU or MPS. |

### Metadata

| Option | Default | What it does |
| --- | --- | --- |
| `--metadata-backend auto\|exiftool\|pillow` | `auto` | The metadata reader. `auto` uses ExifTool when it is available. |
| `--assume-timezone IANA_NAME` | The system zone | The zone for EXIF times without a UTC offset, such as `America/New_York`. |

If you request `exiftool` and the command is missing, the run stops. If `auto` chooses
ExifTool and the bulk read fails, Photo Cull falls back to embedded metadata for that run
and does not cache the evaluations it makes during the fallback.

### Selection and review

| Option | Default | What it does |
| --- | --- | --- |
| `--select N\|all\|none` | Prompt, or `none` without a terminal | How many photos to export. `N` runs from 1 to the number of burst winners. The format is checked before evaluation starts; the count is checked against the winners after ranking. |
| `--diversity NUMBER` | `0.0` | Preference for photos that look different, from 0 to 1. See [Diversity](#diversity). |
| `--feedback PATH` | None | Apply `keep` and `reject` decisions from a CSV file. |
| `--contact-sheet INTEGER` | `100` | Maximum thumbnails on the review page. `0` skips the page. |
| `--write-xmp` | Off | Write ratings into XMP sidecars beside the exported copies. |
| `--aesthetic-head PATH` | The pinned default head | Use compatible personal aesthetic-head weights. |

`all` sets the requested count to the number of burst winners. Feedback still applies, so
forced keeps can add photos that did not win their burst. If you have more forced keeps
than `N`, Photo Cull exports all of them.

A diversity of zero uses pure score order. A value of one gives the most weight to
novelty in CLIP space, and high values can select lower-quality photos.

### Cache and interface

| Option | Default | What it does |
| --- | --- | --- |
| `--no-cache` | Off | Do not read or write the evaluation cache. |
| `--refresh-cache` | Off | Ignore cached evaluations, evaluate again, and replace the cache records. |
| `--plain` | Off | Static output without color or animation, for logs and scripts. |
| `--debug` | Off | Show a traceback for a fatal error. Per-image failures still go to `failures.csv`. |
| `-h`, `--help` | Not applicable | Show the help and stop. |

`--no-cache` and `--refresh-cache` cannot be used together.

## Processing pipeline

Each run follows these steps:

1. Validate the configuration.
2. Discover supported image files.
3. Choose the primary image in each family.
4. Read valid cache records.
5. Read metadata for uncached images.
6. Load the pinned models, only if uncached work exists.
7. Decode and evaluate the uncached images.
8. Assign session focus factors.
9. Group the candidates into bursts.
10. Assign all ranks and score reasons.
11. Apply feedback and diversity rules.
12. Write the reports and the review page.
13. Copy the selected asset families.
14. Write optional XMP ratings into the exported copies.

When every evaluation is already cached, Photo Cull does not load the machine-learning
models at all.

## Discovery and file rules

### Supported files

Photo Cull scans subfolders in a stable name order. Extension matching ignores letter case.

| Type | Extensions |
| --- | --- |
| RAW | `.3fr`, `.arw`, `.cr2`, `.cr3`, `.dng`, `.erf`, `.iiq`, `.kdc`, `.mos`, `.mrw`, `.nef`, `.orf`, `.pef`, `.raf`, `.rw2`, `.sr2`, `.srw`, `.x3f` |
| Standard | `.bmp`, `.jpeg`, `.jpg`, `.png`, `.tif`, `.tiff`, `.webp` |

RAW support also depends on the LibRaw version that `rawpy` supplies.

### Excluded items

Discovery skips:

- symbolic-link files and symbolic-link directories
- every directory named `.photo-cull`, and the configured output root
- generated directories named like `run_YYYYMMDD_HHMMSS`
- legacy directories named like `picks_YYYYMMDD_HHMMSS`
- AppleDouble companions, whose names start with `._`
- system directories such as `.Trashes`, `.Spotlight-V100`, `.fseventsd`, `@eaDir`,
  `$RECYCLE.BIN`, `System Volume Information`, and `lost+found`

macOS writes AppleDouble companions whenever it writes to exFAT or FAT media. They carry an
image extension but no image data. The system directories hold deleted or derived copies,
which must not be culled as if they were originals.

An unreadable directory is never skipped silently. It produces a warning and a `discovery`
row in `failures.csv`. If discovery fails before it finds any readable supported image, the
run exits with code 1 and writes a failed audit when the output root is writable. A readable folder with no supported images
exits with code 0 and creates no run directory.

### Primary images

Files form a family only when they are in the same folder and share a logical base name.
`--primary` decides which file in each family is evaluated:

| Value | Evaluates |
| --- | --- |
| `raw` | One RAW file when the family has one; otherwise one standard file. |
| `jpeg` | One standard file when the family has one; otherwise one RAW file. |
| `all` | Every supported image file. |

`jpeg` covers every supported standard format, not only JPEG. When a family has more than
one file of the preferred type, Photo Cull picks the first name in stable case-insensitive
order.

This choice affects only evaluation cost. An export still copies every member of the family.

### Image decode

Photo Cull applies EXIF orientation before it measures anything. It converts the image to RGB
and limits the longest side to 1024 pixels.

- **JPEG:** Pillow requests a reduced decode when possible. The requested box keeps the
  image's aspect ratio, because Pillow picks the reduction from whichever axis is most
  constrained. A square box would measure the short side and pick one step less reduction,
  and on a 3:2 frame it could pick no reduction at all.
- **RAW:** `rawpy` first requests the embedded preview. That preview is often full sensor
  resolution, so it gets the same reduced decode. Without a preview, `rawpy` uses a half-size
  demosaic with camera white balance.

One decoded image serves every pixel metric and the fallback metadata.

### Timestamps and metadata

With ExifTool available, the `auto` backend runs one ExifTool process for all uncached files.
It reads:

- the original, creation, or modification capture time
- subseconds and the UTC offset
- camera model and serial number
- image or sequence number
- available autofocus-point descriptions

The capture time comes from the first valid source in this order:

1. The original capture time.
2. The creation time.
3. The metadata modification time.
4. The file-system modification time.

Photo Cull uses an embedded UTC offset when one exists. Otherwise it uses `--assume-timezone`,
and without that option the current system zone. It never silently treats a local EXIF time
as UTC. The report records the local timestamp, its UTC value, the timestamp source, and the
timezone source.

## Scores

The score supports a first selection. It is not an objective measure of artistic value, and
every preset weight and threshold below is uncalibrated until your own keep and reject
decisions support it.

### Focus score

1. Convert the preview to grayscale and compute one Laplacian image.
2. Divide it into a 16 by 16 grid. Cells keep remainder pixels, so the grid does not need to
   divide the image evenly.
3. Take the mean variance of the sharpest 3 percent of cells. Cell variances come from a
   summed-area table, so the cost does not grow with the number of cells.

A small image uses the variance of the whole Laplacian image instead.

The raw value depends heavily on scene detail, so Photo Cull converts it to a percentile
within the current run and then to an absolute focus factor:

~~~text
absolute focus factor = focus floor + (1 - focus floor) × focus percentile
~~~

Within a burst, each photo's focus score is also compared with the sharpest frame in that
burst (the relative focus ratio).

### Exposure score

Clipping is measured per color channel on the rendered 8-bit preview, not on a luminance
conversion:

- A pixel is **blown** when any channel is 254 or higher.
- A pixel is **crushed** only when every channel is 1 or lower.

Per-channel measurement matters. A clipped red at BGR (50, 100, 255) converts to mid grey,
so a luminance measurement reported no clipping for a saturated sunset that had genuinely
lost red detail. Requiring every channel for shadows is the matching rule, because a single
channel at zero is ordinary in a saturated color.

The exposure factor starts at 1.0:

- Highlight clipping above 2 percent lowers it, to no less than 0.4.
- Shadow clipping above 5 percent lowers it, to no less than 0.6.

This does not measure recoverable RAW highlight or shadow data. The 2 and 5 percent
thresholds were carried over from the older luminance measurement, and per-channel
measurement reports more clipping for the same photograph. Validate them against your own
decisions before you trust the highlight penalty on saturated subjects.

### Neural scores

| Source | Gives |
| --- | --- |
| MUSIQ | A no-reference technical-quality score from 0 to 100. |
| CLIP ViT-L/14 | A normalized image embedding, used for bursts, diversity, and subject checks. |
| Pinned LAION head | The aesthetic score, the base of the composite. |
| Genre prompts | A subject-integrity score for the wildlife, portrait, and landscape presets. |
| OpenCV cascades | Face and eye warnings for the portrait preset. |

The subject-integrity check compares one positive prompt with one negative prompt. The result
is a heuristic probability, not an object detection.

The portrait eye factor is 0.7 when a face has fewer than two detected eyes, and 1.0
otherwise, including when no face is found. A missing face is neutral because the cascade is
frontal: it misses profiles, hats, sunglasses, and backlit subjects, and a photo is not worse
because the detector failed. Photo Cull still reports `No face detected` in `eye_warning`.

Default model identities:

| Component | Identity |
| --- | --- |
| CLIP | `openai/clip-vit-large-patch14` at revision `32bd64288804d66eefd0ccbe215aa642df71cc41` |
| MUSIQ | PyIQA `musiq` with `musiq_koniq_ckpt-e95806b9.pth` |
| MUSIQ weights | Revision `0df2df423c65f6a64209309695f3845727431027`, SHA-256 `e95806b9eae5f3814c410f574ba8e552362bd5bc63d758ed5b97860f5d6185aa` |
| Aesthetic head | `sac+logos+ava1-l14-linearMSE.pth` at revision `6934dd81792f086e613a121dbce43082cb8be85e` |
| Aesthetic weights | SHA-256 `21dd590f3ccdc646f0d53120778b296013b096a035a2718c9cb0d511bff0f1e0` |

### Scoring profiles

The aesthetic score is the base of the composite. Every other metric is a factor between 0
and 1, raised to a per-preset weight. A higher weight makes that factor matter more.

| Profile | Focus floor | Absolute focus weight | Relative focus exponent | MUSIQ weight | Exposure weight | Eye weight | Subject weight |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `balanced` | 0.50 | 1.0 | 1.5 | 1.0 | 1.0 | 0.0 | 0.0 |
| `wildlife` | 0.55 | 0.8 | 1.8 | 1.0 | 0.8 | 0.0 | 0.2 |
| `portrait` | 0.60 | 0.7 | 1.6 | 1.0 | 0.8 | 0.35 | 0.15 |
| `landscape` | 0.45 | 1.2 | 1.2 | 1.1 | 1.2 | 0.0 | 0.15 |

### Composite score

~~~text
composite =
  max(0, aesthetic)
  × absolute_focus_factor ^ absolute_focus_weight
  × relative_focus_ratio ^ relative_focus_exponent
  × clamp(MUSIQ / 100) ^ MUSIQ_weight
  × exposure_factor ^ exposure_weight
  × eye_factor ^ eye_weight
  × subject_integrity ^ subject_weight
~~~

`clamp` limits a value to the range 0 through 1.

`evaluation.csv` records each weighted term in a `*_multiplier` column. The aesthetic score
multiplied by the six multipliers, in column order, equals `composite_score` exactly.

The absolute focus factor compares a photo with the other candidates in the same run, so the
same photo can score differently in a different collection.

### Score reasons

Every candidate gets a plain-language reason in the `score_reason` column. The terminal
table and the review page show the same text. A reason joins up to three parts with `; `:

1. **Burst result.** A winner names the next-best frame, how much lower it scored, and the
   factor with the largest difference. A frame that lost names the winner the same way. A
   burst with one frame reads `single shot`. `--no-group` removes this part.
2. **Standing among all candidates.** The strongest of aesthetic, sharpness, and technical
   quality is named when it is in the top 25 percent. The weakest is named when it is in the
   bottom 25 percent.
3. **Penalties** that cut the score by 5 percent or more: exposure clipping, a portrait eye
   warning, or a weak subject check. `No face detected` appears without a penalty, because it
   does not change the score.

Example:

~~~text
best of 6 in burst; next best IMG_0412.CR3 scored 18% lower, mainly on sharpness;
aesthetic in top 10% of all photos
~~~

A reason with no part reads `no standout strength or penalty`.

## Bursts and ranks

### Burst rules

Photo Cull sorts candidates by aware timestamp, then sequence number, then path, and compares
each candidate with the one before it. Two adjacent candidates join the same burst only when
all of these are true:

1. The time gap is not negative.
2. The time gap is at most `--burst-window`.
3. The whole burst lasts at most `--max-burst-duration`.
4. Known camera identities do not conflict.
5. The perceptual-hash distance is at most `--phash-threshold`, **or** the CLIP similarity is
   at least `--sim-threshold`.

Camera identity: when both files have serial numbers, they must match. Without serial
numbers, known camera models must not conflict.

Because only adjacent frames are compared, a frame from a second camera between two frames of
the same burst breaks that burst. Photos of the same subject taken minutes apart are never
grouped; use `--diversity` to keep them out of the same export.

### Rank definitions

| Rank | Meaning |
| --- | --- |
| `global_quality_rank` | Position by score among all candidates. |
| `burst_rank` | Position by score within one burst. |
| `selection_rank` | Position by score among burst winners. Empty for a non-winner. |

Rank 1 is the best. Equal scores are ordered by normalized path.

## Selection, feedback, and ratings

### Selection count

The requested count picks that many burst winners in score order. Feedback then applies:

- A `keep` forces a photo into the selection, even if it lost its burst, and counts toward the
  requested number.
- A `reject` removes a photo from the selection.
- Forced keeps can make the selection larger than the requested count.

### Feedback files

The minimum format:

~~~csv
file_path,decision
/absolute/path/IMG_0001.ARW,keep
/absolute/path/IMG_0002.ARW,reject
relative/path/IMG_0003.ARW,keep
~~~

- The `file_path` column is required. A relative path is resolved from the source directory.
- `decision` can be `keep`, `reject`, or empty. Letter case and surrounding spaces do not
  matter.
- An invalid non-empty decision stops the run.
- When a path appears more than once, the last decision wins.
- Extra columns are ignored.

Each run writes a `feedback.csv` template containing every candidate, with the current
selection marked `keep`. It adds the columns `selection_rank`, `global_quality_rank`, and
`composite_score`.

### Diversity

Diversity uses maximal marginal relevance: each pick balances normalized composite quality
against similarity to photos already chosen, measured with CLIP embeddings. Start small; a
value from 0.1 to 0.3 usually still favors quality. Validate the value on your own photos.

Diversity changes *which* photos are selected, not their order. The exported selection is
always in composite-score order, so the selection position and any XMP rating agree with
measured quality. Equal scores resolve by normalized path, the same rule as pure score
order, so diversity cannot reverse a tie.

### XMP ratings

`--write-xmp` works only when you export at least one photo. Photo Cull creates or updates one
XMP sidecar for each exported asset family, and applies the green label with these ratings:

| Selection position | Rating |
| --- | ---: |
| First 10 percent | 5 |
| Next 30 percent | 4 |
| The rest | 3 |

- An existing sidecar is changed only after it is copied into the export. The source sidecar
  never changes.
- Sidecar matching uses the asset-family rule. `IMG_0001.xmp` and `IMG_0001.ARW.xmp` belong to
  `IMG_0001.ARW`. `IMG_0001.v2.xmp` belongs to a different asset, so Photo Cull leaves it alone
  and creates `IMG_0001.xmp` instead.
- A sidecar can hold a rating as an attribute or a child element, in more than one
  description. Photo Cull writes exactly one rating and one label and removes any other copy,
  so an exported sidecar never carries two conflicting ratings. Other metadata is preserved.
- Forced keeps do not take the first position. Position follows the composite score, so the
  best photo gets the highest rating.

Test XMP import with your version of Lightroom, Capture One, or darktable before you rely on
the ratings.

## Terminal interface

The interface streams output into normal terminal history. It has no full-screen mode,
menus, mouse input, or cursor navigation.

### Display and input modes

Display and selection input are decided separately:

| Situation | Behavior |
| --- | --- |
| Output is a terminal, no `--plain` | Color, animated status, and a live progress bar. |
| `--plain`, or output is redirected | Static text without color, with one progress line per evaluation batch. |
| Input and output are both terminals, no `--select` | Prompt for the export count. |
| `--select` given, or either stream redirected | No prompt. Without `--select`, nothing is exported automatically; feedback keeps still are. |

For example, `--plain` in a normal terminal still prompts, and redirected input disables the
prompt even when the display still shows Rich progress.

### What the terminal shows

1. The run-directory path, as soon as the audit exists.
2. The number of cached evaluations reused, when any are.
3. An environment panel: compute device, source, output root, primary-image count, metadata
   backend, preset, and whether burst grouping is on.
4. Metadata and model-loading status. Direct model-weight downloads show the cache
   destination, progress, and total size when the server supplies it. Hugging Face controls
   the separate CLIP download display.
5. Evaluation progress. Rich mode shows completed and total counts, percentage, and time
   remaining. Static mode prints processed and successful counts, percentage, rate, and time
   remaining after each batch.
6. The `Top Burst Winners` table: up to ten winners with selection rank, path within the
   source directory, composite score, and [score reason](#score-reasons). A line below it
   gives the winner and candidate counts and says how the score is formed.
7. The export prompt (see below).
8. After an export, the `Exported Photos` table: each exported photo in selection order, its
   score, and why it was selected (`burst winner #N` or `marked keep in feedback`). It shows
   at most 20 rows; the `portfolio_selected` column in `evaluation.csv` lists them all. With
   `--diversity` above zero, a note says a lower-ranked winner can replace a similar one.
9. `Run finished:` with the run directory and one line each for `evaluation.csv`,
   `review.html`, `picks/`, and `failures.csv` when a failure occurred.

`evaluation.csv` is written before the prompt, so an interrupt or bad answer at the prompt
never loses completed metrics.

### Export prompt

The prompt reads `How many photos to export, best first?`. If the feedback file has keeps, a
line first states how many and that they count toward the number you enter.

| Answer | Result |
| --- | --- |
| Enter, `none`, `skip`, or `0` | Export no burst winners. Feedback keeps are still exported. |
| `all` | Request every burst winner. |
| A number from 1 to the winner count | Request that many. |
| Anything else, or a number out of range | Show the error and ask again. |
| End of input (EOF) | Same as `none`. |

A `--select` value skips the prompt, so use it in scripts.

### Warnings and errors

A recoverable problem prints a `Warning:` line and the run continues with other images. A
fatal problem prints an `Error:` line and exits with a nonzero code. Scripts should rely on
exit codes and report files, never on parsing the formatted terminal output.

Use `--plain` when a terminal cannot show animation or color correctly. The formal interface
contract and remaining platform gaps are in section 7 of
[Specifications.md](../Specifications.md).

## Review page

`review.html` opens in any web browser. It uses local thumbnails and sends nothing to any
service.

Each card shows the selection rank, file name, score, and score reason. A forced keep that
did not win its burst shows `Feedback keep` instead of a rank, and an `Exported` badge marks
each photo in the current selection.

Select a photo to open it full screen. Click the large photo to zoom to the preview's full
resolution around that point, and click again to fit it to the window. In the full-screen
view, the arrow keys move between photos, `K` marks keep, `R` marks reject, and `Esc` closes.

Mark cards `Keep` or `Reject`, then select `Download feedback.csv`. The downloaded file
contains only the decisions you made on the page. The page keeps decisions only in browser
memory, so download before you close or reload it.

## Output files

The default layout:

~~~text
.photo-cull/
├── evaluation_cache.sqlite3
├── thumbnails/                  shared review previews
└── run_YYYYMMDD_HHMMSS_microseconds/
    ├── run.json
    ├── evaluation.csv
    ├── failures.csv
    ├── feedback.csv
    ├── review.html
    ├── thumbnails/
    ├── picks/
    └── export_manifest.csv
~~~

Some items are conditional:

- `review.html` and `thumbnails/` are absent with `--contact-sheet 0`.
- `picks/` and `export_manifest.csv` are absent when nothing is exported.
- Generated XMP files are absent without `--write-xmp`.
- The cache is absent with `--no-cache` when no earlier cache exists.

### run.json

The run audit, updated throughout the run. Its final status is `completed`, `failed`, or
`interrupted`. It records:

- schema version, current status and phase
- start, update, and completion times
- source, output, and run paths
- all configuration values and scoring-profile values
- model names, revisions, and SHA-256 values
- Python, platform, and package versions
- counts: discovered, cached, evaluated, preset-refreshed, failed, winners, selected, and
  exported
- contact-sheet counts: images, thumbnails decoded, reused, and pruned
- phase times, and per-stage times with an image count for each stage
- paths to generated outputs

`timings_seconds` is wall-clock time per phase. Phases overlap, because decoding runs in
worker threads while inference uses the previous batch, so these values do not add up to the
total.

`stage_seconds` and `stage_counts` are aggregate worker time and image count per stage:
`decode`, `cpu_metrics`, `phash`, `musiq`, `clip`, `portrait`, `preset_refresh`, and
`thumbnail_decode`. Divide one by the other for a per-image cost, and use these, not phase
times, to compare throughput between runs. A fully cached run records no stages.

### evaluation.csv

One row per candidate, in global-quality order. Numbers keep full precision.

| Column | Meaning |
| --- | --- |
| `global_quality_rank` | Rank among all candidates. |
| `selection_rank` | Rank among burst winners. Empty for a non-winner. |
| `burst_rank` | Rank within the burst. |
| `file_name` | File name of the primary image. |
| `file_path` | Absolute path of the primary image. |
| `timestamp` | Aware capture time with its offset. |
| `timestamp_utc` | Capture time in UTC. |
| `timestamp_source` | Metadata field or file-system source of the time. |
| `timezone_source` | Source of the UTC offset or zone assumption. |
| `camera_model` | Camera model, when available. |
| `camera_serial` | Camera serial number, when available. |
| `sequence_number` | Image or sequence number, when available. |
| `autofocus_info` | Vendor autofocus description, when available. |
| `burst_id` | Sequential burst number for this run. |
| `burst_size` | Number of candidates in the burst. |
| `burst_winner` | `True` for the candidate with `burst_rank` 1. |
| `focus_score` | Raw top-cell Laplacian variance. |
| `focus_percentile` | Focus position in this run, from 0 to 1. |
| `absolute_focus_factor` | Preset focus factor from the session percentile. |
| `relative_focus_factor` | Preset-weighted focus factor within the burst. |
| `musiq_score` | MUSIQ technical-quality score. |
| `aesthetic_score` | Aesthetic-head output. |
| `subject_integrity` | CLIP prompt comparison. The balanced preset uses 1.0. |
| `face_count` | Faces detected (portrait preset). |
| `eye_count` | Eyes detected (portrait preset). |
| `eye_factor` | Advisory portrait factor. |
| `eye_warning` | Advisory portrait warning. |
| `blown_pct` | Percent of preview pixels with any channel at 254 or higher. |
| `crushed_pct` | Percent of preview pixels with every channel at 1 or lower. |
| `exposure_penalty` | Combined highlight and shadow factor. |
| `absolute_focus_multiplier` | Preset-weighted session focus factor. |
| `relative_focus_multiplier` | Preset-weighted burst focus factor; equals `relative_focus_factor`. |
| `musiq_multiplier` | Preset-weighted MUSIQ factor. |
| `exposure_multiplier` | Preset-weighted exposure factor. |
| `eye_multiplier` | Preset-weighted portrait eye factor. |
| `subject_multiplier` | Preset-weighted subject-integrity factor. |
| `composite_score` | Final score used for ranking; `aesthetic_score` times the six multipliers. |
| `score_reason` | Plain-language reason. See [Score reasons](#score-reasons). |
| `feedback_decision` | The `keep` or `reject` decision applied. |
| `portfolio_selected` | `True` when the candidate is in the current selection. |
| `cache_hit` | `True` when the stored evaluation was reused. |

Spreadsheet programs can run text as a formula. Photo Cull prefixes any cell that starts with
`=`, `+`, `-`, or `@` to prevent that. Import the CSV as UTF-8 and keep path columns as text.

### failures.csv

Columns: `file_path`, `stage`, `error_type`, `message`. An empty file has only the header.

One image's failure never stops the others, but the run fails if no image is evaluated
successfully. The `stage` column names the step that failed:

- `cache_write`: the evaluation finished but could not be stored. The image still ranks and
  exports; only the resumable cache record is lost.
- `checkpoint`: the file changed while it was read. This is the one condition that discards a
  completed evaluation.
- `discovery`: a directory could not be read, so its contents were not examined.

Rows are appended and flushed immediately instead of rewriting the file. A run against an
unsupported RAW format fails every image, so the row count is unbounded and a full rewrite
per failure would dominate the run.

### feedback.csv

The editable feedback template for this run. See [Feedback files](#feedback-files).

### review.html and thumbnails

The review page and its JPEG previews, at most 2048 pixels on the longest side. A RAW file
whose embedded preview is smaller gives a smaller preview. A thumbnail failure goes to
`failures.csv`.

Previews are made once per source file and kept in a shared store at
`OUTPUT_ROOT/thumbnails/`, so a repeat run over the same photos decodes nothing. Each run
directory gets hard links to the shared files, which keeps the run self-contained (moving or
archiving it carries the pixels) while several runs share one copy on disk. If the file system
refuses a link, Photo Cull copies instead.

The store is keyed by the path relative to the source directory, the file size, and the
modification time. It survives the collection moving, and an edited file gets a new preview.
It keeps the 2000 most recent entries, roughly 2 GB; `run.json` reports removed ones as
`thumbnails_pruned`.

### picks/

The exported asset families, at the same paths relative to the source directory. A family
includes every regular, non-symbolic-link file in the same folder with the same logical base
name: a RAW file, a standard image, and sidecars such as `.xmp`, `.cos`, `.dop`, `.on1`, and
`.pp3`. Both direct sidecar names (`IMG_0001.xmp`) and compound names (`IMG_0001.ARW.xmp`) are
recognized.

### export_manifest.csv

One row per copied or generated export file.

| Column | Meaning |
| --- | --- |
| `source` | Source path. Empty for a newly created XMP file. |
| `destination` | Export path. |
| `source_size_bytes` | Source size before the copy. |
| `destination_size_bytes` | Destination size after the copy or XMP write. |
| `kind` | `copied`, `copied_and_rated`, or `generated_xmp`. |

## Evaluation cache

The cache lives at `OUTPUT_ROOT/evaluation_cache.sqlite3`. It uses SQLite WAL mode and stores
no executable pickle data. Each evaluation is saved as soon as Photo Cull confirms the source
file did not change while it was read, so an interruption never removes earlier records.

### What invalidates a cached evaluation

A cached evaluation is reused only when all of these match:

- source path relative to the source directory
- file size and nanosecond modification time
- evaluation-algorithm version
- maximum preview size
- metadata backend and assumed timezone
- CLIP identity and revision
- MUSIQ identity, revision, and SHA-256
- aesthetic-head SHA-256

Burst settings, selection count, diversity, feedback, contact-sheet count, and export
settings do not invalidate anything, so you can change them and rerun cheaply.

### Moving a collection

The path is stored relative to the source directory, with forward slashes. Copy a card to
another volume, remount it under another name, or move it to another drive letter, and the
cache still applies, because the default cache travels inside the collection at
`SOURCE/.photo-cull/`. A cache written on macOS is readable on Windows. The absolute path is
also stored, for diagnostics only.

If several collections share one `--output-dir`, two could hold the same relative path. File
size and nanosecond modification time must also match, so a wrong reuse is very unlikely, but
the default per-collection cache avoids the question.

### Comparing presets without re-evaluating

The preset is deliberately not part of the cache identity. Only five stored values depend on
it: the subject-integrity score and the four portrait face and eye values. These are stored
separately per preset, so one evaluation serves every preset. When you change `--preset`:

- `balanced` defines no subject prompts, so its subject score is a constant and no model
  loads.
- `wildlife` and `landscape` compare two text prompts with the stored image embedding. Only
  the CLIP text tower loads: no decode, no image tower, no MUSIQ, no image inference. A
  supported accelerator failure retries this on the CPU.
- `portrait` needs decoded pixels for face and eye detection, so those images are evaluated
  again.

A refreshed preset score is stored, so returning to a preset is an ordinary cache hit. The
run reports the work as the `preset_refresh` phase and the `preset_refreshed` count.

### Algorithm version and maintenance

The evaluation-algorithm version is part of the identity, so a release that changes what a
metric means invalidates every stored row. Version 5 did this: it changed JPEG draft scaling,
measured exposure per channel, and gave the landscape preset a non-zero subject weight.
Collections evaluated with an earlier version are evaluated once more.

- Use `--refresh-cache` after a change that the identity does not cover.
- Use `--no-cache` for a temporary run that must not use stored evaluations.
- Do not edit the cache while Photo Cull uses it. If Photo Cull reports an unsupported cache
  schema, move the cache file to a backup location and run again.

## Interruption, recovery, and exit codes

Press Ctrl+C once to stop. Photo Cull marks the audit `interrupted` and exits with code 130.
Completed evaluations stay in the cache, so running the same command again evaluates only the
remaining images.

- Each new evaluation is checkpointed as soon as it succeeds, and each known failure is
  recorded immediately.
- An interrupt at the export prompt keeps the complete `evaluation.csv`; its
  `portfolio_selected` column is `False` because nothing was selected.
- After a supported device or memory failure, work moves from the accelerator to the CPU.
  This also applies when scoring cached embeddings for a new preset. A large CLIP batch is
  first split into smaller batches.

| Exit code | Meaning |
| ---: | --- |
| 0 | The run completed, or a readable source directory had no supported images. |
| 1 | A fatal pipeline failure after the audit started. |
| 2 | An argument, path, configuration, or startup error. |
| 130 | You interrupted the run with Ctrl+C. |

After a nonzero exit, inspect `run.json` and `failures.csv`.

## Validate the ranking

Mark a representative collection with both `keep` and `reject` decisions, then compare them
with a run's scores:

~~~text
uv run photo-cull-validate \
  .photo-cull/run_.../evaluation.csv \
  /path/to/feedback.csv \
  --output validation.json
~~~

Without `--output`, the JSON report goes to standard output. It contains:

| Field | Meaning |
| --- | --- |
| `total_images` | Rows in the evaluation file. |
| `labeled_images` | Evaluation rows that have feedback. |
| `keeps` | Matched `keep` decisions. |
| `rejects` | Matched `reject` decisions. |
| `precision_at_keep_count` | Share of keeps among the top K scores, where K is the number of keeps. |
| `pairwise_accuracy` | Share of keep/reject pairs where the keep scored higher. |
| `mean_keep_global_rank` | Mean global rank of the keeps. |
| `mean_reject_global_rank` | Mean global rank of the rejects. |

A metric is `null` when the feedback lacks the decisions it needs. Use results from several
representative sessions before you change profile or model weights.

### Personal aesthetic head

~~~text
uv run photo-cull /photos --aesthetic-head /path/to/personal-head.pth --select none
~~~

The file must match the included 768-input LAION multilayer-perceptron architecture. Photo
Cull loads it with `weights_only=True` and records its SHA-256 in `run.json` and in the cache
identity.

## Performance

Try these in order:

1. Keep the cache enabled.
2. Use `--primary raw` or `--primary jpeg` so each family is evaluated once.
3. Use `--device auto`.
4. Raise `--batch-size` only when the device has enough memory.
5. Tune `--workers` for your disk and CPU, and raise `--batch-size` with it: decode
   concurrency is the smaller of the two, so `--workers` alone stops helping once it passes
   `--batch-size`.
6. Keep the source and output directories on an SSD.

How the pipeline uses resources: images decode in a bounded thread pool, and the next batch
is prepared while the current one runs through the models. CLIP runs in batches, with CUDA
mixed precision by default. MUSIQ processes one image at a time to keep each image's native
aspect ratio.

A large worker count can slow a slow disk, and a large batch can exhaust device memory;
automatic batch splitting reduces that risk. The first run is slower because it downloads and
initializes the models. A fully cached run does not import the machine-learning runtime.

## Privacy and model integrity

Photographs are processed on your computer and never uploaded.

The first scored run downloads model files from Hugging Face and GitHub. CLIP uses a fixed
revision, and the MUSIQ and default aesthetic-head files are verified with SHA-256. Model
files live in the operating system's user cache directory under the `photo-cull` application
name.

If a checksum does not match, Photo Cull stops and prints the exact file path. Remove only
that file and run again to download a verified copy. Do not remove the whole user cache
directory unless you want to download every model again.

`run.json` records model identities and installed package versions, and the committed
`uv.lock` records the Python dependency resolution.

## Safety protections

Photo Cull never changes your originals. Specifically, it:

- never deletes, renames, moves, or modifies a source file
- never writes metadata to a source file
- does not follow symbolic-link directories or evaluate symbolic-link files
- excludes its own output directories from later scans
- rejects an output root that is the source directory or one of its parents
- rejects selected paths that fall outside the source directory
- checks the complete export plan before it copies anything
- checks destination names without regard to letter case, so a plan made on macOS is safe on
  Windows
- never overwrites an existing export file
- checks free disk space before the first copy
- stages the export in a temporary directory and publishes it only after every copy succeeds
- writes CSV, JSON, HTML, and XMP files atomically

A backup of important photographs still protects them from unrelated failures.

## Known limitations

- Photo Cull ranks rendered previews. It does not inspect the full recoverable range of RAW
  sensor data.
- A high focus score can come from noise or a detailed background.
- Scoring profiles are heuristics that need calibration with your own decisions.
- CLIP prompt checks and OpenCV face and eye checks can give false results.
- Autofocus metadata is vendor-specific. Photo Cull records available descriptions but does
  not map focus-point coordinates to the oriented preview.
- Clock offsets between cameras can affect burst groups.
- Preview conversion is not a complete color-managed RAW workflow.
- Burst detection compares only adjacent images inside the time window.
- MUSIQ processes images one at a time, which can limit throughput.
- XMP round trips have not been tested with every version of Lightroom, Capture One, and
  darktable.
- CUDA and MPS behavior still needs testing with representative photographs and hardware.

Proposed work is tracked in [PotentialEnhancements.md](../PotentialEnhancements.md).
