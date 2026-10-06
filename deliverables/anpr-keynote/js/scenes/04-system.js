/* The system around the reader (a live flowchart, then the console), what comes next, and the close. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;

  /* ---------------------------------------------------------------- system */
  K.scene({
    id: "system", act: "The system", title: "How the pieces connect",
    build(ctx) {
      const E = K.EVIDENCE || { plate: "" };
      const X = [260, 713, 1166, 1620];
      const [R, R2] = [540, 830];
      const nodes = [
        { id: "cams", n: 1, x: X[0], y: R, tone: "io", title: "Cameras", sub: "the grid · Delhi",
          media: () => h("div.mosaic", null, ["wall_cam04", "wall_cam12", "wall_cam15", "delhi_raw"].map((k) => K.media.video(k, { cls: "media-cover" }))),
          say: "The department's own cameras, unchanged. Vigentra reads the video they already stream." },
        { id: "reader", n: 2, x: X[1], y: R, title: "Vigentra reader", sub: "find · follow · read", media: () => S.carThumb({ plate: true }),
          say: "Reads plates next to the cameras. Only a confirmed plate leaves it, as text." },
        { id: "central", n: 3, x: X[2], y: R, title: "Vigentra central", sub: "plate records · watchlist",
          media: () => h("div.fc-rec", null, [h("div", null, E.plate), h("span", null, "plate · time · camera")]),
          say: "Stores every plate record and checks it against the watchlist the moment it arrives." },
        { id: "console", n: 4, x: X[3], y: R, tone: "ok", title: "Console", sub: "search · trace · report", media: () => K.media.img("app_trace", { cls: "media-cover" }),
          say: "Officers search plates, trace a vehicle across cameras and run reports. Every action is audited." },
        { id: "list", type: "decision", x: X[2], y: R2, w: 260, h: 170, title: "On the watchlist?" },
        { id: "alert", type: "end", tone: "bad", x: X[3], y: R2, w: 300, h: 110, title: "Alert", sub: "critical · high · review" },
        { id: "kept", type: "end", tone: "io", x: X[1], y: R2, w: 300, h: 110, title: "Kept for search", sub: "and for tracing later" },
        { id: "live", type: "end", tone: "video", x: 940, y: 300, w: 400, h: 100, title: "Live video", sub: "only with the owning unit's permission" },
      ];
      const edges = [
        { from: "cams", to: "reader", label: "video", at: [X[0] + 172, R - 14] },
        { from: "reader", to: "central", label: "text", at: [X[1] + 172, R - 14] },
        { from: "central", to: "console" },
        { from: "central", to: "list", out: "bottom", in: "top" },
        { from: "list", to: "alert", kind: "yes", label: "Yes", at: [X[2] + 150, R2 - 14] },
        { from: "alert", to: "console", out: "top", in: "bottom", kind: "alert" },
        { from: "list", to: "kept", out: "left", in: "right", kind: "no", label: "No", at: [X[2] - 200, R2 - 14] },
        { from: "cams", to: "live", out: "top", in: "left", via: [[X[0], 300]], kind: "video" },
        { from: "live", to: "console", out: "right", in: "top", via: [[X[3], 300]], kind: "video" },
      ];
      const caption = h("div.flow-say", null, [h("span.fs-n.blank"), h("div", null, [h("b", null, "Video in, text out"), h("span", null, "Follow the dots. Click any box to read what it does.")])]);
      const chart = K.ui.flow({ nodes, edges, caption });
      ctx.el.append(
        S.title("The system", "How the pieces connect."),
        S.at(1060, 92, 760, null, h("div.r", { id: "say" }, caption)),
        chart,
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "text" }, "Plates travel as text. Video is shared only with its owner's permission.")),
        S.tech(["<b>Plate record</b> plate, camera, time, confidence and boxes; no image, no video", "<b>Unconfirmed readings</b> not sent (ANPR_EMIT_UNCONFIRMED=false)", "<b>Video</b> central oversight roles need the owning unit's grant"], "services/edge-worker/app/worker.py · services/central-api (access model)")
      );
      return [
        async () => {
          await chart.reveal(ctx, { gap: 170 });
          await ctx.in("#say");
          chart.run(ctx, [
            { path: ["cams", "reader", "central", "console"], every: 9500, main: true },
            { path: ["cams", "reader", "central", "list", "alert", "console"], every: 14000, offset: 4000, cls: "bad" },
            { path: ["cams", "reader", "central", "list", "kept"], every: 14000, offset: 11000, cls: "dim" },
            { path: ["cams", "live", "console"], every: 9000, offset: 2000, cls: "video" },
          ]);
        },
        () => ctx.in("#text"),
      ];
    },
  });

  /* ---------------------------------------------------------------- console */
  K.scene({
    id: "console", act: "The system", title: "The Vigentra console",
    build(ctx) {
      // from 1:16, live CAM06 onward: earlier, the recording shows the 50-camera wall and registry
      const screen = S.framed(K.media.video("app_demo", { start: 75.5, cls: "media-fit" }), 300, 250, 1320, 743, "console tilt");
      const chips = ["Live detection on the feeds", "Search every detection", "Trace one vehicle", "Plate report", "Every action audited"];
      ctx.el.append(
        S.title("The Vigentra console", "From a plate to an answer, in a browser."),
        screen,
        h("div.chips", null, chips.map((c, i) => h("span.fchip.r", { style: { top: `${300 + i * 130}px` } }, c))),
        S.tech(["<b>Console</b> Next.js; detections, trace, report and audit log as shown; watchlist and camera-health alerts"], "deliverables/Vigentra_Demo_Short.mp4 (screen recording, from 1:16)")
      );
      return [
        async () => { await ctx.wait(200); screen.classList.add("in"); },
        () => ctx.in(".fchip", { stagger: 260 }),
      ];
    },
  });

  /* ---------------------------------------------------------------- next */
  K.scene({
    id: "next", act: "What's next", title: "What still doesn't work",
    build(ctx) {
      const items = [
        ["Night and glare", "Plates are found after dark, but rarely legible."],
        ["Small, distant plates", "Under 22 pixels wide, Vigentra will not read a plate."],
        ["GPU servers for every feed", "One laptop keeps pace with one camera. Thirty need GPU servers."],
      ];
      ctx.el.append(
        S.title("What's next", "What still doesn't work, honestly."),
        S.at(140, 330, 1640, null, h("div.nexts", null, items.map(([a, b], i) => h("div.nx.r", null, [h("span.nx-n.num", null, String(i + 1)), h("div", null, [h("div.h3", null, a), h("div.lede", null, b)])]))))
      );
      return [() => ctx.in(".nx", { stagger: 450 })];
    },
  });

  /* ---------------------------------------------------------------- finale */
  K.scene({
    id: "finale", act: "Vigentra", title: "From pixels to information", cls: "black nobrand",
    build(ctx) {
      const live = S.replay(ctx, { rate: 0.5, ids: true, plate: true });
      const layer = h("div.fill.r.slow.media-layer.finale-bg", null, live.el);
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
