# AGENTS.md

This file applies to the complete repository.

## Project purpose

Photo Cull is a local, cross-platform photo-selection tool. It evaluates RAW and
standard images, groups bursts, ranks candidates, generates review artifacts, and
exports selected asset families.

The source collection is read-only. Treat that rule as the principal product
invariant.

Before a behavioral change, read:

1. `readme.md` for the user contract and command reference.
2. `Specifications.md` for scoring, grouping, cache, and export requirements.
3. `PotentialEnhancements.md` for known limits and deferred work.

Keep these files consistent with the implementation. The README uses
ASD-STE100-style technical English. Use short, direct sentences when you change it.

## Environment and standard commands

Use Python 3.12 and `uv`. Do not manage the environment with a second package tool.

~~~text
uv sync --locked --group dev
uv run pytest -q
uv run ruff check .
uv run photo-cull --help
uv run photo-cull-validate --help
uv lock --check
uv pip check
~~~

Run `uv build` after a packaging, dependency, entry-point, or module-list change.

The case-collision test can skip on a case-insensitive file system. A documented skip
is not a test failure.

Do not update `uv.lock` unless the dependency declaration changes or the user requests
an update. If the lock changes, include it in validation and explain why it changed.

## Repository map

| Path | Responsibility |
| --- | --- |
| `cull.py` | CLI, pipeline control, ranking orchestration, reporting, and export orchestration |
| `file_ops.py` | Discovery, asset-family selection, export planning, and safe file copies |
| `image_loader.py` | Orientation-aware standard-image and RAW-preview decode |
| `metadata_reader.py` | ExifTool and Pillow metadata, timestamps, and timezone handling |
| `model_config.py` | Model names, immutable revisions, URLs, and checksums |
| `model_runtime.py` | Verified model download, model load, inference, batching, and device fallback |
| `scoring.py` | Scoring profiles, focus factors, burst grouping, and composite scores |
| `advanced_analysis.py` | CLIP subject prompts and portrait face/eye checks |
| `evaluation_cache.py` | SQLite cache schema, migration, lookup, and checkpoint writes |
| `portfolio.py` | Feedback parsing, diversity selection, and HTML review output |
| `xmp_rating.py` | Export-only XMP rating updates |
| `run_audit.py` | Atomic JSON/CSV output and failure records |
| `validation.py` | Human-feedback quality metrics and validator CLI |
| `tests/` | Unit and mocked integration tests |

`readme.md` is lowercase because `pyproject.toml` refers to that exact name.

## Non-destructive file rules

Do not weaken these rules:

- Never delete, rename, move, or modify a source asset.
- Write XMP data only beside an exported copy.
- Do not follow symbolic-link directories.
- Do not evaluate symbolic-link files.
- Exclude the configured output root and known generated directories from discovery.
- Resolve and validate every selected source against the source root.
- Build and validate the complete export plan before the first copy.
- Preserve paths relative to the source root.
- Include all applicable members of a selected asset family.
- Detect destination collisions without regard to letter case.
- Refuse to overwrite an existing destination.
- Check free space before the first copy.
- Copy through a temporary file and publish with an atomic rename.
- Write reports and metadata through atomic replacement.

Use temporary directories for tests. Do not use a real photo collection as a test
fixture. Do not commit photographs, model weights, cache databases, run outputs, or
personal paths.

## Pipeline invariants

Keep these behaviors when you change the pipeline:

- Discovery order and score tie resolution are deterministic.
- EXIF orientation is applied before every image metric.
- A normal evaluation decodes each primary image only once.
- The review page adds one reduced decode per new thumbnail, cached in the shared
  thumbnail store, so a repeat run over unchanged photographs decodes nothing.
- Image failures are isolated and recorded in `failures.csv`.
- One bad image does not stop the remaining image evaluations.
- A run fails when no image has a successful evaluation.
- The audit exists before model initialization.
- The audit records final `completed`, `failed`, or `interrupted` status.
- Ctrl+C returns code 130 and keeps completed cache records.
- Non-interactive input never waits for an export answer.
- A cache-only run does not import or initialize the machine-learning stack.
- Heavy machine-learning imports stay outside the CLI help path.

Use aware timestamps. Preserve the timestamp source and timezone source in the report
and cache. Do not silently treat a local EXIF time as UTC.

Keep the ExifTool bulk path optional. The `auto` backend must continue with embedded
metadata if ExifTool fails. The explicit `exiftool` backend must report the failure.

## Ranking and selection invariants

Do not combine these three rank meanings:

- `global_quality_rank` compares all successful candidates.
- `burst_rank` compares candidates in one burst.
- `selection_rank` compares burst winners.

Rank 1 is the best rank. Use normalized path order for deterministic score ties.

Burst grouping requires the time and camera rules. It also requires the pHash rule or
the CLIP-similarity rule. A burst must not exceed the configured maximum duration.

When grouping is disabled, the session-relative focus factor must still affect the
composite score.

Feedback has mandatory semantics:

- `keep` forces an eligible image into the selection.
- `keep` can force a non-winner into the selection.
- `reject` removes an image from the selection.
- Forced keeps can make the result larger than the requested count.

