#!/usr/bin/env bash
# After both tracks: install the chosen reader and the upscaler beside the
# detector in models/, then compare on real footage at the 10 px floor:
#
#   A. today's pipeline: baseline detector, PaddleOCR, floor 40 px
#   B. new detector + reader, no size floor, plate SR on
#
#   READER=runs/reader/R3_enhanced/best.pt   (or R2_all_crops)
#   SR=runs/sr/S1_hard_first/best.pt
#   DETECTOR=G_uniform_hard_first
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
READER=${READER:-runs/reader/R3_enhanced/best.pt}
SR=${SR:-runs/sr/S1_hard_first/best.pt}
DETECTOR=${DETECTOR:-G_uniform_hard_first}
LOG=reports/final_compare.log
step() { echo "=== $(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

DW=$(ls -d runs/plate/${DETECTOR}_stage_all*/weights/best.pt 2>/dev/null | tail -1)
[ -n "$DW" ] || DW=runs/plate/B_highres_960/weights/best.pt
[ -f "$READER" ] || { step "no reader at $READER"; exit 1; }
[ -f "$SR" ] || { step "no upscaler at $SR"; exit 1; }

step "installing: detector=$DW reader=$READER sr=$SR"
mkdir -p models
cp "$DW" models/plate_detector.pt
cp "$READER" models/plate_reader.pt
cp "$SR" models/plate_sr.pt
[ -f models/ESPCN_x4.pb ] || cp "D:/ANPR/models/ESPCN_x4.pb" models/ESPCN_x4.pb
$PY - "$DW" "$READER" "$SR" <<'EOF' >> "$LOG" 2>&1
import hashlib, json, pathlib, sys
src = dict(zip(("plate_detector.pt", "plate_reader.pt", "plate_sr.pt"), sys.argv[1:4]))
out = {}
for f, s in src.items():
    p = pathlib.Path("models") / f
    out[f] = {"from": s, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
pathlib.Path("models/PROVENANCE.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=1))
EOF

step "real footage no floor: baseline+paddle@40 vs new detector+reader+SR, every plate read (cam06, cam07)"
$PY -u tools/compare_on_footage.py \
  --weights "D:/ANPR/models/plate_detector.pt" "$DW" \
  --labels baseline_paddle_40px "${DETECTOR}_reader_sr_nofloor" --cameras cam06 cam07 --per-camera 30 \
  --engines paddle reader,paddle --min-plate-width 40 0 \
  --out "reports/footage_comparison_final.json" >> "$LOG" 2>&1

step "DONE"
