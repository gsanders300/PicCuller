# Give the thumbnail store its own module

The key, decode-on-miss, and pruning move out of cull.py behind a small interface.

## Decisions

- New module `thumbnail_store.py`, added to `py-modules`, the AGENTS.md repo map, and the docs/development.md module map.
- Interface: `ThumbnailStore(root, source_root)` with `thumbnail(source) -> Path`, `prune(keep=THUMBNAIL_STORE_LIMIT) -> int`, and `decoded` / `decode_seconds` tallies of successful decodes.
- `MAX_THUMBNAIL_DIMENSION` and `THUMBNAIL_STORE_LIMIT` move into the module.
- The thumbnail key is private. Tests check its properties through `thumbnail()`.
- `link_or_copy` stays in portfolio.py. `generate_contact_sheet` receives `store.thumbnail`.
- cull.py keeps deciding when to prune and still writes `thumbnails_decoded`, `thumbnails_reused`, `thumbnails_pruned`, and the `thumbnail_decode` stage time.
- No change in behaviour, output, or run.json fields.

## Steps

- [x] Create thumbnail_store.py from `_thumbnail_key`, `_cached_thumbnail`, `_prune_thumbnail_store`, and the two constants. Compute the key once per request.
- [x] cull.py: delete those functions, the constants, and the `provide_thumbnail` closure. Build a `ThumbnailStore` in the contact-sheet phase and copy its tallies into the audit.
- [x] tests/test_thumbnail_store.py: go through `ThumbnailStore` only. Add a test that `decoded` counts only real decodes.
- [x] tests/test_pipeline_integration.py: patch `ThumbnailStore.thumbnail` instead of `cull._cached_thumbnail`.
- [x] pyproject.toml, AGENTS.md, docs/development.md: list the module.

## Verification

- [x] `uv run pytest -q`: 193 passed, 3 skipped, only the 3 known Windows failures in test_run_audit.
- [x] `uv run ruff check .` and `git diff --check`: clean.
- [x] `uv build`: the wheel contains thumbnail_store.py. `uv lock --check` passes.
- [x] `photo-cull --help` imports none of numpy, imagehash, PIL, or torch.

## Review

- cull.py no longer imports `hashlib`, `tempfile`, or `relative_key`, and loses the closure that recomputed the key to detect a decode.
- The `thumbnail_decode` stage is now recorded once per run with the total time and count, instead of once per decode. Counts are identical; the seconds are rounded once rather than per call.

---

# Deepen the evaluation cache interface (done, 3442c68)

The cache accepts and returns the pipeline record, so cull.py stops hand-marshaling cache rows.

## Decisions

- The cache accepts and returns the pipeline's record dict.
- Interface: `lookup(path, fingerprint, signature, preset) -> CacheHit | None`, `store(path, fingerprint, signature, preset, record)`, `store_preset(path, signature, preset, record)`.
- `CacheHit` is a frozen dataclass: `record: dict`, `preset_missing: bool`. `None` means a miss.
- A preset-missing record carries default preset values (subject integrity 1.0, no faces, eye factor 1.0).
- `lookup` fills `file_name`, the resolved `file_path`, and `cache_hit=True`. `store` ignores keys it does not persist.
- `store` writes the base and preset evaluations in one transaction.
- `CachedEvaluation` and `CachedPresetEvaluation` are deleted.
- A corrupt stored row makes `lookup` raise. cull.py keeps recording `cache_lookup` and dropping the image.
- Out of scope: a null cache adapter, the record literal in `_evaluate_pending`, `REPORT_FIELDS`, and re-evaluating corrupt rows.
- No `CACHE_SCHEMA_VERSION` or `EVALUATION_ALGORITHM_VERSION` bump: stored columns do not change.
- `numpy` and `imagehash` stay lazily imported, to keep the `--help` path light.

## Steps

- [x] evaluation_cache.py: add `CacheHit`, private record-to-row and row-to-record functions, and a private preset-defaults constant.
- [x] evaluation_cache.py: replace `get`/`get_preset`/`put`/`put_preset` with `lookup`/`store`/`store_preset`. Convert both rows before opening the transaction.
- [x] evaluation_cache.py: delete `CachedEvaluation` and `CachedPresetEvaluation`.
- [x] cull.py: delete `record_from_cache`, `record_to_cache`, and `record_to_preset_cache`. Update the lookup loop, `_refresh_preset_scores`, and `_evaluate_pending`. `preset_pending` holds records.
- [x] tests/test_cache_round_trip.py: rewrite against `store`/`lookup`. Keep the existing four tests.
- [x] tests/test_cache_round_trip.py: add tests for a preset-missing lookup, for `store_preset` filling the gap, and for a failed preset write leaving no base row (SQLite trigger, no mocks).
- [x] tests/test_evaluation_cache.py: build records instead of dataclasses. The legacy-migration tests use plain row tuples.
- [x] Specifications.md section 6: state that the base and preset evaluations are written together or not at all.

## Verification

- [x] `uv run pytest -q`: 193 passed, 3 skipped. 3 failures in `tests/test_run_audit.py` exist on unmodified `main` too (Windows file lock on `failures.csv` during temp-dir cleanup).
- [x] `uv run ruff check .`: clean.
- [x] `git diff --check`: no whitespace errors.
- [x] `photo-cull --help` imports neither numpy nor imagehash.
- [x] Grep finds no remaining reference to the removed names outside the dated notes file.

## Review

- cull.py lost about 100 lines. It no longer imports any cache-internal type, and the field list and type conversions exist only in evaluation_cache.py.
- The cache's interface is `lookup`, `store`, `store_preset`, `CacheHit`, `FileFingerprint`, and `relative_key`.
- Side effect of decoding the row inside `lookup`: a corrupt stored row on the preset-missing path now raises during lookup.
  - Other presets: before, the fault surfaced inside preset refresh and ended the run. Now it is an isolated `cache_lookup` failure.
  - Portrait: before, the image was re-evaluated without reading the row. Now it is a `cache_lookup` failure and the image is dropped, the same as any other corrupt row.
- Environment: `uv` needed `UV_NATIVE_TLS=1` (now `UV_SYSTEM_CERTS`) to download Python 3.12 through this machine's TLS interception.
