# Photo Cull: Enhancement Review

Date: 2026-09-09. Baseline at review time: `uv run pytest -q` -> 36 passed, 1 skipped
(the documented case-insensitive-filesystem skip), clean worktree, commit `3c251f7`.

## How this review was produced, and how much to trust each claim

Two passes, deliberately independent:

1. A direct read of all 13 modules plus `tests/`, with hypotheses checked by running the
   real functions. Everything in "Measured directly" below was executed, not inferred.
2. A ten-lens agent review (113 proposals), each proposal then handed to a separate
   adversarial verifier instructed to refute it against the source. 8 were refuted or
   found already handled; 105 survived; 21 more were dropped as already-handled or
   low-severity speculative; 84 were synthesized into the body.

Provenance matters when acting on this, so it is labeled:

- **Measured** means a number produced by running code on this machine.
- **Verified** means the premise was confirmed by reading or executing the cited lines.
- **Reported** means an agent measured or asserted it and a verifier confirmed the premise,
  but it was not independently re-run here.

Of the 113 agent proposals, exactly **one** was rated high severity by its verifier. That
near-absence of severe findings is the headline result, and it is not flattery: the
export path, the atomic writers, the deferred ML imports, the per-image cache commit, and
the CSV formula guard are all really implemented as documented. The remaining risk is
concentrated in three narrow places: `try` blocks one statement too wide, `xmp_rating.py`,
and test coverage of the cache-hit path.

## Start here

Ordered by (impact x confidence) / effort. **All six were applied on 2026-09-09**; see
"Applied changes" below for what landed and how it was verified. The rest of this document
describes the state before those changes.

1. **Vectorize the `--diversity` MMR loop** (`portfolio.py:89-107`, `201-205`). Measured:
   `--diversity` is unusable beyond a few hundred candidates. Not found by the agent
   review's proposal lenses; see "Measured directly" item 1.
2. **Write exactly one `xmp:Rating` and match sidecars with `logical_asset_stem`**
   (`xmp_rating.py:34-69`). The only high-severity finding, and verified by execution: it
   overwrites an unrelated asset's sidecar inside `picks/`.
3. **Order the exported selection by quality** (`portfolio.py:83`). Verified by execution:
   a feedback-pinned image takes rank 1 and the only 5-star XMP rating, ahead of images
   scoring 67% higher. The agent report filed this as minor; it is promoted here.
4. **Narrow the `try` at `cull.py:781-797`** so a failed cache write records a checkpoint
   failure instead of discarding a completed CLIP-plus-MUSIQ evaluation.
5. **Validate `--select` in `_validate_config`** (`cull.py:968-986`) so a typo exits 2 in
   under a second instead of after a full model pass, and **write `evaluation.csv` before
   the blocking prompt** (`cull.py:506` vs `528`).
6. **Cover the cache-hit path end to end**, including the binary embedding round trip. The
   only integration test runs `cache_mode="none"`, and the fixture's `embedding_bytes` is
   not even valid `<f4`.

## Applied changes (2026-09-09)

Test suite went from 36 passed / 1 skipped to **64 passed / 1 skipped**, with Ruff clean,
`uv lock --check` and `uv pip check` clean, and both console entry points working.

No `EVALUATION_ALGORITHM_VERSION` or `CACHE_SCHEMA_VERSION` bump was required: none of
these changes alters a per-image metric or a stored cache field, so existing caches stay
valid. The new cache-hit tests confirm that.

| Item | Change | Verified by |
| --- | --- | --- |
| 1 | `portfolio._select_with_diversity` holds similarity as a running per-candidate maximum and updates it with one matrix-vector product per pick, O(K x N x D) instead of O(K^2 x N x D). `_cosine_similarity` and the fragile `available.remove` are gone. | Measured 5,000 winners selecting 100: **1,646 s projected -> 0.122 s, 13,504x**. A reference O(K^2 x N) implementation in `tests/test_portfolio.py` must agree on 8 randomized inputs. |
| 1b | Diversity tie-breaks now resolve by higher quality then normalized path order, matching the pure-quality path. Folded in because `AGENTS.md:225` requires deterministic ties for any diversity change, and the bug was in the rewritten loop. | Equal scores select the same images at diversity 0, 0.2, 0.4, 0.8, and 1.0. |
| 2 | `xmp_rating._find_sidecar` uses `file_ops.logical_asset_stem`, with a deterministic `(length, name)` tie-break. The writer emits exactly one `xmp:Rating` and `xmp:Label`, clearing attribute and child-element copies across every `rdf:Description`. | `write_xmp_rating(shot.ARW, 5)` leaves `shot.v2.xmp` byte-identical and creates `shot.xmp`; `shot.ARW.xmp` is still matched; unrelated `dc:creator` survives. |
| 3 | `select_portfolio_candidates` returns `sorted(..., key=_quality_key)`. Pins are still forced in and can still exceed the requested count, but no longer take rank 1. | The 10.0-scoring image now takes rank 1 and 5 stars; the pinned 6.0 image drops to rank 3 and 3 stars, at both diversity 0.0 and 0.5. |
| 4 | The `try` in `_evaluate_pending` is split: the fingerprint re-check keeps its discarding `checkpoint` failure, while a failing `evaluation_cache.put` records a new `cache_write` failure and the record still ranks and exports. | Patching `EvaluationCache.put` to raise `OperationalError` still yields exit 0, `evaluated == 2`, 2 CSV rows, 2 exports, and a `cache_write` row in `failures.csv`. A genuine mid-read file change still produces exactly one `checkpoint` row. |
| 5 | New `_validate_selection` runs inside `_validate_config`, before the run directory exists. `evaluation.csv` is written once before the prompt (new `awaiting_selection` audit phase) and rewritten after selection. | `--select 3O` rejected in **4 us** with no run directory created; a `KeyboardInterrupt` at the prompt exits 130 with a complete 2-row `evaluation.csv` registered in `run.json`. |
| 6 | New `tests/test_cache_round_trip.py` covers `record_to_cache` -> `put` -> `get` -> `record_from_cache`: bit-identical embedding, dtype, pHash comparability, aware timestamp and offset, full float precision, truncated-blob rejection, and re-cacheability. The invalid `embedding_bytes=b"embedding"` fixture is replaced with a real `<f4` vector and an offset-bearing timestamp. Three pipeline tests cover cached reuse. | A second run reports `cached=2, evaluated=0` and `resolved_device="cache-only"` while `ModelRuntime` is patched to raise, proving no inference occurs. Metrics match the first run exactly; a modified source file is re-evaluated. |

Docs updated in the same change, per `AGENTS.md:200`: `readme.md` (the `--select` row, diversity
ordering, XMP sidecar matching, `failures.csv` stages, interrupt recovery) and
`Specifications.md` (sections 6, 7, 8, and 9).

Not folded in, and still open: the numpy-import placement in `portfolio.py` is a local
import specifically to keep the CLI help and cache-only paths free of the machine-learning
stack, which was re-verified after the change (`import cull` and `--help` load none of
torch, cv2, pyiqa, transformers, rawpy, numpy, imagehash, or PIL).

## Measured directly

### 1. `--diversity` is quadratic in pure Python and unusable at scale

`_cosine_similarity` (`portfolio.py:201-205`) is a Python generator dot product over 768
floats, measured at **67.4 us** per call versus **0.6 us** for `numpy` (114x). It is called
from `objective` (`portfolio.py:97-101`) inside `max(available, key=objective)` inside the
`while` at `portfolio.py:89`, so it recomputes similarity against every already-selected
item, for every remaining candidate, on every pick: O(K^2 * N * D).

| winners N | selected K | dot products | measured wall time |
| --- | --- | --- | --- |
| 500 | 20 | 93,030 | 6.3 s |
| 2,000 | 50 | 2,411,575 | 2.7 min |
| 5,000 | 100 | 24,426,650 | **27 min** |
| 20,000 | 200 | 395,373,300 | **7.4 h** |

Fix: stack embeddings into one `(N, 768)` float32 array and keep a running per-candidate
max-similarity vector, updated with one matmul per pick. That is K*N dots rather than
K^2*N (500,000 instead of 24.4M for the 5,000/100 row), and each dot is 114x cheaper.
Net for that row: ~27 minutes to well under a second.

This also removes `available.remove(chosen)` (`portfolio.py:107`), which is latently
broken: comparing two record dicts whose first differing key falls *after* `embedding`
raises `ValueError: The truth value of an array with more than one element is ambiguous`
(verified). It works today only because `file_path` precedes `embedding` in the record
literals at `cull.py:756-780` and `cull.py:197-221`.

The same pure-Python dot appears at `scoring.py:161-168` for burst similarity, but there it
runs once per adjacent pair (O(N)), so it is a much smaller win.

### 2. Feedback-pinned images take rank 1 and the only 5-star rating

`selected = list(pinned)` (`portfolio.py:73`) places pinned images ahead of everything
regardless of score. Executed against the real functions with candidates scoring 10.0
down to 6.0 and the 6.0 pinned `keep`, at `--select 3`:

```
diversity=0.0 -> order [('e.jpg', 6.0), ('a.jpg', 10.0), ('b.jpg', 9.0)]
                 stars [('e.jpg', 5),   ('a.jpg', 4),    ('b.jpg', 3)]
diversity=0.5 -> identical
```

`cull.py:521-523` assigns `portfolio_selection_order` in that order and `cull.py:582` feeds
it to `rating_for_rank`, so **the worst exported image is the only one rated 5 stars**.
Sort the returned list by `(-composite_score, path.casefold())`.

