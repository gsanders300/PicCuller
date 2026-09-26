# Photo Cull

Photo Cull is a local photo-selection tool. It evaluates large collections of RAW and
standard image files. It ranks the images and selects one image from each burst. It can
also prepare a portfolio selection.

Photo Cull does not delete or change a source file. The program writes optional XMP
ratings only in the export directory.

This document uses ASD-STE100 principles where practical. Command names, file names,
and model names keep their official spelling.

For a narrative walkthrough that follows one photograph through every stage, from discovery
to export, see [docs/how-a-photograph-is-judged.md](docs/how-a-photograph-is-judged.md).
That document explains the reasoning behind each step in ordinary prose rather than
specifying the rules.

## Contents

- [Safety summary](#safety-summary)
- [System requirements](#system-requirements)
- [Install Photo Cull](#install-photo-cull)
- [Start Photo Cull](#start-photo-cull)
- [Use the principal workflows](#use-the-principal-workflows)
- [Command reference](#command-reference)
- [Understand the process](#understand-the-process)
- [Understand the scores](#understand-the-scores)
- [Review and select images](#review-and-select-images)
- [Understand the output files](#understand-the-output-files)
- [Use the evaluation cache](#use-the-evaluation-cache)
- [Recover from an interruption or failure](#recover-from-an-interruption-or-failure)
- [Validate the ranking](#validate-the-ranking)
- [Improve performance](#improve-performance)
- [Protect privacy and model integrity](#protect-privacy-and-model-integrity)
- [Troubleshoot the program](#troubleshoot-the-program)
- [Develop and test the program](#develop-and-test-the-program)
- [Known limitations](#known-limitations)

## Safety summary

WARNING: Do not use the output root as the source directory or as a parent of the
source directory. Photo Cull rejects these configurations.

CAUTION: Make a backup of important photographs. Photo Cull does not change source
files, but a backup protects the photographs from unrelated failures.

Photo Cull gives these protections:

- It does not delete source files.
- It does not write metadata to source files.
- It does not follow symbolic-link directories.
- It does not evaluate symbolic-link files.
- It excludes its output directories from later scans.
- It checks the complete export plan before it copies a file.
- It rejects source paths that are outside the source directory.
- It checks destination names without regard to letter case.
- It does not overwrite an export file.
- It checks available disk space before an export.
- It stages the complete export in a temporary directory.
- It publishes the export directory only after all copies succeed.
- It uses atomic writes for CSV, JSON, HTML, and XMP files.

## Terms

This guide uses the terms in this table.

| Term | Meaning |
| --- | --- |
| Source directory | The directory that contains the photographs. Photo Cull scans all applicable subdirectories. |
| Output root | The directory that contains the cache and all run directories. |
| Run directory | The unique output directory for one operation. |
| Primary image | The image that Photo Cull evaluates for an asset family. |
| Asset family | Files in one directory that have the same logical base name. For example, `IMG_0001.ARW`, `IMG_0001.JPG`, and `IMG_0001.ARW.xmp` are one family. |
| Candidate | A primary image that Photo Cull evaluated successfully. |
| Burst | A group of related images that are close in time and similar in content. |
| Burst winner | The candidate with the best composite score in a burst. |
| Selection | The burst winners and forced keeps that Photo Cull chooses for export. |
| Feedback file | A CSV file that contains a `keep` or `reject` decision for an image. |

## System requirements

Photo Cull has these primary targets:

- macOS 13 or later on Apple silicon
- Windows 10 or Windows 11 with an NVIDIA GPU
- CPU operation on macOS or Windows
- Python 3.12, with a project requirement of `>=3.12,<3.13`
- `uv` for the Python environment and package lock

The continuous-integration tests use macOS 14 and the latest Windows runner. Other
systems can work, but the project does not test them.

For CUDA operation on Windows, the locked environment uses the PyTorch CUDA 12.4
wheels. Install a compatible NVIDIA driver.

The program can use these compute devices:

| Device value | Operation |
| --- | --- |
| `auto` | Use CUDA first. If CUDA is not available, use MPS. If MPS is not available, use the CPU. |
| `cuda` | Require an available CUDA device. |
| `mps` | Require an available Apple Metal Performance Shaders device. |
| `cpu` | Use the CPU. |

The first scored operation requires an internet connection. Photo Cull downloads the
pinned CLIP model, the MUSIQ weights, and the aesthetic-head weights. Later operations
use the local model cache.

ExifTool is optional. Install ExifTool if you need the most reliable RAW timestamps,
camera identifiers, sequence numbers, and autofocus descriptions.

## Install Photo Cull

1. Install [`uv`](https://docs.astral.sh/uv/).
2. Clone this repository.
3. Open a terminal in the repository.
4. Install the locked environment.

~~~text
uv sync --locked
~~~

Use this command if you also need the test and lint tools:

~~~text
uv sync --locked --group dev
~~~

Install ExifTool separately if you need it. Make sure that the `exiftool` command is on
`PATH`. Photo Cull detects the command when the metadata backend is `auto`.

## Start Photo Cull

Use this command for the first operation:

~~~text
uv run photo-cull /path/to/photos
~~~

Photo Cull scans the source directory. The program then shows the ten highest-ranked
burst winners. Each row gives the reason for its position.

If the terminal is interactive, the program asks for the number of images to export.
Press Enter to request zero automatic selections. You can also enter `none`, `skip`, or
`0`. Enter a positive integer or `all` to start the export. A `keep` decision in a
feedback file can still force an export when the requested count is zero. Feedback keeps
count toward the number that you enter.

If the terminal is not interactive, the program requests zero automatic selections by
default. Feedback keeps still apply. Use `--select` for a script or a
continuous-integration job.

The default output root is:

~~~text
/path/to/photos/.photo-cull/
~~~

Each operation creates a directory with this form:

~~~text
.photo-cull/run_YYYYMMDD_HHMMSS_microseconds/
~~~

Photo Cull prints the exact run-directory path when it creates the audit.

Use this command to show the current option list:

~~~text
uv run photo-cull --help
~~~

## Use the principal workflows

### Evaluate images and do not export files

~~~text
uv run photo-cull /photos --select none
~~~

The program still writes the evaluation, feedback, audit, failure, and review files.

### Export 30 burst winners without an interactive prompt

~~~text
uv run photo-cull /photos --select 30 --plain
~~~

### Process a wildlife collection

~~~text
uv run photo-cull /photos --preset wildlife --diversity 0.25 \
  --select 40 --write-xmp
~~~

### Process portraits

~~~text
uv run photo-cull /portraits --preset portrait
~~~

The portrait preset adds face and eye checks. These checks give advisory warnings.

### Evaluate all RAW and standard files

~~~text
uv run photo-cull /photos --primary all
~~~

### Disable burst grouping

~~~text
uv run photo-cull /photos --no-group --select 20
~~~

Each image becomes a separate burst. The session focus score still affects the result.

### Use prior decisions

~~~text
uv run photo-cull /photos --feedback /path/to/feedback.csv --select 25
~~~

A `keep` decision forces an image into the selection. A `reject` decision removes an
image from the selection.

### Force CPU operation

~~~text
uv run photo-cull /photos --device cpu --plain --select none
~~~

### Recalculate all image evaluations

~~~text
uv run photo-cull /photos --refresh-cache --select none
~~~

## Command reference

The command syntax is:

~~~text
uv run photo-cull [OPTIONS] SOURCE_DIRECTORY
~~~

### Source and output options

| Option | Default | Instruction |
| --- | --- | --- |
| `SOURCE_DIRECTORY` | Required | Specify the directory that contains the photographs. |
| `--output-dir PATH` | `SOURCE_DIRECTORY/.photo-cull` | Specify the output root. Do not specify the source directory or one of its parents. |
| `--primary raw|jpeg|all` | `raw` | Select the primary image in each same-name family. See [Primary-image rules](#primary-image-rules). |

### Ranking and burst options

| Option | Default | Instruction |
| --- | --- | --- |
| `--preset balanced|landscape|portrait|wildlife` | `balanced` | Select a scoring profile. |
| `--burst-window SECONDS` | `2.0` | Set the maximum time from one image to the next image in a burst. Use zero or a positive value. |
| `--max-burst-duration SECONDS` | `10.0` | Set the maximum time from the first image to the last image in a burst. The value must not be less than the burst window. |
| `--phash-threshold INTEGER` | `8` | Set the maximum perceptual-hash distance for two adjacent burst images. Use zero or a positive value. |
| `--sim-threshold NUMBER` | `0.88` | Set the minimum CLIP similarity for two adjacent burst images. Use a value from zero through one. |
| `--no-group` | Off | Put each candidate in a separate burst. |

The perceptual-hash test and the CLIP-similarity test are alternatives. A time test and
a camera test must also pass.

### Compute options

| Option | Default | Instruction |
| --- | --- | --- |
| `--device auto|cpu|cuda|mps` | `auto` | Select the compute device. |
| `--batch-size INTEGER` | `8` | Set the CLIP batch size. Use a positive integer. |
| `--workers INTEGER` | The smaller value of 4 and the CPU count | Set the number of image-decode workers. Use a positive integer. A value above `--batch-size` has no effect, because decoding is prefetched one batch at a time. |
| `--no-mixed-precision` | Off | Disable CUDA automatic mixed precision. This option does not change CPU or MPS operation. |

### Metadata options

| Option | Default | Instruction |
| --- | --- | --- |
| `--metadata-backend auto|exiftool|pillow` | `auto` | Select the metadata reader. `auto` uses ExifTool when it is available. |
| `--assume-timezone IANA_NAME` | Current system zone | Specify the zone for an EXIF time that has no UTC offset. For example, use `America/New_York`. |

If you request `exiftool` and the command is not available, the operation stops. If
`auto` selects ExifTool and bulk extraction fails, Photo Cull uses embedded metadata
for that operation. Photo Cull does not cache the new evaluations from that fallback.

### Selection and review options

| Option | Default | Instruction |
| --- | --- | --- |
| `--select N|all|none` | Interactive prompt or `none` | Select the export quantity. `N` must be from one through the number of burst winners. Photo Cull checks the format before it evaluates any image, and checks the count against the winners after it ranks them. |
| `--diversity NUMBER` | `0.0` | Set the portfolio diversity strength. Use a value from zero through one. |
| `--feedback PATH` | None | Apply decisions from a feedback CSV file. |
| `--contact-sheet INTEGER` | `100` | Set the maximum number of review thumbnails. Use zero to disable the review page. |
| `--write-xmp` | Off | Write XMP ratings beside exported copies. |
| `--aesthetic-head PATH` | Pinned default head | Use compatible personal aesthetic-head weights. |

The value `all` sets the requested count to the number of burst winners. Feedback still
applies. Forced keeps can add non-winners. If the number of forced keeps is larger than
`N`, Photo Cull selects all forced keeps.

A diversity value of zero uses score order. A value of one gives maximum weight to
novelty in CLIP space. High values can select a lower-quality image.

### Cache and interface options

| Option | Default | Instruction |
| --- | --- | --- |
| `--no-cache` | Off | Do not read or write the evaluation cache. |
| `--refresh-cache` | Off | Do not read cached evaluations. Recalculate and replace applicable cache records. |
| `--plain` | Off | Disable color and animated progress. Use this option for logs and scripts. |
| `--debug` | Off | Show a traceback for a fatal error. Per-image failures stay in `failures.csv`. |
| `-h` or `--help` | Not applicable | Show command help and stop. |

Do not use `--no-cache` and `--refresh-cache` in the same command.

## Understand the process

Photo Cull uses this process:

1. It validates the configuration.
2. It discovers supported image files.
3. It selects the primary images.
4. It reads valid cache records.
5. It reads metadata for uncached images.
6. It loads the pinned models when uncached work exists.
7. It decodes and evaluates uncached images.
8. It assigns session focus factors.
9. It groups the candidates into bursts.
10. It assigns all ranks.
11. It applies feedback and diversity rules.
12. It writes the reports and the review page.
13. It copies the selected asset families.
14. It writes optional XMP ratings to the exported copies.

If all evaluations are in the cache, Photo Cull does not load the machine-learning
models.

### Discovery rules

Photo Cull scans subdirectories in a stable name order. It supports these extensions:

| Type | Extensions |
| --- | --- |
| RAW | `.3fr`, `.arw`, `.cr2`, `.cr3`, `.dng`, `.erf`, `.iiq`, `.kdc`, `.mos`, `.mrw`, `.nef`, `.orf`, `.pef`, `.raf`, `.rw2`, `.sr2`, `.srw`, `.x3f` |
| Standard | `.bmp`, `.jpeg`, `.jpg`, `.png`, `.tif`, `.tiff`, `.webp` |

File-extension matching does not depend on letter case. RAW support also depends on
the LibRaw version that `rawpy` supplies.

Photo Cull excludes these items:

- symbolic-link files
- symbolic-link directories
- each directory named `.photo-cull`
- the configured output root
- generated directories with names such as `run_YYYYMMDD_HHMMSS`
- legacy directories with names such as `picks_YYYYMMDD_HHMMSS`
- AppleDouble companions with names that start with `._`
- operating-system directories such as `.Trashes`, `.Spotlight-V100`, `.fseventsd`,
  `@eaDir`, `$RECYCLE.BIN`, `System Volume Information`, and `lost+found`

AppleDouble companions appear whenever macOS writes to exFAT or FAT media. They carry an
image extension but no image data. The operating-system directories hold deleted or
derived copies, which must not be culled as if they were originals.

Photo Cull does not silently skip a directory it cannot read. Each unreadable directory
produces a warning and a `discovery` row in `failures.csv`. If discovery fails before it
finds a readable supported image, the operation creates a failed audit and returns exit
code 1. A readable directory with no supported images still returns exit code 0 and does
not create a run directory.

### Primary-image rules

Photo Cull groups files only when they are in the same directory and have the same
logical base name.

The `--primary` values have these effects:

| Value | Effect |
| --- | --- |
| `raw` | Evaluate one RAW file when the family has a RAW file. Otherwise, evaluate one standard file. |
| `jpeg` | Evaluate one standard file when the family has a standard file. Otherwise, evaluate one RAW file. |
| `all` | Evaluate all supported image files. |

The option name `jpeg` includes all supported standard formats. If a family contains
more than one file of the preferred type, Photo Cull selects the first name in stable
case-insensitive order.

Primary-image selection affects evaluation cost. It does not remove family members
from a later export.

### Image decode rules

Photo Cull applies EXIF orientation before it calculates a metric. It converts the
image to RGB and limits the longest dimension to 1024 pixels.

For JPEG files, Pillow requests a reduced decode when possible. The requested box keeps
the image aspect ratio, because Pillow selects the reduction from whichever axis is most
constrained. A square box measures the short side and selects one step less reduction,
and on a 3:2 frame it can select no reduction at all.

For RAW files, `rawpy` first requests an embedded preview. That preview is often full
sensor resolution, so it receives the same reduced decode. If a preview is not available,
`rawpy` uses a half-size demosaic with camera white balance.

Photo Cull uses one decoded image for the pixel metrics and fallback metadata.

### Timestamp and metadata rules

The `auto` backend uses one ExifTool process for all uncached files when ExifTool is
available. The backend reads these types of data:

- original, creation, or modification capture time
- subseconds
- UTC offset
- camera model
- camera serial number
- image or sequence number
- available autofocus-point descriptions

Photo Cull uses this timestamp order:

1. Use the original capture time.
2. Use the creation time if the original time is not valid.
3. Use the metadata modification time if the creation time is not valid.
4. Use the file-system modification time if no metadata time is valid.

Photo Cull uses an embedded UTC offset when it is available. Otherwise, it uses the
zone from `--assume-timezone`. If that option is absent, it uses the current local
system zone.

The evaluation report records the local timestamp, its UTC value, the timestamp
source, and the timezone source.

## Understand the scores

Photo Cull calculates a score to help with a first selection. The score is not an
objective measure of artistic value.

### Focus score

Photo Cull converts the preview to grayscale. It calculates one Laplacian image. It
divides that image into a 16 by 16 grid. It uses the mean variance of the sharpest
3 percent of the grid cells. Cell variances come from a summed-area table, so the cost
does not grow with the number of cells. Cells keep remainder pixels, so grid boundaries
do not need to divide the image evenly.

For a small image, Photo Cull uses the variance of the complete Laplacian image.

The raw focus value depends on scene detail. Photo Cull also calculates the focus
percentile in the current operation. The program converts that percentile to an
absolute focus factor:

~~~text
absolute focus factor =
  focus floor + (1 - focus floor) × focus percentile
~~~

For a burst, Photo Cull also compares each focus score with the largest focus score in
that burst.

### Exposure score

Photo Cull measures the rendered 8-bit preview per colour channel, not on a luminance
conversion. A pixel counts as blown when any channel is at or above 254. A pixel counts
as crushed only when every channel is at or below 1.

Per-channel measurement matters. A clipped red at BGR (50, 100, 255) converts to mid
grey, so a luminance measurement reported no clipping at all for a saturated sunset that
had genuinely lost red detail. Requiring every channel for shadows is the matching rule:
a single channel at zero is ordinary in a saturated colour and is not shadow clipping.

The exposure factor starts at 1.0. Highlight clipping above 2 percent reduces the
factor. Shadow clipping above 5 percent also reduces the factor. The highlight factor
cannot be less than 0.4. The shadow factor cannot be less than 0.6.

This metric does not measure recoverable RAW highlight or shadow data.

The 2 percent and 5 percent thresholds are unchanged from the luminance measurement, and
per-channel measurement reports more clipping for the same photograph. Treat both
thresholds as uncalibrated, and validate them against your own keep and reject decisions
before you trust the highlight penalty on saturated subjects.

### Neural scores

Photo Cull uses these model results:

- MUSIQ gives a no-reference technical-quality score.
- CLIP ViT-L/14 gives a normalized image embedding.
- A pinned LAION head gives the aesthetic score.
- Genre prompts give a subject-integrity score for wildlife, portrait, and landscape.
- OpenCV cascades give face and eye warnings for the portrait preset.

An absent face detection is neutral. The cascade is frontal, so it misses profiles, hats,
sunglasses, and backlit subjects; a photograph is not worse because the detector failed.
Photo Cull still reports `No face detected` in `eye_warning`. A detected face with too few
eyes does reduce the factor, because that is a statement about the photograph.

The subject-integrity calculation compares one positive prompt with one negative
prompt. The value is a heuristic probability. It is not an object detector.

The portrait check gives an eye factor of 0.7 when it finds fewer than two eyes for each
face. It gives 1.0 in other cases, which includes no face.

The default model identities are:

| Component | Identity |
| --- | --- |
| CLIP | `openai/clip-vit-large-patch14` at revision `32bd64288804d66eefd0ccbe215aa642df71cc41` |
| MUSIQ | PyIQA `musiq` with `musiq_koniq_ckpt-e95806b9.pth` |
| MUSIQ weights | Revision `0df2df423c65f6a64209309695f3845727431027` and SHA-256 `e95806b9eae5f3814c410f574ba8e552362bd5bc63d758ed5b97860f5d6185aa` |
| Aesthetic head | `sac+logos+ava1-l14-linearMSE.pth` at revision `6934dd81792f086e613a121dbce43082cb8be85e` |
| Aesthetic weights | SHA-256 `21dd590f3ccdc646f0d53120778b296013b096a035a2718c9cb0d511bff0f1e0` |

### Scoring profiles

The composite score uses the aesthetic score as its base. The other metrics act as
bounded factors.

| Profile | Focus floor | Absolute focus weight | Relative focus exponent | MUSIQ weight | Exposure weight | Eye weight | Subject weight |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `balanced` | 0.50 | 1.0 | 1.5 | 1.0 | 1.0 | 0.0 | 0.0 |
| `wildlife` | 0.55 | 0.8 | 1.8 | 1.0 | 0.8 | 0.0 | 0.2 |
| `portrait` | 0.60 | 0.7 | 1.6 | 1.0 | 0.8 | 0.35 | 0.15 |
| `landscape` | 0.45 | 1.2 | 1.2 | 1.1 | 1.2 | 0.0 | 0.15 |

The composite calculation is:

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

All factors in `clamp` have a range from zero through one.

`evaluation.csv` records each weighted factor in a `*_multiplier` column. The aesthetic
score multiplied by the six multipliers, in column order, equals `composite_score`.

### Score reasons

Photo Cull writes one plain-language reason for each candidate in the `score_reason`
column of `evaluation.csv`. The terminal table and the review page show the same text.

A reason can contain these parts, in this order:

1. The burst result. A winner names the next-best frame, the score difference, and the
   factor with the largest difference. A frame that did not win names the winner in the
   same way. A burst with one frame shows `single shot`. `--no-group` removes this part.
2. The standing among all candidates. The reason names the strongest measurement when it
   is in the top 25 percent. It names the weakest measurement when it is in the bottom
   25 percent. The measurements are aesthetic, sharpness, and technical quality.
3. Each penalty that reduces the score by 5 percent or more: exposure clipping, a portrait
   eye warning, or a weak subject check. The portrait `No face detected` warning shows
   without a penalty, because it does not change the score.

This is an example reason:

~~~text
best of 6 in burst; next best IMG_0412.CR3 scored 18% lower, mainly on sharpness;
aesthetic in top 10% of all photos
~~~

The standing and the absolute focus factor compare a photograph with the other candidates
in the same operation. The same photograph can receive a different score in a different
collection.

### Burst rules

Photo Cull first sorts candidates by aware timestamp, sequence number, and path. It
then compares adjacent candidates.

Two adjacent candidates can be in the same burst when all these conditions are true:

1. The time interval is not negative.
2. The time interval is not more than `--burst-window`.
3. The total burst duration is not more than `--max-burst-duration`.
4. Known camera identities do not conflict.
5. The perceptual-hash distance is not more than `--phash-threshold`, or the CLIP similarity is not less than `--sim-threshold`.

When both files have serial numbers, the serial numbers must be equal. When serial
numbers are absent, known camera models must not conflict.

### Rank definitions

| Rank | Meaning |
| --- | --- |
| `global_quality_rank` | The score position among all successful candidates. |
| `burst_rank` | The score position in one burst. |
| `selection_rank` | The score position among the burst winners. A non-winner has an empty value. |

Rank 1 is the highest rank. Path order resolves equal scores.

## Review and select images

### Use the terminal display

The terminal interface does not use a full screen, menus, mouse input, or cursor
navigation. Output remains in terminal history.

Display mode and selection-input mode are separate:

- A terminal display uses color, animated status, and live progress unless you add
  `--plain`.
- A plain or redirected display uses static text without color. It writes one progress
  line after each evaluation batch.
- Photo Cull asks for an export count only when standard input and the display are both
  terminals and `--select` is absent.
- A script or redirected stream requests zero automatic selections unless you specify
  `--select`. Feedback keeps still apply.

For example, `--plain` keeps the prompt in a normal terminal. Redirected input disables
the prompt even when the display still supports Rich progress.

The environment panel shows the current compute mode, source, output root,
primary-image count, metadata backend, preset, and burst mode. During evaluation, a
terminal display without `--plain` shows completed and total counts, percentage, and
estimated time remaining. Static output shows processed and successful counts,
percentage, rate, and ETA after each batch.

When Photo Cull downloads direct model-weight files, it shows the cache destination and
download progress. It also shows the total size when the server supplies it. Hugging Face
controls the separate CLIP repository download display.

After ranking, Photo Cull shows the ten highest-ranked burst winners in the
`Top Burst Winners` table. The table shows the selection rank, the path in the source
directory, the composite score, and the [score reason](#score-reasons). A line below the
table gives the winner and candidate counts and states how the score is calculated.

Photo Cull writes `evaluation.csv` before it asks for an export count. The prompt is
`How many photos to export, best first?`. Press Enter or enter `none`, `skip`, or `0` to
request zero automatic selections. Enter `all` or an integer from 1 through the displayed
winner count to request selection slots. When the feedback file has keeps, a line before
the prompt gives their number and states that they count toward the requested number.
Feedback keeps can cause an export when the requested count is zero. EOF requests zero
automatic selections. A bad interactive value shows an error and prompts again. A supplied
`--select` value bypasses the prompt and remains suitable for scripts.

After an export, the `Exported Photos` table shows each exported photo in selection order,
its score, and why Photo Cull selected it: its burst-winner rank or a feedback `keep`. The
table shows at most 20 rows. The `portfolio_selected` column in `evaluation.csv` identifies
all exported photos. When `--diversity` is more than zero, a note states that a
lower-ranked winner can replace a similar higher-ranked winner.

At the end, Photo Cull shows the run-directory path and lists the principal outputs:
`evaluation.csv`, `review.html`, `picks/`, and `failures.csv` when a failure occurred.

Recoverable failures produce a `Warning:` line and continue with other images. Fatal
failures use an `Error:` message and a nonzero exit code. Use exit codes and report files
in scripts; do not parse the formatted terminal output.

Use `--plain` when a terminal does not correctly show animation or color. The formal
interface contract and remaining platform-level limitations are in section 7 of
`Specifications.md`.

### Use the review page

Open `review.html` in a web browser. The page uses local thumbnail files. It does not
send the photographs to a service.

Each card shows the selection rank, the file name, the score, and the score reason. A
forced keep that is not a burst winner shows `Feedback keep` instead of a rank. An
`Exported` label identifies each photo in the current selection.

Select `Keep` or `Reject` for applicable images. Then select `Download feedback.csv`.
The browser writes a new feedback file. The file contains only decisions that you made
in the page.

Photo Cull does not save browser decisions automatically. Download the file before you
close the page.

### Edit a feedback file

A feedback file uses this minimum format:

~~~csv
file_path,decision
/absolute/path/IMG_0001.ARW,keep
/absolute/path/IMG_0002.ARW,reject
relative/path/IMG_0003.ARW,keep
~~~

The `file_path` column is mandatory. The `decision` value can be `keep`, `reject`, or
empty. Letter case and surrounding spaces do not affect a non-empty decision.

Photo Cull resolves a relative path from the source directory. If the file contains
the same path more than once, the last decision has effect. An invalid non-empty
decision stops the operation.

The generated `feedback.csv` has these additional columns:

- `selection_rank`
- `global_quality_rank`
- `composite_score`

Photo Cull ignores additional columns when it reads feedback. The generated file marks
the current selection as `keep`.

### Use diversity selection

Photo Cull uses maximal marginal relevance for diversity selection. The calculation
combines normalized composite quality with similarity to prior selections.

Use a small value first. A value from 0.1 through 0.3 usually gives quality more weight
than novelty. Validate the value with your own photographs.

Diversity changes which photographs Photo Cull selects. It does not change their order:
the exported selection is always in composite-score order, so the selection position and
any XMP rating agree with measured quality. Equal scores resolve by normalized path
order, which is the same rule the pure-quality path uses, so a diversity value cannot
reverse a tie.

### Use XMP ratings

Use `--write-xmp` only when you also export one or more selections. Photo Cull creates
or updates one XMP sidecar for each exported asset family.

The program applies the green label and these ratings:

| Selection position | Rating |
| --- | ---: |
| First 10 percent | 5 |
| Next 30 percent | 4 |
| Remaining selections | 3 |

Photo Cull changes an existing XMP sidecar only after it copies that sidecar to the
export directory. The source sidecar does not change.

Sidecar matching uses the same asset-family rule as discovery and export. A sidecar
belongs to `IMG_0001.ARW` when it is named `IMG_0001.xmp` or `IMG_0001.ARW.xmp`. A file
such as `IMG_0001.v2.xmp` belongs to a different asset, so Photo Cull leaves it alone and
creates `IMG_0001.xmp` instead.

A sidecar can hold a rating as an attribute or as a child element, and can contain more
than one description. Photo Cull writes exactly one rating and one label, and removes any
other copy, so the exported sidecar never carries two conflicting ratings. Other metadata
in the sidecar is preserved.

Feedback keeps are forced into the selection, but they do not take the first position.
The selection position always follows the composite score, so the best photograph
receives the highest rating.

Test XMP import with your version of Lightroom, Capture One, or darktable before you
use the ratings in a production workflow.

## Understand the output files

The default output structure is:

~~~text
.photo-cull/
├── evaluation_cache.sqlite3
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

- `review.html` and `thumbnails/` are absent when `--contact-sheet 0` is set.
- `picks/` and `export_manifest.csv` are absent when the selection is empty.
- Generated XMP files are absent when `--write-xmp` is not set.
- The cache is absent when `--no-cache` is set and no earlier cache exists.

### `run.json`

This file is the operation audit. Photo Cull updates it during the operation.

The file records:

- schema version
- current status and phase
- start, update, and completion times
- source, output, and run paths
- all configuration values
- all scoring-profile values
- model names, revisions, and SHA-256 values
- Python, platform, and package versions
- discovered, cached, evaluated, preset-refreshed, failed, winner, selected, and
  exported counts
- contact-sheet counts: images, thumbnails decoded, reused, and pruned
- phase times in seconds
- per-stage times in seconds, with the image count for each stage
- paths to generated outputs

The final status is `completed`, `failed`, or `interrupted`.

`timings_seconds` measures wall clock for each phase. Phases overlap, because decoding
runs in worker threads while inference uses the previous batch, so these values do not
divide the total.

`stage_seconds` and `stage_counts` measure aggregate worker time for each stage: `decode`,
`cpu_metrics`, `phash`, `musiq`, `clip`, `portrait`, `preset_refresh`, and
`thumbnail_decode`. Divide one by the other for a
per-image cost. Use these values, not the phase times, to compare throughput between runs.
A fully cached run records no stages, because it evaluates nothing.

### `evaluation.csv`

This file contains one row for each successful candidate. Rows use global-quality
order. Numeric values keep full precision.

| Column | Meaning |
| --- | --- |
| `global_quality_rank` | Rank among all candidates. |
| `selection_rank` | Rank among burst winners. The value is empty for a non-winner. |
| `burst_rank` | Rank in the candidate's burst. |
| `file_name` | File name of the primary image. |
| `file_path` | Absolute path of the primary image. |
| `timestamp` | Aware capture time with its offset. |
| `timestamp_utc` | Capture time converted to UTC. |
| `timestamp_source` | Metadata field or file-system source of the time. |
| `timezone_source` | Source of the UTC offset or zone assumption. |
| `camera_model` | Camera model when available. |
| `camera_serial` | Camera serial number when available. |
| `sequence_number` | Image or sequence number when available. |
| `autofocus_info` | Available vendor autofocus description. |
| `burst_id` | Sequential burst identifier for this operation. |
| `burst_size` | Number of candidates in the burst. |
| `burst_winner` | `True` for the candidate with `burst_rank` 1. |
| `focus_score` | Raw top-cell Laplacian variance. |
| `focus_percentile` | Focus position in this operation, from zero through one. |
| `absolute_focus_factor` | Preset focus factor from the session percentile. |
| `relative_focus_factor` | Preset-weighted focus factor in the burst. |
| `musiq_score` | MUSIQ technical-quality score. |
| `aesthetic_score` | Output from the aesthetic head. |
| `subject_integrity` | CLIP prompt comparison result. The balanced preset uses 1.0. |
| `face_count` | Number of detected faces for the portrait preset. |
| `eye_count` | Number of detected eyes for the portrait preset. |
| `eye_factor` | Advisory portrait factor. |
| `eye_warning` | Advisory portrait warning. |
| `blown_pct` | Percentage of preview pixels with any channel at or above 254. |
| `crushed_pct` | Percentage of preview pixels with every channel at or below 1. |
| `exposure_penalty` | Combined highlight and shadow factor. |
| `absolute_focus_multiplier` | Preset-weighted session focus factor. |
| `relative_focus_multiplier` | Preset-weighted burst focus factor. It equals `relative_focus_factor`. |
| `musiq_multiplier` | Preset-weighted MUSIQ factor. |
| `exposure_multiplier` | Preset-weighted exposure factor. |
| `eye_multiplier` | Preset-weighted portrait eye factor. |
| `subject_multiplier` | Preset-weighted subject-integrity factor. |
| `composite_score` | Final score used for rank order. It equals `aesthetic_score` multiplied by the six multipliers. |
| `score_reason` | Plain-language reason for the score and the burst result. See [Score reasons](#score-reasons). |
| `feedback_decision` | Applied `keep` or `reject` decision. |
| `portfolio_selected` | `True` when the candidate is in the current selection. |
| `cache_hit` | `True` when Photo Cull reused the stored evaluation. |

Spreadsheet programs can interpret some text as a formula. Photo Cull prefixes text
that starts with `=`, `+`, `-`, or `@`. This action prevents formula execution.

### `failures.csv`

This file contains these columns:

- `file_path`
- `stage`
- `error_type`
- `message`

Photo Cull updates this file when a failure occurs. An empty file has only the header.
A failure for one image does not stop other images. The operation stops if no image
has a successful evaluation.

The `stage` column names the step that failed. A `cache_write` row means the evaluation
finished but Photo Cull could not store it. That image still ranks and still exports; only
the resumable cache record is lost. A `checkpoint` row means the file changed while Photo
Cull read it, which is the one condition that discards a completed evaluation. A
`discovery` row means a directory could not be read, so its contents were not examined.

Photo Cull appends each row and flushes it immediately, rather than rewriting the whole
file. A run against an unsupported RAW format fails every image, so the row count is
unbounded and a full rewrite for each failure would dominate the run.

### `feedback.csv`

This file is the editable feedback template. It contains all successful candidates.
See [Edit a feedback file](#edit-a-feedback-file).

### `review.html` and `thumbnails/`

The HTML file is the local review interface. The thumbnail directory contains JPEG
previews with a maximum dimension of 480 pixels. A thumbnail failure appears in
`failures.csv`.

Previews are generated once per source file and kept in a shared store at
`OUTPUT_ROOT/thumbnails/`. A repeat operation over the same photographs reuses them, so a
fully cached operation decodes nothing at all. Each run directory receives hard links to
the shared files, so the run directory stays self-contained (moving or archiving it
carries the pixels) while several runs share one copy on disk. If the file system refuses
a link, Photo Cull copies instead.

The shared store is keyed by the source path relative to the input directory, its size,
and its modification time, so it survives the collection moving and a changed file gets a
new preview. The store keeps its 20000 most recent entries and discards the rest, which
`run.json` reports as `thumbnails_pruned`.

### `picks/`

This directory contains the selected asset families. Photo Cull keeps each path
relative to the source directory.

An exported family includes all regular, non-symbolic-link files in the same directory
that have the same logical base name. This can include a RAW file, a standard image,
and sidecars such as `.xmp`, `.cos`, `.dop`, `.on1`, and `.pp3`.

Photo Cull recognizes direct sidecar names such as `IMG_0001.xmp`. It also recognizes
compound names such as `IMG_0001.ARW.xmp`.

### `export_manifest.csv`

This file records each copied or generated export file.

| Column | Meaning |
| --- | --- |
| `source` | Source path. The value is empty for a new XMP file. |
| `destination` | Export path. |
| `source_size_bytes` | Source size before the copy. |
| `destination_size_bytes` | Destination size after the copy or XMP write. |
| `kind` | `copied`, `copied_and_rated`, or `generated_xmp`. |

## Use the evaluation cache

The cache file is `OUTPUT_ROOT/evaluation_cache.sqlite3`. It uses SQLite WAL mode. It
does not contain executable pickle data.

Photo Cull saves a successful evaluation immediately after it checks that the source
file did not change during evaluation. An interruption does not remove prior cache
records.

A cache lookup uses these values:

- source path relative to the input directory
- file size
- modification time in nanoseconds
- evaluation-algorithm version
- maximum preview size
- metadata backend
- assumed timezone
- CLIP identity and revision
- MUSIQ identity, revision, and SHA-256
- aesthetic-head SHA-256

A change to one of these values causes a new evaluation. Burst settings, selection
count, diversity, feedback, contact-sheet count, and export settings do not invalidate
the image metrics.

The path is stored relative to the input directory, not as an absolute path, so moving a
collection does not discard its evaluations. Copy a card to a different volume, remount it
under another name, or move it to a different drive letter, and the cache still applies,
because the default cache location travels inside the collection at
`SOURCE/.photo-cull/`. Paths are stored with forward slashes, so a cache written on macOS
is readable on Windows. The absolute path is also stored, for diagnostics only.

If you point `--output-dir` at one directory shared by several collections, two
collections could hold the same relative path. The file size and nanosecond modification
time still have to match, so this is very unlikely to produce a wrong reuse, but keeping
the default per-collection cache avoids the question entirely.

The scoring preset is deliberately absent from that list. Only five stored values depend
on the preset: the subject-integrity score, and the four portrait face and eye values.
Photo Cull stores those separately, keyed by preset, so one evaluation serves every
preset instead of each preset storing its own copy of every metric.

### Compare presets without re-evaluating

Change `--preset` on a collection Photo Cull has already evaluated and it reuses the
stored metrics:

- `balanced` defines no subject prompts, so its subject score is a constant. The run
  loads no model at all.
- `wildlife` and `landscape` need two text prompts compared with the stored image
  embedding. Photo Cull loads the CLIP text tower once. It does not decode an image, it
  does not load the CLIP image tower or MUSIQ, and it does not run image inference. A
  supported accelerator failure retries this work on the CPU.
- `portrait` needs the decoded pixels for face and eye detection, so it evaluates the
  images again.

A refreshed preset score is stored, so returning to a preset you have already used is an
ordinary cache hit. The run reports the work as the `preset_refresh` phase and the
`preset_refreshed` count.

The evaluation algorithm version is part of the identity, so a release that changes what
a metric means invalidates every stored row. Version 5 does this: it changes JPEG draft
scaling, measures exposure per channel, and gives the landscape preset a non-zero subject
weight. A collection evaluated with an earlier version is evaluated once more.

Use `--refresh-cache` after a change that the cache identity does not include. Use
`--no-cache` for a temporary operation that must not use persistent evaluations.

Do not edit the cache while Photo Cull uses it. If Photo Cull reports an unsupported
cache schema, move the cache to a backup location. Then start Photo Cull again.

## Recover from an interruption or failure

Press Ctrl+C one time to stop an operation. Photo Cull marks the audit as
`interrupted` and returns exit code 130. Completed image evaluations remain in the
cache.

Photo Cull writes `evaluation.csv` before it asks for the export quantity. An interrupt
at that prompt keeps the complete metrics file. The `portfolio_selected` column is `False`
in that file, because no selection occurred.

Start the same command again to continue. Photo Cull reuses valid cache records and
evaluates the remaining images.

Photo Cull writes a checkpoint after each successful new evaluation. It also records
each known failure immediately.

The program can change from an accelerator to the CPU after a supported device or
memory failure. This behavior also applies when it scores cached embeddings for a new
preset. For a large CLIP batch, it first divides the batch and tries smaller batches.

The command uses these exit codes:

| Code | Meaning |
| ---: | --- |
| 0 | The operation completed, or a readable source directory contained no supported images. |
| 1 | A fatal pipeline failure occurred after the audit started. |
| 2 | An argument, path, configuration, or startup error occurred. |
| 130 | The user interrupted the operation with Ctrl+C. |

Inspect `run.json` and `failures.csv` after a nonzero exit.

## Validate the ranking

First, make decisions for a representative collection. Include both `keep` and
`reject` decisions.

Then use this command:

~~~text
uv run photo-cull-validate \
  .photo-cull/run_.../evaluation.csv \
  /path/to/feedback.csv \
  --output validation.json
~~~

Omit `--output` to write the JSON report to standard output.

The validator reports:

| Field | Meaning |
| --- | --- |
| `total_images` | Number of rows in the evaluation file. |
| `labeled_images` | Number of evaluation rows that have feedback. |
| `keeps` | Number of matched `keep` decisions. |
| `rejects` | Number of matched `reject` decisions. |
| `precision_at_keep_count` | Fraction of keeps in the highest-scored K images, where K is the keep count. |
| `pairwise_accuracy` | Fraction of keep/reject pairs where the keep has the larger composite score. |
| `mean_keep_global_rank` | Mean global rank of the keeps. |
| `mean_reject_global_rank` | Mean global rank of the rejects. |

A metric is `null` when the feedback does not contain the decisions that the metric
needs.

Use results from multiple representative sessions before you change profile weights
or model weights.

### Use a personal aesthetic head

Use this command to load a compatible set of PyTorch weights:

~~~text
uv run photo-cull /photos \
  --aesthetic-head /path/to/personal-head.pth \
  --select none
~~~

The file must match the included 768-input LAION multilayer-perceptron architecture.
Photo Cull loads the state dictionary with `weights_only=True`. It records the file
SHA-256 in `run.json` and in the cache identity.

## Improve performance

Use these controls in this order:

1. Keep the cache enabled.
2. Use `--primary raw` or `--primary jpeg` to avoid duplicate family evaluation.
3. Use `--device auto`.
4. Increase `--batch-size` only when the device has sufficient memory.
5. Adjust `--workers` for the storage device and CPU. Raise `--batch-size` with it: decode
   concurrency is the smaller of the two values, so `--workers` alone stops helping once it
   passes `--batch-size`.
6. Use an SSD for the source and output directories.

Photo Cull decodes images in a bounded thread pool. It prepares one batch while the
current batch uses the models. CLIP uses batches. CUDA uses mixed precision by default.
MUSIQ processes one image at a time to keep the native aspect ratio.

A large worker count can reduce performance on a slow disk. A large batch can cause a
device-memory error. Automatic batch division reduces this risk.

The first operation is slower because it downloads and initializes the models. A
cache-only operation does not import the machine-learning runtime.

## Protect privacy and model integrity

Photo Cull processes photographs on the local computer. The program does not upload a
photograph.

The first scored operation downloads model files from Hugging Face and GitHub. The
CLIP source uses a fixed revision. Photo Cull verifies the MUSIQ and default
aesthetic-head files with SHA-256.

Model files use the operating system's user cache directory under the `photo-cull`
application name. If a checksum does not match, Photo Cull stops and gives the exact
file path. Remove only that file, and then start the command again.

The operation audit records model identities and installed package versions. The
committed `uv.lock` records the Python dependency resolution.

## Troubleshoot the program

### The program finds no images

- Make sure that the source path is a directory.
- Make sure that the file extension is in the supported list.
- Make sure that the file is not a symbolic link.
- Make sure that the file is not in an excluded output directory.

The command returns exit code 0 when it finds no supported images. It does not create a
run directory in this case. If a discovery error prevents Photo Cull from finding any
readable supported image, the command returns exit code 1 and writes a failed audit when
the output root is writable.

### ExifTool is not available

Install ExifTool and add it to `PATH`. Alternatively, use
`--metadata-backend pillow`.

The Pillow backend can read standard EXIF and some embedded RAW-preview EXIF. It can
use the file-system modification time when EXIF is absent.

### Capture times have the wrong zone

Use an IANA zone:

~~~text
uv run photo-cull /photos --assume-timezone America/New_York
~~~

This option affects timestamps that do not contain an offset. It does not change a
timestamp that contains an offset.

### CUDA or MPS is not available

Use `--device auto` or `--device cpu`. If you request `cuda` or `mps` explicitly, the
device must be available when model initialization starts.

### Device memory is insufficient

Reduce the CLIP batch size:

~~~text
uv run photo-cull /photos --batch-size 2
~~~

Photo Cull automatically divides a failed multi-image CLIP batch. It can also move the
models to the CPU after a supported accelerator failure.

### A model checksum does not match

Read the file path in the error. Remove that one cached model file. Start the command
again so that Photo Cull downloads and verifies a new copy.

Do not remove the complete user cache directory unless you intend to download all
models again.

### A RAW file fails

Read the row in `failures.csv`. The installed LibRaw build can lack support for a
camera or a RAW variation. Update the locked dependencies only in a tested development
branch.

### The export does not start

Specify `--select` in a non-interactive command. Without this option, Photo Cull skips
the export when standard input is not a terminal.

Also inspect the feedback decisions. A `reject` decision removes a candidate.

### The review page does not retain decisions

The page keeps decisions only in browser memory. Select `Download feedback.csv` before
you close or reload the page.

### A spreadsheet changes CSV text

Import the CSV as UTF-8. Keep path columns as text. Photo Cull already protects cells
that can start a spreadsheet formula.

### The results do not agree with your choices

Do these steps:

1. Read the `score_reason` and `*_multiplier` columns in `evaluation.csv` to find the
   factor that moved each photograph.
2. Label representative images with `keep` and `reject`.
3. Run `photo-cull-validate`.
4. Compare all four presets.
5. Change `--diversity` in small increments.
6. Keep the original reports for comparison.

Do not treat one small collection as sufficient validation.

## Develop and test the program

Install the development group:

~~~text
uv sync --locked --group dev
~~~

Run the tests:

~~~text
uv run pytest -q
~~~

Run the lint check:

~~~text
uv run ruff check .
~~~

Build the source distribution and wheel:

~~~text
uv build
~~~

Check the command interfaces:

~~~text
uv run photo-cull --help
uv run photo-cull-validate --help
~~~

Continuous integration uses the locked environment. It runs tests, lint, and a command
smoke test on macOS and Windows.

The direct dependency controls are:

| Package | Version control | Purpose |
| --- | --- | --- |
| `torch` | `>=2.4,<2.7` | Model execution |
| `torchvision` | `>=0.19,<0.22` | PyTorch image operations |
| `numpy` | `>=1.26,<2` | Numeric arrays |
| `transformers` | `>=4.40,<5` | CLIP model and processor |
| `pyiqa` | `==0.1.15` | MUSIQ quality metric |
| `imagehash` | `>=4.3,<5` | Perceptual image hash |
| `rawpy` | `>=0.21,<0.28` | RAW decode through LibRaw |
| `opencv-python` | `>=4.10,<5` | Focus, exposure, face, and eye analysis |
| `Pillow` | `>=10,<13` | Standard-image and preview operations |
| `platformdirs` | `>=4,<5` | Operating-system cache path |
| `rich` | `>=13.7,<16` | Terminal interface |
| `requests` | `>=2.31,<3` | Verified model download |

The development group adds `pytest>=8,<10` and `ruff>=0.8,<1`.

The principal modules are:

| File | Purpose |
| --- | --- |
| `cull.py` | Command interface and pipeline control |
| `file_ops.py` | Discovery, asset families, export planning, and safe copies |
| `image_loader.py` | Standard-image and RAW-preview decode |
| `metadata_reader.py` | ExifTool and Pillow metadata |
| `model_config.py` | Pinned model identities and checksums |
| `model_runtime.py` | Model download, load, inference, and device fallback |
| `scoring.py` | Score profiles, burst groups, and composite score |
| `advanced_analysis.py` | Subject prompts and portrait checks |
| `evaluation_cache.py` | SQLite evaluation cache |
| `portfolio.py` | Feedback, diversity, and review page |
| `xmp_rating.py` | Export-only XMP ratings |
| `run_audit.py` | Atomic audit and CSV output |
| `validation.py` | Feedback-based ranking validation |
| `Specifications.md` | Detailed system requirements and algorithm contract |
| `PotentialEnhancements.md` | Implemented enhancements and candidate future work |

## Known limitations

- The program ranks rendered previews. It does not inspect the full recoverable range
  of RAW sensor data.
- A high focus score can come from noise or a detailed background.
- The scoring profiles are heuristics. They require calibration with personal
  decisions.
- CLIP prompt checks can give false results.
- OpenCV face and eye checks can give false results.
- Autofocus metadata is vendor-specific. Photo Cull records available descriptions,
  but it does not map all focus-point coordinates to the oriented preview.
- Multi-camera clocks can have offsets that affect burst groups.
- Preview conversion is not a complete color-managed RAW workflow.
- Burst detection only compares adjacent images inside the time window.
- MUSIQ processes images separately. This operation can limit throughput.
- The project has not completed application-specific XMP tests for all versions of
  Lightroom, Capture One, and darktable.
- CUDA and MPS behavior requires tests with representative photographs and hardware.

For proposed work, see [`PotentialEnhancements.md`](PotentialEnhancements.md). For the
formal processing contract, see [`Specifications.md`](Specifications.md).
