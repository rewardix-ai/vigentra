/* The two schematics, shared by both decks (index.html and lens.html): how Vigentra reads a
 * plate, and how the system connects. Each returns { nodes, edges, routes } for K.ui.flow. */
(function () {
  const K = window.K;
  const h = K.h;

  K.CHARTS = {
    reading() {
      const S = K.S;
      const E = K.EVIDENCE || { plate: "", frames: {} };
      const fail = (label) => (K.FAILURES || []).find((f) => f.label === label) || {};
      const tiny = fail("Tiny plate");
      const blur = fail("Motion blur");
      const X = [260, 713, 1166, 1620];
      const [R1, R2, R3] = [370, 695, 942];
      const crop = (src) => () => h("img.pix-fit", { src, alt: "" });
      const nodes = [
        { id: "cam", n: 1, x: X[0], y: R1, title: "Camera", sub: "real CCTV video", media: () => K.media.video("cam06_1080p", { start: 49.6, end: 54.05, cls: "media-cover" }),
          say: "real CCTV video; more frames are taken when a vehicle is close enough to read" },
        { id: "car", n: 2, x: X[1], y: R1, title: "Track the car", sub: "boxed, then given one ID", media: () => S.carThumb(),
          say: "every vehicle boxed in the frames checked; one ID per car" },
        { id: "plate", n: 3, x: X[2], y: R1, title: "Find its plate", sub: "searched only inside the car", media: crop("assets/img/plate_1299.png"),
          say: "the plate detector looks only inside the car's box" },
        { id: "best", n: 4, x: X[3], y: R1, title: "Best frames", sub: `${E.frames.fused || 12} combined into one clearer image`, media: crop("assets/img/journey_fused.png"),
          say: "the best crops are lined up and combined" },
        { id: "q", type: "decision", x: X[0], y: R2, w: 260, h: 180, title: "Enough pixels?", sub: "≥ 22 px wide, sharp",
          say: "too small or too blurred: not read" },
        { id: "read", n: 5, x: X[1], y: R2, title: "Read the plate", sub: "character by character", media: () => h("div.fc-text", null, E.plate),
          say: "characters, each with a confidence" },
        { id: "agree", type: "decision", x: X[2], y: R2, w: 260, h: 180, title: "Readings agree?", sub: "many frames · valid format",
          say: "many frames must agree, in an Indian format" },
        { id: "ok", n: 6, x: X[3], y: R2, tone: "ok", title: "Confirmed plate", sub: "plate · time · camera, never video", media: () => h("div.fc-text.c-ok", null, `✓ ${E.plate}`),
          say: "saved: plate, time, camera. Never video" },
        { id: "silent", type: "end", tone: "bad", x: X[0], y: R3, w: 340, h: 116, title: "Not read", sub: `too small: this crop is ${tiny.width_px || "?"} px`, media: crop(tiny.file),
          say: "below 22 px, Vigentra does not even try" },
        { id: "none", type: "end", tone: "bad", x: X[2], y: R3, w: 340, h: 116, title: "No plate saved", sub: "not sure enough, like this one", media: crop(blur.file),
          say: "the readings did not agree: nothing saved, no alert, no wrong record" },
        { id: "use", type: "end", tone: "io", x: X[3], y: R3, w: 340, h: 116, title: "Search · Trace · Alert", sub: "in the Vigentra console",
          say: "search it, trace the car, alert if it is on a watchlist" },
      ];
      const edges = [
        { from: "cam", to: "car" }, { from: "car", to: "plate" }, { from: "plate", to: "best" },
        { from: "best", to: "q", out: "bottom", in: "top", via: [[X[3], 535], [X[0], 535]] },
        { from: "q", to: "read", kind: "yes", label: "YES", at: [X[0] + 146, R2 - 14] },
        { from: "q", to: "silent", out: "bottom", in: "top", kind: "no", label: "NO", at: [X[0] + 14, R2 + 128] },
        { from: "read", to: "agree" },
        { from: "agree", to: "ok", kind: "yes", label: "YES", at: [X[2] + 146, R2 - 14] },
        { from: "agree", to: "none", out: "bottom", in: "top", kind: "no", label: "NO", at: [X[2] + 14, R2 + 128] },
        { from: "ok", to: "use", out: "bottom", in: "top" },
      ];
      const main = ["cam", "car", "plate", "best", "q", "read", "agree", "ok", "use"];
      return { nodes, edges, intro: `<b>Follow the square</b> one real car, ${E.plate}. Red: plates Vigentra would not guess.`, routes: [
        { path: main, every: 21000, main: true },
        { path: main.slice(0, 5).concat("silent"), every: 21000, offset: 6000, cls: "bad" },
        { path: main.slice(0, 7).concat("none"), every: 21000, offset: 13000, cls: "bad" },
      ] };
    },

    system() {
      const S = K.S;
      const E = K.EVIDENCE || { plate: "" };
      const X = [260, 713, 1166, 1620];
      const [R, R2] = [540, 830];
      const nodes = [
        { id: "cams", n: 1, x: X[0], y: R, tone: "io", title: "Cameras", sub: "the grid · Delhi",
          media: () => h("div.mosaic", null, ["wall_cam04", "wall_cam12", "wall_cam15", "delhi_raw"].map((k) => K.media.video(k, { cls: "media-cover" }))),
          say: "the department's own cameras, unchanged" },
        { id: "reader", n: 2, x: X[1], y: R, title: "Vigentra reader", sub: "find · follow · read", media: () => S.carThumb({ plate: true }),
          say: "reads the plates; only a confirmed plate leaves it, as text" },
        { id: "central", n: 3, x: X[2], y: R, title: "Vigentra central", sub: "plate records · watchlist",
          media: () => h("div.fc-rec", null, [h("div", null, E.plate), h("span", null, "plate · time · camera")]),
          say: "stores the record, checks the watchlist as it arrives" },
        { id: "console", n: 4, x: X[3], y: R, tone: "ok", title: "Console", sub: "search · trace · report", media: () => K.media.img("app_trace", { cls: "media-cover" }),
          say: "search, trace, report; what operators do is audited" },
        { id: "list", type: "decision", x: X[2], y: R2, w: 260, h: 170, title: "On the watchlist?", say: "every new plate record is checked" },
        { id: "alert", type: "end", tone: "bad", x: X[3], y: R2, w: 300, h: 110, title: "Alert", sub: "critical · high · review", say: "a listed plate raises an alert, by priority" },
        { id: "kept", type: "end", tone: "io", x: X[1], y: R2, w: 300, h: 110, title: "Kept for search", sub: "and for tracing later", say: "otherwise the record is kept for search and trace" },
        { id: "live", type: "end", tone: "video", x: 940, y: 300, w: 400, h: 100, title: "Live video", sub: "only with the owning unit's permission", say: "another unit sees live video only when the owner grants it" },
      ];
      const edges = [
        { from: "cams", to: "reader", label: "VIDEO", at: [X[0] + 166, R - 14] },
        { from: "reader", to: "central", label: "TEXT", at: [X[1] + 170, R - 14] },
        { from: "central", to: "console" },
        { from: "central", to: "list", out: "bottom", in: "top" },
        { from: "list", to: "alert", kind: "yes", label: "YES", at: [X[2] + 146, R2 - 14] },
        { from: "alert", to: "console", out: "top", in: "bottom", kind: "alert" },
        { from: "list", to: "kept", out: "left", in: "right", kind: "no", label: "NO", at: [X[2] - 196, R2 - 14] },
        { from: "cams", to: "live", out: "top", in: "left", via: [[X[0], 300]], kind: "video" },
        { from: "live", to: "console", out: "right", in: "top", via: [[X[3], 300]], kind: "video" },
      ];
      return { nodes, edges, intro: "<b>Video in, text out</b> follow the squares; click any box for what it does", routes: [
        { path: ["cams", "reader", "central", "console"], every: 9500, main: true },
        { path: ["cams", "reader", "central", "list", "alert", "console"], every: 14000, offset: 4000, cls: "bad" },
        { path: ["cams", "reader", "central", "list", "kept"], every: 14000, offset: 11000, cls: "dim" },
        { path: ["cams", "live", "console"], every: 9000, offset: 2000, cls: "video" },
      ] };
    },
  };
})();