### 3. Tie-breaks reverse when the diversity knob moves

Three equally-scored images: `--diversity 0` selects `['a.jpg', 'm.jpg']`; `--diversity 0.4`
selects `['z.jpg', 'm.jpg']` (executed). `max(available, key=objective)` at
`portfolio.py:105` resolves equal scores by the *largest* casefolded path, opposite to
`portfolio.py:65-66` and `cull.py:1002`. This violates `AGENTS.md:120`, and `AGENTS.md:225`
lists deterministic ties as a required check for diversity changes.

### 4. XMP rating overwrites an unrelated asset's sidecar

`_sidecar_base` (`xmp_rating.py:65-69`) strips one stem suffix unconditionally, so
`shot.v2.xmp` collapses to base `shot` and matches `shot.ARW`. Executed in a temp
directory: `write_xmp_rating(shot.ARW, 5)` selected `shot.v2.xmp` and rewrote it in place,
destroying its `Rating="1" Label="Red"`. Scope matters and is reassuring: this happens on
the copy under `picks/`, so the read-only source invariant holds; what is lost is user
metadata that was copied in as part of the asset family. `file_ops.logical_asset_stem`
does not have this behavior, so the fix is to use it.

### 5. `validation._common_input_root` can loop forever

`validation.py:61-66` walks `common = common.parent` until every path is beneath it.
Verified that `PureWindowsPath("C:/").parent == PureWindowsPath("C:/")` and
`PurePosixPath("/").parent == PurePosixPath("/")`, so rows spanning two Windows drives
never terminate. Use `os.path.commonpath` and raise a clear error when it fails.

### 6. Findings that look like wins but are not

Reported honestly because they are tempting:

- **Double SHA-256 of model weights** is real (`model_runtime.py:93` and `:226` re-hash what
  `verified_cached_file` already hashed at `:243-247`), but measured SHA-256 throughput here
  is **2,596 MB/s**, so the redundant half costs 0.05 s for the 108 MB MUSIQ checkpoint and
  0.01 s for the 3.5 MB aesthetic head. Tidiness, not performance. Still worth doing as a
  one-line return-the-digest change, and worth hoisting the MUSIQ check above the CLIP fetch
  so a bad checksum fails early.
- **Duplicate `BGR2GRAY`** is real (`cull.py:163`, `cull.py:182`, and a third at
  `advanced_analysis.py:38` for the portrait preset), but measured at 0.08 ms per
  1024x683 frame, so the redundant call costs 0.8 s per 10,000 images. Negligible.

## Where this review disagrees with itself

Two places where the agent passes reached opposite conclusions. Both are flagged rather
than silently resolved:

- **ExifTool chunking.** One lens measured a ~280 MB peak at 100,000 files from
  `capture_output=True` plus `json.loads` and proposed chunking to ~1.4 MB. The final
  synthesis rejected chunking on the grounds that `-@ -` (`metadata_reader.py:216-217`)
  already avoids any `ARG_MAX` limit, and kept only the timeout. Those are different
  concerns: the arg-file answers the argument-length objection, not the buffering one. The
  memory point stands on its own, at lower priority than the timeout and the encoding pin.
- **MMR term scaling.** A correctness lens argued the similarity term has ~5x less leverage
  than quality at `--diversity 0.5`; the verifier refuted it on the grounds that quality is
  already min-max normalized over the same eligible set. Unresolved here, and independent of
  the performance defect in item 1, which is not in dispute.

## Coverage and limits

Source reading and local execution only. Not verified: a representative macOS MPS session,
a representative Windows CUDA session, a real photo collection, a large labeled keep/reject
set, or an XMP round trip through Lightroom, Capture One, or darktable. Preset weights
remain uncalibrated, so every scoring-quality claim here is structural, not empirical.
Numbers labeled "reported" came from the proposing and verifying agents rather than a
profiled production run, and verifier severity ratings are model judgments, not
measurements.

One known gap in the review's own method: the completeness critic correctly identified the
MMR hot spot and wrote it as its first gap to sweep, but the sweep lanes were fixed in
advance rather than derived from the critic's output, so that gap was never swept by an
agent. It is covered above only because the direct pass measured it.

---

# Agent review body (84 verified proposals, synthesized)

The sections below are the agent review's own output, kept as produced. Line citations were
spot-checked but not exhaustively re-verified; treat unlabeled numbers as "reported" per the
provenance key above. Note that its "Explicitly not recommended" list refutes the MMR
*rescaling* proposal, not the MMR *cost* finding in "Measured directly" item 1, and that it
files the pinned-keep ordering bug under "Minor", which items 2 and 3 above promote.

## Explicitly not recommended

- **MMR diversity rescaling** (`portfolio.py:85-101`): quality is already min-max normalized against the same eligible set.
- **`opencv-python-headless` swap** (`pyproject.toml:15`): both distributions already resolve; swapping does not de-duplicate.
- **Lifting the `numpy<2` pin** or **narrowing the universal lock** (`pyproject.toml:6-10, 63-76`): pins and platform routing are deliberate and correct as written.
- **Specifications.md thread-pool correction**: the proposal's own claims about MUSIQ and CLIP placement are wrong; the doc is right.
- **Fast CLIP processor** (`model_runtime.py:79-83`): superseded by fixing the serial prefetch window, which is the actual constraint.
- **Raising the `--workers` cap** (`cull.py:1047`): dead on its own, since the drain loop caps concurrency at `--batch-size`.
- **Chunking the ExifTool bulk read** (`metadata_reader.py:188-218`): the `-@ -` stdin arg-file already avoids any ARG_MAX limit; add a timeout instead.
- **Orphaned `.part` cleanup on export abort** (`file_ops.py:218-225`): the existing `except` already unlinks the temp file before re-raising.
- **Marking generated directories** (`file_ops.py:65-71`): name-pattern plus output-root containment already prunes them.
- **Deriving cache identity from focus/exposure/pHash constants** (`cull.py:154-175`): `AGENTS.md:147-149` deliberately assigns that judgment to a human version bump.
- **Ruff `select` list, Unicode collision keys, export-family assertions, session-percentile guards, per-factor ablation, immutability assertions in the mocked test, decode-size focus comparability, and the torchvision and XMP-filename doc edits**: all accurate but low-value relative to the five above; revisit only after them.

## Coverage and limits (as stated by the agent review)

This review read source only. It did not run a representative macOS MPS or Windows CUDA session, a real photo collection, or a large labeled keep/reject set; no XMP sidecar was round-tripped through Lightroom, Capture One, or darktable; preset weights remain uncalibrated, so every scoring-quality claim here is structural, not empirical. Measured numbers quoted in the body came from the proposing and verifying agents, not from a profiled production run. Verifier severity ratings, including the single high, are model judgments rather than measurements.

---

## Performance