A diversity value of zero must preserve pure score order. Other values use normalized
CLIP embeddings and maximal marginal relevance.

Treat all preset weights and vision heuristics as uncalibrated until representative
human feedback supports them. Do not add a heavy detector only because it is more
advanced. Add it when labeled results show a useful improvement.

## Cache and model reproducibility

The evaluation cache is data, not executable code. Do not replace its serialization
with pickle.

When evaluation semantics change, determine whether to increment
`EVALUATION_ALGORITHM_VERSION`. Increment it when old cached metrics can produce a
different result under the new meaning.

When stored fields change:

1. Increment `CACHE_SCHEMA_VERSION`.
2. Add an idempotent migration from each supported older schema.
3. Add migration and round-trip tests.
4. Keep an unsupported-schema error clear and non-destructive.

The cache identity must include all settings that change per-image evaluation. It does
not have to include settings that only regroup, select, display, or export existing
evaluations.

Keep production models pinned to immutable revisions. Keep mandatory SHA-256 checks
for directly downloaded weights. Do not accept an unverified replacement model.

A custom aesthetic head must use `torch.load(..., weights_only=True)`. Record its
SHA-256 in the audit and cache identity.

Do not make unit tests depend on a model download or a network service. Mock the model
runtime in pipeline tests. Keep a real-model smoke test separate and optional.

## Performance and device rules

Preserve bounded memory use. Do not preload a complete large collection.

The intended flow is:

1. Decode a bounded batch with worker threads.
2. Prepare the next bounded batch while inference uses the current batch.
3. Run CLIP as a batch.
4. Run MUSIQ without distorting the native aspect ratio.
5. Divide a CLIP batch after a device-memory failure.
6. Retry applicable accelerator failures on the CPU.

CUDA mixed precision is enabled by default. The `--no-mixed-precision` option controls
it. Do not apply CUDA autocast behavior to CPU or MPS without evidence and tests.

Use `pathlib` for paths. Do not add shell-dependent path behavior. Treat export
destinations as case-insensitive on all systems so that a macOS plan is safe on
Windows.

## Change guidelines

Make the smallest coherent change. Preserve unrelated user changes in the worktree.
Do not commit, push, or rewrite history unless the user requests it.

Prefer standard-library code when it is sufficient. A new dependency must have a clear
quality, performance, or reliability benefit. Constrain its version and update the
lock file.

Keep CLI option defaults in one implementation location. If an option, default, output
field, or behavior changes, update the README and specification in the same change.

Do not hide a new failure mode. Add the failure to `failures.csv` or return a clear
fatal error. Keep `run.json` useful after an interruption.

Do not reduce numeric precision in `evaluation.csv`. Apply display rounding only in
the terminal or review page.

Protect CSV output from spreadsheet-formula injection.

## Test expectations

Add focused tests for each behavior change. Use a mocked pipeline test when a change
crosses module boundaries.

Changes in these areas require these checks:

| Change area | Required checks |
| --- | --- |
| Discovery or export | Nested paths, same names in different directories, sidecars, symlinks, output exclusion, collisions, and no overwrite |
| Metadata | EXIF field order, subseconds, embedded offsets, assumed zones, malformed values, and file-time fallback |
| Cache | Hit, miss, file invalidation, signature invalidation, migration, interruption, and binary embedding round trip |
| Scoring | Boundary values, ties, zero-focus data, preset behavior, and deterministic order |
| Burst grouping | Time boundary, total duration, camera conflict, pHash alternative, and CLIP alternative |
| Feedback or diversity | Keeps, rejects, forced non-winners, count overflow, zero diversity, and deterministic ties |
| XMP | New sidecar, existing sidecar, family deduplication, rating boundaries, and source immutability |
| CLI | Help, invalid values, non-interactive behavior, exit codes, and `--plain` output |
| Packaging | Locked sync, both console entry points, source distribution, wheel, and import smoke test |

After an implementation change, normally run:

~~~text
uv run pytest -q
uv run ruff check .
git diff --check
~~~

Add `uv lock --check`, `uv pip check`, `uv build`, and both help commands when the
change affects packaging or dependencies.

## Review priorities

When you review code, report concrete defects before style suggestions. Use this
priority:

1. Source-file mutation, data loss, unsafe overwrite, or path escape.
2. Incorrect cache reuse or loss of resumable results.
3. Incorrect timestamps, burst boundaries, ranks, feedback, or exports.
4. Missing audit data or failures that are not visible to the user.
5. Cross-platform path, CUDA, MPS, CPU, or dependency problems.
6. Unbounded memory, duplicate decode, or avoidable model initialization.
7. Documentation that does not match the command behavior.

For each defect, identify the affected file and the smallest relevant line range.
Explain the user-visible result. Recommend a test that fails before the correction.

## Known validation gaps

Do not state that these areas are fully validated:

- representative macOS MPS sessions
- representative Windows CUDA sessions
- camera-specific autofocus-coordinate mapping
- XMP round trips across supported photo applications
- preset calibration with a large personal keep/reject data set
- real-session MUSIQ throughput

See `PotentialEnhancements.md` before you expand one of these areas.
