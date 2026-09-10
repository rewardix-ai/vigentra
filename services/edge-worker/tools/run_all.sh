#!/usr/bin/env bash
# The whole run, from the (already built) uniform dataset to the report:
#   track 2 (tools/run_reader_sr.sh, started separately): upscaler S1, readers
#     R2 (raw) and R3 (enhanced), real-crop evaluation - waited for here;
#   detector G: fine-tune B on stage_hard then stage_all; both best.pt and
#     last.pt are measured per size band and the one with the higher recall
#     on EXTREMELY_TINY + VERY_SMALL plates is kept;
#   the reader with the higher exact-match on the real crops is chosen;
#   install into models/ and compare on real footage with no size floor.
#
# Log: reports/run_all.log (last line "=== DONE" on success).
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
DATASET=dataset/v4_uniform
DETECTOR=${DETECTOR:-G_uniform_hard_first}
DET_EPOCHS=${DET_EPOCHS:-4}
LOG=reports/run_all.log
step() { echo "=== $(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

# ---- detector (runs beside track 2 once the replay has finished) ------------
step "waiting for the reader-only replay before the detector starts"
until grep -q "REPLAY_EXIT=" reports/build_v4_uniform_replay.log 2>/dev/null; do sleep 30; done

# Resume: a finished stage is not repeated. The hard stage leaves
# runs/plate/<name>_stage_hard/weights/best.pt; the all-tier stage starts
# from it. A stage that already has weights is skipped.
HARD_W="runs/plate/${DETECTOR}_stage_hard/weights/best.pt"
ALL_W=$(ls -d runs/plate/${DETECTOR}_stage_all*/weights/best.pt 2>/dev/null | tail -1)
if [ -n "$ALL_W" ]; then
  step "detector: $ALL_W exists - training skipped"
elif [ -f "$HARD_W" ]; then
  step "detector: resuming - stage_all from $HARD_W, $DET_EPOCHS epochs, lr0 0.002"
  $PY -u tools/train_plate_detector.py --curriculum "$DATASET" --stages stage_all \
    --base-model "$HARD_W" --name "$DETECTOR" \
    --epochs "$DET_EPOCHS" --lr0 0.002 --warmup-epochs 1 --device 0 \
    >> "reports/training_${DETECTOR}.log" 2>&1
  echo "DETECTOR_TRAIN_EXIT=$?" | tee -a "$LOG"
else
  step "detector: $DETECTOR from B_highres_960, stage_hard then stage_all, $DET_EPOCHS epochs each, lr0 0.002"
  $PY -u tools/train_plate_detector.py --curriculum "$DATASET" --stages stage_hard,stage_all \
    --base-model runs/plate/B_highres_960/weights/best.pt --name "$DETECTOR" \
    --epochs "$DET_EPOCHS" --lr0 0.002 --warmup-epochs 1 --device 0 \
    >> "reports/training_${DETECTOR}.log" 2>&1
  echo "DETECTOR_TRAIN_EXIT=$?" | tee -a "$LOG"
fi
RUN=$(ls -d runs/plate/${DETECTOR}_stage_all* 2>/dev/null | tail -1)
[ -n "$RUN" ] || { step "no detector run dir - training failed"; exit 1; }

step "detector: per size band, best.pt and last.pt"
$PY -u tools/eval_by_size.py --weights "$RUN/weights/best.pt" --tag "${DETECTOR}_best" >> "$LOG" 2>&1
$PY -u tools/eval_by_size.py --weights "$RUN/weights/last.pt" --tag "${DETECTOR}_last" >> "$LOG" 2>&1
DW=$($PY - "$DETECTOR" "$RUN" <<'EOF'
import json, sys, pathlib
tag, run = sys.argv[1], sys.argv[2]
def tiny(t):
    d = json.load(open(f"reports/eval_by_size/{t}_test_imgsz960.json"))
    bands = d.get("by_size") or d.get("bands") or d
    r = 0.0
    for b in ("EXTREMELY_TINY", "VERY_SMALL"):
        e = bands.get(b, {}) if isinstance(bands, dict) else {}
        r += float(e.get("recall") or 0.0)
    return r
best, last = tiny(f"{tag}_best"), tiny(f"{tag}_last")
print(f"{run}/weights/{'last' if last > best else 'best'}.pt")
EOF
)
step "detector kept: $DW (chosen on EXTREMELY_TINY + VERY_SMALL recall)"
$PY -u tools/eval_by_size.py --weights "$DW" --tag "${DETECTOR}_conf025" --conf 0.25 >> "$LOG" 2>&1
[ -f reports/eval_by_size/B_highres_960_conf025_test_imgsz960.json ] || \
  $PY -u tools/eval_by_size.py --weights runs/plate/B_highres_960/weights/best.pt --tag B_highres_960_conf025 --conf 0.25 >> "$LOG" 2>&1
[ -f reports/eval_by_size/baseline_conf025_test_imgsz960.json ] || \
  $PY -u tools/eval_by_size.py --weights "D:/ANPR/models/plate_detector.pt" --tag baseline_conf025 --conf 0.25 >> "$LOG" 2>&1
step "detector: error sheets"
$PY -u tools/error_analysis.py --weights "$DW" --tag "$DETECTOR" --conf 0.25 --limit 24 >> "$LOG" 2>&1

# ---- wait for track 2, choose the reader ------------------------------------
step "waiting for track 2 (upscaler + readers)"
until grep -q "^=== .* DONE" reports/reader_sr.log 2>/dev/null; do sleep 30; done
READER=$($PY - <<'EOF'
import json, pathlib
def exact(tag):
    p = pathlib.Path(f"reports/reader/{tag}.json")
    if not p.exists():
        return -1.0
    d = json.load(open(p))
    return float((d.get("real") or {}).get("overall", {}).get("exact_repaired") or 0.0)
cands = {"runs/reader/R3_enhanced/best.pt": exact("R3_enhanced_sr"),
         "runs/reader/R2_all_crops/best.pt": max(exact("R2_all_crops_sr"), exact("R2_all_crops_raw"))}
best = max(cands, key=cands.get)
print(best if pathlib.Path(best).exists() else "runs/reader/R2_all_crops/best.pt")
EOF
)
step "reader kept: $READER (higher grammar-repaired exact match on the real crops)"

# ---- install + real footage ---------------------------------------------------
READER="$READER" SR=runs/sr/S1_hard_first/best.pt DETECTOR="$DETECTOR" bash tools/run_final_compare.sh
step "DONE"