**1. Give `Image.draft` an aspect-correct box, in both the JPEG and RAW-preview paths**
- **Where:** image_loader.py:64-65 and 78-85
- **Today:** the square `(max_dim, max_dim)` box makes Pillow pick the DCT scale from the short side (`scale = min(w//box[0], h//box[1])`), one power-of-two coarser than the longest-side target at :43-44; `_load_raw_preview` never receives `max_dim` and never drafts at all.
- **Change:** `box = (max_dim, max(1, round(max_dim*h/w)))` when `w >= h`, transposed otherwise (the `max(1, ...)` avoids `ZeroDivisionError` inside `draft` on extreme panoramas). Use it in both paths; the RAW call must sit between `Image.open` (:79) and `load()` (:85), where after `load()` it is a silent no-op.
- **Gain:** aspect-conditional. 6000x4000: 82.7 -> 31.7 ms; 3000x2000: 45.6 -> 18.0 ms (today's draft is a total no-op there); full-sensor RAW preview: 152.3 -> 86.6 ms. 4032x3024 phone/MFT files gain nothing at `max_dim` 1024, so a phone-heavy library may see zero.
- **Effort / Risk:** small / medium
- **Invariants:** still exactly one decode per image, draft still precedes `exif_transpose`, intermediate 4x smaller. Pixels shift (focus -6% to -13% on high-frequency content, pHash Hamming 2), so `EVALUATION_ALGORITHM_VERSION` (cull.py:64) goes "4" -> "5" and every existing user re-evaluates once.
- **Failing test:** the first `image_loader` test (none exists): record `Image.draft`'s box for a 1200x800 JPEG at `max_dim=300`, assert (300, 200) not (300, 300).

**2. Take `--preset` out of the cache signature and key preset-derived metrics separately**
- **Where:** cull.py:963, evaluation_cache.py:100
- **Today:** `preset=` is part of `pipeline_signature`, so readme.md:977's own advice ("Compare all four presets") costs four complete decode plus MUSIQ plus CLIP passes. Only five of 21 cached fields actually depend on the preset (`subject_integrity`, the four portrait fields).
- **Change:** key `evaluations` on the preset-free signature; move the five preset-dependent columns into a `preset_evaluations` table. On a base hit with a preset miss, recompute `subject_integrity` from the cached embedding, decoding nothing. `balanced` has no `SUBJECT_PROMPTS` entry (advanced_analysis.py:9-22), so its `subject_integrity` is the constant 1.0 and needs no model at all.
- **Gain:** preset comparison on an evaluated shoot drops from N full passes to zero decodes and zero image-tower forwards. Balanced -> landscape becomes free: both have `subject_weight` 0.0 (scoring.py:29, 47-54), so only ranking weights differ.
- **Effort / Risk:** medium / medium
- **Invariants:** cache stays plain columns. Bump `CACHE_SCHEMA_VERSION` to 4 **and** add 3 to the accepted tuple at evaluation_cache.py:66, or every existing v3 cache is rejected. Add a `preset_refresh` state plus a text-features-only runtime path, otherwise such a run reports `cache-only` while loading the whole ML stack, violating AGENTS.md:103.
- **Failing test:** put a row under `balanced`, reopen under `wildlife`, assert `run.json` counts one cached and zero evaluated.

**3. Guard the deferred-import discipline with an offline subprocess test**
- **Where:** cull.py:253-254, 410, 544, 816
- **Today:** AGENTS.md:103-104 and readme.md:875 both assert the ML stack stays out of the help and cache-only paths, and nothing checks it: `pyproject.toml` sets no Ruff `select`, so PLC0415 is inactive, and CI's `--help` smoke test passes either way.
- **Change:** `tests/test_import_hygiene.py` running two `subprocess.run([sys.executable, "-c", ...])` cases (import `cull`; then `cull.main(["--help"])` under `try/except SystemExit`), each asserting the intersection with `{torch, cv2, pyiqa, transformers, rawpy, numpy, imagehash, PIL}` is empty. Both must be subprocesses: tests/test_pipeline_integration.py:8-9 already pollutes in-process `sys.modules`.
- **Gain:** locks in a measured 0.17 s / 25 MB / 268 modules versus 11.68 s / 438 MB / 3605 modules. Hoisting `image_loader` alone costs ~0.55 s.
- **Effort / Risk:** small / low
- **Invariants:** asserts modules are absent, so no network or model download. Add a one-line comment at cull.py:253 and 410 explaining the deferral.
- **Failing test:** the test above passes today and fails the moment any heavy import is hoisted; that is its whole purpose.

**4. Release embeddings and phash once their last consumer has run**
- **Where:** cull.py:498, 520
- **Today:** each record keeps a 768-dim embedding and an `imagehash` object for the whole run, through `_show_summary`, the blocking prompt at cull.py:925, the contact-sheet decode, and export. `phash` is read only at scoring.py:155; `embedding` only at scoring.py:161-168 and portfolio.py:201-205, which is unreachable at `--diversity 0`.
- **Change:** after `winners = _assign_ranks(records)`, loop `record.pop("phash", None)` unconditionally and `record.pop("embedding", None)` when `config.diversity == 0.0`; add the embedding pop after cull.py:520 for the diversity path. Cover every record: fresh embeddings are non-owning views (`owndata` False), so one survivor pins the whole batch buffer.
- **Gain:** ~3,452 bytes per record freed, measured: ~34 MB at 10,000 images, ~342 MB at 100,000, before an unbounded human wait with model weights still resident.
- **Effort / Risk:** small / low
- **Invariants:** neither key is in `REPORT_FIELDS`, and `extrasaction="ignore"` (run_audit.py:185) already drops them, so CSVs are byte-identical. No cache or version change.
- **Failing test:** assert `"embedding" not in records[0]` after ranking at `diversity=0.0` while `evaluation.csv` still has all 34 columns populated.

**5. Express decode prefetch in files, not batches, so `--workers` past `--batch-size` is not dead**
- **Where:** cull.py:680-709, 800-803
- **Today:** the drain loop at :700-706 resolves every future of the current batch before :708-709 submits the next, so queued-plus-running decodes never exceed `batch_size`. Effective concurrency is `min(workers, batch_size)`, and run.json records the requested `workers` as if it were used.
- **Change:** keep a FIFO deque of `(path, future)` pairs, top it up before each inference step, and pop `batch_size` items strictly in order (out-of-order popping would perturb failures.csv row order). Validate a relational ceiling in `_validate_config` (cull.py:977-978) and record the realized window alongside the requested `workers`.
- **Gain:** decode scales with `--workers` again (up to 2x on a 16-core box at the default `--batch-size 8`) and the serial MUSIQ/CLIP stage stops stalling on one slow RAW.
- **Effort / Risk:** medium / medium
- **Invariants:** bounded memory needs the explicit cap: each in-flight `PreparedImage` holds both a `cv_image` and a `pil_image`, so a 24-deep window is ~1.5x today's peak. Ranking is unaffected (`_timestamp_order`, cull.py:994, re-sorts).
- **Failing test:** stub `_prepare_image` with a barrier recording a concurrency high-water mark; with `workers=4, batch_size=1` over 8 files assert >= 2 (today it is exactly 1), and with `workers=1, batch_size=8` assert 1.

**6. Append to failures.csv instead of rewriting it in full on every failure**
- **Where:** run_audit.py:113-124, 148-150
- **Today:** `record_failure` hands the entire accumulated list to `atomic_write_csv`, so F failures write sum(1..F) rows plus F full run.json rewrites. AGENTS.md:97 guarantees a run continues through bad images, and cull.py:704/720/729/749/795 each emit one row per photo, so F is unbounded.
- **Change:** open failures.csv once at construction, append and flush one row per failure, and keep the atomic full rewrite in `finish()` after closing the append handle. Closing first is mandatory: `Path.replace` over an open handle raises `PermissionError` on Windows, a supported target.
- **Gain:** O(F^2) -> O(F): at F=5,000, ~1.5 GB of writes and ~10,000 temp-file pairs become ~600 KB and ~1.
- **Effort / Risk:** medium / low
- **Invariants:** bends AGENTS.md:83 (atomic replacement) for the in-progress file only; the terminal artifact stays atomic. Note that `flush()` is not durability, so an interrupt can still leave a torn final line.
- **Failing test:** record 3 failures, assert header plus 3 ordered rows without calling `finish()`; today no test records more than one.

**7. Resolve each source path once per run**
- **Where:** cull.py:199, 367, 690, 758; portfolio.py:60, 71, 74, 78, 129, 154; file_ops.py:183
- **Today:** `file_path` is already a resolved absolute string from cull.py:199/758, yet `Path.resolve()` runs again in `EvaluationCache.get`, `record_from_cache`, the metadata lookup at cull.py:690, four passes inside `select_portfolio_candidates`, and once per file in `select_primary_images` (the default `--primary raw` path).
- **Change:** thread the resolved string through `get`, `put`, and `record_from_cache`; use `candidate["file_path"]` directly in portfolio.py and document that contract in the docstrings; memoize `path.parent.resolve()` in a `dict[Path, Path]` at file_ops.py:183; add `if count == 0 and not feedback: return []` to `select_portfolio_candidates`, which is the common Enter-at-the-prompt case (cull.py:935).
- **Gain:** 12-17 us per call removed at 4-6 sites per file: warm cache lookup ~3.0 -> ~2.1 s and `select_primary_images` 6.56x faster at 50,000 images; portfolio selection does zero filesystem work. Modest against per-image inference, but provably free.
- **Effort / Risk:** small / low
- **Invariants:** no cache, schema, or version change; strictly removes I/O. Keep `available = list(eligible)` on the diversity path, since portfolio.py:107 mutates it.
- **Failing test:** monkeypatch `Path.resolve` to raise, then run `select_portfolio_candidates` with no feedback and assert the same order and count; fails today at portfolio.py:60.

**Minor, batch these**
- Make cache rows survive a rename or remount: store a per-row root id plus a relative path (a single `cache_meta` root breaks when one `--output-folder` serves several collections), and accept the row only after the existing size/`modified_ns` check; note plain `cp` or exFAT defeats the fingerprint regardless (evaluation_cache.py:132-176).
- Replace the 256-view loop in `calculate_top_percentile_focus` with `cv2.integral2` block variance plus `np.maximum(var, 0.0)`: 2.56 -> 1.22 ms, agreeing to 5e-08, so no version bump; ~11 MB transient per worker (cull.py:154-175).
- Rewrite `discover_image_files` on `os.scandir` with `is_file(follow_symlinks=False)`: 10.3 -> 2.0 us/file, but it must wrap each `scandir` in `try/except OSError: continue` to keep `os.walk`'s silent skip of unreadable directories, and needs an order-equality test (file_ops.py:80-97).
- Store embeddings as float16 behind a new `embedding_dtype` column (not an inferred itemsize, which voids the corruption guard at cull.py:195): saves 1,536 bytes per record and ~154 MB per signature on disk, at ~8e-05 worst-case similarity drift (cull.py:194, 227).
- Stream `write_feedback_template` as a generator into `atomic_write_csv`, which already streams, and hoist the function-scoped import to portfolio.py:11: ~34 MB at 100,000 records, output bytes unchanged (portfolio.py:112-139).
- Key contact-sheet thumbnails by content identity in a shared, pruned directory instead of `f"{index:04d}.jpg"` under the run dir, so repeat cache-only passes stop re-reading up to 100 sources; needs a path component in the key (size plus mtime alone can collide) and a readme.md:545/622 tree update (cull.py:543-557).
- Record `cpu_count`, `cv2_threads`, and `torch_num_threads` in run.json's environment block, and `os.environ.setdefault("OMP_NUM_THREADS", ...)` early in `main` (which lands before OpenMP init only because cull.py imports no cv2/torch at module scope). Do **not** hard-set `cv2.setNumThreads(1)`: `analyze_portrait` runs `detectMultiScale` on the main thread (advanced_analysis.py:38-53).
- Return the digest from `verified_cached_file` so the MUSIQ checkpoint and aesthetic head are hashed once, not twice (four times after a CPU fallback), and hoist the MUSIQ verification above `CLIPModel.from_pretrained` so a bad checksum fails before a gigabyte-scale fetch; keep `sha256_file` in the `custom_weights` branch, which never calls `verified_cached_file` (model_runtime.py:88-93, 219-226).

---

## Reliability and Correctness

**1. Do not discard a completed evaluation when the cache write fails**
- **Where:** `cull.py:781-797`
- **Today:** `evaluated.append(record)` and `completed_count += 1` sit *after* `evaluation_cache.put` inside one `try`, so any SQLite fault (disk full, I/O error, lock on a network or cloud-synced default output root at `cull.py:274`) drops a fully computed CLIP plus MUSIQ record from the run, not just from the cache.
- **Change:** Wrap only the `put` call (785-790) in its own handler that records a `checkpoint` failure and continues; keep the fingerprint re-check at 782-783 as the one condition that still discards (a file changed mid-evaluation genuinely has an invalid record). Optionally pass `timeout=30.0` to `sqlite3.connect` at `evaluation_cache.py:62` as cheap insurance.
- **Gain:** A contended or full cache still produces complete rankings, reports, and export instead of silently losing one record per failed write. Mirrors the existing `allow_cache_write = False` behavior at `cull.py:405`/`464`, which already keeps every record with caching off.
- **Effort / Risk:** small / low
- **Invariants:** Specifications.md:115 (each successful evaluation committed immediately) is preserved; only the consequence of a *failed* commit changes. A failed commit now degrades to an uncached success.
- **Failing test:** Patch `EvaluationCache.put` to raise `sqlite3.OperationalError("database is locked")`; assert every input image still appears in `evaluation.csv` and that a `checkpoint` failure was recorded.

**2. Treat a cache-lookup fault as a miss instead of a per-file verdict**
- **Where:** `cull.py:362-375`, `evaluation_cache.py:60-124`
- **Today:** One `try` wraps `FileFingerprint.from_path`, `evaluation_cache.get`, and `record_from_cache`; the `except` at 373-375 appends to neither `records` nor `pending_files`, so a corrupt DB makes every file take the drop path, `cull.py:475` raises, and the run exits 1 with one failures.csv row per photo. A bad row (raise sites at `cull.py:195-196`, `200`, `207`) can never self-heal, because only `pending_files` reaches the `ON CONFLICT DO UPDATE` at `evaluation_cache.py:216`. Separately, an exception from `PRAGMA journal_mode=WAL` (line 63) or the DDL (72-124) escapes `__enter__` with the connection still open.
- **Change:** Keep `from_path` failures as a real drop; route `get`/`record_from_cache` faults to `pending_files` for re-evaluation. Guard the `EvaluationCache` construction at `cull.py:353-357` so an unusable cache degrades to `cache_mode="none"` with one `cache_open` failure, and close the connection in `__enter__`'s failure paths. Requires entry 1, or re-evaluated photos are dropped a second time at 794-796.
- **Gain:** Corrupt cache goes from total run failure (exit 1, zero output) to a slower but correct run; removes the only permanently self-poisoning row.
- **Effort / Risk:** medium / low
- **Invariants:** Strengthens cache-is-data by making the cache non-authoritative; addresses the AGENTS.md priority-2 risk "loss of resumable results".
- **Failing test:** Populate a cache, truncate `evaluation_cache.sqlite3` to garbage, re-run; assert exit 0 with every photo in `evaluation.csv`.

**3. Write exactly one `xmp:Rating`, and match sidecars with `logical_asset_stem`**
- **Where:** `xmp_rating.py:34-51`, `55-69`
- **Today:** Line 37 takes the *first* `rdf:Description` in the tree, so a real Lightroom/darktable sidecar (copied into `picks/` via `file_ops.py:38`, `102-110`, `149-165`) ends with two conflicting `xmp:Rating` values; a single-Description child-element form (`<xmp:Rating>2</xmp:Rating>`) yields two inside one element. `_sidecar_base` (65-69) strips one stem suffix unconditionally while `file_ops.logical_asset_stem` does not, so `_find_sidecar(shot.ARW)` returns `shot.v2.xmp` and overwrites another asset's sidecar; `export_manifest.csv` then reports it as `copied_and_rated` (`cull.py:588-602`). The tie-break at line 62 is `min(key=len(name))`, resolved by `iterdir()` order.
- **Change:** Iterate all Descriptions, prefer the one already carrying `{XMP_NS}Rating`, strip both attribute and child-element forms from the chosen element and every other one; replace `_sidecar_base` with `logical_asset_stem` (keeping the explicit `.xmp` filter); make the tie-break deterministic with a secondary `path.name.casefold()` key. Harvest the document's actual xmlns prefixes for `register_namespace` rather than a fixed list; do not claim xpacket preservation (ElementTree drops document-level PIs).
- **Gain:** Ends ambiguous multi-rating packets and cross-asset sidecar destruction inside `picks/`; collapses two "asset family" definitions into one.
- **Effort / Risk:** medium / medium
- **Invariants:** Read-only sources hold: all writes stay under `picks/` (`cull.py:573`, Specifications.md:164). Closes the AGENTS.md:226 XMP checks (existing sidecar, family dedup, rating boundaries).
- **Failing test:** Hand-authored fixture with two `rdf:Description` elements where only the second has `xmp:Rating="2"`; assert exactly one `xmp:Rating` survives and equals `"5"`. Plus: `write_xmp_rating(shot.ARW, 5)` creates `shot.xmp` and leaves `shot.v2.xmp` byte-identical.

**4. Stop rewriting `failures.csv` in full on every recorded failure**
- **Where:** `run_audit.py:47`, `113-124`, `141-150`, `170-193`
- **Today:** `record_failure` appends to an unbounded list and passes the *entire* list to `atomic_write_csv`, giving O(F^2) bytes. Measured at 3,000 failures: 10-19 s and 360-473 MB written for a 0.24-0.32 MB file (roughly 1,500x amplification).
- **Change:** Open `failures.csv` once (write the header at construction so the zero-failure assertion at `tests/test_pipeline_integration.py:94` still holds), append and flush one row per failure, replace `self.failures` with an int counter, close in `finish()`. Secondarily, coalesce `_write_manifest` on `record_failure`/`set_count` (run.json is ~1.1 KB, so this saves syscalls, not volume) with unconditional flushes in `set_phase` and `finish`.
- **Gain:** O(F) instead of O(F^2). The motivating case is documented at `readme.md:950`: an installed LibRaw lacking a RAW format fails *every* file at `cull.py:703-706`, so a first run against an unsupported camera looks like a hang instead of a fast "everything failed" report.
- **Effort / Risk:** medium / medium
- **Invariants:** Improves bounded memory (removes the last unbounded audit list). Per-failure durability is kept; whole-file atomicity for `failures.csv` is traded for append-with-flush, which Specifications.md:120-123 must state.
- **Failing test:** Record 50 failures; assert byte accounting is O(F) (fails today by ~1,500x) and that `atomic_write_csv` is never called from `record_failure`.

**5. Validate `--select` before the evaluation pass**
- **Where:** `cull.py:922-945`, `968-986`, `1055`
- **Today:** `--select` has no argparse `type=`, and `_selection_count` first interprets it at `cull.py:506`, after the full model pass; `--select 3O` or `--select -5` raises into the generic handler (`cull.py:328-334`), marking the run `failed` with no `evaluation.csv`, `feedback.csv`, or `review.html`.
- **Change:** In `_validate_config` (which runs at `cull.py:279`, before the run directory exists), reject any `selection` that is not `""`/`none`/`skip`/`all` or a non-negative int, so a typo exits 2 in under a second. Keep the raise at 941-942 for the interactive path. Drop the redundant `count <= available` bound (`portfolio.py:80` already clamps via `min(len(eligible), max(count, len(pinned)))`) and warn instead; that half needs `tests/test_cli.py:16` updated. Drop the unused `config` parameter at `cull.py:922`.
- **Gain:** Malformed `--select` costs zero seconds and exits 2 like every other bad option. Cost of the current behavior is one wasted pass plus a `failed` audit, not repeated inference (rows commit per image at `cull.py:784-790`).
- **Effort / Risk:** small / low
- **Invariants:** Pure input validation in the existing gate; no source writes, no cache change. README (281, 796-803) and Specifications.md:139 update together.
- **Failing test:** `_validate_config` with `selection="3O"` raises `ValueError`; today it does not.

**6. Put effective precision in the cache identity**
- **Where:** `cull.py:948-965`, `model_runtime.py:133-151`
- **Today:** `_cache_signature` omits precision, so a CUDA fp16-autocast row and a CPU fp32 row for the same file share one key (`evaluation_cache.py:100`, `165`). `--no-mixed-precision` on CUDA therefore returns 100% cache hits with unchanged fp16 numbers, while `run.json` (via `asdict` at `cull.py:130-134`) *asserts* fp32. Worse, `move_to_cpu()` (133-143) flips the autocast guard mid-run, so one run can mix precisions under one signature.
- **Change:** Add `precision=fp16-cuda-autocast|fp32` derived from the effective state, exposed as a `ModelRuntime` property read after the load-time fallback at `cull.py:445-450` (do not resolve the device before `cull.py:350`: torch is imported lazily at 410-416 so a fully cached run never loads it). Have `move_to_cpu()` notify the caller so `cull.py` records `record_failure(None, "inference_device_fallback", error)` at the switch (matching `cull.py:442`) and suppresses further cache writes for that run.
- **Gain:** `--no-mixed-precision` becomes an honest miss. State the cost plainly: because `pipeline_signature` is half the primary key, every existing cached row for every user becomes unreachable once, forcing one full re-evaluation. No schema bump (no stored column changes). Device type is deliberately still excluded; say so.
- **Effort / Risk:** small / low
- **Invariants:** AGENTS.md:158-160 (identity includes all settings that change per-image evaluation), currently violated. Does not extend CUDA autocast to MPS/CPU; that stays a separate, evidence-gated change.
- **Failing test:** `_cache_signature` for two configs differing only in resolved precision must differ; today the strings are identical.

**7. Make the default capture timezone depend on the capture date**
- **Where:** `metadata_reader.py:39-40`, `82-83`
- **Today:** `local_timezone()` returns a *fixed-offset* snapshot and is called per parse at line 82, so a January capture read in September under `TZ=America/New_York` is stamped `-04:00` instead of `-05:00` (measured: exactly -3600 s), and a run spanning a DST transition can stamp two offsets in one collection. `--assume-timezone` is already correct because it passes a real `ZoneInfo` (43-49).
- **Change:** In the no-offset branch, build the naive datetime and call `.astimezone()` (verified: January yields `-05:00`, July `-04:00`), matching the pattern already used at `metadata_reader.py:101`. Split `timezone_source` so an explicit zone is distinguishable from a host guess, and delete the now-unused `local_timezone()`. Bump `EVALUATION_ALGORITHM_VERSION` (`cull.py:63`).
- **Gain:** Removes a systematic one-hour error on every offset-less capture on the wrong side of a DST boundary. The ranking-relevant case is a run mixing cached rows (offset frozen at the earlier run's date) with fresh rows: frames 3600 s apart cross the `scoring.py:151-154` gates and change which frame wins.
- **Effort / Risk:** small / medium
- **Invariants:** AGENTS.md:107 (aware timestamps, preserved timezone source) is strengthened. Note the label change breaks `tests/test_metadata_reader.py:40` and Specifications.md:24-26, both in scope.
- **Failing test:** Under `TZ=America/New_York`, assert `parse_capture_datetime("2026:01:04 12:00:00")` has `utcoffset() == -5h`; fails whenever the suite runs during EDT.

**8. Record inference-level fallbacks in `failures.csv`**
- **Where:** `cull.py:735-752`, `455`, `469-473`
- **Today:** The batch CLIP call has a bare `except Exception:` at 739 with no binding, no `record_failure`, and no warning, unlike every sibling handler in the same loop (703, 719, 728, 748). The concrete silent case: `_infer_clip_batch` ends in `zip(..., strict=True)` (`model_runtime.py:170-178`) raising `ValueError`, which `infer_clip_batch`'s `except RuntimeError` (106) does not catch; single-image retries then always succeed, so the run silently degrades to 1-image batches with nothing recorded. `resolved_device` is overwritten at 453/469, losing the starting device.
- **Change:** Bind the exception, record a `clip_batch` failure, and warn before the isolation retries. Give `ModelRuntime` a bounded fallback-event list drained into `record_failure` once after `_evaluate_pending`. Store `starting_device` alongside `resolved_device`. Exclude non-image stages from `counts["failed"]` (or add a separate counter) so batch rows do not inflate the per-image count.
- **Gain:** Unquantified, but this is the only way a user can explain a slow run or a mid-collection score shift; AGENTS.md:203 requires it.
- **Effort / Risk:** small / low
- **Invariants:** Observability only; the divide-on-memory-failure and retry-on-CPU behaviors are unchanged. Document the new stages near `readme.md:699-710` and in Specifications.md.
- **Failing test:** `FakeModelRuntime.infer_clip_batch` raising on `len>1` and succeeding on `len==1`; assert exit 0, both photos in `evaluation.csv`, and one `clip_batch` row in `failures.csv`.

### Minor, batch these
- **Escape leading whitespace and control chars in CSV values** (`run_audit.py:196-199`): `startswith(("=","+","-","@"))` misses TAB, CR, LF, and *space* leaders; `lstrip("\t\r\n\v\f \x00")` before the prefix test completes the AGENTS.md:209 invariant. Zero test coverage today.
- **Fix the diversity tie-break direction** (`portfolio.py:105`): `max(available, key=objective)` resolves equal scores by the *largest* casefolded path, opposite to `portfolio.py:65-66` and `cull.py:1002`; use `min` over a negated key. Reachable with `--no-group` and duplicate files.
- **Order the exported selection by quality** (`portfolio.py:83`): pins precede everything, so with `--write-xmp` the worst pick can be the only 5-star sidecar (`cull.py:582`, `xmp_rating.py:19-25`). Sort the returned list by `(-composite_score, path.casefold())`. Note `portfolio_selection_order` (`cull.py:523`) is dead state and `review.html` is unaffected.
- **Swap `-SubSecCreateDate` for `-SubSecTimeDigitized`** (`metadata_reader.py:203`, `249`): the Composite tag is a full datetime, so the digit-strip at 64-67 injects `int(YYYYMM)` (~0.2026 s) as the fraction. Guard the subsecond with `^\d+$` (keep string form so `"05"` works). Bump `EVALUATION_ALGORITHM_VERSION`.
- **Pin `encoding="utf-8", errors="replace"` on the ExifTool subprocess** (`metadata_reader.py:219-225`): the only encoding-unpinned I/O in the repo; on Windows CI (`ci.yml:12`) a mojibaked `SourceFile` never matches `cull.py:690`'s lookup, so every file under a non-ASCII directory silently loses ExifTool metadata.
- **Clean up the partial model download** (`model_runtime.py:256-272`): a failure inside the write loop (263-265) leaks a `.part` file forever; use `try/finally` (catch `BaseException`, not `Exception`, for Ctrl-C) and name the artifact in the checksum message at 268, which currently says "aesthetic" for MUSIQ too.
- **Guard the range on parsed UTC offsets** (`metadata_reader.py:74-79`): `-0080` passes the 4-digit check and silently becomes UTC-22:40; also strip the NUL that EXIF ASCII fields carry, and add a `MalformedOffsetError` so a junk offset keeps a valid date instead of falling to mtime.
- **Cancel queued decodes on Ctrl-C** (`cull.py:680`): the combined `with` calls `shutdown(wait=True)` with no `cancel_futures`, so up to `batch_size` queued RAW decodes run after the interrupt with the progress display already torn down. Managing the executor explicitly cuts the wait to the in-flight tasks (roughly halving at defaults, large at `--batch-size 64`).
- **Recover ExifTool's partial JSON** (`metadata_reader.py:226-230`): a nonzero exit discards valid JSON for every readable file; parse stdout first and raise only when it is empty or unparseable. Keep the explicit-backend re-raise at `cull.py:403-404`, and gate the auto-mode cache veto per file on `timestamp_source.startswith("exiftool:")`, not on `metadata_by_path` membership (a failed file still gets a SourceFile-only entry).
- **Add a scaled `timeout=` to the ExifTool call** (`metadata_reader.py:219-225`): the only unguarded blocking call left; a stalled mount hangs forever with an indefinite spinner. Let `TimeoutExpired` reach the existing handler at `cull.py:402-407`. Skip the chunking half.
- **Refuse an output root holding source photographs** (`cull.py:271-288`): `--output-dir /photos/2026` with input `/photos` passes the guard at 276 and then silently prunes that whole subtree (`file_ops.py:80-89`); add a bounded early-exit walk and exit 2 before line 288.
- **Cap burst size** (`scoring.py:150-154`): with byte-identical timestamps plus a near-duplicate chain, both gates are permanently 0.0 and one cluster absorbed 300 frames (measured), leaving one winner. Add an opt-in `max_burst_size` (default unlimited, or generous: 20 fps for 10 s is legitimately ~200 frames).
- **Close the `ranking` phase before the blocking prompt** (`cull.py:478`, `506`, `526`): operator keyboard-idle time is billed to `timings_seconds["ranking"]`; add `awaiting_selection`/`selection` phases and a `discovery` timing via `perf_counter()` around `cull.py:281-283` (do not construct the audit early: it would mkdir into the source tree on an empty run).
- **Label pinned non-winners in `review.html`** (`portfolio.py:174`): `_assign_ranks` sets `selection_rank` to `""` for non-winners (`cull.py:859-860`), so the `index` fallback is dead and a pinned card renders as a bare `#`. Use `candidate.get("selection_rank") or ""` and a `Pinned` label; do not print `index` behind a `#` (it is the card position).
- **Apply orientation in the RAW bitmap branch** (`image_loader.py:88`): the only place violating AGENTS.md:94 ("EXIF orientation is applied before every image metric"); rotate from `raw.sizes.flip` (the `postprocess` path already honors it). `readme.md:371` and Specifications.md:29 assert the invariant unconditionally.

---

## Functionality

**1. Close every run with a diagnostics summary, and register the diagnostic paths**
- **Where:** `cull.py:304-334, 636-651`; `run_audit.py:89`
- **Today:** `add_output` is called only for evaluation, feedback, contact_sheet, picks, and export_manifest (`cull.py:533, 541, 557, 633-634`), so `run.json["outputs"]` never names failures.csv or run.json; a run ends with one line naming evaluation.csv, and the fatal handler prints the message alone.
- **Change:** seed `"outputs"` in the dict literal at `run_audit.py:89` with `manifest_path` and `failures_path` (do not call `add_output` before the `exist_ok=False` mkdir at `run_audit.py:92`: `atomic_write_text` creates `run_dir` first and the mkdir then raises). Replace `cull.py:650` with a summary printing the existing `data["counts"]` plus a per-stage tally of `audit.failures[*]["stage"]` and the failures.csv path; print `run_dir` in the interrupt (324-327) and fatal (328-334) handlers, and thread `run_dir` into `_show_environment` (both call sites, `cull.py:383, 418`).
- **Gain:** a partially failed run stops reading as clean; both diagnostic files become discoverable on every exit path. Hidden-failure volume unquantified.
- **Effort / Risk:** small / low
- **Invariants:** all writes stay inside the run directory; counts and failure rows are already resident, so bounded memory holds.
- **Failing test:** `tests/test_run_audit.py` asserting `manifest["outputs"]` has `failures` and `run_manifest` immediately after construction, plus an integration run where the loader raises for one of three images asserting the failure count and failures.csv path appear in output.

**2. Report how many feedback rows actually matched the run**
- **Where:** `cull.py:501-520`, `portfolio.py:29-39`
- **Today:** all three consumers use defaulted lookups (`cull.py:502`, `509-514`, `portfolio.py:57-61`); only file existence is checked (`cull.py:985-986`), so a `--primary` switch, a moved collection, or a narrowed scope matches zero rows and the run completes normally.
- **Change:** count matches against evaluated `file_path` values, warn when `len(feedback) > 0` and matches are zero, and add `feedback_rows` (defined as `len(feedback)`, since blank-decision rows are dropped at `portfolio.py:30-32`), `feedback_matched`, `feedback_keep`, and `feedback_reject` to run.json counts. Keep partial matches informational, not a warning: a subfolder re-run legitimately matches few rows.
- **Gain:** three distinct silent no-ops become one visible warning plus durable counters; `photo-cull-validate` gains a basis for checking a feedback file belongs to its evaluation.
- **Effort / Risk:** small / low
- **Invariants:** counting and printing only; sources read-only, cache format unchanged.
- **Failing test:** mocked pipeline with a feedback file whose paths are all absent, asserting `feedback_rows > 0`, `feedback_matched == 0`, and a warning.

**3. Publish timestamp-provenance counts and warn when the mtime fallback dominates**
- **Where:** `cull.py:392-407, 754-762`
- **Today:** `timestamp_source` and `timezone_source` are per-record, cached, and emitted as CSV columns, but never aggregated; the only degradation signal is the bulk-read exception warning at `cull.py:407`. A run can complete `status: completed` with every capture time a filesystem mtime, which also empties `camera_model`, so `_same_camera` (`scoring.py:202-209`) returns True unconditionally and burst boundaries lose both signals.
- **Change:** aggregate both fields over the complete `records` list (`cull.py:475`, after cache hits are appended at 372) via `audit.set_count`, publishing `timestamps_from_exif`, `timestamps_from_filesystem`, and `timezones_assumed`. Warn above a threshold, pointing `timestamps_from_filesystem` at ExifTool install / `--metadata-backend exiftool` and `timezones_assumed` at `--assume-timezone` only (an assumed zone cannot recover a capture time). Surface in `_show_summary` (`cull.py:894`), not the environment panel, which prints before evaluation.
- **Gain:** the pipeline's most consequential silent degradation becomes one machine-readable fact; one pass over an already-materialized list.
- **Effort / Risk:** small / low
- **Invariants:** both fields are already cached and restored (`cull.py:201-202`), so no schema or signature change; update `readme.md:648` and `Specifications.md:120-122`.
- **Failing test:** integration fixture with a JPEG lacking DateTimeOriginal asserting `counts["timestamps_from_filesystem"] == 1`.

**4. Split evaluation timing into per-stage seconds and image counts**
- **Where:** `run_audit.py:134-139`; `cull.py:454, 478, 654-805`
- **Today:** `_finish_phase_timing` runs only from `set_phase` and `finish`, and the phase is `evaluation` from `cull.py:454` to `478`, so decode stalls (699-707), portrait cascades (711-721), the serial MUSIQ loop (723-731), the CLIP batch plus per-image isolation fallback (733-752), and the per-image cache write (781-797) collapse into one number. AGENTS.md lists real-session MUSIQ throughput as an open validation gap.
- **Change:** add `record_stage_seconds(stage, seconds, images)` accumulating into a new top-level `data["evaluation_stages"]` (not nested inside `timings_seconds`, whose values are floats under `RUN_MANIFEST_SCHEMA_VERSION = 1`), rounded to 6 places like `run_audit.py:138`, flushed on the existing per-batch manifest write at `cull.py:798`. Wrap the five blocks, accumulating a float across the per-image loop rather than entering a context manager per image. Name the decode stage `decode_stall`, not decode time: the next batch is submitted at `cull.py:708-709`, so decode overlaps inference. Document that the portrait key is absent for non-portrait presets and the fallback key is absent on healthy runs.
- **Gain:** per-stage rates for every session, the prerequisite for any batching work, and a batch-to-single CLIP degradation becomes a visible rate collapse. Both hot calls already sync (`model_runtime.py:125, 168-169`), so wall time is meaningful.
- **Effort / Risk:** small / low
- **Invariants:** stdlib `time` only; a handful of counters; no cache or model change.
- **Failing test:** `tests/test_run_audit.py` calling the method twice for one stage and asserting accumulated seconds and image count in run.json; integration assertion that `musiq` and `clip` entries exist with non-zero image counts.

**5. Add `--dry-run` to plan the export without copying**
- **Where:** `cull.py:559-643`; `file_ops.py:196-202`
- **Today:** `picks_dir.mkdir(exist_ok=False)` (562) precedes `build_export_plan` (563-566) and `copy_export_plan` (567), whose free-space check is inlined at `file_ops.py:199-202`, so an insufficient-space `OSError` fires only after `picks/` exists; export_manifest.csv is written after the copy (621-632).
- **Change:** add `--dry-run` near `cull.py:1055-1067`, carry it on `PipelineConfig` (24 fields, `cull.py:105-128`). When set: build the plan, skip the mkdir, the copy, the XMP block (570-586), and `add_output("picks", ...)` (633); write the manifest with `kind="planned"` and `destination_size_bytes=""`. Extract `required_export_bytes(export_items)` from `file_ops.py:199-202` so both paths report the same total. Reject or warn on `--dry-run` with `--write-xmp` in `_validate_config`.
- **Gain:** the true file count including sidecar families (`file_ops.py:141-165`, commonly 2-4x the selection) and the required byte total become inspectable with nothing copied into `picks/`; a failed plan leaves the run directory clean. The run itself still writes run.json, evaluation.csv, feedback.csv, and review.html, and still costs a full evaluation pass (the cache absorbs the follow-up real run).
- **Effort / Risk:** small / low
- **Invariants:** dry run performs only `stat()` and `iterdir()` on sources, strengthening read-only; update `readme.md:277-292`, the manifest `kind` table at `readme.md:740-745`, the empty-selection bullet, and Specifications.md:158.
- **Failing test:** two-family fixture with `dry_run=True`, `selection="all"`, asserting `picks/` absent, every manifest row `kind == "planned"` with non-empty `source_size_bytes`, and status `completed`.

**6. Put the decision-critical signals on review cards, and let decisions survive a reload**
- **Where:** `portfolio.py:153-198`
- **Today:** each card shows only Score, Focus, MUSIQ, and Aesthetic (`portfolio.py:164-169`), while the records already carry `burst_size`, `eye_warning`, `blown_pct`, `crushed_pct`, and `timestamp` (`cull.py:756-780`, `197-221`). Decisions live in a bare `const decisions={}` (192) with no `localStorage` anywhere in the repo, which `readme.md:552-553` and `961-964` acknowledge. A failed thumbnail is `continue`d (159-161) and the header then prints a silently smaller `{len(cards)} candidates` (189).
- **Change:** add a second metrics line with capture time (`strftime`, the field is a datetime), `burst_size` as `1 of N`, and clipping percentages above a threshold (already percent-scaled at `cull.py:831-832`), plus an `html.escape`d badge for non-empty `eye_warning`. Wrap the `<img>` in an anchor to its own `thumbnails/<n>.jpg` so a click shows the 480px pixels already written. Disclose the requested-versus-rendered gap in the header, pointing at failures.csv. Persist `decisions` to `localStorage` keyed on `destination.parent.name` (the run stamp from `cull.py:289-290`; `file://` shares one storage bucket, so the key is load-bearing), restore on load, write on click, all in try/catch. Do not seed decisions from `portfolio_selected`: `validation.py:27-28, 47-49` cannot tell a machine keep from a human one, so exported seeded keeps would score the ranker against itself. Show pre-selection as a distinct non-`keep` class instead.
- **Gain:** the two advisory signals the pipeline computes but never displays, plus burst context, reach the only surface a human reads; a review session survives reload. No new decode.
- **Effort / Risk:** small / low
- **Invariants:** page stays self-contained and offline; every added value goes through the existing `escape` import; the downloaded CSV remains authoritative.
- **Failing test:** direct `generate_contact_sheet` call (none exists today) with `eye_warning`, `blown_pct=31.0`, `burst_size=12`, and a loader raising for the second candidate, asserting one card, the warning text, `of 12`, the disclosed skip, and the run-scoped storage key.

**7. Emit a burst-independent quality score and validate against both bases**
- **Where:** `scoring.py:180-193`; `validation.py:30-46`
- **Today:** a singleton cluster gets `focus_ratio == 1.0` by construction (`scoring.py:185`), so the `relative_focus_factor` discount is structurally unreachable for singletons and structurally present for burst members; all three validation metrics and `global_quality_rank` derive from that single `composite_score` (`cull.py:1001-1002`). Post-hoc recovery by dividing by `relative_focus_factor` fails outright when the factor is 0, since the composite is 0 too.
- **Change:** store `burst_independent_score = calculate_composite_score(record, 1.0, active_profile)` alongside the discounted score, and add it plus a companion `burst_independent_rank` as new `REPORT_FIELDS` columns. Leave `global_quality_rank`, `burst_rank`, `selection_rank`, the CSV row order (`_global_quality_order` also orders both emitted CSVs at `cull.py:531, 538`), and portfolio MMR untouched. Report `precision_at_keep_count`, `pairwise_accuracy`, and the mean ranks against both bases, guarding for the new columns being absent so older evaluation.csv files still validate.
- **Gain:** the validation metrics stop conflating ranking quality with grouping behavior, and a standalone-quality column exists. Cost is a second full `calculate_composite_score` call per record (seven multiplies, six powers), negligible against inference.
- **Effort / Risk:** small / low
- **Invariants:** derived at ranking time from cached raw metrics, so no `EVALUATION_ALGORITHM_VERSION` bump; document that `global_quality_rank` stays burst-discounted.
- **Failing test:** `tests/test_scoring.py` with a 100/60/40 burst plus an otherwise identical focus-60 singleton, asserting equal `burst_independent_score` and unequal `composite_score`.

**Minor, batch these**
- Give `--plain` and non-tty runs (the same condition as `cull.py:677`) a per-batch line with count, rate, and ETA at the existing `cull.py:798` boundary, and print `run_dir` at startup so the already-per-batch run.json is pollable; `Specifications.md:134, 139` is the contract that is broken, not the README.
- Write `latest_run.json` from inside `RunAudit` (`__init__` plus `finish`, or `_write_manifest`) so completed, interrupted, and failed runs are all discoverable, and replace the `readme.md:816` ellipsis; note a shared `--output-dir` makes the pointer last-writer-wins.
- Add `portfolio_selection_order` to `REPORT_FIELDS`: it is assigned at `cull.py:523` and then dropped by `extrasaction="ignore"` (`run_audit.py:185`), so it reaches no artifact today.
- Return drop tallies from `discover_image_files` (`file_ops.py:80, 92-96`) with an `onerror` callback, separate `skipped_unreadable` from `skipped_symlink` (`is_file()` swallows stat errors), cap the recorded error-directory list, and print discovered/primary/skipped; print counts inline on the empty path rather than always materializing a run directory in a mis-typed folder.
- Drive `_download_verified` (`model_runtime.py:253-272`) off `Content-Length` with a `Progress` bar and re-raise connection failures naming the artifact, URL, and cache directory; a `--check-models` preflight needs the cache-dir and hash helpers moved into `model_config.py` first to keep torch off the CLI path.
- Add a `photo-cull-compare` command diffing two evaluation.csv files (re-rank within the joined subset before any Spearman arithmetic, parse the literal `"True"`/`"False"` strings, compare `burst_size` rather than the positional `burst_id`), and add the same missing-column guard to `validation.py:19-22`.
- Emit `loss_reason` and `burst_winner_file` for burst losers from the clamped factors `calculate_composite_score` actually uses (`scoring.py:103-108`), with an explicit `tie` token and a `loss_margin`, documented as the largest single weighted contributor.
- Group up to N burst runners-up under their winner on the review page behind a default-0 flag, capping the combined list at `--contact-sheet`; siblings come from `records`, not `candidate_pool`, and non-winner `selection_rank` is `""`, so the card heading needs a branch.

---

## Foundations

**1. Extract a reusable mocked-pipeline harness**
- **Where:** `tests/test_pipeline_integration.py:14-83`
- **Today:** `FakeModelRuntime` is inline (14-35) with no failure-injection seam, and all 24 `PipelineConfig` fields (`cull.py:105-128`, frozen slots dataclass with no defaults) are spelled out literally across 26 lines, pinned to one configuration.
- **Change:** Add `tests/pipeline_harness.py` with `make_config(**overrides)` (built on `dataclasses.replace` over one canonical base), a `FakeModelRuntime` that can raise a chosen exception on the Nth `infer_clip_batch`/`infer_musiq` call, and `run_mocked(config)` applying the two existing patches. Rewrite the current test on top of it, behavior unchanged. Note that the lazy import is `cull.py:410-414` and pulls three names: exercising the accelerator fallback (`cull.py:437-450`) also needs `is_device_fallback_error` influence plus `device="auto"` and a non-CPU `resolve_device` fake.
- **Gain:** A new mocked pipeline test drops from ~40 lines to ~8, which is what makes entries 2, 3, and 4 practical. Unlocks the CLIP isolation retry (`cull.py:739-752`), multi-batch prefetch rotation (708-709, 800-803, never run today because the one test has a single batch), and `RuntimeError("No images were successfully processed")` (476).
- **Effort / Risk:** small / low
- **Invariants:** AGENTS.md:168-169 (no network or model download) and :213-214 (mocked pipeline test on cross-module changes). Patches only the two lazily imported symbols, so AGENTS.md:104 (no heavy imports on the help path) is untouched.
- **Failing test:** `test_multiple_batches_rotate_the_prefetch_buffer`: five fixture images at `batch_size=2`, assert `counts.evaluated == 5` and five rows in evaluation.csv.

**2. Test the cache hit path end to end, including the binary embedding round trip**
- **Where:** `cull.py:190-250`; `tests/test_evaluation_cache.py:11, 30-31`
- **Today:** `record_from_cache` and `record_to_cache` are the only conversion between a live record and a cache row, and grep finds zero test references to either. The fixture uses `embedding_bytes=b"embedding"` with `embedding_length=9`, which is not valid `<f4` (9 is not a multiple of 4), and a naive `timestamp_iso`. The one integration test runs `cache_mode="none"` (`tests/test_pipeline_integration.py:57`), so the lookup loop (359-376) and cache-only branch (381-390) never execute.
- **Change:** Replace the fixture with a real 768-element `<f4` buffer (`model_config.py:8` pins CLIP ViT-L/14) and an aware ISO timestamp. Add a `record_to_cache` -> `put` -> `get` -> `record_from_cache` round trip asserting `np.array_equal` and `tzinfo is not None`. Add a two-run `cache_mode="use"` test asserting `counts.cached == N`, `resolved_device == "cache-only"`, and every numeric evaluation.csv column matching, excluding `cache_hit`, which flips False -> True. Add `cache_mode="refresh"` (cached 0) and a preset-only signature change (cached 0). Cover the `cull.py:195-196` size guard with a multiple-of-4 blob and a mismatched `embedding_length` (a 9-byte blob dies earlier inside `np.frombuffer`). Add a v2 fixture and a `user_version=99` unsupported-schema case; fix `test_version_one_schema_is_migrated_idempotently` (81) to actually reopen the cache.
- **Gain:** Covers ~93 lines and the only mechanism by which a wrong cached value could silently change a user's picks. Empirically reproduced: a plain subprocess cache-only run needs no `ModelRuntime` patch and imports neither torch nor `model_runtime`.
- **Effort / Risk:** medium / low
- **Invariants:** AGENTS.md:222 (required cache checks; "interruption" stays uncovered, so do not claim the row is satisfied), :103 (cache-only run does not initialize the ML stack), :144-145 (data, never pickle), :158-160 (cache identity).
- **Failing test:** `test_cached_run_reproduces_the_fresh_run_scores`.

**3. Use real ImageHash fixtures and cover the CLIP and camera-metadata grouping branches**
- **Where:** `tests/test_scoring.py:12-28`
- **Today:** The fixture declares `phash: int = 0`, and all four `group_bursts` call sites use the default, so `scoring.py:155` is exercised as arithmetic subtraction rather than `ImageHash.__sub__` (symmetric Hamming). Under `trace.Trace` over the full 37-test suite, `scoring.py:161, 169, 170` (CLIP alternative) and `207, 209` (`_same_camera` permissive branch) all report MISS. Production always passes a real `ImageHash` (`cull.py:207, 834`), so no current test is wrong; the fixture type is a latent trap.
- **Change:** Build `phash` via `imagehash.hex_to_hash`, defaulting to `"0000000000000000"`. Add three tests: CLIP extends a burst when pHash differs (0x0 vs 0xffff..., cosine 0.99, expect `[1, 1]`); dissimilar frames split with orthogonal embeddings (`[1, 2]`); empty `camera_model` and `camera_serial` still group (`[1, 1]`). Re-tracing turns 155, 161, 169, 170, 207, and 209 all to HIT.
- **Gain:** Covers 2 of the 5 checks AGENTS.md:224 requires and pins the permissive camera rule, which is reachable under the Pillow backend on RAWs lacking a JPEG thumbnail (`image_loader.py:88-93` -> `metadata_reader.py:156-163` -> filesystem metadata).
- **Effort / Risk:** small / low
- **Invariants:** AGENTS.md:122-123, :224. `imagehash>=4.3,<5` is already pinned (`pyproject.toml:13`); `hex_to_hash` is local computation, so AGENTS.md:168-169 holds.
- **Failing test:** `test_clip_similarity_can_extend_a_burst_when_phash_differs`.

**4. Cover the CLI argument-to-config mapping, especially the cache-mode ternary**
- **Where:** `cull.py:1024-1106`
- **Today:** 25 `add_argument` calls map onto 24 `PipelineConfig` fields, including `cache_mode = "none" if args.no_cache else "refresh" if args.refresh_cache else "use"` (1080). Grep finds zero test references to `build_parser`, `config_from_args`, or `main`.
- **Change:** Add `tests/test_cli_arguments.py` asserting documented defaults, the three cache-mode mappings plus `SystemExit` when both flags are given (mutually exclusive group at 1073-1075), `mixed_precision` inversion (1095), and that `sys.modules` holds no torch after parsing. Then move `--select`'s format check into `_validate_config` (968-986) via one shared helper, preserving the full accepted set: `None` returns early; `""`, `"none"`, `"skip"`, `"0"` are zero, case-insensitively (934-936); any non-negative int literal is accepted. Leave the range check at 943-944, which needs the winner count.
- **Gain:** Covers ~80 unreached lines and the expression deciding whether a run persists its work. The `--select` move avoids a failed run plus discarded report artifacts after a full pass (and discarded inference under `--no-cache`); cached inference survives today because `put` runs at 785 inside the block closing at 474.
- **Effort / Risk:** medium / low
- **Invariants:** AGENTS.md:227, :200-201 (one defaults location; README and Specifications.md in the same change), :104 (already smoke-covered by `.github/workflows/ci.yml:24`).
- **Failing test:** `test_cache_flags_map_to_cache_mode`.

**5. Override pyiqa's mis-declared runtime dependencies**
- **Where:** `pyproject.toml:7-20`
- **Today:** `pyiqa==0.1.15` (line 12) declares `pre-commit`, `tensorboard`, `lmdb`, `datasets`, `sentencepiece`, `accelerate`, `facexlib`, `openai-clip`, `bitsandbytes` (non-darwin), plus `pytest`/`ruff`/`yapf` as runtime requirements. The repo calls only `pyiqa.create_metric` (`model_runtime.py:94-98`).
- **Change:** Add `[tool.uv] override-dependencies` neutralizing 10 names with never-satisfiable markers: `pre-commit`, `yapf`, `tensorboard`, `lmdb`, `datasets`, `sentencepiece`, `facexlib`, `openai-clip`, `accelerate`, `bitsandbytes`. Do **not** override `pytest` or `ruff`: uv overrides replace the project's own groups too (a scratch lock emitted `dev = [{ name = "pytest", marker = "sys_platform == 'never'" }]`), which would break `readme.md:120`, `readme.md:988`, and `ci.yml:21-23`. Comment the pyiqa version as the reason, note that `openai-clip` is the only non-extra source of `ftfy` used by the slow CLIPTokenizer (`model_runtime.py:78-82`; token ids identical for all six `advanced_analysis.py:9-22` prompts today), relock, and document in `readme.md:1019-1036` and Specifications.md.
- **Gain:** ~382 MB and ~44 of 100 distributions removed on macOS. Largest wins: llvmlite 130.1 MB plus numba 12.3 MB (via `facexlib`), pyarrow 126.6 MB (via `datasets`), grpcio 39.7 MB (via `tensorboard`), matplotlib 23.8 MB plus fonttools 13.7 MB. Verified by running full MUSIQ plus CLIP inference against a shadow site-packages that physically omits all 12 names (MUSIQ score 50.92).
- **Effort / Risk:** medium / medium
- **Invariants:** AGENTS.md:196 (this is removal, not addition); model pinning untouched (`model_config.py:10-25`, `model_runtime.py:88-93` still SHA-256 verify before `create_metric`); AGENTS.md:42 permits the lock change.
- **Failing test:** No new unit test; verify with `uv lock`, `uv sync --locked --group dev`, `uv run pytest -q`, `uv run ruff check .`, `uv build`, and a manual MUSIQ plus CLIP smoke run on cached weights.

**6. Document that cache identity excludes `--device` and `--no-mixed-precision`**
- **Where:** `readme.md:770-772`; `Specifications.md:113-116`
- **Today:** `_cache_signature` (`cull.py:948-965`) uses nine fields; neither device nor `mixed_precision` appears, and the signature is computed at `cull.py:350` before the device is even resolved (417). `model_runtime.py:146-161` wraps image features, normalization, the aesthetic head, and `subject_integrity_scores` in `torch.autocast(..., float16)` on CUDA, and all three results land under one `pipeline_signature` (`evaluation_cache.py:92-100`).
- **Change:** Append to the exclusion sentence: the compute device and CUDA mixed-precision setting are also excluded. Then: CUDA mixed precision slightly changes the aesthetic score, subject integrity, and the stored embedding, so use `--refresh-cache` when comparing across devices, toggling `--no-mixed-precision`, or after a `model_device_fallback` entry in run.json. `infer_musiq` (117-129) has no autocast, so `musiq_score` is unaffected. Mirror in Specifications.md; cross-reference from `readme.md:264`.
- **Gain:** Closes a silent-reuse trap, including the intra-run case: `cull.py:437-449` rebuilds with `mixed_precision=False` and `model_runtime.py:131-141` moves to CPU mid-batch, so one run can write fp16 and fp32 rows under one signature. Unquantified.
- **Effort / Risk:** small / low
- **Invariants:** AGENTS.md:158-160 is in tension with the code; widening the signature is a separate change needing `CACHE_SCHEMA_VERSION` handling, so document first.
- **Failing test:** Assert `_cache_signature` is identical for two configs differing only in `device` and `mixed_precision` (true today, zero coverage now).

**7. Document that review.html shows only capped, quality-ordered burst winners**
- **Where:** `readme.md:284, 719-721`; `Specifications.md:144-145`; `portfolio.py:189`
- **Today:** `cull.py:547` passes `candidate_pool[: config.contact_sheet_count]`, and the pool (507-514) is winners sorted by descending composite score (853-856, 1001-1002) followed by forced keeps. Default cap is 100 (1063). Nothing documents this, while `readme.md:714` correctly says feedback.csv "contains all successful candidates."
- **Change:** State that the page holds burst winners in composite-score order, then forced keeps, capped at `--contact-sheet`, and that because forced keeps come last they vanish once winners reach the limit. Point non-winning frames at feedback.csv. Amend `readme.md:284` and Specifications.md:144-145. Also fix `portfolio.py:189`, which prints "{len(cards)} candidates" using the README's whole-collection term (`readme.md:67`) for a 100-row winner subset; make it "{len(cards)} of the ranked burst winners."
- **Gain:** Tells a user culling 3,000 images across 400 bursts that the page covers at most 100 winners, and where the rest live.
- **Effort / Risk:** small / low
- **Invariants:** AGENTS.md:128-133 (feedback semantics) unaffected; documents existing scoping rather than widening it, preserving the bounded thumbnail count.
- **Failing test:** Mocked run with three images, `no_group=False`, `contact_sheet_count=100`; assert `<article class="card"` occurrences equal the winner count, not the record count. No test asserts review.html content today.

**Minor, batch these**
- Add `tests/test_csv_safety.py` pinning `_safe_csv_value` (`run_audit.py:196-199`), the only defense for AGENTS.md:209 and untested; assert int and float pass-through per AGENTS.md:206-207, then extend the tuple with TAB, CR, and LF as a second change updating `readme.md:696-697` and `:969`. Skip the `portfolio.py:193` alignment: neither emitted column can start with a prefix character.
- Append `uv run photo-cull-validate --help`, `uv pip check`, and `uv build` to `.github/workflows/ci.yml:24`, plus the wheel-install-and-help step AGENTS.md:228 actually names; add a pytest case for `validation.main` (`validation.py:75-93`, zero coverage). Update `readme.md:1016-1017`. `uv lock --check` is redundant with `uv sync --locked`; `uv pip check` gain is near zero on a locked resolve.
- Cache pruning: add `last_used_at` (schema 3->4), stamp on `put` only plus one batched `UPDATE` after the lookup loop (never inside `get`, which is called per file at `cull.py:363-372`), and add `--prune-cache DAYS` with `VACUUM`. Must widen the accepted-version tuple at `evaluation_cache.py:66` to include 3, or every field cache raises. Measured 4.2 KB per row, ~340 MB worst case at 20,000 photos across four presets; the user can already delete the cache file (`readme.md:775-777`), so this is convenience.
- Reword AGENTS.md:95 to "The evaluation phase decodes each primary image only once. The review page performs one additional reduced decode per thumbnail candidate," and change PotentialEnhancements.md:11's "Single-pass decode" credit. On a fully cached run the thumbnail pass at `cull.py:543-553` is the *only* decode; RAW pays full cost because `max_dim` never reaches `_load_raw_preview` (`image_loader.py:34-35`). Add one line near Specifications.md:144.
- Fix `readme.md:648`'s run.json counter list to `discovered, primary_candidates, cached, evaluated, failed, winners, selected, contact_sheet_images, exported_files` (two stale names, two undocumented counters set by raw dict mutation at `cull.py:312, 556`), noting `primary_candidates` is the submitted candidate set that `cached`/`evaluated`/`failed` partition. Fix `Specifications.md:163`, which names three of export_manifest.csv's five columns (`cull.py:625-631`), omitting `kind`. Optionally switch both counters to `audit.set_count` so they reach disk when known (AGENTS.md:203-204).
- Amend `readme.md:281` and `Specifications.md:139`: `--select` is checked inside the pipeline after ranking, so an invalid or out-of-range value exits 1 with no reports (cached evaluations survive, so a corrected rerun is fast). It is the only option skipping `_validate_config` (`cull.py:279, 968-986`), and even the format branch (941-942) never reads `available`. Add the same note to the exit-code-1 row at `readme.md:801`, which also covers unavailable `--metadata-backend exiftool`.
- Split `readme.md:103-104` so autofocus is not grouped with three capabilities Pillow does supply, and append to `readme.md:673`: "Requires the ExifTool backend; empty with the Pillow backend." (`metadata_reader.py:292` is the sole populating site; the Pillow constructions at 143-152 and 157-164 leave the default `""`.) Skip the third edit at `readme.md:910-911`; `readme.md:391` already scopes AF to ExifTool.