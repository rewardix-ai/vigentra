/* The system around the reader (a live schematic, then the console on a monitor), what makes
 * Vigentra different, what comes next, and the close. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;

  /* ---------------------------------------------------------------- system */
  K.scene({
    id: "system", act: "The system", title: "How the pieces connect", src: "Schematic · Vigentra deployment · services/edge-worker · services/central-api",
    build(ctx) {
      const C = K.CHARTS.system();
      const log = S.log({ max: 2 });
      log.add("··", C.intro);
      const chart = K.ui.flow({ nodes: C.nodes, edges: C.edges, caption: log });
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
          chart.run(ctx, C.routes);
        },
        () => { ctx.auto(5); chart.classList.add("dimmed"); return S.show(ctx, "#b1"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- console */
  K.scene({
    id: "console", act: "The system", title: "The Vigentra console", src: "SRC Vigentra console · screen recording · 1:15–1:24 and 1:28–2:42",
    build(ctx) {
      // The useful parts of the recording: live CAM06 detection (1:16.2-1:24) and the console pages
      // (1:28.5-2:42.6). Skipped: the camera wall at 1:24 and the closing card at 2:43, which show and
      // credit the London feeds.
      const PARTS = [["Live detection on a grid feed", 76.2, 83.9], ["Search every detection", 88.5, 108.6],
        ["Trace one vehicle", 108.6, 132.1], ["Plate report", 132.1, 148], ["Audit log", 148, 162.6]];
      const video = K.media.video("app_demo", { start: 76.2, loop: false, cls: "media-fit" });
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
          ctx.auto(82);
          ctx.in("#cp");
          await ctx.in("#fp");
          await S.show(ctx, "#b1");
          await ctx.wait(4000);
          S.hide(ctx, "#b1");
        },
      ];
    },
  });

  /* ---------------------------------------------------------------- different */
  K.scene({
    id: "different", act: "Why Vigentra", title: "What makes Vigentra different", src: "Each point is a design choice in the code, with its evidence on screen",
    build(ctx) {
      const D = K.DATA;
      const cards = D.distinct.map(([title, line, proof], i) => S.panel(80 + (i % 3) * 600, 230 + Math.floor(i / 3) * 330, 570, 300, `${String(i + 1).padStart(2, "0")} · ${title}`,
        h("div.dist", null, [h("p", null, line), h("span.dist-proof", null, proof)]), { id: `d${i}` }));
      ctx.el.append(S.title("Why Vigentra", "What makes Vigentra different."), ...cards,
        S.tech(D.distinct.map(([t, , , src]) => `<b>${t}</b> ${src}`), "Design choices, not claims about other products"));
      return [
        async () => { for (let i = 0; i < 3; i += 1) { ctx.$(`#d${i}`).classList.add("in"); await ctx.wait(220); } },
        async () => { for (let i = 3; i < 6; i += 1) { ctx.$(`#d${i}`).classList.add("in"); await ctx.wait(220); } },
      ];
    },
  });

  /* ---------------------------------------------------------------- roadmap */
  K.scene({
    id: "roadmap", act: "Next", title: "What we will do next", src: "SRC docs/scalability.md · known limits from the project's own reports",
    build(ctx) {
      const D = K.DATA;
      const rows = D.roadmap.map(([title, line, tag], i) => h("div.rm", { "data-i": i }, [
        h("span.rm-n.num", null, String(i + 1).padStart(2, "0")),
        h("div.rm-t", null, [h("b", null, title), h("span", null, line)]),
        h("span.rm-tag" + (tag === "Next" ? ".next" : ""), null, tag),
      ]));
      ctx.el.append(
        S.title("Next", "What we will do next."),
        S.panel(80, 220, 1760, 760, "Deployment plan", h("div.rms", null, rows), { id: "rp", right: "from today's limits" }),
        S.tech([D.roadmapSrc], "")
      );
      return [async () => { await ctx.in("#rp"); for (const r of ctx.$$(".rm")) { r.classList.add("in"); await ctx.wait(260); } }];
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
