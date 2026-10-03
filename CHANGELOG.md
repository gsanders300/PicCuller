# Changelog

Each release lists what changed for people who use Photo Cull. The release workflow
publishes the section that matches the tag, so every tagged version needs a section here.

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

[0.4.0]: https://github.com/gsanders300/PicCuller/releases/tag/v0.4.0
