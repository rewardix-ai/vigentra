#!/usr/bin/env bash
# When R7 (width-balanced continuation) is done: evaluate it on the real
# crops cut to the detector's box, raw and through S3, against PaddleOCR on
# the same crops. The reader joins the OCR ensemble only if it beats PaddleOCR
# there; otherwise the verdict is recorded and nothing changes.
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
DATASET=dataset/v4_uniform
LOG=reports/after_r7.log
step() { echo "=== $(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

step "waiting for R7 and the estate run (memory)"
until grep -q "R7_EXIT=" reports/r7.log 2>/dev/null && grep -q "ESTATE_EXIT=" reports/footage_estate.log 2>/dev/null; do sleep 30; done
[ -f runs/reader/R7_balanced/best.pt ] || { step "no R7 weights"; exit 1; }

step "R7: tight real crops, raw"
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --eval runs/reader/R7_balanced/best.pt \
  --tight runs/plate/B_highres_960/weights/best.pt --compare-paddle --tag R7_balanced_tight >> "$LOG" 2>&1
step "R7: tight real crops, through S3"
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --eval runs/reader/R7_balanced/best.pt \
  --tight runs/plate/B_highres_960/weights/best.pt --sr runs/sr/S3_wide_cont/best.pt --compare-paddle --tag R7_balanced_tight_sr >> "$LOG" 2>&1

VERDICT=$($PY - <<'EOF'
import json, pathlib, sys
def load(tag):
    p = pathlib.Path(f"reports/reader/{tag}.json")
    return json.load(open(p)) if p.exists() else {}
best_reader, best_paddle = 0.0, 0.0
for tag in ("R7_balanced_tight", "R7_balanced_tight_sr"):
    d = load(tag)
    best_reader = max(best_reader, float((d.get("real") or {}).get("overall", {}).get("exact_repaired") or 0))
    best_paddle = max(best_paddle, float((d.get("real_paddle") or {}).get("overall", {}).get("exact_repaired") or 0))
print(f"reader {best_reader:.3f} vs paddle {best_paddle:.3f}", file=sys.stderr)
print("reader" if best_reader > best_paddle else "paddle")
EOF
)
step "verdict: $VERDICT wins on tight real crops"
if [ "$VERDICT" = "reader" ]; then
  cp runs/reader/R7_balanced/best.pt models/plate_reader.pt
  $PY - <<'EOF'
from pathlib import Path
p = Path("config.yaml"); s = p.read_text(encoding="utf-8")
s = s.replace("  engines: [paddle]", "  engines: [reader, paddle]")
p.write_text(s, encoding="utf-8")
print("config: engines -> [reader, paddle]")
EOF
fi
step "DONE"
