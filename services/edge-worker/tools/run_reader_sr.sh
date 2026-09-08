#!/usr/bin/env bash
# Second track, unattended, runs beside the detector training:
#   1. wait for the --reader-only replay (every crop labelled, SR pairs written)
#   2. train the plate upscaler (S1) on the SR pairs, evaluate it, render a sheet
#   3. train the reader on the complete crops, raw (R2)
#   4. train the reader on the SAME crops after the upscaler (R3):
#      detection -> enhancement -> OCR, end to end
#   5. evaluate R2 raw, R2+SR, R3+SR and PaddleOCR (raw and +SR) on the real crops
#
# Log: reports/reader_sr.log (last line "=== DONE" on success).
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
DATASET=dataset/v4_uniform
SR=${SR:-S1_hard_first}
R2=${R2:-R2_all_crops}
R3=${R3:-R3_enhanced}
READER_EPOCHS=${READER_EPOCHS:---epochs-hard 4 --epochs-all 10}
LOG=reports/reader_sr.log
step() { echo "=== $(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

step "waiting for the reader-only replay"
until grep -q "REPLAY_EXIT=" reports/build_v4_uniform_replay.log 2>/dev/null; do sleep 30; done
grep -q "REPLAY_EXIT=0" reports/build_v4_uniform_replay.log || { step "replay failed"; exit 1; }
step "replay done: $(grep -c '' "$DATASET/plates.csv") reader rows, $(ls "$DATASET/sr/train" | grep -c _lr.png) SR pairs"

step "SR: training $SR"
$PY -u tools/train_plate_sr.py --dataset "$DATASET" --name "$SR" --epochs 12 --batch 64 \
  >> "reports/training_${SR}.log" 2>&1
echo "SR_TRAIN_EXIT=$?" | tee -a "$LOG"
SW="runs/sr/$SR/best.pt"
[ -f "$SW" ] || { step "no SR weights"; exit 1; }
step "SR: evaluating + sheet"
$PY -u tools/train_plate_sr.py --dataset "$DATASET" --eval "$SW" --sheet "reports/sr_sheet_${SR}.jpg" --count 24 >> "$LOG" 2>&1

step "reader R2: complete crops, raw ($READER_EPOCHS)"
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --name "$R2" $READER_EPOCHS --batch 128 --device cuda \
  >> "reports/training_${R2}.log" 2>&1
echo "R2_TRAIN_EXIT=$?" | tee -a "$LOG"

step "reader R3: complete crops through the upscaler, initialised from R2"
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --name "$R3" --epochs-hard 2 --epochs-all 6 \
  --batch 128 --device cuda --sr "$SW" --init "runs/reader/$R2/best.pt" \
  >> "reports/training_${R3}.log" 2>&1
echo "R3_TRAIN_EXIT=$?" | tee -a "$LOG"

step "evaluating on the real crops: R2 raw (+Paddle raw)"
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --eval "runs/reader/$R2/best.pt" --compare-paddle --tag "${R2}_raw" >> "$LOG" 2>&1
step "evaluating: R2 with SR (+Paddle with SR)"
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --eval "runs/reader/$R2/best.pt" --compare-paddle --sr "$SW" --tag "${R2}_sr" >> "$LOG" 2>&1
step "evaluating: R3 with SR"
[ -f "runs/reader/$R3/best.pt" ] && \
  $PY -u tools/train_plate_reader.py --dataset "$DATASET" --eval "runs/reader/$R3/best.pt" --sr "$SW" --tag "${R3}_sr" >> "$LOG" 2>&1

step "DONE"
