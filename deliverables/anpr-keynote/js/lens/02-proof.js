/* Story edition, part 2: proof on real footage, what makes Vigentra different, the system, the
 * console, what we will do next, and the close. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const L = K.L;
  const D = K.DATA;
  const F = D.final;

  /* ---------------------------------------------------------------- field */
  K.scene({
    id: "field", act: "Proof", title: "Real footage, real results", cls: "paper light",
    notes: {
      say: "We counted every plate a person can read in these clips, frame by frame. CAM06: 16. Vigentra read 15. Wrong: zero. Delhi, hand-held, busy: 20 legible, 13 read. Wrong: zero.",
      sees: "Vigentra's own output on CAM06 (from 0:29, where its readings start) and on the Delhi street; the scores count up under each.",
      transition: "52 s: CAM06 24, Delhi 28. Moves on by itself (press A to hold).",
    },
    build(ctx) {
      const nums = (id, legible, read) => h("div.nums", { id }, [
        h("div", null, [h("span", null, "Readable by eye"), h("b", { "data-to": legible }, "–")]),
        h("div", null, [h("span", null, "Read by Vigentra"), h("b", { "data-to": read, style: { color: "var(--iris)" } }, "–")]),
        h("div", null, [h("span", null, "Wrong"), h("b", { "data-to": 0, style: { color: "var(--mint)" } }, "–")]),
      ]);
      ctx.el.append(
        L.head("Proof", "Real footage. Real results."),
        L.card(96, 290, 846, 720, [h("div.cap", null, "CAM06 · government camera · midday"), h("div.lmedia.dark", null, K.media.video("cam06_vigentra", { start: 29, end: 56, cls: "media-cover" })), nums("n6", F.cam06Noon[1], F.cam06Noon[0])], { id: "c6" }),
        L.card(978, 290, 846, 720, [h("div.cap", null, "Delhi · busy street · hand-held"), h("div.lmedia.dark", null, K.media.video("delhi_vigentra", { autoplay: false, cls: "media-cover" })), nums("nd", F.delhi[1], F.delhi[0])], { id: "dl" })
      );
      const countIn = async (id) => { for (const b of ctx.$$(`#${id} b[data-to]`)) { await ctx.count(b, Number(b.dataset.to), { dur: 800 }); await ctx.wait(200); } };
      return [
        async () => { ctx.auto(24); await ctx.in("#c6"); await countIn("n6"); },
        async () => {
          ctx.auto(28);
          const v = ctx.$("#dl video");
          if (v && v.play) { v.currentTime = 0; v.play().catch(() => {}); }
          await ctx.in("#dl");
          await countIn("nd");
        },
      ];
    },
  });

  /* ---------------------------------------------------------------- results */
  K.scene({
    id: "results", act: "Proof", title: "Every plate a person could read, scored", cls: "paper light",
    build(ctx) {
      const fast = D.speed.rows.find((r) => r.res === "720p");
      const tiles = [
        ["n1", `<span class="num" id="nr">0</span><span class="of"> / ${F.legible}</span>`, "iris", "Legible plates read", "Across every test clip, counted by eye before scoring."],
        ["n2", String(F.wrong), "mint", "Wrong plates", "Not one."],
        ["n3", String(F.legible - F.read), "mute", "Missed", "Nothing saved for them, rather than a wrong plate."],
        ["n4", "Live", "iris", "Speed", `Keeps pace with a ${fast.res} camera on ${D.speed.machine}.`],
      ];
      ctx.el.append(
        L.head("Results", "Every plate a person could read, scored."),
        ...tiles.map(([id, big, tone, k, s], i) => L.card(96 + i * 438, 340, 414, 520, [h("span.pill.line", null, k), h(`div.big.${tone}`, { html: big }), h("p", null, s)], { id })),
        S.tech([`<b>Per resolution</b> ${D.speed.rows.map((r) => `${r.res} ${r.ms} ms a frame (${r.rt.toFixed(2)}× live)`).join(" · ")}`], `${F.src} · ${D.speed.src}`)
      );
      return [
        async () => { await ctx.in("#n1"); await ctx.count("#nr", F.read, { dur: 1300 }); },
        () => ctx.in("#n2"),
        () => ctx.in("#n3"),
        () => ctx.in("#n4"),
      ];
    },
  });

  /* ---------------------------------------------------------------- different */
  K.scene({
    id: "different", act: "Why Vigentra", title: "What makes Vigentra different", cls: "paper light",
    build(ctx) {
      ctx.el.append(
        L.head("Why Vigentra", "What makes Vigentra different."),
        ...D.distinct.map(([title, line, proof], i) => L.card(96 + (i % 3) * 584, 300 + Math.floor(i / 3) * 350, 560, 326, [h("h3", null, title), h("p", null, line), h("div", { style: { flex: 1 } }), L.pill(proof, "iris")], { id: `d${i}` })),
        S.tech(D.distinct.map(([t, , , src]) => `<b>${t}</b> ${src}`), "Design choices, not claims about other products")
      );
      return [
        async () => { for (let i = 0; i < 3; i += 1) { ctx.$(`#d${i}`).classList.add("in"); await ctx.wait(200); } },
        async () => { for (let i = 3; i < 6; i += 1) { ctx.$(`#d${i}`).classList.add("in"); await ctx.wait(200); } },
      ];
    },
  });

  /* ---------------------------------------------------------------- system */
  K.scene({
    id: "system", act: "The system", title: "The layer between your cameras and your control room", cls: "paper light",
    notes: {
      say: "Video comes in; text goes out. The reader sends only the confirmed plate, time and camera to central, which checks it against the watchlist: yes means an alert, no means it is kept for search. Live video is a separate lane, shared only with the owning unit's permission.",
      sees: "The system schematic on paper with travelling dots; then the line over the dimmed chart.",
      transition: "27 s. Moves on by itself (press A to hold).",
    },
    build(ctx) {
      const C = K.CHARTS.system();
      const log = S.log({ max: 2 });
      log.add("··", C.intro);
      const chart = K.ui.flow({ nodes: C.nodes, edges: C.edges, caption: log });
      ctx.el.append(L.head("The system", "Between your cameras and your control room.", { sm: true }), L.card(1040, 64, 840, 160, [log], { id: "lc" }), chart,
        L.mantra("Plates travel as text. Video only with its owner's permission.", "m1"));
      return [
        async () => { ctx.auto(22); ctx.in("#lc"); await chart.reveal(ctx, { gap: 160 }); chart.run(ctx, C.routes); },
        async () => { ctx.auto(5); chart.classList.add("dimmed"); ctx.$("#lc").classList.add("gone"); await ctx.in("#m1 p"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- console */
  K.scene({
    id: "console", act: "The console", title: "From a plate to an answer, in a browser", cls: "nightbg",
    build(ctx) {
      // the useful parts of the recording (see the control-room deck): live CAM06 detection, then the
      // console pages; the camera wall and the closing card, which show the London feeds, are skipped
      const PARTS = [["Live detection on a grid feed", 76.2, 83.9], ["Search every detection", 88.5, 108.6], ["Trace one vehicle", 108.6, 132.1], ["Plate report", 132.1, 148], ["Audit log", 148, 162.6]];
      const video = K.media.video("app_demo", { start: 76.2, loop: false, cls: "media-fit" });
      const items = PARTS.map(([name], i) => h("div.lfeat", null, [h("b", null, String(i + 1).padStart(2, "0")), name]));
      if (video.tagName === "VIDEO") {
        video.addEventListener("timeupdate", () => {
          const t = video.currentTime;
          if (t >= 83.9 && t < 88.5) video.currentTime = 88.6;
          if (t >= 162.6) video.pause();
          items.forEach((c, i) => c.classList.toggle("now", t >= PARTS[i][1] && t < PARTS[i][2]));
        });
      }
      const browser = h("div.browser", { id: "bw", style: { left: "96px", top: "250px", width: "1240px", height: "737px" } }, [
        h("div.bar", null, [h("i"), h("i"), h("i"), h("span", null, "Vigentra console · State Joint Control Room")]),
        h("div.bv", null, video),
      ]);
      ctx.el.append(L.head("The console", "From a plate to an answer, in a browser.", { sm: true }), browser,
        h("div.lfeats", { style: { left: "1380px", top: "250px", width: "444px" } }, items));
      return [async () => { ctx.auto(82); await ctx.in("#bw"); }];
    },
  });

  /* ---------------------------------------------------------------- roadmap */
  K.scene({
    id: "roadmap", act: "On our roadmap", title: "What we will do next", cls: "paper light",
    build(ctx) {
      ctx.el.append(
        L.head("On our roadmap", "What we'll build next."),
        ...D.roadmap.map(([title, line, tag], i) => L.card(96 + (i % 3) * 584, 300 + Math.floor(i / 3) * 350, 560, 326, [L.pill(tag === "Next" ? "Next" : "On our roadmap", tag === "Next" ? "iris" : "line"), h("h3", null, title), h("p", null, line)], { id: `r${i}` })),
        S.tech([D.roadmapSrc], "")
      );
      return [async () => { for (let i = 0; i < 6; i += 1) { ctx.$(`#r${i}`).classList.add("in"); await ctx.wait(200); } }];
    },
  });

  /* ---------------------------------------------------------------- close */
  K.scene({
    id: "close", act: "Vigentra", title: "From pixels to information", cls: "nightbg nochrome",
    build(ctx) {
      const live = S.replay(ctx, { rate: 0.5, ids: true, plate: true });
      const layer = h("div.fill.r.slow.media-layer.finale-bg", null, L.cine(live.el, { push: false }));
      ctx.el.append(layer, L.mantra("Vigentra turns pixels into information.", "m1"),
        h("div.center", null, h("div.stack.brand-stack", { id: "end" }, [S.lockup({ size: 0.9 }), h("div.h3.r", { id: "thanks" }, "Thank you.")])));
      return [
        async () => { await ctx.in(layer); await ctx.wait(700); await ctx.in("#m1 p"); },
        async () => {
          ctx.out("#m1 p");
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
