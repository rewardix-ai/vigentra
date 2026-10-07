/* The system around the reader (a live schematic, then the console on a monitor), and the close. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;

  /* ---------------------------------------------------------------- system */
  K.scene({
    id: "system", act: "The system", title: "How the pieces connect", src: "Schematic · Vigentra deployment · services/edge-worker · services/central-api",
    build(ctx) {
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
      const log = S.log({ max: 2 });
      log.add("··", "<b>Video in, text out</b> follow the squares; click any box for what it does");
      const chart = K.ui.flow({ nodes, edges, caption: log });
      ctx.el.append(
        S.title("The system", "How the pieces connect."),
        S.panel(1040, 68, 840, 160, "Event log", log, { id: "lp", right: S.rec() }),
        chart,
        S.banner("By design", "Plates travel as text. Video is shared only with its owner's permission.", { id: "b1" }),
        S.tech(["<b>Plate record</b> plate, camera, time, confidence and boxes; no image, no video", "<b>Unconfirmed readings</b> not sent (ANPR_EMIT_UNCONFIRMED=false)", "<b>Video</b> central oversight roles need the owning unit's grant"], "services/edge-worker/app/worker.py · services/central-api (access model)")
      );
      return [
        async () => {
          ctx.auto(22);
          ctx.in("#lp");
          await chart.reveal(ctx, { gap: 160 });
          chart.run(ctx, [
            { path: ["cams", "reader", "central", "console"], every: 9500, main: true },
            { path: ["cams", "reader", "central", "list", "alert", "console"], every: 14000, offset: 4000, cls: "bad" },
            { path: ["cams", "reader", "central", "list", "kept"], every: 14000, offset: 11000, cls: "dim" },
            { path: ["cams", "live", "console"], every: 9000, offset: 2000, cls: "video" },
          ]);
        },
        () => { ctx.auto(5); chart.classList.add("dimmed"); return S.show(ctx, "#b1"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- console */
  K.scene({
    id: "console", act: "The system", title: "The Vigentra console", src: "SRC Vigentra console · screen recording · 1:15–1:24 and 1:28–2:42",
    build(ctx) {
      // The useful parts of the recording: live CAM06 detection (1:15.5-1:24) and the console pages
      // (1:28.5-2:42.6). Skipped: the camera wall at 1:24 and the closing card at 2:43, which show and
      // credit the London feeds.
      const PARTS = [["Live detection on a grid feed", 75.5, 83.9], ["Search every detection", 88.5, 108.6],
        ["Trace one vehicle", 108.6, 132.1], ["Plate report", 132.1, 148], ["Audit log", 148, 162.6]];
      const video = K.media.video("app_demo", { start: 75.5, loop: false, cls: "media-fit" });
      const items = PARTS.map(([name], i) => h("div.feat", null, [h("span.feat-n.num", null, String(i + 1).padStart(2, "0")), h("span", null, name)]));
      if (video.tagName === "VIDEO") {
        video.addEventListener("timeupdate", () => {
          const t = video.currentTime;
          if (t >= 83.9 && t < 88.5) video.currentTime = 88.6;
          if (t >= 162.6) video.pause();
          items.forEach((c, i) => c.classList.toggle("now", t >= PARTS[i][1] && t < PARTS[i][2]));
        });
      }
      ctx.el.append(
        S.panel(40, 70, 1380, 816, "Vigentra console · State Joint Control Room", h("div.fill", null, video), { id: "cp", right: S.rec() }),
        S.panel(1440, 70, 440, 816, "On screen now", h("div.feats", null, items), { id: "fp" }),
        S.banner("The console", "From a plate to an answer, in a browser.", { id: "b1" }),
        S.tech(["<b>Console</b> Next.js; detections, trace, report and audit log as shown; watchlist and camera-health alerts"], "deliverables/Vigentra_Demo_Short.mp4 (screen recording, 1:15-1:24 and 1:28-2:42)")
      );
      return [
        // 83 s: the two parts of the recording, back to back
        async () => {
          ctx.auto(83);
          ctx.in("#cp");
          await ctx.in("#fp");
          await S.show(ctx, "#b1");
          await ctx.wait(4000);
          S.hide(ctx, "#b1");
        },
      ];
    },
  });

  /* ---------------------------------------------------------------- close */
  K.scene({
    id: "close", act: "Vigentra", title: "From pixels to information", cls: "black nochrome", src: "",
    build(ctx) {
      const live = S.replay(ctx, { rate: 0.5, ids: true, plate: true });
      const layer = h("div.fill.r.slow.media-layer.finale-bg", null, S.monitor(live.el, { tl: "CAM06 · replay", tr: S.rec(), full: true }));
      ctx.el.append(
        layer,
        h("div.center", null, h("div.stack.final-type", null, [h("div.hero.r.soft", { id: "fp" }, "From pixels"), h("div.hero.r.soft.c-track", { id: "ti" }, "to information.")])),
        h("div.center", null, h("div.stack.brand-stack", { id: "end" }, [S.lockup({ size: 0.9 }), h("div.h3.r", { id: "thanks" }, "Thank you.")]))
      );
      return [
        async () => { await ctx.in(layer); await ctx.wait(800); await ctx.in("#fp"); await ctx.in("#ti", { delay: 300 }); },
        async () => {
          ctx.out("#fp, #ti");
          layer.classList.add("dim-strong");
          await ctx.wait(500);
          await ctx.in(".lk-mark");
          ctx.$(".lk-word").classList.add("in");
          await ctx.wait(900);
          await ctx.in(".lk-tag");
          await ctx.in("#thanks", { delay: 400 });
        },
      ];
    },
  });
})();
