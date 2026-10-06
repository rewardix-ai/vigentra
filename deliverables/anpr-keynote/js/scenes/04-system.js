/* Acts 20-25: failures become data, the engineering loop, evaluation, the whole pipeline,
 * the control room, and one vehicle's journey. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const D = K.DATA;

  /* ---------------------------------------------------------------- 22 */
  K.scene({
    id: "dataset", act: "Act 20 · Dataset", title: "When the model fails, we collect evidence",
    build(ctx) {
      const DS = D.dataset;
      const flow = h("div.flow", null, ["Hard examples", "Labelled", "Dataset", "Training", "Evaluation"].map((t, i) => h("span.flow-step.r", { "data-i": i }, t)));
      const bars = K.ui.bars(DS.hard.map(([label, n]) => ({ label, value: n })), { max: DS.hard[0][1], color: "var(--warn)" });
      bars.classList.add("compact");
      ctx.el.append(
        S.title("Dataset", "We collect the cases where it struggles."),
        S.framed(K.media.img("gt_sheet", { cls: "media-cover" }), 140, 280, 820, 474, "r sheetf"),
        S.at(140, 780, 820, null, h("div.caption.r", { id: "sheetcap" }, "A real labelling sheet: plate crops from CAM06 at noon, checked by eye.")),
        S.at(1020, 280, 780, null, h("div.stack", null, [h("div.r", { id: "flow" }, flow), h("div.r", { id: "hard" }, bars)])),
        S.at(1020, 860, 780, null, h("div.ds-stats.r", { id: "dsn" }, [
          h("span", null, [h("b.num", null, S.fmt(DS.images)), " images"]), h("span", null, [h("b.num", null, S.fmt(DS.boxes)), " plates"]),
          h("span", null, [h("b.num", null, `${DS.medianWidth} px`), " median plate width"]),
        ])),
        S.tech([`<b>Split</b> ${DS.split}; ${DS.negatives} images with no plate`, "<b>Tags</b> overlap: one plate can be tiny and low-contrast"], DS.src)
      );
      return [
        async () => { await ctx.in(".sheetf"); await ctx.in("#sheetcap"); },
        async () => { await ctx.in("#flow"); await ctx.in(".flow-step", { stagger: 260 }); },
        async () => { await ctx.in("#hard"); await ctx.in("#hard .bar", { stagger: 140 }); },
        () => ctx.in("#dsn"),
      ];
    },
  });

  /* ---------------------------------------------------------------- 23 */
  K.scene({
    id: "loop", act: "Act 21 · The engineering loop", title: "Train, test, fail, learn, improve",
    build(ctx) {
      const loop = K.ui.loop(["Deploy", "Observe failures", "Collect hard cases", "Label", "Train", "Evaluate"], { radius: 300 });
      const T = D.training;
      ctx.el.append(
        S.title("The engineering loop", "The real world shows us where it fails."),
        S.at(80, 220, null, null, loop),
        S.at(1180, 300, 620, null, h("div.loop-notes", null, [
          h("div.ln.r", null, [h("b", null, "Observe"), ` ${S.fmt(D.failures.causes[0][1])} vehicles gave no plate at all in the final benchmark.`]),
          h("div.ln.r", null, [h("b", null, "Collect"), ` ${S.fmt(D.dataset.images)} images, most of them hard.`]),
          h("div.ln.r", null, [h("b", null, "Train"), ` ${T.runs} runs of ${T.model}; best mAP50 ${T.best.map50}.`]),
          h("div.ln.r", null, [h("b", null, "Decide"), " None of the five is in production yet. The loop also says when not to ship."]),
        ])),
        S.tech(["<b>mAP50</b> average precision at IoU 0.5, on the hard set's validation split", `<b>Best run</b> ${T.best.run}: mAP50 ${T.best.map50}, mAP50-95 ${T.best.map5095}, P ${T.best.precision}, R ${T.best.recall}`], T.src)
      );
      return [
        async () => { await ctx.in(".loop-node", { stagger: 220 }); loop.spin(ctx, 900); },
        () => ctx.in(".ln", { stagger: 500 }),
      ];
    },
  });

  /* ---------------------------------------------------------------- 24 */
  K.scene({
    id: "evaluation", act: "Act 22 · Evaluation", title: "How do we know it improved?",
    build(ctx) {
      const B = D.sizeBands;
      const run = D.eval.runs[D.eval.shown];
      const dots = (n, on, cls) => h("div.dots", null, Array.from({ length: n }, (_, i) => h("span" + (i < on ? "." + cls : ""))));
      const defs = h("div.defs", null, [
        h("div.def.r", { id: "d-rec" }, [h("div.h3", null, "Recall"), h("div.lede", null, "Of all the plates that were there, how many did we find?"), dots(10, 8, "found")]),
        h("div.def.r", { id: "d-pre" }, [h("div.h3", null, "Precision"), h("div.lede", null, "Of everything we called a plate, how much really was one?"), dots(10, 8, "real")]),
        h("div.def.r", { id: "d-ap" }, [h("div.h3", null, "AP"), h("div.lede", null, "One score that rewards finding plates and penalises false ones, across confidence levels.")]),
      ]);
      const recall = K.ui.bars(B.names.map((n, i) => ({ label: n, sub: B.widths[i], value: run.recall[i] })), { max: 1, format: (v) => v.toFixed(2) });
      const ap = K.ui.bars(B.names.map((n, i) => ({ label: n, sub: B.widths[i], value: run.ap[i] })), { max: 1, format: (v) => v.toFixed(3), color: "var(--track)" });
      ctx.el.append(
        S.title("Evaluation", "How do we know it improved?"),
        S.at(140, 290, 1640, null, defs),
        S.at(140, 290, 800, null, h("div.r", { id: "rec-b" }, [h("div.h3", null, "Recall by plate size"), recall])),
        S.at(1000, 290, 800, null, h("div.r", { id: "ap-b" }, [h("div.h3", null, "AP by plate size"), ap])),
        S.at(140, 900, 1640, null, h("div.caption.r", { id: "cav" }, `${run.label}. ${D.eval.caveat}`)),
        S.tech(Object.values(D.eval.runs).map((r) => `<b>${r.label}</b> recall ${r.overall.recall} · AP ${r.overall.ap} · AP by band ${r.ap.join(" / ")}`), D.eval.src)
      );
      return [
        () => ctx.in("#d-rec"),
        () => ctx.in("#d-pre"),
        () => ctx.in("#d-ap"),
        async () => {
          ctx.$(".defs").closest(".abs").classList.add("gone");
          await ctx.in("#rec-b");
          await ctx.in("#rec-b .bar", { stagger: 160 });
          await ctx.in("#ap-b");
          await ctx.in("#ap-b .bar", { stagger: 160 });
          await ctx.in("#cav");
        },
      ];
    },
  });

  /* ---------------------------------------------------------------- 25 */
  const STAGES = [
    { name: "Camera & stream", hint: "RTSP / TCP", color: "var(--ink-2)", detail: () => D.pipeline.capture },
    { name: "Frame sampling", hint: "burst sampler", color: "var(--ink-2)", detail: () => D.pipeline.sampling },
    { name: "Vehicle detection", hint: "YOLO11s", color: "var(--vehicle)", detail: () => `${D.pipeline.vehicle.model}; ${D.pipeline.vehicle.classes}; confidence ≥ ${D.pipeline.vehicle.conf}; ${D.pipeline.vehicle.note}. The camera's burned-in clock and captions are masked first.` },
    { name: "Tracking", hint: "ByteTrack", color: "var(--track)", detail: () => `${D.pipeline.tracker.name}; one identity per vehicle; it settles after ${D.pipeline.tracker.closeAfter} frames unseen.` },
    { name: "Plate detection", hint: "YOLO11n · 640 px", color: "var(--plate)", detail: () => `${D.pipeline.plate.model}; ${D.pipeline.plate.note}; confidence ≥ ${D.pipeline.plate.conf}.` },
    { name: "Crop bank", hint: "best 12 of 64", color: "var(--plate)", detail: () => `Every crop of the vehicle's plate is scored for quality; ${D.pipeline.crops}.` },
    { name: "Quality gate", hint: "≥ 22 px · sharp", color: "var(--warn)", detail: () => `Width ≥ ${D.pipeline.gate.width} px, height ≥ ${D.pipeline.gate.height} px, sharpness ≥ ${D.pipeline.gate.sharpness}, contrast ≥ ${D.pipeline.gate.contrast}. Below it, nothing is read.` },
    { name: "Enhancement", hint: "multi-frame", color: "var(--warn)", detail: () => `${D.pipeline.enhancement.join(" → ")}. ${D.pipeline.sr}.` },
    { name: "Reading", hint: "3 readers", color: "var(--ok)", detail: () => D.pipeline.readers.join(" · ") },
    { name: "Vote", hint: "ROVER", color: "var(--ok)", detail: () => D.pipeline.rover },
    { name: "Validation", hint: "rules · grammar · glyphs", color: "var(--ok)", detail: () => `${D.pipeline.confirm}. Glyph check: ${D.pipeline.glyph}. Indian plate grammar.` },
    { name: "Result", hint: "confirmed or silent", color: "var(--ok)", detail: () => "A confirmed plate is uploaded at once as text, time and camera: never video. Otherwise nothing is sent." },
  ];

  K.scene({
    id: "pipeline", act: "Act 23 · The complete pipeline", title: "Everything comes together",
    build(ctx) {
      const panel = h("div.pipe-detail.r", { id: "pd" }, [h("div.h3", { id: "pd-name" }, ""), h("div.lede", { id: "pd-text" }, "")]);
      const onSelect = (stage) => {
        ctx.$("#pd-name").textContent = stage.name;
        ctx.$("#pd-text").textContent = stage.detail();
        panel.classList.add("in");
      };
      const rowA = K.ui.pipeline(STAGES.slice(0, 6), { direction: "row", onSelect });
      const rowB = K.ui.pipeline(STAGES.slice(6), { direction: "row", onSelect: (s) => onSelect(s) });
      ctx.el.append(
        S.title("The complete pipeline", "Every stage answers a problem you have seen."),
        S.at(110, 330, 1700, null, h("div.pipe-rows", null, [rowA, h("div.pipe-turn.r"), rowB])),
        S.at(110, 760, 1700, null, panel)
      );
      return [
        async () => {
          const nodes = ctx.$$(".pipe-node, .pipe-link, .pipe-turn");
          for (const n of nodes) { n.classList.add("in"); await ctx.wait(140); }
          rowA.flow(true);
          rowB.flow(true);
        },
        () => { rowA.select(2); },
        () => { rowB.select(0); },
        () => { rowB.select(5); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 26 */
  K.scene({
    id: "control", act: "Act 24 · The control room", title: "The system comes alive",
    build(ctx) {
      const feeds = [
        { slot: "cam06_vigentra", label: "CAM06 · read by Vigentra", badge: "plates", detail: "Every plate the system confirmed, beside the crop it read it from. 15 of 16 legible plates, none wrong." },
        { slot: "delhi_vigentra", label: "DELHI · read by Vigentra", badge: "plates", detail: "13 of 20 legible plates, none wrong." },
        { slot: "wall_cam01", label: "GRID · cam01" }, { slot: "wall_cam02", label: "GRID · cam02" },
        { slot: "wall_cam07", label: "GRID · cam07" }, { slot: "wall_cam12", label: "GRID · cam12" },
      ].map((f) => ({ ...f, media: () => K.media.video(f.slot, { cls: "media-cover" }) }));
      ctx.el.append(
        S.title("The control room", "This is what the finished system looks like."),
        S.at(140, 280, 1640, null, h("div.r", { id: "wall" }, K.ui.wall(feeds, { columns: 3 }))),
        S.framed(K.media.video("app_demo", { cls: "media-fit" }), 260, 250, 1400, 788, "r console"),
        S.tech(["<b>Console</b> Next.js dashboard; alerts refresh every 5 s; watchlist matches arrive as critical, high or review", "<b>Feeds</b> recordings of the grid cameras and of the system's own output"], "deliverables/Vigentra_Demo_Short.mp4 (screen recording)")
      );
      return [
        async () => { await ctx.in("#wall"); await ctx.in(".wall-tile", { stagger: 150 }); },
        async () => { ctx.$("#wall").classList.add("gone"); await ctx.in(".console"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 27 */
  K.scene({
    id: "journey", act: "Act 25 · One vehicle's journey", title: "Follow one vehicle",
    build(ctx) {
      const E = K.EVIDENCE || { frames: {}, quality: {}, top_readings: [], alternates: [] };
      const T = K.TRACK || { sequence: [] };
      const s = S.at1299() || {};
      const frameWith = (boxes) => {
        const m = h("div.frame.j-media", null, [K.media.img("frame_best", { cls: "media-cover" }), h("div.fill.ov")]);
        const b = K.ui.boxes(m.querySelector(".ov"), boxes);
        requestAnimationFrame(() => boxes.forEach((x) => b.show(ctx, x.id, 600)));
        return m;
      };
      const vbox = { id: "v", kind: "vehicle", ...S.box(s.vehicle), label: `vehicle ${s.vehicle_conf}` };
      const pbox = { id: "p", kind: "plate", ...S.box(s.plate), label: `plate ${s.plate_conf}` };
      const tbox = { id: "t", kind: "track", ...S.box(s.vehicle), label: `ID ${T.track_id}` };
      const q = E.quality;
      const stages = [
        ["Camera frame", "CAM06, frame 1299, 1920 × 1080.", () => frameWith([])],
        ["Vehicle found", `YOLO11s: a vehicle, confidence ${s.vehicle_conf} (raw label: ${s.class}).`, () => frameWith([vbox])],
        ["Track", `ByteTrack ID ${T.track_id}, followed for ${T.seen} frames (${T.frames ? T.frames.join("–") : ""}).`, () => frameWith([tbox])],
        ["Plate found", `Plate detector inside the vehicle: confidence ${s.plate_conf}, ${s.plate_px} px wide.`, () => frameWith([vbox, pbox])],
        ["Crop bank", "The plate in every frame, scored; the best twelve are kept.", () => h("div.j-row", null, T.sequence.filter((x) => x.file).slice(0, 8).map((x) => S.pixels(x.file, null, { targetW: 170 })))],
        ["Quality gate", `Best crop: sharpness ${S.fmt(q.sharpness_lap)}, contrast ${S.fmt(q.local_contrast, 1)}, quality ${q.quality_score ? q.quality_score.toFixed(2) : ""}. Passes.`, () => S.pixels("assets/img/journey_best.png", null, { targetW: 640 })],
        ["Enhancement", `${E.frames.fused} frames aligned and combined, then cleaned.`, () => h("div.j-pair", null, [K.media.img("journey_fused", { cls: "media-fit" }), K.media.img("journey_enhanced", { cls: "media-fit" })])],
        ["Reading", `${E.hypotheses_total} readings from three readers on the crops.`, () => K.ui.readings((E.top_readings || []).slice(0, 4).map((r) => ({ src: `${r.count}×`, text: r.text, note: r.sources.join(", ") })), E.plate)],
        ["Vote", `${E.frames.agreeing} frames agree. Runner-up ${E.alternates[0] ? E.alternates[0].plate + " at " + E.alternates[0].confidence : ""}.`, () => K.media.img("journey_charconf", { cls: "media-fit" })],
        ["Validation", `Valid Indian format: ${E.valid_format ? "yes" : "no"}. Real character shapes in the crops: yes.`, () => K.ui.plate(E.plate, { size: 0.8 })],
        ["Confirmed", `${E.plate}, confidence ${E.confidence}.`, () => h("div.j-final", null, [h("div.kicker", null, "Status"), h("div.h1.c-ok", null, "CONFIRMED"), K.ui.plate(E.plate, { size: 0.8 })])],
        ["Event", "Uploaded as text, time and camera, never video; checked against the watchlist; an alert within seconds if it is listed.", () => h("div.j-event.mono", null, `{ plate: "${E.plate}", camera: "${E.camera}", frame: ${E.frames.best}, status: "CONFIRMED" }`)],
      ];
      const list = h("div.j-list", { "data-interactive": true }, stages.map(([name], i) => h("button.j-item", { onclick: () => show(i) }, [h("span.num", null, String(i + 1).padStart(2, "0")), name])));
      const view = h("div.j-view");
      const caption = h("div.j-cap.lede");
      const show = (i) => {
        list.querySelectorAll(".j-item").forEach((b, j) => b.classList.toggle("on", j === i));
        view.innerHTML = "";
        view.appendChild(stages[i][2]());
        caption.textContent = stages[i][1];
      };
      ctx.el.append(
        S.title(`One vehicle · ${E.plate}`, "Follow it through every stage."),
        S.at(140, 260, 420, null, list),
        S.at(620, 260, 1160, 600, view),
        S.at(620, 880, 1160, null, caption),
        S.tech([`<b>Boxes and track</b> ${T.models || ""}`, `<b>Reading and fusion</b> ${E.source || ""}`], "tools/track_evidence.py · tools/build_assets.py")
      );
      return stages.map((_, i) => () => show(i));
    },
  });
})();
