# Changelog

Each release lists what changed for people who use Photo Cull. The release workflow
generates new sections from commit messages and publishes the section that matches the tag.

## [1.1.0](https://github.com/gsanders300/PicCuller/compare/v1.0.2...v1.1.0) (2026-10-25)


### Added

* check for newer upstream models at startup ([1c964d6](https://github.com/gsanders300/PicCuller/commit/1c964d65586c209f79155958ec7b2127963348ec))


### Build and dependencies

* add hf-xet as a direct dependency ([34b8b26](https://github.com/gsanders300/PicCuller/commit/34b8b26df1b203a6259335d0abe2cf9748c535bb))

## [1.0.2] - 2026-10-04

A documentation release with no change to how Photo Cull works.

### Changed

- The README now says up front that Photo Cull is for non-commercial use, because it
  depends on `pyiqa` and the MUSIQ weights.
- The license badge now separates the MIT code license from the non-commercial use limit.
- Added `THIRD-PARTY-NOTICES.md`, which lists every component and its license.

## [1.0.1] - 2026-10-03

A maintenance release with no change to how Photo Cull works.

### Changed

- The CI and release workflows use `actions/checkout` v7 and `astral-sh/setup-uv` v10.2.0,
  which run on Node.js 24, because GitHub is retiring Node.js 20 for actions.

## [1.0.0] - 2026-10-03

The first stable release. The code is the same as 0.4.0; only the version number, the package
status, and the project status in the README change. See 0.4.0 below for what changed since
earlier development.

Photo Cull 1.0:

- groups bursts of near-identical frames and keeps the best frame of each
- scores every photo for sharpness, exposure, technical quality, and visual appeal, and
  explains each score in plain words
- reads 18 RAW formats plus JPEG, PNG, TIFF, WebP, and BMP, and never modifies the originals
- exports whole asset families, with optional XMP star ratings on the copies only
- offers a review page with whole bursts, a full-screen viewer, and saved decisions
- runs on Apple silicon, NVIDIA GPUs, or the CPU, entirely on your computer

## [0.4.0] - 2026-10-03

The first public release. Earlier versions were developed without tagged releases.

### Added

- The review page shows each burst whole. The frame Photo Cull chose is labelled `Pick`, the
  frames it beat are labelled `Lost`, and rejecting a `Pick` while keeping a better frame
  records which frame you prefer.
- A full-screen viewer on the review page, with click-to-zoom and keyboard marking (arrow
  keys, `K`, `R`, `Esc`).
- The review page saves your decisions in the browser for each run, so a reload or a closed
  tab no longer loses them.
- A warning, and `run.json` counts, when no decision in a feedback file matches a photo in
  the run.
- A warning, and `run.json` counts, when photos have no readable capture time and fall back
  to their file dates.
- A "Scoring presets in detail" section in the reference, with each preset's subject prompts.
- The MIT license, a changelog, and an automated GitHub release for each version tag.

### Changed

- The `balanced` preset weighs sharpness compared with the whole run at 0.5 instead of 1.0,
  after one shoot's keep and reject decisions. The value is provisional.
- Review previews are up to 2048 pixels instead of 480, and the shared preview store keeps
  2000 entries instead of 20000.
- `--contact-sheet` still limits photos, but the page now fills it with whole bursts, so a
  shoot with large bursts shows fewer distinct scenes at the default of 100.

### Fixed

- Three tests left a file open, which failed every Windows CI run. Tests now fail on any
  platform when a file is left open.

[1.0.1]: https://github.com/gsanders300/PicCuller/releases/tag/v1.0.1
[1.0.0]: https://github.com/gsanders300/PicCuller/releases/tag/v1.0.0
[0.4.0]: https://github.com/gsanders300/PicCuller/releases/tag/v0.4.0
