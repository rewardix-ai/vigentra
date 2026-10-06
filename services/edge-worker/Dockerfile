# Vigentra edge analytics worker.
#
# Build targets:
#
#   base  (default) - mock detector only. ~120 MB. No torch, no CUDA.
#                     Runs the full ingestion path for demos and CI.
#   yolo            - real Ultralytics inference. ~2.5 GB.
#                     Build with:  docker build --target yolo ...
#   anpr            - yolo plus the consensus number-plate reader. ~3.5 GB.
#                     Build with:  docker build --target anpr ...
#   anpr-text       - anpr plus the whole-crop text reader (see the end of this file).
#
# The demo Compose stack uses `base` on purpose: pulling 2.5 GB of CUDA
# userspace to show a working pipeline is a poor trade. Switch to `yolo` on a
# machine that will actually run a model.
#
# Dependencies first, code last. Each target is a `*-deps` stage that installs
# everything heavy (apt packages, torch, the reader's libraries) and a thin
# stage on top that copies the code. A code change therefore rebuilds in
# seconds from cache and needs no network - which matters on a venue's network.
# Copying the code into a stage the heavy installs then build FROM made every
# code change re-download all of them.
FROM python:3.11-slim AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YOLO_ENABLE=false

WORKDIR /app

# apt over HTTPS. A network that intercepts plain HTTP (a captive portal, a
# transparent proxy) breaks `apt-get update` with "Clearsigned file isn't
# valid ... does the network require authentication?"; HTTPS goes through.
RUN find /etc/apt -name '*.list' -o -name '*.sources' \
    | xargs -r sed -i 's|http://deb.debian.org|https://deb.debian.org|g'

COPY services/edge-worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10004 edge


# ---------------------------------------------------------------------------
FROM deps AS base

COPY --chown=edge:edge services/edge-worker/app ./app
# tools/ carries the operator entrypoints (snapshot_wall.py serves the live
# wall's frames, run by the grid-snapshots service); app.grid is the shared
# grid client they use.
COPY --chown=edge:edge services/edge-worker/tools ./tools
USER edge

# No long-running server: this is a job. Compose runs it on demand.
CMD ["python", "-m", "app.worker", "--help"]


# ---------------------------------------------------------------------------
FROM deps AS yolo-deps

# OpenCV needs these even in the headless build.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

COPY services/edge-worker/requirements-yolo.txt ./requirements-yolo.txt
# CPU-only torch first, so the default CUDA wheels are not pulled - and
# torchvision from the SAME index in the same step. Left to ultralytics it comes
# from PyPI, built against a different torch, and its compiled ops fail to
# register: every inference dies with "operator torchvision::nms does not exist".
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements-yolo.txt
# ultralytics depends on the desktop opencv-python, which lands beside the
# headless build and wins the import - and it needs X11 libraries (libxcb) this
# slim image does not have, so `import cv2` fails and every capture dies. Keep
# only the pinned headless build, as the anpr stage below does for contrib.
RUN pip uninstall -y opencv-python \
 && pip install --no-cache-dir --force-reinstall --no-deps "opencv-python-headless>=4.10,<5.0"

# Weights are NOT baked in. Mount them at /app/weights, or set
# YOLO_ALLOW_DOWNLOAD=true to let Ultralytics fetch them on first load.
RUN mkdir -p /app/weights && chown -R edge:edge /app
ENV YOLO_ENABLE=true \
    YOLO_WEIGHTS_DIR=/app/weights \
    YOLO_ALLOW_DOWNLOAD=false


FROM yolo-deps AS yolo

COPY --chown=edge:edge services/edge-worker/app ./app
COPY --chown=edge:edge services/edge-worker/tools ./tools
USER edge
CMD ["python", "-m", "app.worker", "--help"]


# ---------------------------------------------------------------------------
FROM yolo-deps AS anpr-deps

COPY services/edge-worker/requirements-anpr.txt ./requirements-anpr.txt
RUN pip install --no-cache-dir -r requirements-anpr.txt

# Weights are NOT baked in - docker-compose.yml mounts services/edge-worker/models.
# Missing weights are not fatal: build_engine() logs one actionable line and
# the worker carries on producing detections without plates.
RUN mkdir -p /app/models && chown -R edge:edge /app
ENV ANPR_ENABLE=true \
    ANPR_MODELS_DIR=/app/models


FROM anpr-deps AS anpr

# Number-plate reading with the consensus engine. ~3.5 GB.
#
#   docker build --target anpr -f services/edge-worker/Dockerfile .
#
# The `anpr/` package beside `app/` is the reader: it tracks each vehicle,
# reads its plate across every frame the vehicle appears in, repairs each
# reading against the Indian plate grammar and votes. It is copied explicitly
# here - a feature that is fully tested and simply absent from the image is a
# failure mode this repository has already had once, and it is silent.
COPY --chown=edge:edge services/edge-worker/app ./app
COPY --chown=edge:edge services/edge-worker/tools ./tools
COPY --chown=edge:edge services/edge-worker/anpr ./anpr
# The engine reads its thresholds, ROI and tracker settings from config/ at
# construction (ANPRPipeline(thresholds=..., roi_cfg=...)), so the YAML has to
# be in the image beside the package, not just on a developer's machine.
COPY --chown=edge:edge services/edge-worker/config ./config

USER edge
CMD ["python", "-m", "app.worker", "--help"]


# ---------------------------------------------------------------------------
# anpr-text: the anpr image plus the whole-crop text reader (anpr/read/awiros.py).
#
#   EDGE_ANPR_TARGET=anpr-text docker compose --profile anpr build edge-worker
#
# config/thresholds.yaml asks for this reader; without PaddlePaddle the engine logs one line and
# falls back to the two CRNNs. Measured on the 36-clip set (docs/anpr-optimisation.md): 16 of 26
# plates with it, 13 without, none wrong either way, and the cross-reader confirm rule can only
# fire when it is loaded. ~1 GB larger, which is why it is a target and not the default.
# Weights (models/awiros) and the PaddleOCR model code (third_party/PaddleOCR) are mounted by
# docker-compose.yml, like every other weight.
# ---------------------------------------------------------------------------
FROM anpr-deps AS anpr-text-deps
RUN pip install --no-cache-dir "paddlepaddle==3.3.1" "safetensors>=0.4"


FROM anpr-text-deps AS anpr-text

COPY --chown=edge:edge services/edge-worker/app ./app
COPY --chown=edge:edge services/edge-worker/tools ./tools
COPY --chown=edge:edge services/edge-worker/anpr ./anpr
COPY --chown=edge:edge services/edge-worker/config ./config
USER edge
CMD ["python", "-m", "app.worker", "--help"]
