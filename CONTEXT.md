# Photo Cull

Photo Cull evaluates a read-only photo collection, groups bursts, ranks candidates, and exports a selected portfolio. This glossary names the concepts the code and its documents share.

## Language

### Evaluation cache

**Base evaluation**:
The metrics measured for one image that do not depend on the scoring preset, such as capture time, focus, exposure, technical quality, aesthetic score, and embedding.
_Avoid_: base row, evaluation row

**Preset evaluation**:
The metrics for one image whose meaning depends on the scoring preset: subject integrity and the portrait face and eye values.
_Avoid_: preset row

**Preset missing**:
The cache state where an image's base evaluation is stored but its preset evaluation for the current preset is not.
_Avoid_: base hit, partial hit

**Preset refresh**:
Producing a missing preset evaluation from a stored base evaluation without evaluating the image again.
_Avoid_: rescoring, re-evaluation

### Review page

**Thumbnail store**:
The bounded set of review thumbnails shared by every run over a collection, one per unchanged source image, so a repeat run decodes nothing for the review page.
_Avoid_: thumbnail cache, contact-sheet cache
