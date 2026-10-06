/* How Vigentra reads a plate: the whole journey as a live flowchart, then each step on one real
 * car (GJ23H1546, CAM06): find and follow it, keep its best frames, check quality, read, vote. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const D = K.DATA;
  const P = D.pipeline;

  /* ---------------------------------------------------------------- flow */
  K.scene({
    id: "flow", act: "How Vigentra reads", title: "From camera to confirmed plate",
    build(ctx) {
      const E = K.EVIDENCE || { plate: "" };
      const R1 = 400;
      const R2 = 680;
      const nodes = [
        { id: "cam", x: 240, y: R1, label: "Camera", sub: "real CCTV video", kind: "io", detail: "Grid video arrives continuously; Vigentra samples its frames, more densely when a vehicle is big enough to read." },
        { id: "veh", x: 600, y: R1, label: "Find vehicles", sub: "in every frame", detail: "Every car, motorcycle, bus and truck is boxed." },
        { id: "trk", x: 960, y: R1, label: "Follow each one", sub: "one car = one ID", detail: "Tracking keeps one identity per vehicle across frames." },
        { id: "plt", x: 1320, y: R1, label: "Find its plate", sub: "inside the vehicle", detail: "The plate detector looks only inside each vehicle's box." },
        { id: "best", x: 1680, y: R1, label: "Keep the best", sub: "best frames combined", detail: "Every crop of the plate is scored; the best are kept and combined." },
        { id: "q", x: 1680, y: R2, label: "Enough pixels?", sub: "22 px wide, sharp", detail: "Too small or too blurred: Vigentra does not read it." },
        { id: "read", x: 1320, y: R2, label: "Read", sub: "character by character", detail: "Readers turn the crop into characters, each with a confidence." },
        { id: "vote", x: 960, y: R2, label: "Do they agree?", sub: "many frames · plate rules", detail: "Readings from many frames must agree, and the plate must fit an Indian format." },
        { id: "ok", x: 600, y: R2, label: "Confirmed plate", sub: "text · time · camera", kind: "ok", detail: "Only a confirmed plate becomes a record: text, time and camera. Never video." },
        { id: "use", x: 240, y: R2, label: "Search · Trace · Alert", sub: "in the console", kind: "io", detail: "Officers search plates, trace a vehicle across cameras and get watchlist alerts." },
        { id: "silent", x: 1680, y: 920, label: "Stay silent", sub: "no guess", kind: "bad", detail: "No record when the pixels are not there." },
        { id: "none", x: 960, y: 920, label: "No record", sub: "not sure enough", kind: "bad", detail: "If the readings don't agree, nothing is sent." },
      ];
      const chain = ["cam", "veh", "trk", "plt", "best", "q", "read", "vote", "ok", "use"];
      const edges = chain.slice(1).map((to, i) => ({ from: chain[i], to }))
        .concat([{ from: "q", to: "silent", kind: "bad" }, { from: "vote", to: "none", kind: "bad" }]);
      const chart = K.ui.flow({ nodes, edges });
      const hero = (node, el) => {
        const body = { cam: `<span class="pk-text vid">frame</span>`, plt: `<img src="assets/img/plate_1299.png" alt="">`, read: `<span class="pk-text">${E.plate}</span>`, ok: `<span class="pk-text ok">✓ ${E.plate}</span>` }[node];
        if (body) el.innerHTML = `<div class="pk-body">${body}</div>`;
      };
      ctx.el.append(
        S.title("The whole journey", "From camera to confirmed plate."),
        chart,
        h("div.flow-hint.r", null, "Click any box"),
        S.tech([`<b>Vehicles</b> ${P.vehicle}`, `<b>Tracking</b> ${P.tracker}`, `<b>Plates</b> ${P.plate}`, `<b>Confirmed when</b> ${P.confirm}`], P.src)
      );
      return [
        () => chart.reveal(ctx, { gap: 140 }),
        () => {
          ctx.in(".flow-hint");
          chart.run(ctx, [
            { path: chain, every: 2600, carry: hero },
            { path: chain.slice(0, 6).concat("silent"), every: 5200, offset: 1300, cls: "bad" },
            { path: chain.slice(0, 8).concat("none"), every: 5200, offset: 3900, cls: "warn" },
          ]);
        },
      ];
    },
  });

  /* ---------------------------------------------------------------- detect */
  K.scene({
    id: "detect", act: "How Vigentra reads", title: "Find it, follow it, find its plate", cls: "black",
    build(ctx) {
      const live = S.replay(ctx, { rate: 0.5 });
      const ribbon = S.ribbon(0);
      const line = (id, text) => h("div.h1.r.soft.shadowed", { id }, text);
      // jump to just before the evidence car enters, so its identity and plate show at once
      const toCar = () => {
        const v = live.el.querySelector("video");
        const s0 = K.TRACK && K.TRACK.sequence[0];
        if (!v || !s0) return;
        if (v.readyState) v.currentTime = s0.t - 0.3;
        else v.addEventListener("loadedmetadata", () => { v.currentTime = s0.t - 0.3; }, { once: true });
      };
      ctx.el.append(
        h("div.fill.r.slow.media-layer", null, live.el),
        h("div.fill.shade-bottom"),
        ribbon,
        h("div.cap-left", null, [line("c1", "Find every vehicle."), line("c2", "Follow each one."), line("c3", "Find its plate.")]),
        h("div.replay-note.r", null, "Real output of Vigentra's models on CAM06, frame by frame · half speed"),
        S.tech([`<b>Vehicles</b> ${P.vehicle}`, `<b>Tracking</b> ${P.tracker}`, `<b>Plates</b> ${P.plate}`], (K.TRACK && K.TRACK.models) || "")
      );
      return [
        async () => { await ctx.in(".media-layer"); await ctx.in("#c1"); ctx.in(".replay-note"); },
        async () => { ctx.out("#c1"); ribbon.set(1); toCar(); live.overlay.set({ ids: true, trail: true }); await ctx.in("#c2"); },
        async () => { ctx.out("#c2"); ribbon.set(2); toCar(); live.overlay.set({ plate: true }); await ctx.in("#c3"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- frames */
  K.scene({
    id: "frames", act: "How Vigentra reads", title: "One car, many frames",
    build(ctx) {
      const T = K.TRACK || { sequence: [] };
      const E = K.EVIDENCE || { frames: {} };
      // the approach, up to the best frames; the two later crops are the detector's mistakes
      const crops = T.sequence.filter((s) => s.file && s.frame <= 1304);
      const strip = h("div.grow", null, crops.map((s) => h("div.grow-item.r", null, [S.pixels(s.file, { scale: 1.25 }), h("div.caption.num", null, `${s.plate_px} px`)])));
      const ba = K.ui.beforeAfter(K.media.img("journey_best", { cls: "media-fit" }), K.media.img("journey_enhanced", { cls: "media-fit" }), ["One frame", `${E.frames.fused || 12} frames combined`]);
      ctx.el.append(
        S.ribbon(3),
        S.title("One car, many frames", `As it comes closer, the plate grows: <span class="c-plate num">${crops.length ? crops[0].plate_px : "?"} → ${crops.length ? crops[crops.length - 1].plate_px : "?"} px</span>.`),
        S.at(140, 380, 1640, 420, strip),
        S.at(360, 330, 1200, 430, h("div.r.ba-wrap", { id: "ba" }, ba)),
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "best" }, "Vigentra keeps the best frames and combines them. It never invents detail.")),
        S.tech([`<b>Crop bank</b> ${P.crops}`, "<b>Crops</b> the deployed plate detector's output, frames 1244–1304"], E.source || "")
      );
      return [
        () => ctx.in(".grow-item", { stagger: 260 }),
        async () => { strip.classList.add("gone"); await ctx.in("#ba"); await ctx.in("#best"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- gate */
  K.scene({
    id: "gate", act: "How Vigentra reads", title: "Is there enough to read?",
    build(ctx) {
      const E = K.EVIDENCE || { quality: {} };
      const fail = (label) => (K.FAILURES || []).find((f) => f.label === label) || {};
      const tiny = fail("Tiny plate");
      const faint = fail("Low contrast");
      const cards = [
        { file: "assets/img/journey_best.png", scale: 2, name: `CAM06 · ${E.quality.width_px} px wide`, stamp: "READ", ok: true },
        { file: tiny.file, scale: 14, name: `Delhi · ${tiny.width_px} px wide`, stamp: "TOO SMALL", ok: false },
        { file: faint.file, scale: 6, name: `CAM06 · ${faint.width_px} px wide`, stamp: "TOO BLURRED", ok: false },
      ];
      ctx.el.append(
        S.ribbon(4),
        S.title("The quality check", "Is there enough here to read?"),
        S.at(140, 330, 1640, 470, h("div.gate-cards", null, cards.map((c, i) => h("div.gcard.r.zoom", { "data-i": i }, [
          h("div.gcard-img", null, c.file ? S.pixels(c.file, { scale: c.scale }) : h("div.missing", null, "[REAL CROP REQUIRED]")),
          h("div.caption", null, c.name),
          h("div.stamp" + (c.ok ? ".ok" : ".bad"), null, c.stamp),
        ])))),
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "silent" }, "If the pixels aren't there, Vigentra doesn't guess.")),
        S.tech([`<b>Gate</b> width ≥ ${P.gate.width} px, height ≥ ${P.gate.height} px, sharpness ≥ ${P.gate.sharpness}, contrast ≥ ${P.gate.contrast}`,
          `<b>Refusals shown</b> ${tiny.reason || ""} · ${faint.reason || ""}`], P.src)
      );
      const stamp = async (i) => { await ctx.in(`.gcard[data-i="${i}"]`); await ctx.wait(350); ctx.$(`.gcard[data-i="${i}"] .stamp`).classList.add("in"); };
      return [() => stamp(0), () => stamp(1), async () => { await stamp(2); await ctx.in("#silent", { delay: 600 }); }];
    },
  });

  /* ---------------------------------------------------------------- read */
  K.scene({
    id: "read", act: "How Vigentra reads", title: "From pixels to characters",
    build(ctx) {
      const E = K.EVIDENCE || { plate: "", per_char_conf: [] };
      const plate = K.ui.plate(E.plate, { size: 1.05, hidden: true });
      const confs = h("div.charconf", null, [...E.plate].map((ch, i) => {
        const c = E.per_char_conf[i] || 0;
        return h("div.cc", null, [h("div.cc-bar", { style: { "--v": `${Math.round(c * 100)}%` } }), h("div.cc-v.num", null, c.toFixed(2))]);
      }));
      const seg = ([txt, label]) => h("div.seg.r", null, [h("div.seg-txt.mono", null, txt), h("div.seg-label", null, label)]);
      ctx.el.append(
        S.ribbon(5),
        S.title("Reading", "From pixels to characters."),
        S.at(560, 280, 800, 180, h("div.frame.r.scan", { id: "src" }, K.media.img("journey_enhanced", { cls: "media-fit" }))),
        h("div.center.ocr-plate", null, plate),
        S.at(560, 770, 800, null, h("div.r", { id: "confs" }, [confs, h("div.caption", null, "How sure Vigentra is of each character")])),
        h("div.center.segs-wrap.r", { id: "segs" }, h("div.stack", { style: { alignItems: "center", gap: "40px" } }, [
          h("div.segs", null, D.grammar.segments.map(seg)),
          h("div.lede", null, "Indian plates follow a pattern. A 6 where a letter must be is read as G."),
        ])),
        S.tech([`<b>Readers</b> ${P.readers}`], P.src)
      );
      return [
        async () => { await ctx.in("#src"); ctx.$("#src").classList.add("sweep"); await ctx.wait(500); await K.ui.revealPlate(ctx, plate, 220); },
        async () => { await ctx.in("#confs"); ctx.$(".charconf").classList.add("in"); },
        async () => { ["#src", "#confs"].forEach((s) => ctx.$(s).closest(".abs").classList.add("gone")); ctx.$(".ocr-plate").classList.add("gone"); await ctx.in("#segs"); await ctx.in(".seg", { stagger: 300 }); },
      ];
    },
  });

  /* ---------------------------------------------------------------- vote */
  K.scene({
    id: "vote", act: "How Vigentra reads", title: "Many readings, one answer",
    build(ctx) {
      const E = K.EVIDENCE || { top_readings: [], plate: "" };
      const total = E.hypotheses_total || 0;
      const top = E.top_readings || [];
      const win = top.find((r) => r.text === E.plate) || { count: 0 };
      // one chip for every two readings of the top ones (in proportion), scattered, then pulled in
      const chips = [];
      top.forEach((r) => { for (let i = 0; i < Math.max(1, Math.round(r.count / 2)); i += 1) chips.push(r.text); });
      let seed = 7;
      const rnd = () => ((seed = (seed * 9301 + 49297) % 233280) / 233280);
      const cloud = h("div.cloud", null, chips.map((t) => h("span.chip.mono" + (t === E.plate ? ".win" : ""), {
        style: { left: `${6 + rnd() * 82}%`, top: `${8 + rnd() * 78}%`, "--d": `${rnd() * 900}ms` },
      }, t)));
      const checks = [`${E.frames ? E.frames.agreeing : "?"} frames agree`, "A valid Indian plate", "Real character shapes"];
      ctx.el.append(
        S.ribbon(6),
        S.title("The vote", `<span class="num">${total}</span> readings of one plate. Which one is true?`),
        S.at(140, 300, 1640, 560, cloud),
        h("div.center.verdict-wrap", null, h("div.stack", { style: { alignItems: "center", gap: "26px" } }, [
          h("div.h2.r.soft", { id: "said" }, [h("span.num.c-ok", null, String(win.count)), ` of ${total} say`]),
          h("div.r.zoom", { id: "winner" }, K.ui.plate(E.plate, { size: 0.9 })),
          h("div.stamp.ok.big", { id: "conf" }, "CONFIRMED"),
          h("div.ticks", null, checks.map((c) => h("span.tk.r", null, [h("b", null, "✓"), c]))),
        ])),
        S.tech([`<b>Confirmed when</b> ${P.confirm}`, `<b>Runner-up</b> ${E.alternates && E.alternates[0] ? `${E.alternates[0].plate} at ${E.alternates[0].confidence}` : ""}`], E.source || "")
      );
      return [
        async () => { cloud.classList.add("in"); },
        async () => {
          cloud.classList.add("pull");
          await ctx.wait(1100);
          await ctx.in("#said");
          await ctx.in("#winner");
          ctx.$("#conf").classList.add("in");
        },
        () => ctx.in(".tk", { stagger: 300 }),
      ];
    },
  });
})();
