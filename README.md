# PNP Access Control

A same-network access control and identity verification system providing separate admin and monitoring dashboards backed by Django, PostgreSQL, and a native Python recognition worker. The host machine runs all services; other devices on the LAN access the admin website via trusted HTTPS with application login.

See [SETUP.md](SETUP.md) for installation, credentials, local HTTPS trust, starting/stopping services, enrollment, and backup instructions. See [IMPLEMENTATION.md](IMPLEMENTATION.md) for verified functionality and hardware acceptance, and [plan.md](plan.md) for architecture and scope.

## Quick Start

Prerequisites: Python 3.10+, Node.js 22+, PostgreSQL 17, Caddy, and a webcam.

```sh
# 1. Setup virtual environment and dependencies
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Build frontend and initialize database
cd frontend && pnpm install --frozen-lockfile && pnpm run build && cd ..
python scripts/download_models.py
python scripts/setup.py

# 3. Start services
python scripts/start.py
```

* Admin Dashboard: `https://localhost:8443/`
* Local Monitor: `https://localhost:8443/monitor`

---

## Models and inference

Browser webcam → JPEG frame (longest side ≤1280px) → Django API / Worker → **RetinaFace ResNet34** detection and five landmarks → **112×112 similarity-aligned face crop** → **ArcFace ResNet50 trained on WebFace600K** → normalized **512-dimensional embedding** → cosine comparison → browser overlays.

RetinaFace uses the R34 ONNX checkpoint from [yakhyo/retinaface-pytorch](https://github.com/yakhyo/retinaface-pytorch), with 640×640 aspect-preserving resize and padding, BGR mean subtraction, anchor decoding and non-maximum suppression. Boxes and landmarks are mapped back to the submitted frame. The recognition checkpoint is `w600k_r50.onnx` from [InsightFace's official buffalo_l release](https://github.com/deepinsight/insightface/releases/tag/v0.7); only that recognition file is extracted. Detection uses RetinaFace, not buffalo_l's SCRFD detector.

Recognition follows the checkpoint's RGB `(pixel − 127.5) / 127.5` preprocessing and five-point alignment. Enrollment samples are individually normalized, averaged, and normalized again to create a reference per registration. Live faces pass size (initially at least 100px per side), visibility, lighting, confidence, coarse yaw, and blur checks before contributing to identity comparison. Calibration captures technically usable crops from 50px per side so stricter selection rules can be evaluated offline. Sharpness is measured on a standardized 128×128 grayscale crop, with an initial minimum Laplacian variance of 20. This avoids applying a fixed edge threshold at different image scales. The warning reports the measured score and required minimum. Set `FACE_STUDIO_BLUR_THRESHOLD` to adjust this heuristic after checking live frames; it is not a calibrated image-quality model. Enrollment keeps its existing quality gates; live settings can be calibrated and may reject distant or moving faces; they need validation at the facility.

Model files total approximately **263 MB**. The downloader pins release URLs, expected sizes, and SHA256 hashes in `models/manifest.json`. First setup downloads the larger buffalo_l archive to extract ArcFace. Downloads are verified before replacement; inference needs no internet. SHA256 values were recorded from the selected upstream release artifacts.

ONNX Runtime uses CPU inference with four intra-operation threads by default. To experiment with CoreML acceleration:

```sh
FACE_STUDIO_PROVIDER=CoreMLExecutionProvider python scripts/start.py
```

CoreML is optional, requires a compatible ONNX Runtime build, and can fall back to CPU for unsupported operations. Benchmark both performance and similarity scores before adopting it. This project does not require CUDA, PyTorch, or the InsightFace Python package.

## Threshold and validation

The default cosine threshold is **0.60**, an uncalibrated starting point. Override it with:

```sh
ARCFACE_MATCH_THRESHOLD=0.65 python scripts/start.py
```

Raise it to reject more candidate matches; lower it to accept more, with greater risk of wrong identities. Calibrate using separate genuine and impostor captures from the entrance, then evaluate on further held-out recordings. Cosine similarity ranges from −1 to 1; it is not identity probability. Detection confidence is a separate score.

Registration collects examples for comparison; it does not retrain the recognition model. Pose guidance uses coarse landmark ratios calibrated against the initial front-facing samples, not exact head angles. Use modest turns, not full profiles.

Live recognition uses Supervision's ByteTrack to associate face boxes, keeps up to 24 recent samples per track, and selects up to 8 quality-ranked views within a 3-second window. The average embedding is weighted by sharpness, size, and a coarse yaw cue. By default, confirmation requires 3 agreeing individual matches, an aggregate similarity of 0.60, and a 0.04 margin over the second candidate. A sudden incompatible embedding clears the history. Evidence expires after 1 second without a new usable sample; gaps longer than 1.5 seconds or frame-size changes reset tracking. These rules reduce flicker but do not guarantee that tracks remain correct when people cross or occlude one another.

Confirmed sightings are recorded once per track; the dashboard shows recent sightings. There is no liveness protection.

## Existing registrations and storage

Every person profile maintains ArcFace embeddings and reference images in PostgreSQL.

`python scripts/rebuild_profiles.py` creates separate ArcFace archives from existing registration photos if converting legacy folder archives. It requires exactly one detected face in every image and writes a completed profile atomically. Failures are reported per folder; re-register those people.

## Verification & Tests

```sh
# Unit and recognition pipeline tests
python -m unittest discover -s tests -v

# Backend tests
python backend/manage.py test access
```

Checks cover registration, multi-person matching, threshold and quality gates, model output decoding, alignment, RGB preprocessing, and blank-frame inference when weights are available.

## Licensing

RetinaFace implementation: [upstream source and MIT license](https://github.com/yakhyo/retinaface-pytorch). ArcFace checkpoint: [InsightFace model licensing](https://github.com/deepinsight/insightface/blob/master/python-package/docs/model_zoo.md). InsightFace's supplied pretrained weights are for non-commercial research; obtain appropriate licensing before commercial deployment. Their code license does not grant commercial rights to the weights.
