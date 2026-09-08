#!/usr/bin/env bash
# Multi-frame super-resolution, unattended:
#   1. wait for the multi-frame replay (5 frames per plate + sharp target)
#   2. wait for track 2 (so the card is free and reader R2 exists to score with)
#   3. train M1 on the multi-frame pairs, hard pairs 3x
#   4. evaluate: PSNR and READER EXACT MATCH per LR width for raw frame 0,
#      single-frame S1, and multi-frame M1 - on the same plates
#   5. sheet: frame 0 | bicubic | single | multi | truth
#
# Log: reports/mfsr.log (last line "=== DONE" on success).
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
DATASET=dataset/v4_uniform
M1=${M1:-M1_hard_first}
FRAMES=${FRAMES:-5}
EPOCHS=${EPOCHS:-10}
SINGLE=runs/sr/S1_hard_first/best.pt
LOG=reports/mfsr.log
step() { echo "=== $(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

step "waiting for the multi-frame replay"
until grep -q "MFSR_REPLAY_EXIT=" reports/build_v4_uniform_mfsr.log 2>/dev/null; do sleep 30; done
grep -q "MFSR_REPLAY_EXIT=0" reports/build_v4_uniform_mfsr.log || { step "multi-frame replay failed"; exit 1; }
step "replay done: $(ls "$DATASET/mfsr/train" | grep -c _hr.jpg) multi-frame pairs"

step "waiting for track 2 (readers) before using the card"
until grep -q "^=== .* DONE" reports/reader_sr.log 2>/dev/null; do sleep 30; done
READER=runs/reader/R3_enhanced/best.pt
[ -f "$READER" ] || READER=runs/reader/R2_all_crops/best.pt

step "M1: training on $FRAMES-frame pairs, $EPOCHS epochs"
$PY -u tools/train_plate_sr.py --dataset "$DATASET" --name "$M1" --frames "$FRAMES" --epochs "$EPOCHS" --batch 32 \
  >> "reports/training_${M1}.log" 2>&1
echo "M1_TRAIN_EXIT=$?" | tee -a "$LOG"
MW="runs/mfsr/$M1/best.pt"
[ -f "$MW" ] || { step "no multi-frame weights"; exit 1; }

step "M1: evaluating against single-frame S1 and raw, scored by reader $READER"
$PY -u tools/train_plate_sr.py --dataset "$DATASET" --frames "$FRAMES" --eval "$MW" --single "$SINGLE" \
  --reader "$READER" --sheet "reports/mfsr_sheet_${M1}.jpg" --count 24 --device cpu >> "$LOG" 2>&1

step "DONE"
