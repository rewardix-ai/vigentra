/* Media slots. Change a `src` to swap footage; a slot without a file shows its `need` label on
 * stage instead of an invented stand-in. All files are built from real project footage by
 * tools/build_assets.py and tools/track_evidence.py (assets/ is not committed).
 */
window.K = window.K || {};
K.ASSETS = {
  // video
  cam06_1080p: { src: "assets/video/cam06_1080p.mp4", label: "CAM06, Madhuram Bypass Road, 1080p recording", need: "[REAL CAM 06 FOOTAGE REQUIRED]" },
  cam06_noon: { src: "assets/video/cam06_noon.mp4", label: "CAM06 at noon, 854×480 grid stream", need: "[REAL CAM 06 FOOTAGE REQUIRED]" },
  cam06_night: { src: "assets/video/cam06_night.mp4", label: "CAM06 at night", need: "[REAL NIGHT FOOTAGE REQUIRED]" },
  delhi_raw: { src: "assets/video/delhi_raw.mp4", label: "Delhi street, hand-held", need: "[REAL DELHI FOOTAGE REQUIRED]" },
  cam06_vigentra: { src: "assets/video/cam06_vigentra.mp4", label: "CAM06 noon, read by Vigentra (15 of 16, none wrong)", need: "[CAM 06 RESULT VIDEO REQUIRED]" },
  delhi_vigentra: { src: "assets/video/delhi_vigentra.mp4", label: "Delhi, read by Vigentra (13 of 20, none wrong)", need: "[DELHI RESULT VIDEO REQUIRED]" },
  app_demo: { src: "assets/video/app_demo.mp4", label: "Vigentra console, screen recording", need: "[CONSOLE RECORDING REQUIRED]" },
  tfl_low: { src: "assets/video/tfl_low.mp4", label: "352×288 traffic camera", need: "[LOW-RESOLUTION FOOTAGE REQUIRED]" },
  wall_cam01: { src: "assets/video/wall_cam01.mp4", label: "Grid cam01", need: "[GRID FEED REQUIRED]" },
  wall_cam02: { src: "assets/video/wall_cam02.mp4", label: "Grid cam02", need: "[GRID FEED REQUIRED]" },
  wall_cam04: { src: "assets/video/wall_cam04.mp4", label: "Grid cam04", need: "[GRID FEED REQUIRED]" },
  wall_cam05: { src: "assets/video/wall_cam05.mp4", label: "Grid cam05", need: "[GRID FEED REQUIRED]" },
  wall_cam07: { src: "assets/video/wall_cam07.mp4", label: "Grid cam07", need: "[GRID FEED REQUIRED]" },
  wall_cam12: { src: "assets/video/wall_cam12.mp4", label: "Grid cam12", need: "[GRID FEED REQUIRED]" },

  // images
  best_frame: { src: "assets/img/cam06_best_frame.jpg", label: "CAM06 frame 1299", need: "[REAL CAM 06 FRAME REQUIRED]" },
  frame_first: { src: "assets/img/frame_1244.jpg", label: "CAM06 frame 1244", need: "[REAL FRAME REQUIRED]" },
  frame_best: { src: "assets/img/frame_1299.jpg", label: "CAM06 frame 1299", need: "[REAL FRAME REQUIRED]" },
  frame_last: { src: "assets/img/frame_1314.jpg", label: "CAM06 frame 1314", need: "[REAL FRAME REQUIRED]" },
  journey_best: { src: "assets/img/journey_best.png", label: "Best crop, GJ23H1546", need: "[REAL PLATE CROP REQUIRED]" },
  journey_fused: { src: "assets/img/journey_fused.png", label: "12 frames fused", need: "[FUSED CROP REQUIRED]" },
  journey_enhanced: { src: "assets/img/journey_enhanced.png", label: "Enhanced", need: "[ENHANCED CROP REQUIRED]" },
  journey_charconf: { src: "assets/img/journey_charconf.png", label: "Confidence per character", need: "[CHARACTER CONFIDENCE REQUIRED]" },
  gt_sheet: { src: "assets/img/gt_sheet.jpg", label: "By-eye labelling sheet, CAM06 noon", need: "[LABELLING SHEET REQUIRED]" },
  verify_sheet: { src: "assets/img/verify_sheet.png", label: "Readings checked by eye; refused ones marked", need: "[VERIFICATION SHEET REQUIRED]" },
  app_live_wall: { src: "assets/img/app_live_wall.jpg", label: "Live wall, 50 cameras", need: "[CONSOLE SCREENSHOT REQUIRED]" },
  app_trace: { src: "assets/img/app_trace.jpg", label: "Trace a vehicle", need: "[CONSOLE SCREENSHOT REQUIRED]" },
  app_report: { src: "assets/img/app_report.jpg", label: "ANPR output report", need: "[CONSOLE SCREENSHOT REQUIRED]" },
};
