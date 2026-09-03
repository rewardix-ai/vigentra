# Vigentra edge analytics worker.
#
# Three build targets:
#
#   base  (default) - mock detector only. ~120 MB. No torch, no CUDA.
#                     Runs the full ingestion path for demos and CI.
#   yolo            - real Ultralytics inference. ~2.5 GB.
#                     Build with:  docker build --target yolo ...
#   anpr            - yolo plus the consensus number-plate reader. ~3.5 GB.
#                     Build with:  docker build --target anpr ...
#
# The demo Compose stack uses `base` on purpose: pulling 2.5 GB of CUDA
# userspace to show a working pipeline is a poor trade. Switch to `yolo` on a
# machine that will actually run a model.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YOLO_ENABLE=false

WORKDIR /app

COPY services/edge-worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/edge-worker/app ./app

RUN useradd --create-home --uid 10004 edge && chown -R edge:edge /app
USER edge

# No long-running server: this is a job. Compose runs it on demand.
CMD ["python", "-m", "app.worker", "--help"]


# ---------------------------------------------------------------------------
FROM base AS yolo

USER root

# OpenCV needs these even in the headless build.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

COPY services/edge-worker/requirements-yolo.txt ./requirements-yolo.txt
# CPU-only torch first, so the default CUDA wheels are not pulled.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements-yolo.txt

# Weights are NOT baked in. Mount them at /app/weights, or set
# YOLO_ALLOW_DOWNLOAD=true to let Ultralytics fetch them on first load.
RUN mkdir -p /app/weights && chown -R edge:edge /app
ENV YOLO_ENABLE=true \
    YOLO_WEIGHTS_DIR=/app/weights \
    YOLO_ALLOW_DOWNLOAD=false

USER edge
CMD ["python", "-m", "app.worker", "--help"]


# ---------------------------------------------------------------------------
FROM yolo AS anpr

# Number-plate reading with the consensus engine. ~3.5 GB.
#
#   docker build --target anpr -f services/edge-worker/Dockerfile .
#
# The `anpr/` package beside `app/` is the reader: it tracks each vehicle,
# reads its plate across every frame the vehicle appears in, repairs each
# reading against the Indian plate grammar and votes. It is copied explicitly
# here - a feature that is fully tested and simply absent from the image is a
# failure mode this repository has already had once, and it is silent.
#
# The base is python:3.11-slim, which PaddlePaddle publishes wheels for. Do NOT
# move it to 3.13/3.14: there are no Paddle wheels there, the OCR install
# fails, and the worker silently degrades to the single-frame reader.

USER root

COPY services/edge-worker/requirements-anpr.txt ./requirements-anpr.txt
# opencv-contrib replaces the headless build pulled in by requirements-yolo:
# the restoration stack needs dnn_superres, which only ships in contrib.
RUN pip uninstall -y opencv-python opencv-python-headless opencv-contrib-python || true \
 && pip install --no-cache-dir -r requirements-anpr.txt

COPY services/edge-worker/anpr ./anpr

# Weights are NOT baked in - plate_detector.pt and ESPCN_x4.pb are mounted.
# Missing weights are not fatal: build_engine() logs one actionable line and
# the worker carries on producing detections without plates.
RUN mkdir -p /app/models && chown -R edge:edge /app
ENV ANPR_ENABLE=true \
    ANPR_MODELS_DIR=/app/models

USER edge
CMD ["python", "-m", "app.worker", "--help"]
