---

## 4. Potential Extensions and Improvements

| Area | Concept | Implementation Path |
| :--- | :--- | :--- |
| **Focus Verification** | Direct Autofocus Bracket Extraction | Integrate `PyExifTool` to read proprietary camera MakerNotes (Sony, Nikon, Canon), extracting active AF coordinates $(X, Y)$ to score sharpness directly on the camera's intended focus target rather than across generic grid tiles. |
| **Subject Integrity** | Open-Vocabulary Subject Verification | Incorporate lightweight `YOLO-World` to ensure critical anatomy (e.g., animal head, eyes, wings) is within frame, penalizing shots where subjects turned away, clipped wings, or exited the field of view. |
| **Metadata Tagging** | Non-Destructive XMP Sidecar Ratings | Use `pyexiv2` to write star ratings (1–5) and color labels directly into `.xmp` sidecar files, allowing AI culling picks and ratings to appear instantly inside Adobe Lightroom, Capture One, or darktable without copying physical files. |
| **Ingestion Throughput** | Mini-Batch GPU Tensor Queuing | Decouple CPU disk I/O and thumbnail decoding from GPU inference using a multi-threaded producer-consumer queue, passing batched tensors (e.g., batch size 16 or 32) to saturated CUDA/MPS cores. |
| **Facial & Eye Tracking** | Dedicated Eye/Iris Landmark Scoring | For human and domestic pet portrait sessions, add `MediaPipe` face mesh checks to detect blinking, closed eyes, and unfavorable head-angle deviations. |
| **Model Customization** | Fine-Tuned Aesthetic Linear Heads | Train specialized regression heads atop the CLIP ViT-L/14 backbone using curated wildlife competition datasets to reward genre-specific lighting and dynamic action postures. |