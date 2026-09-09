# Photo Cull: System and Terminal Specifications

This document defines the complete technical architecture, mathematical models, and console interface design for `photo-cull`.

---

## 1. Metric Specifications and Mathematical Formulas

### Focus Scoring (Top-Percentile Patch Energy)

To prevent creamy background bokeh from lowering sharpness scores in wildlife and portrait photography, the image is divided into a 16x16 grid (256 local patches). The Laplacian variance is computed on each patch:

$$\text{Var}(\Delta I_{\text{patch}}) = \frac{1}{N}\sum (L(x, y) - \mu_L)^2$$

The global focus score is the arithmetic mean of the top 3% sharpest patches. If an eye, feather, or ridge is in critical focus, the metric captures it regardless of background blur.

### Exposure and Clipping Penalties

Using the 8-bit luminance channel ($Y$):

* **Blown Highlights ($P_{\text{white}}$)**: Fraction of pixels with $Y \ge 254$.
* **Crushed Shadows ($P_{\text{black}}$)**: Fraction of pixels with $Y \le 1$.
* **Penalty Multiplier ($M_{\text{exp}}$)**:

$$M_{\text{exp}} = \max(0.4, 1.0 - 5.0 \times \max(0, P_{\text{white}} - 0.02)) \times \max(0.6, 1.0 - 2.0 \times \max(0, P_{\text{black}} - 0.05))$$

Shots with clipped highlights exceeding 2% or blocked shadows exceeding 5% receive progressive score reductions.

### Technical Quality Assessment (PyIQA MUSIQ)

The image tensor is evaluated by the **Multi-scale Image Quality Transformer (MUSIQ)**, generating an objective score from 0 to 100 that quantifies compression artifacts, sensor noise, high-ISO grain, and motion smear independent of input resolution.

### Hybrid Burst Grouping

Consecutive images sorted by timestamp are clustered into a burst if:

1. $\vert{}\Delta t\vert{} \le 2.0 \text{ seconds}$, **and**
2. $\text{HammingDistance}(\text{pHash}_A, \text{pHash}_B) \le 8$, **or**
3. $\text{CosineSimilarity}(\vec{E}_A, \vec{E}_B) \ge 0.88$, where $\vec{E}$ represents the normalized 768-dimensional CLIP ViT-L/14 embedding.

### Burst Winner Selection (Multiplicative Gating)

Because raw Laplacian energy is content-dependent, each image first receives a session-relative focus percentile $P_{\text{focus}}$. A deliberately moderate absolute focus gate prevents a blurred standalone image from receiving a neutral sharpness score:

$$F_{\text{absolute}} = 0.5 + 0.5 \times P_{\text{focus}}$$

Within each burst cluster, the frame with the highest focus score establishes $\text{Focus}_{\text{max}}$. Each image is then assigned a composite score:

$$\text{Composite Score} = S_{\text{aesthetic}} \times F_{\text{absolute}} \times \left(\frac{\text{Focus}}{\text{Focus}_{\text{max}}}\right)^{1.5} \times \operatorname{clip}\left(\frac{\text{MUSIQ}}{100}, 0, 1\right) \times M_{\text{exp}}$$

The highest composite score in the cluster is designated the burst winner (`burst_winner = True`). Standalone shots with no burst companions are treated as single-frame clusters with $\text{Focus} / \text{Focus}_{\text{max}} = 1.0$.

When burst clustering is bypassed via the `--no-group` flag, each image is scored independently:

$$\text{Composite Score} = S_{\text{aesthetic}} \times F_{\text{absolute}} \times \operatorname{clip}\left(\frac{\text{MUSIQ}}{100}, 0, 1\right) \times M_{\text{exp}}$$

---

## 2. Terminal User Interface (TUI) Specifications

The terminal interface uses `rich` to provide a clear, non-interactive live dashboard during processing, followed by an interactive selection prompt. It avoids full-screen curses capture, ensuring standard terminal history, stdout pipes, and script logs remain scrollable and accessible.

### Interface Architecture and Lifecycle

