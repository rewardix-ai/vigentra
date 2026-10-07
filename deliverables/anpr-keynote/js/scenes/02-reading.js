/* How Vigentra reads a plate: the signal chain as a live schematic, then each step on one real
 * car (GJ23H1546, CAM06): the deployed models' replay with its event log, the evidence buffer,
 * the quality check, reading, and the vote that saves the record. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const D = K.DATA;
  const P = D.pipeline;

  /* ---------------------------------------------------------------- flow */
  K.scene({
    id: "flow", act: "How Vigentra reads", title: "The signal chain", src: "Schematic · Vigentra ANPR · services/edge-worker",
    build(ctx) {
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
      const log = S.log({ max: 2 });
      log.add("··", `<b>Follow the square</b> one real car, ${E.plate}. Red: plates Vigentra would not guess.`);
      const chart = K.ui.flow({ nodes, edges, caption: log });
      const main = ["cam", "car", "plate", "best", "q", "read", "agree", "ok", "use"];
      ctx.el.append(
        S.title("Signal chain", "How Vigentra reads a plate."),
        S.panel(1040, 68, 840, 160, "Event log", log, { id: "lp", right: S.rec() }),
        chart,
        S.tech([`<b>Vehicles</b> ${P.vehicle}`, `<b>Tracking</b> ${P.tracker}`, `<b>Plates</b> ${P.plate}`, `<b>Confirmed when</b> ${P.confirm}`], P.src)
      );
      return [
        async () => {
          ctx.auto(23);
          ctx.in("#lp");
          await chart.reveal(ctx, { gap: 140 });
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
    id: "detect", act: "How Vigentra reads", title: "Find it, follow it, find its plate", src: "SRC CAM06 1080p · frames 1190–1335 · deployed models · half speed",
    build(ctx) {
      const T = K.TRACK || { replay: [], sequence: [], types: {} };
      const type = (id) => (T.types && T.types[id]) || "vehicle";
      // the event log: what the models saw, frame by frame (K.TRACK, from tools/track_evidence.py)
      const events = [];
      const first = {};
      T.replay.forEach((fr) => fr.boxes.forEach(([id]) => { if (!(id in first)) first[id] = fr.f; }));
      Object.entries(first).forEach(([id, f]) => events.push({ f, lvl: 0, tone: "", html: (lvl) => (lvl ? `<b>${type(id)} · ID ${id}</b> tracking` : `<b>${type(id)}</b> found`) }));
      const withPlate = T.sequence.filter((s) => s.plate);
      if (withPlate.length) {
        const p0 = withPlate[0];
        events.push({ f: p0.frame, lvl: 2, tone: "plate", html: () => `<b>plate</b> in ID ${T.track_id} · ${p0.plate_px} px` });
        [100, 120].forEach((px) => {
          const s = withPlate.find((x) => x.plate_px >= px);
          if (s) events.push({ f: s.frame, lvl: 2, tone: "plate", html: () => `plate grows · <span class="v">${s.plate_px} px</span>` });
        });
        const best = withPlate.find((x) => x.frame === 1299);
        if (best) events.push({ f: 1299, lvl: 2, tone: "ok", html: () => `<b>best frame</b> · ${best.plate_px} px` });
      }
      if (T.frames) events.push({ f: T.frames[1], lvl: 1, tone: "", html: () => `<b>ID ${T.track_id}</b> leaves · ${T.seen} frames, plate in ${T.with_plate}` });
      events.sort((a, b) => a.f - b.f);
      const log = S.log({ max: 14 });
      const frameOsd = h("span.num", null, "F----");
      let level = 0;
      let lastF = -1;
      const printed = new Set();
      const onFrame = (f) => {
        frameOsd.textContent = `F${f}`;
        if (f < lastF) { log.clear(); printed.clear(); }
        lastF = f;
        events.forEach((e, i) => {
          if (e.f <= f && e.lvl <= level && !printed.has(i)) { printed.add(i); log.add(`F${e.f}`, e.html(level), e.tone); }
        });
      };
      const live = S.replay(ctx, { rate: 0.5, onFrame });
      const toCar = () => {
        const v = live.el.querySelector("video");
        const s0 = T.sequence[0];
        lastF = 1e9; // the next frame clears the log and replays it at the new level
        if (!v || !s0) return;
        if (v.readyState) v.currentTime = s0.t - 0.3;
        else v.addEventListener("loadedmetadata", () => { v.currentTime = s0.t - 0.3; }, { once: true });
      };
      ctx.el.append(
        S.panel(40, 70, 1340, 794, "CAM06 · replay of the deployed models", S.monitor(live.el, { bl: frameOsd, br: "½ speed" }), { id: "vp", right: S.rec() }),
        S.panel(1400, 70, 480, 794, "Event log", log, { id: "lg" }),
        S.banner("Step 1 of 3", "Find every vehicle.", { id: "b1" }),
        S.banner("Step 2 of 3", "Follow each one: one car, one ID.", { id: "b2" }),
        S.banner("Step 3 of 3", "Find its plate, inside the car.", { id: "b3" }),
        S.tech([`<b>Vehicles</b> ${P.vehicle}`, `<b>Tracking</b> ${P.tracker}`, `<b>Plates</b> ${P.plate}`, "<b>Type</b> one per vehicle: the class of its most confident detection over its track (this car: car 0.91; frame by frame the model also said truck, at most 0.84)"], T.models || "")
      );
      return [
        async () => { ctx.auto(12.5); ctx.in("#vp"); await ctx.in("#lg"); await S.show(ctx, "#b1"); },
        async () => { ctx.auto(8.5); S.hide(ctx, "#b1"); level = 1; toCar(); live.overlay.set({ ids: true, trail: true }); await S.show(ctx, "#b2"); },
        async () => { ctx.auto(8.5); S.hide(ctx, "#b2"); level = 2; toCar(); live.overlay.set({ plate: true }); await S.show(ctx, "#b3"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- frames */
  K.scene({
    id: "frames", act: "How Vigentra reads", title: "One car, many frames", src: "SRC CAM06 · plate crops from the deployed detector · ID 3",
    build(ctx) {
      const T = K.TRACK || { sequence: [] };
      const E = K.EVIDENCE || { frames: {} };
      // the approach, where the plate detector was sure (the last pick is the dashboard, conf 0.37)
      const crops = T.sequence.filter((s) => s.file && s.plate_conf >= 0.5);
      const strip = h("div.grow", null, crops.map((s) => h("div.grow-item", null, [S.pixels(s.file, { scale: 1.25 }), h("span.gi-px.num", null, `${s.plate_px} px`), h("span.gi-f.num", null, `F${s.frame}`)])));
      const ba = K.ui.beforeAfter(K.media.img("journey_best", { cls: "media-fit" }), K.media.img("journey_enhanced", { cls: "media-fit" }), ["One frame", `${E.frames.fused || 12} frames, combined and cleaned`]);
      ctx.el.append(
        S.title("Step 4 · Best frames", `One car, many frames: <span class="c-plate num">${crops.length ? crops[0].plate_px : "?"} → ${crops.length ? crops[crops.length - 1].plate_px : "?"} px</span>.`),
        S.panel(80, 230, 1760, 440, `Evidence buffer · ID ${T.track_id}`, strip, { id: "buf", right: `${crops.length} crops shown` }),
        S.panel(360, 230, 1200, 560, "Fusion", h("div.fill.ba-wrap", null, ba), { id: "fus", right: "drag to compare" }),
        S.banner("Vigentra", "It keeps the best frames and combines them. It never invents detail.", { id: "b1", tone: "ok" }),
        S.tech([`<b>Crop bank</b> ${P.crops}`, "<b>Crops</b> the deployed plate detector's output where its confidence was at least 0.5"], E.source || "")
      );
      return [
        async () => { await ctx.in("#buf"); await ctx.wait(300); for (const g of ctx.$$(".grow-item")) { g.classList.add("in"); await ctx.wait(220); } },
        async () => { ctx.$("#buf").classList.add("gone"); await ctx.in("#fus"); await S.show(ctx, "#b1"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- gate */
  K.scene({
    id: "gate", act: "How Vigentra reads", title: "Is there enough to read?", src: "SRC CAM06 · Delhi · real crops and what Vigentra did with them",
    build(ctx) {
      const E = K.EVIDENCE || { quality: {} };
      const fail = (label) => (K.FAILURES || []).find((f) => f.label === label) || {};
      const tiny = fail("Tiny plate");
      const blurred = fail("Blurred plate");
      const cards = [
        { file: "assets/img/journey_best.png", scale: 2, title: `Sample 01 · CAM06 · ${E.quality.width_px} px`, note: "sharp, wide enough", stamp: "Read", ok: true },
        { file: tiny.file, scale: 14, title: `Sample 02 · Delhi · ${tiny.width_px} px`, note: "under 22 px: not even tried", stamp: "Too small", ok: false },
        { file: blurred.file, scale: 6, title: `Sample 03 · Delhi · ${blurred.width_px} px`, note: "blurred: the readings disagreed", stamp: "Not saved", ok: false },
      ];
      ctx.el.append(
        S.title("Step 5 · Quality check", "Is there enough here to read?"),
        ...cards.map((c, i) => S.panel(80 + i * 600, 230, 560, 540, c.title, h("div.sample", null, [
          h("div.sample-img", null, c.file ? S.pixels(c.file, { scale: c.scale }) : h("div.missing", null, "[REAL CROP REQUIRED]")),
          h("div.sample-foot", null, [h("span.stamp" + (c.ok ? ".ok" : ".bad"), null, c.stamp), h("span.sample-note", null, c.note)]),
        ]), { id: `s${i}` })),
        S.banner("Vigentra", "If the pixels aren't there, it doesn't guess.", { id: "b1" }),
        S.tech([`<b>Gate</b> width ≥ ${P.gate.width} px, height ≥ ${P.gate.height} px, sharpness ≥ ${P.gate.sharpness}, contrast ≥ ${P.gate.contrast}`,
          `<b>Outcomes shown</b> ${tiny.reason || ""} (refused before reading) · ${blurred.reason || ""} (read, never confirmed)`], P.src)
      );
      const stamp = async (i) => { await ctx.in(`#s${i}`); await ctx.wait(450); ctx.$(`#s${i} .stamp`).classList.add("in"); };
      return [() => stamp(0), () => stamp(1), async () => { await stamp(2); await ctx.wait(500); await S.show(ctx, "#b1"); }];
    },
  });

  /* ---------------------------------------------------------------- read */
  K.scene({
    id: "read", act: "How Vigentra reads", title: "From pixels to characters", src: "SRC evidence pack cam06_s0_t132 · GJ23H1546",
    build(ctx) {
      const E = K.EVIDENCE || { plate: "", per_char_conf: [] };
      const cols = h("div.ocr-cols", null, [...E.plate].map((ch, i) => {
        const c = E.per_char_conf[i] || 0;
        return h("div.oc", null, [h("span.ch", null, ch), h("div.oc-barbox", null, h("div.oc-bar", { style: { "--v": `${Math.round(c * 100)}%` } })), h("div.oc-v.num", null, c.toFixed(2))]);
      }));
      const seg = ([txt, label]) => h("div.seg", null, [h("div.seg-txt.mono", null, txt), h("div.seg-label", null, label)]);
      ctx.el.append(
        S.title("Step 6 · Reading", "From pixels to characters."),
        S.panel(260, 230, 1400, 640, "OCR · combined crop", h("div.ocr", null, [
          h("div.ocr-src", null, K.media.img("journey_enhanced", { cls: "media-fit" })),
          cols,
          h("div.ocr-cap.r", { id: "cap" }, "How sure Vigentra is of each character"),
        ]), { id: "op" }),
        S.panel(260, 230, 1400, 640, "Plate format · India", h("div.fmt", null, [
          h("div.segs", null, D.grammar.segments.map(seg)),
          h("div.fmt-note", null, "A 6 where a letter must be is read as G: the format catches mistakes."),
        ]), { id: "fp" }),
        S.tech([`<b>Readers</b> ${P.readers}`], P.src)
      );
      return [
        async () => { await ctx.in("#op"); await ctx.wait(500); await K.ui.revealChars(ctx, cols, 200, { scramble: true }); },
        async () => { cols.classList.add("conf"); await ctx.in("#cap"); },
        async () => { ctx.$("#op").classList.add("gone"); await ctx.in("#fp"); for (const s of ctx.$$(".seg")) { s.classList.add("in"); await ctx.wait(280); } },
      ];
    },
  });

  /* ---------------------------------------------------------------- vote */
  K.scene({
    id: "vote", act: "How Vigentra reads", title: "Many readings, one answer", src: "SRC evidence pack cam06_s0_t132 · 136 readings of one plate",
    build(ctx) {
      const E = K.EVIDENCE || { top_readings: [], plate: "", frames: {} };
      const total = E.hypotheses_total || 0;
      const top = (E.top_readings || []).slice(0, 7);
      const rest = total - top.reduce((n, r) => n + r.count, 0);
      const rows = [...top.map((r) => [r.text, r.count, r.text === E.plate]), ["other readings", rest, false]];
      const max = Math.max(...rows.map((r) => r[1]));
      const tally = h("div.tally", null, rows.map(([text, n, win]) => h("div.tr" + (win ? ".win" : ""), null, [
        h("span.tr-t.mono", null, text), h("span.tr-bar", null, h("i", { style: { "--w": `${(100 * n) / max}%` } })), h("span.tr-n.num", null, String(n)),
      ])));
      const fields = [
        ["Plate", E.plate], ["Camera", (E.camera || "cam06").toUpperCase()], ["Status", "Confirmed"],
        ["Readings", `${rows[0][1]} of ${total} agree`], ["Frames", `${E.frames.agreeing} agree`],
        ["Format", D.grammar.segments.map((s) => s[0]).join(" · ")], ["Sent", "text only · no image · no video"],
      ];
      const rec = h("div.recfields", null, fields.map(([k]) => h("div.rf", null, [h("span.rf-k", null, k), h("span.rf-v")])));
      ctx.el.append(
        S.title("Step 7 · Vote", `<span class="num">${total}</span> readings. One answer.`),
        S.panel(80, 230, 1000, 640, `Readings of one plate · ${total}`, h("div.tally-wrap", null, [tally, h("span.stamp.ok.big", { id: "cf" }, "Confirmed")]), { id: "tp" }),
        S.panel(1110, 230, 730, 640, "Record saved", rec, { id: "rp", right: "→ central" }),
        S.tech([`<b>Confirmed when</b> ${P.confirm}`, `<b>Runner-up</b> ${E.alternates && E.alternates[0] ? `${E.alternates[0].plate} at ${E.alternates[0].confidence}` : ""}`], E.source || "")
      );
      return [
        async () => { await ctx.in("#tp"); await ctx.wait(300); tally.classList.add("in"); },
        async () => { tally.classList.add("decided"); await ctx.wait(500); ctx.$("#cf").classList.add("in"); },
        async () => {
          await ctx.in("#rp");
          const vals = ctx.$$(".rf-v");
          for (let i = 0; i < fields.length; i += 1) { ctx.$$(".rf")[i].classList.add("in"); await ctx.type(vals[i], fields[i][1], 50); }
        },
      ];
    },
  });
})();
