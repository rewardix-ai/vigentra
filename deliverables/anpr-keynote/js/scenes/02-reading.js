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
    id: "flow", act: "How Vigentra reads", title: "How Vigentra reads a plate",
    build(ctx) {
      const E = K.EVIDENCE || { plate: "", frames: {} };
      const fail = (label) => (K.FAILURES || []).find((f) => f.label === label) || {};
      const tiny = fail("Tiny plate");
      const blur = fail("Motion blur");
      const X = [260, 713, 1166, 1620];
      const [R1, R2, R3] = [360, 690, 940];
      const crop = (src) => () => h("img.pix-fit", { src, alt: "" });
      const nodes = [
        { id: "cam", n: 1, x: X[0], y: R1, title: "Camera", sub: "real CCTV video", media: () => K.media.video("cam06_1080p", { start: 49.6, end: 54.05, cls: "media-cover" }),
          say: "Real CCTV video, frame after frame. Vigentra takes more frames when a vehicle is close enough to read." },
        { id: "car", n: 2, x: X[1], y: R1, title: "Track the car", sub: "boxed, then given one ID", media: () => S.carThumb(),
          say: "In the frames Vigentra checks, every vehicle is boxed, and tracking gives each car one ID, so one car makes one record." },
        { id: "plate", n: 3, x: X[2], y: R1, title: "Find its plate", sub: "searched only inside the car", media: crop("assets/img/plate_1299.png"),
          say: "The plate detector looks only inside the car's box." },
        { id: "best", n: 4, x: X[3], y: R1, title: "Best frames", sub: `${E.frames.fused || 12} combined into one clearer image`, media: crop("assets/img/journey_fused.png"),
          say: "Every crop of the plate is scored; the best ones are lined up and combined into one clearer image." },
        { id: "q", type: "decision", x: X[0], y: R2, w: 260, h: 180, title: "Enough pixels?", sub: "≥ 22 px wide, sharp",
          say: "Too small or too blurred? Then Vigentra does not try to read it." },
        { id: "read", n: 5, x: X[1], y: R2, title: "Read the plate", sub: "character by character", media: () => h("div.fc-text", null, E.plate),
          say: "The readers turn the image into characters, each with its own confidence." },
        { id: "agree", type: "decision", x: X[2], y: R2, w: 260, h: 180, title: "Readings agree?", sub: "many frames · valid format",
          say: "Readings from many frames must agree, and the plate must fit an Indian format. If not, no plate is saved." },
        { id: "ok", n: 6, x: X[3], y: R2, tone: "ok", title: "Confirmed plate", sub: "plate · time · camera, never video", media: () => h("div.fc-text.c-ok", null, `✓ ${E.plate}`),
          say: "Only now is the plate saved: the plate, the time and the camera. Never video." },
        { id: "silent", type: "end", tone: "bad", x: X[0], y: R3, w: 340, h: 120, title: "Not read", sub: `too small: this crop is ${tiny.width_px || "?"} px`, media: crop(tiny.file),
          say: "Below 22 pixels, or too blurred, Vigentra does not even try. No guess is made." },
        { id: "none", type: "end", tone: "bad", x: X[2], y: R3, w: 340, h: 120, title: "No plate saved", sub: "not sure enough, like this one", media: crop(blur.file),
          say: "The readings did not agree well enough, so no plate number is saved: nothing to search, no alert, no wrong record." },
        { id: "use", type: "end", tone: "io", x: X[3], y: R3, w: 340, h: 120, title: "Search · Trace · Alert", sub: "in the Vigentra console",
          say: "Officers search it, trace the car across cameras, and get an alert if it is on a watchlist." },
      ];
      const edges = [
        { from: "cam", to: "car" }, { from: "car", to: "plate" }, { from: "plate", to: "best" },
        { from: "best", to: "q", out: "bottom", in: "top", via: [[X[3], 530], [X[0], 530]] },
        { from: "q", to: "read", kind: "yes", label: "Yes", at: [X[0] + 150, R2 - 14] },
        { from: "q", to: "silent", out: "bottom", in: "top", kind: "no", label: "No", at: [X[0] + 14, R2 + 130] },
        { from: "read", to: "agree" },
        { from: "agree", to: "ok", kind: "yes", label: "Yes", at: [X[2] + 150, R2 - 14] },
        { from: "agree", to: "none", out: "bottom", in: "top", kind: "no", label: "No", at: [X[2] + 14, R2 + 130] },
        { from: "ok", to: "use", out: "bottom", in: "top" },
      ];
      const caption = h("div.flow-say", null, [h("span.fs-n.blank"), h("div", null, [h("b", null, `Follow the dot: one real car, ${E.plate}`), h("span", null, "Red dots are plates Vigentra would not guess. Click any box to read what it does.")])]);
      const chart = K.ui.flow({ nodes, edges, caption });
      const main = ["cam", "car", "plate", "best", "q", "read", "agree", "ok", "use"];
      ctx.el.append(
        S.title("The whole journey", "How Vigentra reads a plate."),
        S.at(1060, 92, 760, null, h("div.r", { id: "say" }, caption)),
        chart,
        S.tech([`<b>Vehicles</b> ${P.vehicle}`, `<b>Tracking</b> ${P.tracker}`, `<b>Plates</b> ${P.plate}`, `<b>Confirmed when</b> ${P.confirm}`], P.src)
      );
      return [
        async () => {
          ctx.auto(23);
          await chart.reveal(ctx, { gap: 150 });
          await ctx.in("#say");
          chart.run(ctx, [
            { path: main, every: 21000, main: true },
            { path: main.slice(0, 5).concat("silent"), every: 21000, offset: 6000, cls: "bad" },
            { path: main.slice(0, 7).concat("none"), every: 21000, offset: 13000, cls: "bad" },
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
        S.tech([`<b>Vehicles</b> ${P.vehicle}`, `<b>Tracking</b> ${P.tracker}`, `<b>Plates</b> ${P.plate}`, "<b>Type</b> one per vehicle: the class of its most confident detection over its track (this car: car 0.91; frame by frame the model also said truck, at most 0.84)"], (K.TRACK && K.TRACK.models) || "")
      );
      return [
        async () => { ctx.auto(12.5); await ctx.in(".media-layer"); await ctx.in("#c1"); ctx.in(".replay-note"); },
        async () => { ctx.auto(8.5); ctx.out("#c1"); ribbon.set(1); toCar(); live.overlay.set({ ids: true, trail: true }); await ctx.in("#c2"); },
        async () => { ctx.auto(8.5); ctx.out("#c2"); ribbon.set(2); toCar(); live.overlay.set({ plate: true }); await ctx.in("#c3"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- frames */
  K.scene({
    id: "frames", act: "How Vigentra reads", title: "One car, many frames",
    build(ctx) {
      const T = K.TRACK || { sequence: [] };
      const E = K.EVIDENCE || { frames: {} };
      // the approach, where the plate detector was sure (the last pick, conf 0.37, is the
      // dashboard after the car has passed)
      const crops = T.sequence.filter((s) => s.file && s.plate_conf >= 0.5);
      const strip = h("div.grow", null, crops.map((s) => h("div.grow-item.r", null, [S.pixels(s.file, { scale: 1.25 }), h("div.caption.num", null, `${s.plate_px} px`)])));
      const ba = K.ui.beforeAfter(K.media.img("journey_best", { cls: "media-fit" }), K.media.img("journey_enhanced", { cls: "media-fit" }), ["One frame", `${E.frames.fused || 12} frames, combined and cleaned`]);
      ctx.el.append(
        S.ribbon(3),
        S.title("One car, many frames", `As it comes closer, the plate grows: <span class="c-plate num">${crops.length ? crops[0].plate_px : "?"} → ${crops.length ? crops[crops.length - 1].plate_px : "?"} px</span>.`),
        S.at(140, 380, 1640, 420, strip),
        S.at(360, 330, 1200, 430, h("div.r.ba-wrap", { id: "ba" }, ba)),
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "best" }, "Vigentra keeps the best frames and combines them. It never invents detail.")),
        S.tech([`<b>Crop bank</b> ${P.crops}`, "<b>Crops</b> the deployed plate detector's output where its confidence was at least 0.5"], E.source || "")
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
      const blurred = fail("Blurred plate");
      const cards = [
        { file: "assets/img/journey_best.png", scale: 2, name: `CAM06 · ${E.quality.width_px} px wide`, stamp: "READ", ok: true },
        { file: tiny.file, scale: 14, name: `Delhi · ${tiny.width_px} px wide`, stamp: "TOO SMALL", ok: false },
        { file: blurred.file, scale: 6, name: `Delhi · ${blurred.width_px} px wide · blurred: readings disagreed`, stamp: "NOT SAVED", ok: false },
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
          `<b>Outcomes shown</b> ${tiny.reason || ""} (refused before reading) · ${blurred.reason || ""} (read, never confirmed)`], P.src)
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
      const cols = h("div.ocr-cols", null, [...E.plate].map((ch, i) => {
        const c = E.per_char_conf[i] || 0;
        return h("div.oc", null, [h("span.ch", null, ch), h("div.oc-barbox", null, h("div.oc-bar", { style: { "--v": `${Math.round(c * 100)}%` } })), h("div.oc-v.num", null, c.toFixed(2))]);
      }));
      const seg = ([txt, label]) => h("div.seg.r", null, [h("div.seg-txt.mono", null, txt), h("div.seg-label", null, label)]);
      ctx.el.append(
        S.ribbon(5),
        S.title("Reading", "From pixels to characters."),
        S.at(560, 270, 800, 180, h("div.frame.r", { id: "src" }, K.media.img("journey_enhanced", { cls: "media-fit" }))),
        S.at(360, 500, 1200, null, cols),
        S.at(360, 820, 1200, null, h("div.caption.r", { id: "conf-cap", style: { textAlign: "center" } }, "How sure Vigentra is of each character")),
        h("div.center.segs-wrap.r", { id: "segs" }, h("div.stack", { style: { alignItems: "center", gap: "40px" } }, [
          h("div.segs", null, D.grammar.segments.map(seg)),
          h("div.lede", null, "Indian plates follow a pattern. A 6 where a letter must be is read as G."),
        ])),
        S.tech([`<b>Readers</b> ${P.readers}`], P.src)
      );
      return [
        async () => { await ctx.in("#src"); await ctx.wait(400); await K.ui.revealChars(ctx, cols, 200, { scramble: true }); },
        async () => { cols.classList.add("conf"); await ctx.in("#conf-cap"); },
        async () => { ["#src", "#conf-cap"].forEach((s) => ctx.$(s).closest(".abs").classList.add("gone")); cols.classList.add("gone"); await ctx.in("#segs"); await ctx.in(".seg", { stagger: 300 }); },
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
          h("div.r.zoom", { id: "winner" }, K.ui.chars(E.plate, { cls: "big" })),
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
