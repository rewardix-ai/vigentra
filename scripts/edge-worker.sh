#!/usr/bin/env bash
# Run the Vigentra edge worker on this machine.
#
# The worker lives in services/edge-worker and imports its own `app` package,
# so `python -m app.worker` only resolves from inside that directory - and only
# with the project venv, which is where torch, ultralytics and OpenCV are
# installed. Getting either wrong gives you "No module named 'app'" or a
# missing-torch error, so this script settles both and passes everything else
# straight through.
#
#   ./scripts/edge-worker.sh --all-cameras --forever
#   ./scripts/edge-worker.sh --camera VIGENTRA-TRAFFIC-AHM-0001 --max-frames 40
#   ./scripts/edge-worker.sh --camera VIGENTRA-TRAFFIC-AHM-0001 --synthetic
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
weights_dir="$root/weights"

for candidate in "$root/.venv/bin/python" "$root/.venv/Scripts/python.exe"; do
  if [ -x "$candidate" ]; then python="$candidate"; break; fi
done

if [ -z "${python:-}" ]; then
  cat >&2 <<MSG
No project virtualenv under $root/.venv

Create one and install the worker's dependencies:
    python -m venv .venv
    .venv/bin/python -m pip install -r services/edge-worker/requirements.txt

For real YOLO inference, also install the analytics extras (docs/yolo-setup.md):
    .venv/bin/python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    .venv/bin/python -m pip install -r services/edge-worker/requirements-yolo.txt
MSG
  exit 1
fi

if [ "$#" -eq 0 ]; then
  cat >&2 <<'MSG'
Give the worker something to do, for example:
    ./scripts/edge-worker.sh --all-cameras --forever
    ./scripts/edge-worker.sh --camera VIGENTRA-TRAFFIC-AHM-0001 --max-frames 40
MSG
  exit 2
fi

# Reachable on localhost from the host; inside compose it is the service name,
# which is why the default baked into the code differs from this one.
export CENTRAL_API_URL="${CENTRAL_API_URL:-http://localhost:8000}"
export YOLO_WEIGHTS_DIR="$weights_dir"

# Only claim YOLO when the weights are actually here. Defaulting it on and
# failing at load would be a worse first run than starting on the mock.
if [ -z "${YOLO_ENABLE:-}" ]; then
  if [ -f "$weights_dir/yolo11n.pt" ]; then
    export YOLO_ENABLE=true
  else
    export YOLO_ENABLE=false
    echo "No weights at $weights_dir/yolo11n.pt - running the mock detector." >&2
    echo "See docs/yolo-setup.md to install the real model." >&2
  fi
fi

# Resolve any path argument BEFORE changing directory. The worker runs from
# services/edge-worker, so a relative --clip given at the repo root would
# otherwise resolve against the wrong place and fail to open.
args=()
expect_path=0
for arg in "$@"; do
  if [ "$expect_path" = "1" ]; then
    expect_path=0
    case "$arg" in
      /*) args+=("$arg") ;;
      *) if [ -e "$arg" ]; then args+=("$(cd "$(dirname "$arg")" && pwd)/$(basename "$arg")"); else args+=("$arg"); fi ;;
    esac
    continue
  fi
  [ "$arg" = "--clip" ] && expect_path=1
  args+=("$arg")
done

echo "edge-worker -> $CENTRAL_API_URL  (YOLO_ENABLE=$YOLO_ENABLE)"
cd "$root/services/edge-worker"
exec "$python" -m app.worker "${args[@]}"
