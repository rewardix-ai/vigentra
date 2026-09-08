#!/usr/bin/env bash
# Iterate the upscaler against the truth pairs, unattended, and stop there.
#   wait for the multi-frame replay (it rewrites sr/ and mfsr/ pairs)
#   S2: single-frame, wider and deeper (64 features, 8 blocks), 24 epochs
#   S3: S2 continued for 24 more epochs at a lower rate
#   M1: multi-frame (5 frames), 12 epochs
#   evaluate every one on the same validation pairs; sheets for each
# Log: reports/sr_iterations.log (last line "=== DONE").
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
DATASET=dataset/v4_uniform
LOG=reports/sr_iterations.log
step() { echo "=== $(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

step "waiting for the multi-frame replay"
until grep -q "MFSR_REPLAY_EXIT=" reports/build_v4_uniform_mfsr.log 2>/dev/null; do sleep 30; done
grep -q "MFSR_REPLAY_EXIT=0" reports/build_v4_uniform_mfsr.log || { step "replay failed"; exit 1; }
step "pairs: $(ls $DATASET/sr/train | grep -c _lr.png) single-frame, $(ls $DATASET/mfsr/train | grep -c _hr.jpg) multi-frame"

step "S1 (baseline iteration): re-evaluating on the regenerated pairs"
$PY -u tools/train_plate_sr.py --dataset "$DATASET" --eval runs/sr/S1_hard_first/best.pt --device cpu >> "$LOG" 2>&1

step "S2: single-frame, 64 features, 8 blocks, 24 epochs"
$PY -u tools/train_plate_sr.py --dataset "$DATASET" --name S2_wide --features 64 --blocks 8 --epochs 24 --batch 64 --lr 1e-3 \
  > reports/training_S2_wide.log 2>&1
echo "S2_EXIT=$?" | tee -a "$LOG"
$PY -u tools/train_plate_sr.py --dataset "$DATASET" --eval runs/sr/S2_wide/best.pt --sheet reports/sr_sheet_S2_wide.jpg --device cpu >> "$LOG" 2>&1

step "S3: S2 continued, 24 epochs at 3e-4"
$PY -u tools/train_plate_sr.py --dataset "$DATASET" --name S3_wide_cont --features 64 --blocks 8 --epochs 24 --batch 64 --lr 3e-4 \
  --init runs/sr/S2_wide/best.pt > reports/training_S3_wide_cont.log 2>&1
echo "S3_EXIT=$?" | tee -a "$LOG"
$PY -u tools/train_plate_sr.py --dataset "$DATASET" --eval runs/sr/S3_wide_cont/best.pt --sheet reports/sr_sheet_S3_wide_cont.jpg --device cpu >> "$LOG" 2>&1

step "M1: multi-frame, 5 frames, 12 epochs"
$PY -u tools/train_plate_sr.py --dataset "$DATASET" --name M1_hard_first --frames 5 --epochs 12 --batch 32 \
  > reports/training_M1_hard_first.log 2>&1
echo "M1_EXIT=$?" | tee -a "$LOG"
BEST_SINGLE=runs/sr/S3_wide_cont/best.pt
[ -f "$BEST_SINGLE" ] || BEST_SINGLE=runs/sr/S1_hard_first/best.pt
$PY -u tools/train_plate_sr.py --dataset "$DATASET" --frames 5 --eval runs/mfsr/M1_hard_first/best.pt \
  --single "$BEST_SINGLE" --sheet reports/mfsr_sheet_M1.jpg --count 24 --device cpu >> "$LOG" 2>&1

step "DONE"
