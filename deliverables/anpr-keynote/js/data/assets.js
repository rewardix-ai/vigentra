/* Media slots. Change a `src` to swap footage; a slot without a file shows its `need` label on
 * stage instead of an invented stand-in. All files are built from real project footage by
 * tools/build_assets.py and tools/track_evidence.py (assets/ is not committed). Cameras are the
 * grid's own (cam01-cam16) and the Delhi street clip; no London feeds.
 */
window.K = window.K || {};
K.ASSETS = {
  // video
  cam06_1080p: { src: "assets/video/cam06_1080p.mp4", label: "CAM06, Madhuram Bypass Road, 1080p recording", need: "[REAL CAM 06 FOOTAGE REQUIRED]" },
  cam06_noon: { src: "assets/video/cam06_noon.mp4", label: "CAM06 at midday, 854×480 grid stream", need: "[REAL CAM 06 FOOTAGE REQUIRED]" },
  delhi_raw: { src: "assets/video/delhi_raw.mp4", label: "Delhi street, hand-held", need: "[REAL DELHI FOOTAGE REQUIRED]" },
  cam06_vigentra: { src: "assets/video/cam06_vigentra.mp4", label: "CAM06 midday, read by Vigentra", need: "[CAM 06 RESULT VIDEO REQUIRED]" },
  delhi_vigentra: { src: "assets/video/delhi_vigentra.mp4", label: "Delhi, read by Vigentra", need: "[DELHI RESULT VIDEO REQUIRED]" },
  app_demo: { src: "assets/video/app_demo.mp4", label: "Vigentra console, screen recording", need: "[CONSOLE RECORDING REQUIRED]" },
  ...Object.fromEntries(["01", "02", "04", "05", "06", "07", "08", "10", "11", "12", "13", "14", "15", "16"].map((n) => [
    `wall_cam${n}`, { src: `assets/video/wall_cam${n}.mp4`, label: `Grid cam${n}`, need: "[GRID FEED REQUIRED]" },
  ])),
  reel_cam01: { src: "assets/video/reel_cam01.mp4", label: "Grid cam01, Chiman bhai Bridge, 21:00", need: "[GRID FEED REQUIRED]" },
  reel_cam04: { src: "assets/video/reel_cam04.mp4", label: "Grid cam04, junction, 21:00", need: "[GRID FEED REQUIRED]" },
  reel_cam07: { src: "assets/video/reel_cam07.mp4", label: "Grid cam07, Bhavani Char Rasta, night", need: "[GRID FEED REQUIRED]" },
  reel_cam15: { src: "assets/video/reel_cam15.mp4", label: "Grid cam15, Suvidhapark, 21:00", need: "[GRID FEED REQUIRED]" },

  // images
  best_frame: { src: "assets/img/cam06_best_frame.jpg", label: "CAM06 frame 1299", need: "[REAL CAM 06 FRAME REQUIRED]" },
  journey_best: { src: "assets/img/journey_best.png", label: "Best crop, GJ23H1546", need: "[REAL PLATE CROP REQUIRED]" },
  journey_fused: { src: "assets/img/journey_fused.png", label: "Frames fused", need: "[FUSED CROP REQUIRED]" },
  journey_enhanced: { src: "assets/img/journey_enhanced.png", label: "Enhanced", need: "[ENHANCED CROP REQUIRED]" },
  app_trace: { src: "assets/img/app_trace.jpg", label: "Vigentra console, trace a vehicle", need: "[CONSOLE SCREENSHOT REQUIRED]" },
};
