#!/usr/bin/env bash
# Unattended: train the reader and the detector on the uniform dataset, hard
# tiers first, evaluate both, install the winners into models/, and compare
# the whole pipeline (detect + read) against today's on real footage.
#
#   READER=R1_hard_first  DETECTOR=G_uniform_hard_first
#   READER_EPOCHS="--epochs-hard 6 --epochs-all 10"
#   DET_EPOCHS=4          epochs per detector stage (stage_hard, then stage_all)
#
# Log: reports/uniform_training.log (last line "=== DONE" on success).
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
READER=${READER:-R1_hard_first}
DETECTOR=${DETECTOR:-G_uniform_hard_first}
READER_EPOCHS=${READER_EPOCHS:---epochs-hard 6 --epochs-all 10}
DET_EPOCHS=${DET_EPOCHS:-4}
DATASET=dataset/v4_uniform
LOG=reports/uniform_training.log
step() { echo "=== $(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

[ -f "$DATASET/manifest.json" ] || { step "no $DATASET/manifest.json - build the dataset first"; exit 1; }

# ---- reader ---------------------------------------------------------------
step "reader: training $READER on $DATASET ($READER_EPOCHS)"
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --name "$READER" $READER_EPOCHS \
  --batch 128 --device cuda >> "reports/training_${READER}.log" 2>&1
echo "READER_TRAIN_EXIT=$?" | tee -a "$LOG"
RW="runs/reader/$READER/best.pt"
[ -f "$RW" ] || { step "no reader weights - training failed"; exit 1; }

step "reader: evaluating on synthetic val and the real crops, against PaddleOCR"
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --eval "$RW" --compare-paddle \
  --tag "$READER" >> "$LOG" 2>&1

# ---- detector -------------------------------------------------------------
step "detector: $DETECTOR from B_highres_960, stage_hard then stage_all, $DET_EPOCHS epochs each, lr0 0.002"
$PY -u tools/train_plate_detector.py --curriculum "$DATASET" --stages stage_hard,stage_all \
  --base-model runs/plate/B_highres_960/weights/best.pt --name "$DETECTOR" \
  --epochs "$DET_EPOCHS" --lr0 0.002 --warmup-epochs 1 --device 0 \
  >> "reports/training_${DETECTOR}.log" 2>&1
echo "DETECTOR_TRAIN_EXIT=$?" | tee -a "$LOG"
DW=$(ls -d runs/plate/${DETECTOR}_stage_all*/weights/best.pt 2>/dev/null | tail -1)
[ -n "$DW" ] || { step "no detector weights - training failed"; exit 1; }

step "detector: per size band at the AP floor and at conf 0.25"
$PY -u tools/eval_by_size.py --weights "$DW" --tag "$DETECTOR" >> "$LOG" 2>&1
$PY -u tools/eval_by_size.py --weights "$DW" --tag "${DETECTOR}_conf025" --conf 0.25 >> "$LOG" 2>&1
[ -f reports/eval_by_size/B_highres_960_conf025_test_imgsz960.json ] || \
  $PY -u tools/eval_by_size.py --weights runs/plate/B_highres_960/weights/best.pt --tag B_highres_960_conf025 --conf 0.25 >> "$LOG" 2>&1
[ -f reports/eval_by_size/baseline_conf025_test_imgsz960.json ] || \
  $PY -u tools/eval_by_size.py --weights "D:/ANPR/models/plate_detector.pt" --tag baseline_conf025 --conf 0.25 >> "$LOG" 2>&1
step "detector: error sheets"
$PY -u tools/error_analysis.py --weights "$DW" --tag "$DETECTOR" --conf 0.25 --limit 24 >> "$LOG" 2>&1

# ---- install + whole-pipeline comparison ------------------------------------
step "installing weights into models/ (D:/ANPR/models untouched)"
mkdir -p models
cp "$DW" models/plate_detector.pt
cp "$RW" models/plate_reader.pt
[ -f models/ESPCN_x4.pb ] || cp "D:/ANPR/models/ESPCN_x4.pb" models/ESPCN_x4.pb
$PY - <<'EOF' >> "$LOG" 2>&1
import hashlib, json, pathlib
out = {}
for f in ("plate_detector.pt", "plate_reader.pt"):
    p = pathlib.Path("models") / f
    out[f] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
pathlib.Path("models/PROVENANCE.json").write_text(json.dumps(out, indent=2))
print("models/PROVENANCE.json", out)
EOF

step "real footage: today's pipeline (baseline detector, paddle) vs new (uniform detector + reader), cam06 cam07"
$PY -u tools/compare_on_footage.py \
  --weights "D:/ANPR/models/plate_detector.pt" "$DW" \
  --labels baseline_paddle "${DETECTOR}_reader" --cameras cam06 cam07 --per-camera 30 \
  --engines paddle reader,paddle --min-plate-width 40 24 \
  --out "reports/footage_comparison_${DETECTOR}.json" >> "$LOG" 2>&1

step "DONE"
