# Photo Cull: Architecture & Design Discussion Log

## Topic 1: Initial Discovery & Library Landscape
- **User Request**: Open-source libraries to evaluate large sets of pictures for composition and focus.
- **Analysis**: Evaluated PyIQA, LAION Aesthetic Predictor, OpenCV (Laplacian variance), and Fastdup. Recommended a tiered pipeline: fast Laplacian pruning, burst grouping, and aesthetic ranking via CLIP embeddings.

## Topic 2: Cross-Platform & Hardware Target Specifications
- **User Request**: Run locally on macOS (Apple Silicon GPU) or Windows (NVIDIA GPU), evaluating RAW and JPEG images.
- **Architecture**:
  - Unified runtime via `uv`.
  - Ingestion using `rawpy` embedded thumbnail extraction to bypass slow demosaicing.
  - PyTorch device abstraction dynamically selecting `cuda` on Windows and `mps` on macOS.

## Topic 3: Output Handling & Selection Workflow
- **User Decision**: Generate a CSV report detailing individual and composite metrics. Follow up with an interactive terminal prompt to copy the top $N$ selects into a timestamped directory.

## Topic 4: Burst Handling & Shallow Depth-of-Field Focus
- **User Decision**: Default to grouping burst sequences.
- **Challenge**: In wildlife and shallow-DoF portraits, 80-90% of the frame is smooth bokeh. Standard Laplacian averages penalize creamy backgrounds.
- **Solution**: Implemented top-percentile edge energy (16x16 grid, taking top 3% sharpest tiles). Avoided heavy object detectors that fail on obscure wildlife or wide landscapes.

## Topic 5: Selection Scoring & Asset Bundling
- **Scoring Strategy**: Multiplicative Focus Gating ($Composite = Aesthetic \times (Focus / MaxFocus)^{1.5} \times NormalizedMUSIQ \times ExposurePenalty$).
- **Companion Preservation**: Automatically detect and copy all companion files sharing the image stem (`.xmp`, paired `.jpg`, etc.) to keep metadata intact.
- **Output Storage**: Timestamped outputs (`evaluation_YYYYMMDD_HHMMSS.csv` and `picks_YYYYMMDD_HHMMSS/`).

## Topic 6: Pipeline Enhancements & Runtime Optimization
- **Additions**:
  1. `imagehash` (pHash) for rapid CPU-level structural burst grouping.
  2. Exposure clipping analysis to detect blown highlights ($Y \ge 254$) and crushed blacks ($Y \le 1$).
  3. `pyiqa` MUSIQ transformer for technical image quality scoring (ISO noise, sensor grain).
- **Environment**: Locked to Python 3.12 (`>=3.12,<3.13`) with `numpy<2.0.0` to balance interpreter speed enhancements with pre-compiled wheel stability across platforms.

## Topic 7: Code Audit and Edge-Case Resolution
- **Inconsistencies Resolved**:
  - Added the `--no-group` command-line switch to allow flat global rankings.
  - Fixed RAW EXIF timestamp extraction by parsing metadata from embedded JPEG thumbnails rather than relying on `file_path.stat().st_mtime`.
  - Formalized the TUI specifications to document the Rich console dashboard and interactive workflow.