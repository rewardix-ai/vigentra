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

  /* ---------------------------------------------------------------- the high-level design */
  K.scene({
    id: "hld", act: "The system", title: "The high-level design", src: "Schematic · docs/hld.md · docs/scalability.md §1-2",
    build(ctx) {
      const C = K.CHARTS.hld();
      const log = S.log({ max: 2 });
      log.add("··", C.intro);
      const chart = K.ui.flow({ nodes: C.nodes, edges: C.edges, caption: log });
      const line = S.at(955, 300, 6, 430, h("div", { style: { height: "100%", borderLeft: "4px dashed #e5484d" } }), "r");
      const tag = S.at(975, 690, 420, 40, h("div", { style: { font: "700 18px var(--mono)", letterSpacing: "0.12em", color: "#e5484d" } }, "ONLY METADATA CROSSES"), "r");
      line.id = "ml"; tag.id = "mt";
      ctx.el.append(
        S.title("The high-level design", "Four tiers."),
        S.panel(1040, 68, 840, 160, "Event log", log, { id: "lp", right: S.rec() }),
        chart, line, tag,
        S.banner("Grows tier by tier", "Add GPUs, districts or replicas without touching the other tiers.", { id: "b1" }),
        S.tech(["<b>Edge</b> reads the video where it is recorded", "<b>Regional</b> speaks each local VMS and buffers outages", "<b>Central</b> keeps the records and decides access"], "docs/hld.md §3 · docs/scalability.md §1")
      );
      return [
        async () => {
          ctx.auto(22);
          ctx.in("#lp");
          await chart.reveal(ctx, { gap: 160 });
          await ctx.in("#ml"); await ctx.in("#mt");
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

  /* ---------------------------------------------------------------- every feature, in two scenes */
  function featureScene(id, kicker, line, rows, right) {
    K.scene({
      id, act: "The system", title: line, src: "SRC " + K.DATA.featuresSrc,
      build(ctx) {
        const cards = rows.map(([name, what], i) => S.panel(80 + (i % 4) * 445, 230 + Math.floor(i / 4) * 255, 420, 235, `${String(i + 1).padStart(2, "0")} · ${name}`,
          h("div.dist", null, [h("p", { style: { fontSize: "25px" } }, what)]), { id: `f${i}` }));
        ctx.el.append(S.title(kicker, line), ...cards, S.tech([right], K.DATA.featuresSrc));
        const row = (r) => async () => { for (let i = r * 4; i < r * 4 + 4; i += 1) { ctx.$(`#f${i}`).classList.add("in"); await ctx.wait(160); } };
        return [row(0), row(1), row(2)];
      },
    });
  }
  featureScene("features-find", "Every feature · 1 of 2", "See every camera. Find any vehicle.", K.DATA.featuresFind,
    "<b>Model 1</b> registry, GIS and onboarding · <b>Model 3</b> federation · ANPR, trace, pursuit and alerts on top");
  featureScene("features-act", "Every feature · 2 of 2", "Act on it, with a person in charge.", K.DATA.featuresAct,
    "<b>Offences</b> candidates with evidence, confirmed by a person · <b>Governance</b> access by role and zone, everything audited");

  /* ---------------------------------------------------------------- under the hood: the stack */
  K.scene({
    id: "stack", act: "Under the hood", title: "What Vigentra is built with", src: "All open source · the reasoning for each part is in TECHNICAL.md",
    build(ctx) {
      const D = K.DATA;
      const cards = D.stack.map(([layer, choice, why], i) => S.panel(80 + (i % 4) * 445, 230 + Math.floor(i / 4) * 340, 420, 310, `${String(i + 1).padStart(2, "0")} · ${layer}`,
        h("div.stk", null, [h("b", null, choice), h("p", null, why)]), { id: `k${i}` }));
      ctx.el.append(S.title("Under the hood", "What Vigentra is built with."), ...cards,
        S.tech([D.stackSrc], "All open source: no licence fee, vendor account or API key"));
      return [
        async () => { for (let i = 0; i < 4; i += 1) { ctx.$(`#k${i}`).classList.add("in"); await ctx.wait(180); } },
        async () => { for (let i = 4; i < 8; i += 1) { ctx.$(`#k${i}`).classList.add("in"); await ctx.wait(180); } },
      ];
    },
  });

  /* ---------------------------------------------------------------- under the hood: why this, not that */
  K.scene({
    id: "choices", act: "Under the hood", title: "Why this, not that", src: "SRC docs/scalability.md · docs/anpr.md · final2 benchmark · docs/hld.md",
    build(ctx) {
      const D = K.DATA;
      const table = S.table(["Decision", "We use", "Instead of", "Why it is better"], D.choices, "250px 400px 330px 1fr", { cls: "choices" });
      ctx.el.append(S.title("Under the hood", "Why this, not that."),
        S.panel(80, 220, 1760, 770, "Design decisions", table, { id: "cp", right: `${D.choices.length} decisions` }),
        S.tech([D.choicesSrc], ""));
      return [
        async () => { await ctx.in("#cp"); ctx.$(".tr.th").classList.add("in"); await S.rows(ctx, 0, 4); },
        () => S.rows(ctx, 4, D.choices.length),
      ];
    },
  });

  /* ---------------------------------------------------------------- under the hood: tested and dropped */
  K.scene({
    id: "dropped", act: "Under the hood", title: "What we tested and dropped", src: "SRC docs/anpr-optimisation.md · EW/config/thresholds.yaml · docs/anpr.md",
    build(ctx) {
      const D = K.DATA;
      const table = S.table(["We tried", "What happened", "So we kept"], D.dropped, "600px 1fr 470px", { cls: "dropped" });
      ctx.el.append(S.title("Under the hood", "What we tested and dropped."),
        S.panel(80, 220, 1760, 770, "Measured on the same clips", table, { id: "dp", right: "every change replayed" }),
        S.tech([D.droppedSrc], ""));
      return [
        async () => { await ctx.in("#dp"); ctx.$(".tr.th").classList.add("in"); await S.rows(ctx, 0, 3); },
        () => S.rows(ctx, 3, D.dropped.length),
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

  /* ---------------------------------------------------------------- 80,000 cameras: what becomes possible */
  K.scene({
    id: "scale-possible", act: "80,000 cameras", title: "What becomes possible", src: "SRC " + K.DATA.scaleSrc,
    build(ctx) {
      const D = K.DATA;
      const cards = D.possible.map(([title, line, tag], i) => S.panel(80 + (i % 3) * 600, 230 + Math.floor(i / 3) * 330, 570, 300, `${String(i + 1).padStart(2, "0")} · ${title}`,
        h("div.dist", null, [h("p", null, line), h("span.dist-proof", null, tag)]), { id: `p${i}` }));
      ctx.el.append(S.title("At 80,000 cameras", "One query instead of 26 footage requests."), ...cards,
        S.banner("Today", "Built and running on the 30-camera grid; the rest extends the same pipeline.", { id: "b1" }),
        S.tech(["<b>Built on 30</b> running live on the Sentinel grid today", "<b>Next</b> the same edge and central, new models and integrations"], D.scaleSrc));
      return [
        async () => { for (let i = 0; i < 3; i += 1) { ctx.$(`#p${i}`).classList.add("in"); await ctx.wait(220); } },
        async () => { for (let i = 3; i < 6; i += 1) { ctx.$(`#p${i}`).classList.add("in"); await ctx.wait(220); } },
        () => S.show(ctx, "#b1"),
      ];
    },
  });

  /* ---------------------------------------------------------------- 80,000 cameras: what it needs */
  K.scene({
    id: "scale-needs", act: "80,000 cameras", title: "What it needs", src: "SRC " + K.DATA.scaleSrc,
    build(ctx) {
      const D = K.DATA;
      const big = D.needsBig.map(([n, l], i) => S.panel(80 + i * 890, 210, 860, 210, i ? "Vigentra: metadata only" : "Streaming every camera centrally",
        h("div.dist", null, [h("p", { style: { font: "800 92px/1 var(--text)", color: i ? "var(--track)" : "#e5484d" } }, n), h("p", null, l)]), { id: `g${i}` }));
      const table = S.table(["Need", "Requirement (estimate)", "Why"], D.needs, "250px 1fr 560px", { cls: "choices" });
      ctx.el.append(S.title("At 80,000 cameras", "What it needs, tier by tier."), ...big,
        S.panel(80, 440, 1760, 580, "Requirements", table, { id: "np", right: "for planning" }),
        S.tech([D.scaleSrc], "Estimates from the project's scalability plan"));
      return [
        async () => { await ctx.in("#g0"); await ctx.wait(500); await ctx.in("#g1"); },
        async () => { await ctx.in("#np"); ctx.$(".tr.th").classList.add("in"); await S.rows(ctx, 0, 4); },
        () => S.rows(ctx, 4, D.needs.length),
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
