/* Story edition, part 1: the cameras you have, a day on the grid, one car, and how Vigentra
 * reads its plate. Same data and parts as the control-room deck; an editorial, cinematic look. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const D = K.DATA;
  const P = D.pipeline;

  // the story edition's own layouts
  const L = (K.L = {
    cine(media, { push = true, tint = "" } = {}) {
      return h("div.cine", null, [h("div.cine-media" + (push ? ".push" : ""), null, media), h("div.tint" + (tint ? "." + tint : ""))]);
    },
    copy({ time, place, title, sub = null, id = null, size = "" }) {
      return h("div.copy", { id }, [
        h("div.stamp-k", null, [h("span", null, time), h("b", null, place)]),
        h("h1.disp.r.soft" + (size ? "." + size : ""), { html: title }),
        sub ? h("p.sub", null, sub) : null,
      ]);
    },
    async reveal(ctx, copy) {
      copy.querySelector(".stamp-k").classList.add("in");
      await ctx.wait(250);
      await ctx.in(copy.querySelector(".disp"));
      const sub = copy.querySelector(".sub");
      if (sub) sub.classList.add("in");
    },
    head(kicker, title, { sm = false } = {}) {
      return h("div.lhead" + (sm ? ".sm" : ""), null, [h("div.lk", null, kicker), h("h2.ldisp.r.soft", { html: title })]);
    },
    card(x, y, w, hgt, children, { id = null, cls = "" } = {}) {
      return h("div.lcard" + (cls ? "." + cls : ""), { id, style: { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${hgt}px` } }, children);
    },
    pill: (text, tone = "line") => h(`span.pill.${tone}`, null, text),
    mantra(text, id) {
      return h("div.mantra", { id }, h("p.r.soft", { html: text }));
    },
  });

  /* ---------------------------------------------------------------- hero */
  K.scene({
    id: "hero", act: "Vigentra", title: "The cameras you already have", cls: "nightbg",
    notes: {
      say: "This is Vigentra. It turns the CCTV you already have into answers: it reads number plates from the existing cameras, and it saves only what it is sure of.",
      sees: "CAM06 full screen under a night tint: a car comes down Madhuram Bypass Road. Headline and one line of explanation.",
      transition: "10 s, then a day on the grid. Moves on by itself (press A to hold).",
    },
    build(ctx) {
      const copy = L.copy({ time: "18:00:56", place: "CAM06 · Madhuram Bypass Road", title: "Turn the cameras you already have into answers.",
        sub: "Vigentra reads number plates from existing CCTV, and saves only what it is sure of." });
      ctx.el.append(L.cine(K.media.video("cam06_1080p", { start: 44, end: 55, cls: "media-cover" })), copy);
      return [async () => { ctx.auto(10); await ctx.wait(500); await L.reveal(ctx, copy); }];
    },
  });

  /* ---------------------------------------------------------------- day */
  K.scene({
    id: "day", act: "A day on the grid", title: "A day on the grid", cls: "nightbg",
    notes: {
      say: "A day on our grid. At 11:46, in daylight, CAM06 showed 16 legible plates; Vigentra read 15, none wrong. At 18:00, the same road: one car, GJ23H1546, the one we will follow. At 21:00, the grid at night: headlights and distance win, and we say so.",
      sees: "Three real chapters with their own time stamps, and a timeline along the bottom: 11:46 CAM06 at midday, 18:00 CAM06 in the evening, 21:00 the grid at night.",
      transition: "7 s a chapter, 21 s in all. Moves on by itself (press A to hold).",
    },
    build(ctx) {
      const T = K.TRACK || {};
      const chapters = [
        ["cam06_noon", 30, "11:46", "CAM06 at midday", "In daylight, the plates are there to read.", `CAM06 showed ${D.final.cam06Noon[1]} legible plates. Vigentra read ${D.final.cam06Noon[0]}, none wrong.`],
        ["cam06_1080p", 46, "18:00", "CAM06 in the evening", "One car. One plate.", `Seen in ${T.seen || "?"} frames, its plate found in ${T.with_plate || "?"}. This is the car we'll follow.`],
        ["reel_cam15", 0, "21:00", "Grid cam15 · Suvidhapark", "After dark, headlights and distance win.", "Plates are still found at night, but rarely legible. We say so."],
      ].map(([slot, start, time, place, title, sub], i) => h("div.chapter", { "data-i": i }, [
        L.cine(K.media.video(slot, { start, cls: "media-cover" })),
        L.copy({ time, place, title, sub }),
      ]));
      const line = h("div.timeline", null, [["11:46", "CAM06 at midday"], ["18:00", "CAM06 in the evening"], ["21:00", "The grid at night"]].map(([t, l]) => h("span", null, [h("b", null, t), l])));
      ctx.el.append(...chapters, line);
      const show = async (i) => {
        ctx.auto(7);
        chapters.forEach((c, j) => c.classList.toggle("on", i === j));
        line.querySelectorAll("span").forEach((s, j) => s.classList.toggle("on", i === j));
        await ctx.wait(400);
        await ctx.in(chapters[i].querySelector(".disp"));
      };
      return [() => show(0), () => show(1), () => show(2)];
    },
  });

  /* ---------------------------------------------------------------- watch */
  K.scene({
    id: "watch", act: "The problem", title: "Nobody can watch them all", cls: "nightbg",
    notes: {
      say: "These are recordings from our grid cameras: thirty on the grid alone. Nobody can watch them all. Vigentra turns this video into plates you can search.",
      sees: "A wall of sixteen real feeds under the night tint. Then the line, word by word, in lavender.",
      transition: "12 s. Moves on by itself (press A to hold).",
    },
    build(ctx) {
      const feeds = ["01", "02", "04", "05", "06", "07", "08", "10", "11", "12", "13", "14", "15", "16"]
        .map((n) => ({ label: `cam${n}`, media: () => K.media.video(`wall_cam${n}`, { cls: "media-cover" }) }))
        .concat([{ label: "cam06 · 480p", media: () => K.media.video("cam06_noon", { start: 20, cls: "media-cover" }) }, { label: "Delhi", media: () => K.media.video("delhi_raw", { cls: "media-cover" }) }]);
      const wall = h("div.fill", { style: { padding: "96px 64px 64px" } }, K.ui.wall(feeds, { columns: 4 }));
      const copy = L.copy({ time: "The grid", place: `${D.grid.cameras} cameras`, title: "Nobody can watch them all." });
      ctx.el.append(L.cine(wall, { push: false }), copy, L.mantra("Vigentra turns this video into plates you can search.", "m1"));
      return [
        async () => { ctx.auto(7); ctx.$$(".wall-tile").forEach((t) => t.classList.add("in")); await ctx.wait(600); await L.reveal(ctx, copy); },
        async () => { ctx.auto(5); copy.classList.add("gone"); ctx.$(".cine").classList.add("dimmed"); await ctx.in("#m1 p"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- chain */
  K.scene({
    id: "chain", act: "How it works", title: "How Vigentra reads a plate", cls: "paper light",
    notes: {
      say: "Here is how it works, on one real car. Camera; track the car; find its plate; combine its best frames. Enough pixels? If not, it is not read. Read the plate. Do the readings agree? If not, no plate is saved. Only then a confirmed plate: plate, time, camera. Never video.",
      sees: "The signal chain on paper: six numbered stages showing the real data at each step, two Yes/No decisions, outcomes with real refused plates. A dot follows one car; the card at the top right says what each stage does.",
      transition: "23 s, one full trip of the dot. Moves on by itself (press A to hold).",
    },
    build(ctx) {
      const C = K.CHARTS.reading();
      const log = S.log({ max: 2 });
      log.add("··", C.intro);
      const chart = K.ui.flow({ nodes: C.nodes, edges: C.edges, caption: log });
      ctx.el.append(L.head("How it works", "How Vigentra reads a plate.", { sm: true }), L.card(1040, 64, 840, 160, [log], { id: "lc" }), chart,
        S.tech([`<b>Vehicles</b> ${P.vehicle}`, `<b>Tracking</b> ${P.tracker}`, `<b>Plates</b> ${P.plate}`, `<b>Confirmed when</b> ${P.confirm}`], P.src));
      return [async () => { ctx.auto(23); ctx.in("#lc"); await chart.reveal(ctx, { gap: 140 }); chart.run(ctx, C.routes); }];
    },
  });

  /* ---------------------------------------------------------------- replay */
  K.scene({
    id: "replay", act: "How it works", title: "Find it, follow it, find its plate", cls: "nightbg",
    notes: {
      say: "Watch it work. First, find every vehicle, and what kind it is. Then follow each one: one car, one ID. Then, inside that car, find the plate. The card on the right is the system telling you what it saw, frame by frame.",
      sees: "CAM06 at half speed with the deployed models' real boxes (car, motorcycle; then ID and trail; then the plate), and an event log printing, frame by frame, what they saw.",
      transition: "29.5 s: vehicles 12.5, tracking 8.5, plate 8.5. Moves on by itself (press A to hold).",
    },
    build(ctx) {
      const T = K.TRACK || { replay: [], sequence: [], types: {} };
      const type = (id) => (T.types && T.types[id]) || "vehicle";
      const events = [];
      const first = {};
      T.replay.forEach((fr) => fr.boxes.forEach(([id]) => { if (!(id in first)) first[id] = fr.f; }));
      Object.entries(first).forEach(([id, f]) => events.push({ f, lvl: 0, tone: "", html: (lvl) => (lvl ? `<b>${type(id)} · ID ${id}</b> tracking` : `<b>${type(id)}</b> found`) }));
      const withPlate = T.sequence.filter((s) => s.plate);
      if (withPlate.length) {
        events.push({ f: withPlate[0].frame, lvl: 2, tone: "plate", html: () => `<b>plate</b> in ID ${T.track_id} · ${withPlate[0].plate_px} px` });
        const best = withPlate.find((x) => x.frame === 1299);
        if (best) events.push({ f: 1299, lvl: 2, tone: "ok", html: () => `<b>best frame</b> · ${best.plate_px} px` });
      }
      if (T.frames) events.push({ f: T.frames[1], lvl: 1, tone: "", html: () => `<b>ID ${T.track_id}</b> leaves · ${T.seen} frames` });
      events.sort((a, b) => a.f - b.f);
      const log = S.log({ max: 9 });
      const fnum = h("span", null, "F----");
      let level = 0;
      let lastF = -1;
      const printed = new Set();
      const live = S.replay(ctx, {
        rate: 0.5,
        onFrame(f) {
          fnum.textContent = `F${f}`;
          if (f < lastF) { log.clear(); printed.clear(); }
          lastF = f;
          events.forEach((e, i) => { if (e.f <= f && e.lvl <= level && !printed.has(i)) { printed.add(i); log.add(`F${e.f}`, e.html(level), e.tone); } });
        },
      });
      const toCar = () => {
        const v = live.el.querySelector("video");
        const s0 = T.sequence[0];
        lastF = 1e9;
        if (!v || !s0) return;
        if (v.readyState) v.currentTime = s0.t - 0.3;
        else v.addEventListener("loadedmetadata", () => { v.currentTime = s0.t - 0.3; }, { once: true });
      };
      const lines = ["Find every vehicle.", "Follow each one.", "Find its plate."];
      const copies = lines.map((t, i) => {
        const c = L.copy({ time: "", place: `Step ${i + 1} of 3 · CAM06, half speed`, title: t, size: "md" });
        c.querySelector(".stamp-k span").replaceWith(i === 0 ? fnum : h("span", null, `0${i + 1}`));
        return c;
      });
      ctx.el.append(L.cine(live.el, { push: false, tint: "light" }), ...copies, L.card(1380, 120, 470, 560, [h("div.cap", null, "Event log · what the models saw"), log], { id: "lg", cls: "glass" }),
        S.tech([`<b>Vehicles</b> ${P.vehicle}`, `<b>Tracking</b> ${P.tracker}`, `<b>Plates</b> ${P.plate}`, "<b>Type</b> one per vehicle: the class of its most confident detection over its track (this car: car 0.91)"], T.models || ""));
      const step = async (i) => {
        copies.forEach((c, j) => { if (j !== i) c.classList.add("gone"); });
        copies[i].classList.remove("gone");
        await L.reveal(ctx, copies[i]);
      };
      return [
        async () => { ctx.auto(12.5); ctx.in("#lg"); await step(0); },
        async () => { ctx.auto(8.5); level = 1; toCar(); live.overlay.set({ ids: true, trail: true }); await step(1); },
        async () => { ctx.auto(8.5); level = 2; toCar(); live.overlay.set({ plate: true }); await step(2); },
      ];
    },
  });

  /* ---------------------------------------------------------------- evidence */
  K.scene({
    id: "evidence", act: "How it works", title: "One car, many frames", cls: "paper light",
    notes: {
      say: "Because we follow the car, we see its plate many times, bigger as it comes closer. Vigentra keeps the best frames and combines them. Drag it: one frame, then twelve. Nothing is invented.",
      sees: "Nine real crops of the same plate growing from 67 to 138 pixels; a slider between one frame and twelve combined and cleaned.",
      transition: "You move on.",
    },
    build(ctx) {
      const T = K.TRACK || { sequence: [] };
      const E = K.EVIDENCE || { frames: {} };
      const crops = T.sequence.filter((s) => s.file && s.plate_conf >= 0.5);
      const strip = h("div.strip", null, crops.map((s) => h("div.grow-item", null, [S.pixels(s.file, { scale: 1.2 }), h("span.gi-px", null, `${s.plate_px} px`)])));
      const ba = K.ui.beforeAfter(K.media.img("journey_best", { cls: "media-fit" }), K.media.img("journey_enhanced", { cls: "media-fit" }), ["One frame", `${E.frames.fused || 12} frames, combined and cleaned`]);
      ctx.el.append(
        L.head("Evidence", "One car, many frames."),
        L.card(96, 300, 1728, 290, [h("div.cap", null, `The same plate, frame by frame · ID ${T.track_id}`), h("div.lmedia", null, strip)], { id: "c1" }),
        L.card(96, 620, 1100, 400, [h("div.lmedia", null, h("div.ba-l", null, ba))], { id: "c2" }),
        L.card(1220, 620, 604, 400, [h("h3", null, "Many frames beat one"), h("p", null, `As the car comes closer the plate grows from ${crops.length ? crops[0].plate_px : "?"} to ${crops.length ? crops[crops.length - 1].plate_px : "?"} pixels. Vigentra keeps the best frames and combines them. It never invents detail.`), h("div.pills", null, [L.pill("Real crops", "iris"), L.pill("Drag to compare")])], { id: "c3" })
      );
      return [
        async () => { await ctx.in("#c1"); for (const g of ctx.$$(".grow-item")) { g.classList.add("in"); await ctx.wait(200); } },
        async () => { await ctx.in("#c2"); await ctx.in("#c3"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- quality */
  K.scene({
    id: "quality", act: "How it works", title: "If the pixels aren't there, it doesn't guess", cls: "paper light",
    notes: {
      say: "Before we read, we ask: is there enough here? This one, yes. This one is ten pixels wide: we don't even try. This one is a plate, but blurred: the readings disagree, so no plate number is saved.",
      sees: "Three real samples: CAM06 plate (Read), a 10-pixel Delhi crop (Too small), a blurred Delhi plate (Not saved).",
      transition: "You move on.",
    },
    build(ctx) {
      const E = K.EVIDENCE || { quality: {} };
      const fail = (label) => (K.FAILURES || []).find((f) => f.label === label) || {};
      const tiny = fail("Tiny plate");
      const blurred = fail("Blurred plate");
      const cards = [
        ["assets/img/journey_best.png", 2, `CAM06 · ${E.quality.width_px} px`, "Read", "mint", "Sharp and wide enough."],
        [tiny.file, 14, `Delhi · ${tiny.width_px} px`, "Too small", "rose", "Under 22 pixels: not even tried."],
        [blurred.file, 6, `Delhi · ${blurred.width_px} px`, "Not saved", "rose", "Blurred: the readings disagreed."],
      ];
      ctx.el.append(
        L.head("Quality check", "If the pixels aren't there, it doesn't guess."),
        ...cards.map(([file, scale, title, verdict, tone, note], i) => L.card(96 + i * 584, 320, 560, 600, [
          h("div.lmedia", null, file ? S.pixels(file, { scale }) : h("div.missing", null, "[REAL CROP REQUIRED]")),
          h("h3", null, title), h("p", null, note), L.pill(verdict, tone),
        ], { id: `q${i}` }))
      );
      return [() => ctx.in("#q0"), () => ctx.in("#q1"), () => ctx.in("#q2")];
    },
  });

  /* ---------------------------------------------------------------- read */
  K.scene({
    id: "read", act: "How it works", title: "From pixels to characters", cls: "paper light",
    notes: {
      say: "Now we read it, character by character, each with a confidence. And Indian plates follow a pattern: state, RTO, series, number. A 6 where a letter must be is read as G.",
      sees: "The combined crop; each character flickers before it settles, with a bar for how sure Vigentra is; then the plate splits into State, RTO, Series, Number.",
      transition: "You move on.",
    },
    build(ctx) {
      const E = K.EVIDENCE || { plate: "", per_char_conf: [] };
      const cols = h("div.ocr-cols", null, [...E.plate].map((ch, i) => {
        const c = E.per_char_conf[i] || 0;
        return h("div.oc", null, [h("span.ch", null, ch), h("div.oc-barbox", null, h("div.oc-bar", { style: { "--v": `${Math.round(c * 100)}%` } })), h("div.oc-v.num", null, c.toFixed(2))]);
      }));
      const seg = ([txt, label]) => h("div.seg", null, [h("div.seg-txt", null, txt), h("div.seg-label", null, label)]);
      ctx.el.append(
        L.head("Reading", "From pixels to characters."),
        L.card(96, 300, 1728, 680, [h("div.cap", null, "The combined crop, read"), h("div.l-ocr", null, [h("div.ocr-src", null, K.media.img("journey_enhanced", { cls: "media-fit" })), cols])], { id: "r1" }),
        L.card(96, 300, 1728, 680, [h("div.cap", null, "Indian plates follow a pattern"), h("div.l-segs", null, [h("div.segs", null, D.grammar.segments.map(seg)), h("p", null, "A 6 where a letter must be is read as G: the format catches mistakes.")])], { id: "r2" }),
        S.tech([`<b>Readers</b> ${P.readers}`], P.src)
      );
      return [
        async () => { await ctx.in("#r1"); await ctx.wait(300); await K.ui.revealChars(ctx, cols, 200, { scramble: true }); },
        () => cols.classList.add("conf"),
        async () => { ctx.$("#r1").classList.add("gone"); await ctx.in("#r2"); for (const s of ctx.$$(".seg")) { s.classList.add("in"); await ctx.wait(260); } },
      ];
    },
  });

  /* ---------------------------------------------------------------- vote */
  K.scene({
    id: "vote", act: "How it works", title: "Many readings, one answer", cls: "paper light",
    build(ctx) {
      const E = K.EVIDENCE || { top_readings: [], plate: "", frames: {} };
      const total = E.hypotheses_total || 0;
      const top = (E.top_readings || []).slice(0, 7);
      const rows = [...top.map((r) => [r.text, r.count, r.text === E.plate]), ["other readings", total - top.reduce((n, r) => n + r.count, 0), false]];
      const max = Math.max(...rows.map((r) => r[1]));
      const tally = h("div.l-tally", null, rows.map(([text, n, win]) => h("div.tr" + (win ? ".win" : ""), null, [h("span.tr-t", null, text), h("span.tr-bar", null, h("i", { style: { "--w": `${(100 * n) / max}%` } })), h("span.tr-n", null, String(n))])));
      const fields = [["Plate", E.plate], ["Camera", (E.camera || "cam06").toUpperCase()], ["Readings", `${rows[0][1]} of ${total} agree`], ["Frames", `${E.frames.agreeing} agree`],
        ["Format", D.grammar.segments.map((s) => s[0]).join(" · ")], ["Sent as", "text · no image · no video"]];
      const rec = h("div.l-rec", null, fields.map(([k]) => h("div.rf", null, [h("span.rf-k", null, k), h("span.rf-v")])));
      ctx.el.append(
        L.head("The vote", `<span class="num">${total}</span> readings. One answer.`),
        L.card(96, 300, 1000, 680, [h("div.cap", null, `Every reading of this plate · ${total}`), tally, h("span.pill.mint.r", { id: "cf" }, "Confirmed")], { id: "t1" }),
        L.card(1120, 300, 704, 680, [h("div.pills", null, [L.pill("Read by Vigentra", "mint"), L.pill("Saved", "iris")]), rec], { id: "t2" })
      );
      return [
        async () => { await ctx.in("#t1"); await ctx.wait(300); tally.classList.add("in"); },
        async () => { tally.classList.add("decided"); await ctx.in("#cf"); },
        async () => { await ctx.in("#t2"); const vals = ctx.$$(".rf-v"); for (let i = 0; i < fields.length; i += 1) { ctx.$$(".rf")[i].classList.add("in"); await ctx.type(vals[i], fields[i][1], 50); } },
      ];
    },
  });
})();
