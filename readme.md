# Photo Cull

A cross-platform, automated image evaluation and culling pipeline for high-volume photography. It ranks RAW and JPEG files by combining deep learning aesthetic scoring with edge-focus analysis, clusters rapid-fire bursts, and exports selections along with all associated sidecars and companion files.

---

## 1. Overall Objectives

* **Automate Initial Culling**: Rapidly filter thousands of unedited RAW and JPEG frames down to a curated set of candidates without manual inspection of every capture.
* **Preserve Intentional Blur**: Prevent shallow depth-of-field captures (such as wildlife portraits with heavy bokeh) from being penalized by standard full-frame blur algorithms.
* **Eliminate Redundant Bursts**: Group high-speed burst sequences into single scene clusters and select the sharpest, best-composed winner from each set.
* **Filter Exposure and Technical Defects**: Detect clipped highlights, blocked shadows, digital sensor noise, and compression artifacts using objective computer vision metrics and neural image quality assessment.
* **Protect Original Assets**: Run non-destructive evaluations, preserve source directories, maintain companion files (such as `.xmp`, `.jpg`, and camera sidecars), and record all metrics in timestamped reports.
* **Zero-Setup Cross-Platform Execution**: Enable execution on macOS (Apple Silicon GPU via Metal Performance Shaders) and Windows (NVIDIA GPU via CUDA) with a single command using `uv`.

---

## 2. Architecture and Technical Specifications

### System Requirements and Environment

| Component | macOS Target | Windows Target |
| :--- | :--- | :--- |
| **Operating System** | macOS 13+ (Apple Silicon M-Series) | Windows 10/11 (64-bit) |
| **Runtime** | Python `>=3.12,<3.13` (managed via `uv`) | Python `>=3.12,<3.13` (managed via `uv`) |
| **Compute Hardware** | Unified Memory Apple GPU via Metal (MPS) | NVIDIA Dedicated GPU via CUDA 12.4 |
| **RAW Ingestion** | `rawpy` (LibRaw bindings) | `rawpy` (LibRaw bindings) |
| **Array Standard** | `numpy<2.0.0` (C-ABI compatibility) | `numpy<2.0.0` (C-ABI compatibility) |

---

## 3. Quick Start

### Prerequisites
Install `uv` on your system:
* **macOS / Linux**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
* **Windows**: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

### Running the Tool
Clone or navigate into this folder, then execute:

```bash
# Standard run with hybrid burst clustering
uv run cull.py /path/to/photos

# Optional: Disable burst clustering to rank all photos globally
uv run cull.py /path/to/photos --no-group
```

By default, reports and exported selections are written beneath
`/path/to/photos/.photo-cull/`. This reserved directory and legacy
`picks_YYYYMMDD_HHMMSS` directories are excluded from future scans. Use
`--output-dir /another/location` to keep all generated files elsewhere.

Exports preserve each source file's relative directory, include paired files and
sidecars such as `IMG_0001.ARW.xmp`, refuse to overwrite existing files, and write
an `export_manifest.csv` mapping every source to its exported destination.

Successful evaluations are checkpointed after every image in
`.photo-cull/evaluation_cache.sqlite3`. Unchanged images are reused on later runs,
including after an interruption. Use `--refresh-cache` to recompute all metrics or
`--no-cache` for a run that neither reads nor writes the cache.