```
[Phase 1: Environment Banner]
  └── Displays compute device, source path, burst mode, and active models.
        │
        ▼
[Phase 2: Live Processing Dashboard]
  ├── Multi-metric progress bar with spinner, completion count, and ETA.
  └── Asynchronous logging of skipped or unreadable files without tearing bars.
        │
        ▼
[Phase 3: Ranked Summary Table]
  └── Formatted, color-coded preview of the top-ranked candidates.
        │
        ▼
[Phase 4: Interactive Selection & Feedback Panel]
  ├── Interactive prompt for candidate count (N, 'all', or skip).
  └── Formatted confirmation panel summarizing total copied primary and companion files.

```

### Component Details

* **Environment Banner (`rich.panel.Panel`)**: Highlights the active compute target (`CUDA`, `MPS`, or `CPU`), source path, burst grouping mode, and active evaluation models.
* **Live Progress Dashboard (`rich.progress.Progress`)**: Features an animated spinner, stage description, proportional progress bar, completion percentage, frame counter, and rolling ETA. Corrupt files are logged above the bar using `console.log()` without corrupting layout.
* **Ranked Summary Table (`rich.table.Table`)**: Renders the top 10 candidates with color-coded columns:

| Column Name | Alignment | Content Description |
| --- | --- | --- |
| **Rank** | Center | Global sort rank across the directory |
| **Filename** | Left | Source image filename |
| **Burst** | Center | Assigned numeric cluster ID |
| **Winner** | Center | Burst winner designation (`Yes` or `No`) |
| **Focus** | Right | Mean top-percentile Laplacian patch variance |
| **MUSIQ** | Right | Technical IQA rating (0 to 100) |
| **Aesthetic** | Right | LAION linear aesthetic model rating (1 to 10) |
| **Blown %** | Right | Percentage of clipped highlight pixels |
| **Composite** | Right | Final computed score used for ranking |

* **Interactive Export**: Pauses execution to accept an integer count, `all`, or empty input to exit. Copies selected primary files and matching companion files (`.xmp`, `.jpg`, `.mp4`) into a timestamped run directory, concluding with a formatted transfer summary panel. Relative source directories are preserved, duplicate family members are copied only once, existing destinations are never overwritten, and an export manifest records every source/destination pair.
* **Output Isolation**: Reports and selections default to `<source>/.photo-cull/`, or to an explicit `--output-dir`. That directory and legacy timestamped `picks_*` directories are excluded from subsequent source scans.
* **Resumable Evaluation**: Each successful image evaluation is committed to a SQLite cache using the resolved path, byte size, nanosecond modification time, and scoring-pipeline signature as its identity. Unchanged records are reused after interruption; `--refresh-cache` bypasses reads and replaces metrics, while `--no-cache` disables persistence.

---

## 3. Potential Extensions and Improvements

| Area | Concept | Implementation Path |
| --- | --- | --- |
| **Focus Verification** | Direct Autofocus Bracket Extraction | Integrate `PyExifTool` to read proprietary camera MakerNotes (Sony, Nikon, Canon), extracting active AF coordinates $(X, Y)$ to score sharpness directly on the camera's intended focus target rather than across generic grid tiles. |
| **Subject Integrity** | Open-Vocabulary Subject Verification | Incorporate lightweight `YOLO-World` to ensure critical anatomy (animal head, eyes, wings) is within frame, penalizing shots where subjects turned away, clipped wings, or exited the field of view. |
| **Metadata Tagging** | Non-Destructive XMP Sidecar Ratings | Use `pyexiv2` to write star ratings (1–5) and color labels directly into `.xmp` sidecar files, allowing AI culling picks and ratings to appear instantly inside Adobe Lightroom, Capture One, or darktable without copying physical files. |
| **Ingestion Throughput** | Mini-Batch GPU Tensor Queuing | Decouple CPU disk I/O and thumbnail decoding from GPU inference using a multi-threaded producer-consumer queue, passing batched tensors (batch size 16 or 32) to saturated CUDA/MPS cores. |
| **Facial & Eye Tracking** | Dedicated Eye/Iris Landmark Scoring | For human and domestic pet portrait sessions, add `MediaPipe` face mesh checks to detect blinking, closed eyes, and unfavorable head-angle deviations. |
| **Model Customization** | Fine-Tuned Aesthetic Linear Heads | Train specialized regression heads atop the CLIP ViT-L/14 backbone using curated wildlife competition datasets to reward genre-specific lighting and dynamic action postures. |
