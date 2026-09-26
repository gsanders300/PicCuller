# Developing Photo Cull

This page covers setting up a development environment, running the checks, and finding your
way around the code. Contributor and coding-agent rules, including the safety invariants
that every change must keep, are in [AGENTS.md](../AGENTS.md).

## Set up

Install the locked environment with the test and lint tools:

~~~text
uv sync --locked --group dev
~~~

Use `uv` for everything. Do not manage the environment with a second package tool, and do
not update `uv.lock` unless a dependency declaration changes.

## Run the checks

| Check | Command |
| --- | --- |
| Tests | `uv run pytest -q` |
| Lint | `uv run ruff check .` |
| Whitespace | `git diff --check` |
| Command help | `uv run photo-cull --help` and `uv run photo-cull-validate --help` |
| Lock file | `uv lock --check` and `uv pip check` |
| Package build | `uv build` (source distribution and wheel) |

Run the build, lock, and help checks after any packaging, dependency, entry-point, or module
change. The case-collision test skips on a case-insensitive file system, such as the macOS
default; that skip is expected, not a failure.

Tests never download models or touch a network service. Pipeline tests mock the model
runtime, and they use temporary directories rather than real photo collections.

Continuous integration uses the locked environment and runs the tests, lint, and a command
smoke test on macOS and Windows.

## Dependencies

| Package | Version | Purpose |
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

## Code layout

| File | Responsibility |
| --- | --- |
| `cull.py` | Command-line interface, pipeline control, ranking, terminal output, and export orchestration |
| `file_ops.py` | Discovery, asset families, export planning, and safe copies |
| `image_loader.py` | Orientation-aware standard-image and RAW-preview decode |
| `metadata_reader.py` | ExifTool and Pillow metadata, timestamps, and time zones |
| `model_config.py` | Pinned model identities, revisions, URLs, and checksums |
| `model_runtime.py` | Verified model download, loading, inference, batching, and device fallback |
| `scoring.py` | Scoring profiles, focus factors, burst grouping, composite scores, and score reasons |
| `advanced_analysis.py` | CLIP subject prompts and portrait face and eye checks |
| `evaluation_cache.py` | SQLite evaluation cache: schema, migration, lookup, and writes |
| `portfolio.py` | Feedback, diversity selection, and the review page |
| `xmp_rating.py` | Export-only XMP ratings |
| `run_audit.py` | Atomic audit, JSON, and CSV output, and failure records |
| `validation.py` | The `photo-cull-validate` ranking check |
| `tests/` | Unit tests and mocked pipeline tests |

`readme.md` is lowercase because `pyproject.toml` refers to that exact name.

## Documentation map

Keep the documents consistent with the code. When behavior changes, update each document
that describes it in the same change.

| Document | Audience and scope | Update it when |
| --- | --- | --- |
| [readme.md](../readme.md) | Users: install, first run, everyday tasks, troubleshooting | A workflow, prompt, or common task changes |
| [docs/reference.md](reference.md) | Users: every option, default, rule, score, output column, and exit code | An option, default, output field, or behavior changes |
| [Specifications.md](../Specifications.md) | Developers: the formal contract the code must meet | Scoring, grouping, cache, terminal, or export requirements change |
| [docs/how-a-photograph-is-judged.md](how-a-photograph-is-judged.md) | Readers who want the reasoning, as narrative prose | A step's behavior or rationale changes |
| [PotentialEnhancements.md](../PotentialEnhancements.md) | Known limits and deferred work | Work is done, deferred, or ruled out |

The user documents use plain technical English: short sentences, active voice, and the
reader addressed as "you".
