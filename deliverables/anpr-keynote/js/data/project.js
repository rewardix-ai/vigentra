/* Every number the keynote shows, with where it was measured. Nothing here is projected or
 * invented; estimates are marked as estimates. Paths are relative to the vigentra repository
 * (docs/, services/edge-worker/ = EW) or the research repository (ANPR/).
 */
window.K = window.K || {};
K.DATA = {
  sandbox: {
    cameras: 30, hoursPerCamera: 12, gujaratCameras: 80000,
    src: "sentinel.gujarat.gov.in: '30+ cameras, 12 hours of footage per camera'; '80,000+ cameras'",
  },

  // Plate width bands and the test set (EW/reports/eval_by_size/*.json; dataset v2 manifest)
  sizeBands: {
    names: ["Large", "Medium", "Small", "Very small", "Extremely tiny"],
    widths: ["≥ 43 px", "22–43 px", "10–22 px", "8–10 px", "< 8 px"],
    demoPx: [48, 30, 16, 9, 6],
    counts: [11, 35, 63, 28, 21],
    total: 158,
    src: "EW/reports/eval_by_size (test set: 223 images, 158 plate boxes); bands by plate width in pixels",
  },

  // Plate-detector evaluation by size. Research detectors only: the deployed plate_det_mix_n.pt
  // has not been evaluated by size. Labels are model-adjudicated; IoU >= 0.30.
  eval: {
    shown: "baseline_960",
    runs: {
      baseline_960: { label: "Baseline detector (YOLO11m) at 960 px", recall: [1.0, 0.9429, 0.8095, 0.6429, 0.3333], ap: [0.9266, 0.5549, 0.282, 0.0076, 0.0014], overall: { recall: 0.7595, ap: 0.376 } },
      baseline_640: { label: "Baseline detector (YOLO11m) at 640 px", recall: [1.0, 1.0, 0.8413, 0.6786, 0.2381], ap: [0.601, 0.7608, 0.4157, 0.0268, 0.007], overall: { recall: 0.7785, ap: 0.5014 } },
      B_highres_960: { label: "Experiment B, high resolution (YOLO11s)", recall: [1.0, 0.9714, 0.873, 0.6786, 0.7619], ap: [0.4981, 0.7494, 0.4909, 0.2649, 0.045], overall: { recall: 0.8544, ap: 0.5955 } },
      D_tiny_oversample: { label: "Experiment D, tiny-plate oversampling", recall: [1.0, 0.9714, 0.8889, 0.7857, 0.6667], ap: [0.3777, 0.6335, 0.3516, 0.2848, 0.0577], overall: { recall: 0.8671, ap: 0.5204 } },
    },
    caveat: "Research detectors on dataset v2's test set; labels model-adjudicated, not yet human-verified; the deployed detector (plate_det_mix_n.pt) was not evaluated by size.",
    src: "EW/reports/eval_by_size/*.json, EW/tools/eval_by_size.py",
  },

  pipeline: {
    vehicle: { model: "YOLO11s (yolo11s.pt)", classes: "car, motorcycle, bus, truck", conf: 0.15, note: "frames detected at native size, never upscaled (cap 1920 px)" },
    plate: { model: "YOLO11n (plate_det_mix_n.pt), fine-tuned on CCPD + vehicle-rear plates", imgsz: 640, conf: 0.2, minVehiclePx: 96, note: "runs only inside each vehicle box; one plate per vehicle" },
    tracker: { name: "ByteTrack (config/bytetrack.yaml)", high: 0.3, low: 0.05, newTrack: 0.2, buffer: 45, match: 0.8, closeAfter: 45 },
    crops: "best 12 of up to 64 crops per vehicle",
    gate: { width: 22, height: 8, sharpness: 8.0, contrast: 25.0, readFloor: 24 },
    enhancement: ["Rectify (straighten)", "Register (align frames)", "Fuse (combine frames)", "Glare suppression", "Denoise", "Deblur (only when blurred)", "Deskew"],
    sr: "Learned super-resolution is off: no model is shipped, so it is not used for votes",
    readers: ["CRNN (primary)", "CRNN v6 (fallback)", "PP-OCRv5 text reader, weight 2.0"],
    rover: "ROVER letter-by-letter fusion, temperature 0.3",
    confirm: "fused confidence ≥ 0.75 · ≥ 3 frames · best crop ≥ 40 px · ≥ 6 hypotheses · each character's vote ≥ 0.5 · ≥ 4 crops with ≥ 40% vote share · runner-up < half the winner · or cross-reader agreement (2 crops from each reader type, 6 in all)",
    glyph: "≥ 3 glyph-shaped marks across the 8 best crops",
    sampling: "burst sampler: 24-frame bursts when a vehicle large enough for a ≥ 45 px plate appears; default stride 20",
    capture: "RTSP over TCP; timing from presentation timestamps (PTS); reconnect 2 s → 30 s backoff",
    viewing: "HLS in the browser (hls.js)",
    src: "EW/config/thresholds.yaml, EW/config/bytetrack.yaml, EW/anpr/*, EW/app/grid.py",
  },

  grammar: {
    formats: ["Standard: GJ 23 H 1546", "Delhi short series", "Bharat series (BH)", "Diplomatic", "Temporary"],
    stateCodes: 40, gujaratRto: "01–39",
    src: "EW/anpr/plate_grammar.py:74-99, EW/config/india_codes.yaml",
  },

  dataset: {
    images: 933, negatives: 336, boxes: 634, split: "386 train · 90 validation · 158 test", medianWidth: 14.65,
    hard: [["Unreadable", 624], ["Low contrast", 275], ["Tiny", 238], ["Low confidence", 204], ["Low light", 78], ["Overexposed", 21], ["Blur", 17], ["Glare", 5]],
    src: "dataset v2 manifest (ANPR research repo); hard-case tags overlap",
  },

  training: { runs: 5, model: "YOLO11s", best: { run: "A", map50: 0.6495, map5095: 0.4455, precision: 0.8765, recall: 0.5556 }, deployed: false, src: "EW/reports/training_experiments.json" },

  baseline: { read: 7, legible: 21, wrong: 0, src: "docs/anpr-baseline.md:98" },
  sameClips: { clips: 36, before: 9, after: 16, of: 26, candidates: [22279, 3448], ocrImages: [36727, 14915], seconds: [5784, 3341], src: "docs/anpr-optimisation.md:16-23" },
  improvements: [
    ["Decision rules ported from research", "Delhi 7 → 9 plates; junk candidates halved; 3.7× faster"],
    ["Per-camera settings", "Delhi camera prefers DL plates"],
    ["Detect at native resolution", "+330 tracks; 2.4× faster"],
    ["24 px reading floor, 15 s same-plate merge", "one car stops becoming several sightings (Delhi had 17 for 11 vehicles)"],
    ["Dense pass where plates are legible", "Delhi: 2 → 4 → 11 plates as frames increased"],
    ["Cross-reader confirmation", "CAM06 noon 14 → 15"],
  ],
  final: {
    read: 36, legible: 48, wrong: 0, clips: 38, cam06Noon: [15, 16], delhi: [13, 20], vehicles: [141, 84],
    day: { plates: 59, minutes: 26, wrong: 0 },
    src: "services/edge-worker/reports/anpr_benchmark/final2/REPORT.md; docs/anpr-cam06-day.md; deliverables/samples/cam06_noon_output_report.md",
  },

  failures: {
    unresolved: 1760,
    causes: [
      ["No plate ever detected", 1576, "camera"],
      ["Readers disagree", 103, "reading"],
      ["Only one kind of reader agrees", 29, "reading"],
      ["Low confidence", 15, "reading"],
      ["Too few agreeing frames", 11, "ambiguous"],
      ["No glyph evidence", 10, "quality"],
      ["Below the sharpness gate", 5, "quality"],
      ["Other (overlay, static text, vote share, width)", 11, "other"],
    ],
    missedLegible: 12, missedDelhi: 7, missedDelhiSecondReader: 6,
    src: "services/edge-worker/reports/anpr_benchmark/final2/FAILURES.md",
  },

  speed: {
    machine: "Apple M1, 16 GB, GPU via MPS",
    rows: [
      { res: "1080p", ms: 173, rt: 0.921 },
      { res: "720p", ms: 96, rt: 0.999 },
      { res: "854 × 480", ms: 58, rt: 1.309 },
    ],
    gpuMemMB: [1158, 1260], perReaderFps: "5–10", ingest: 164,
    estimates: { camerasPerT4: 15, nodesFor80k: 3000 },
    src: "docs/anpr-optimisation.md:167-183; EW/reports/anpr_benchmark/rt_paced; docs/scalability.md:190-230 (estimates)",
  },
};
