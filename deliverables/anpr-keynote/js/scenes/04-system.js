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
      const nodes = [
        { id: "cams", x: 240, y: 560, label: "Cameras", sub: "grid · Delhi", kind: "io", detail: "The department's cameras, unchanged. Vigentra reads their existing streams." },
        { id: "edge", x: 700, y: 560, label: "Vigentra reader", sub: "find · follow · read", detail: "Reads plates beside the cameras. Only a confirmed plate leaves it, as text." },
        { id: "central", x: 1180, y: 560, label: "Vigentra central", sub: "plate records · watchlist", detail: "Stores each plate record and checks it against the watchlist as it arrives." },
        { id: "console", x: 1660, y: 560, label: "Console", sub: "search · trace · report", kind: "ok", detail: "Operators search, trace and report; every action is audited." },
        { id: "alert", x: 1180, y: 850, label: "Alert", sub: "watchlist match", kind: "bad", detail: "A listed plate raises an alert in the console, by priority." },
        { id: "video", x: 950, y: 300, label: "Live video", sub: "with the owner's permission", kind: "io", detail: "Another unit sees a camera's video only when the unit that owns it has granted access." },
      ];
      const edges = [
        { from: "cams", to: "edge" }, { from: "edge", to: "central" }, { from: "central", to: "console" },
        { from: "central", to: "alert", kind: "bad" }, { from: "alert", to: "console", kind: "bad" },
        { from: "cams", to: "video", kind: "dash", d: "M240,508 V300 H825" }, { from: "video", to: "console", kind: "dash", d: "M1075,300 H1660 V508" },
      ];
      const chart = K.ui.flow({ nodes, edges });
      const chip = (html, cls = "") => `<div class="pk-body"><span class="pk-text ${cls}">${html}</span></div>`;
      ctx.el.append(
        S.title("The system", "How the pieces connect."),
        chart,
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "text" }, "Plates travel as text. Video is shared only with its owner's permission.")),
        S.tech(["<b>Plate record</b> plate, camera, time, confidence and boxes; no image, no video", "<b>Unconfirmed readings</b> not sent (ANPR_EMIT_UNCONFIRMED=false)", "<b>Video</b> central oversight roles need the owning unit's grant"], "services/edge-worker/app/worker.py · services/central-api (access model)")
      );
      return [
        () => chart.reveal(ctx, { gap: 220 }),
        async () => {
          chart.run(ctx, [
            { path: ["cams", "edge", "central", "console"], every: 1900, carry: (n, el) => { if (n === "cams") el.innerHTML = chip("video", "vid"); if (n === "edge") el.innerHTML = chip(`${E.plate} · cam06`); } },
            { path: ["cams", "edge", "central", "alert", "console"], every: 6200, offset: 2600, cls: "bad", carry: (n, el) => { if (n === "central") el.innerHTML = chip("watchlist match", "bad"); } },
            { path: ["cams", "video", "console"], every: 4400, offset: 900, cls: "vid", carry: (n, el) => { if (n === "cams") el.innerHTML = chip("live video", "vid"); } },
          ]);
          await ctx.in("#text", { delay: 1800 });
        },
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
