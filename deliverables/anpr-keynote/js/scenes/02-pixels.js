/* Acts 6-12: pixels, detection vs recognition, vehicles, plates, plate scale, tracking, time. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const D = K.DATA;

  const W = 1920;
  const H = 1080;
  const best = () => S.at1299();

  /* ---------------------------------------------------------------- 8 */
  K.scene({
    id: "pixels", act: "Act 6 · Pixels matter", title: "The plate is a tiny part of the image",
    build(ctx) {
      const s = best();
      const v = S.box(s && s.vehicle);
      const p = S.box(s && s.plate);
      const vpx = v ? [Math.round(v.w * W), Math.round(v.h * H)] : [0, 0];
      const ppx = p ? [Math.round(p.w * W), Math.round(p.h * H)] : [0, 0];
      const zoom = h("div.fill.zoombox", null, K.media.img("best_frame", { cls: "media-cover" }));
      const overlay = h("div.fill");
      zoom.appendChild(overlay);
      const boxes = K.ui.boxes(overlay, [
        v && { id: "v", kind: "vehicle", ...v },
        p && { id: "p", kind: "plate", ...p },
      ].filter(Boolean));
      const row = (id, name, dims, count, note) =>
        h("div.px-row.r", { id }, [h("span.px-name", null, name), h("span.px-dims.num", null, dims), h("span.px-count.num", null, count), note ? h("span.px-note", null, note) : null]);
      const ledger = h("div.px-ledger", null, [
        row("r-frame", "Frame", "1920 × 1080", S.fmt(W * H) + " px"),
        row("r-veh", "Vehicle", `${vpx[0]} × ${vpx[1]}`, S.fmt(vpx[0] * vpx[1]) + " px", `${((100 * vpx[0] * vpx[1]) / (W * H)).toFixed(1)}%`),
        row("r-plate", "Plate", `${ppx[0]} × ${ppx[1]}`, S.fmt(ppx[0] * ppx[1]) + " px", `${((100 * ppx[0] * ppx[1]) / (W * H)).toFixed(2)}%`),
        row("r-char", "One character", "≈ 1/9 of the plate", "≈ " + S.fmt((ppx[0] * ppx[1]) / 9) + " px"),
      ]);
      const pixView = h("div.center.pixview.r.zoom", null, h("div.stack", { style: { alignItems: "center", gap: "22px" } }, [
        S.pixels("assets/img/plate_1299.png", null, { scale: 7, grid: true }),
        h("div.caption", null, "The real pixels of that plate, enlarged: every square is one pixel the camera recorded."),
      ]));
      ctx.el.append(h("div.fill.frame-layer", null, zoom), h("div.fill.shade-right"), S.at(1290, 640, 560, null, ledger), pixView);
      return [
        () => ctx.in("#r-frame"),
        async () => { if (v) ctx.move(zoom, K.ui.zoomTo(v, { fill: 0.75 }), 1800); await boxes.show(ctx, "v"); await ctx.in("#r-veh"); },
        async () => { boxes.hide("v"); if (p) ctx.move(zoom, K.ui.zoomTo(p, { fill: 0.45 }), 2000); await boxes.show(ctx, "p"); await ctx.in("#r-plate"); },
        async () => { ctx.$(".frame-layer").classList.add("gone"); await ctx.in(pixView); await ctx.in("#r-char"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 9 */
  K.scene({
    id: "difference", act: "Act 7 · Detection is not recognition", title: "Where is it? What does it say?",
    build(ctx) {
      const p = S.box(best() && best().plate);
      const left = h("div.half.r", { id: "det" }, [
        h("div.kicker", null, "Detection"),
        h("div.h2", null, "Where is the plate?"),
        h("div.frame.half-media", null, [K.media.img("frame_best", { cls: "media-cover" }), h("div.fill.ov")]),
      ]);
      const right = h("div.half.r", { id: "rec" }, [
        h("div.kicker", null, "Recognition"),
        h("div.h2", null, "What does it say?"),
        h("div.half-media.rec-media", null, [S.pixels("assets/img/plate_1299.png", null, { targetW: 560 }), K.ui.plate(K.EVIDENCE ? K.EVIDENCE.plate : "", { size: 0.62, hidden: true })]),
      ]);
      ctx.el.append(S.at(120, 140, 820, null, left), S.at(980, 140, 820, null, right),
        h("div.chain.r", { id: "chain" }, ["Frame", "→", "Box", "→", "Crop", "→", "Characters"].map((t) => h("span", null, t))));
      const boxes = K.ui.boxes(left.querySelector(".ov"), p ? [{ id: "p", kind: "plate", ...p }] : []);
      return [
        async () => { await ctx.in("#det"); await boxes.show(ctx, "p"); },
        async () => { await ctx.in("#rec"); await K.ui.revealPlate(ctx, ctx.$(".rec-media .hsrp"), 110); },
        () => ctx.in("#chain"),
      ];
    },
  });

  /* ---------------------------------------------------------------- 10 */
  K.scene({
    id: "vehicles", act: "Act 8 · Finding the vehicle", title: "Object detection",
    build(ctx) {
      const others = (K.TRACK && K.TRACK.others_at_best) || [];
      const list = others.map((o) => ({ id: "o" + o.id, kind: "vehicle", ...S.box(o.box), label: `${o.class === "motorcycle" ? "motorcycle" : "vehicle"} ${o.conf.toFixed(2)}` }));
      const media = h("div.frame.full-media", null, [K.media.img("frame_best", { cls: "media-cover" }), h("div.fill.ov")]);
      ctx.el.append(
        S.framed(media, 160, 120, 1600, 900, "r m"),
        h("div.center.name-over", null, h("div.stack", { style: { gap: "10px" } }, [h("div.h1.r.soft", { id: "od" }, "Object detection"), h("div.h2.r.soft.c-vehicle", { id: "yolo" }, "YOLO")])),
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "job" }, "The first job is simply to locate the vehicles.")),
        S.tech([
          `<b>Model</b> ${D.pipeline.vehicle.model}`,
          `<b>Classes</b> ${D.pipeline.vehicle.classes}; any of them counts as a vehicle`,
          `<b>Threshold</b> confidence ≥ ${D.pipeline.vehicle.conf}; ${D.pipeline.vehicle.note}`,
          `<b>This frame</b> ${others.map((o) => `ID ${o.id}: ${o.class} ${o.conf}`).join(" · ")} (raw YOLO11s labels)`,
        ], "Run on CAM06 frame 1299 with the deployed weights (tools/track_evidence.py)")
      );
      const boxes = K.ui.boxes(media.querySelector(".ov"), list);
      return [
        () => ctx.in(".m"),
        async () => { await boxes.all(ctx, 350); await ctx.in("#job"); },
        async () => { ctx.out("#job"); media.classList.add("soft-dim"); await ctx.in("#od"); },
        () => ctx.in("#yolo"),
      ];
    },
  });

  /* ---------------------------------------------------------------- 11 */
  K.scene({
    id: "platefind", act: "Act 9 · Finding the plate", title: "A tiny object inside a large one",
    build(ctx) {
      const s = best();
      const v = S.box(s && s.vehicle);
      const p = S.box(s && s.plate);
      const zoom = h("div.fill.zoombox", null, [K.media.img("best_frame", { cls: "media-cover" }), h("div.fill.ov")]);
      const boxes = K.ui.boxes(zoom.querySelector(".ov"), [
        v && { id: "v", kind: "vehicle", ...v, label: "vehicle" },
        p && { id: "p", kind: "plate", ...p, label: `plate ${s.plate_conf.toFixed(2)}` },
      ].filter(Boolean));
      const lift = h("div.lift.r.zoom", null, [S.pixels("assets/img/plate_1299.png", null, { targetW: 640 }), h("div.caption", null, `Crop: ${s ? s.plate_px : "?"} px wide`)]);
      ctx.el.append(
        h("div.fill.zl", null, zoom),
        S.at(1180, 380, 660, null, lift),
        h("div.cap-top", null, [h("div.h2.r.soft", { id: "harder" }, "Now a harder problem."), h("div.lede.r", { id: "inside" }, "A very small object, inside an object we already found.")]),
        S.tech([
          `<b>Model</b> ${D.pipeline.plate.model}`,
          `<b>Where</b> ${D.pipeline.plate.note}, upscaled to ${D.pipeline.plate.imgsz} px`,
          `<b>Threshold</b> confidence ≥ ${D.pipeline.plate.conf}; vehicles narrower than ${D.pipeline.plate.minVehiclePx} px are skipped`,
          `<b>This frame</b> plate confidence ${s ? s.plate_conf : "?"}`,
        ], D.pipeline.src)
      );
      return [
        async () => { await boxes.show(ctx, "v"); await ctx.in("#harder"); },
        async () => { if (v) await ctx.move(zoom, K.ui.zoomTo(v, { fill: 0.85 }), 1800); await boxes.show(ctx, "p"); await ctx.in("#inside"); },
        async () => { ctx.$(".zl").classList.add("dimmed"); await ctx.in(lift); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 12 */
  K.scene({
    id: "scale", act: "Act 10 · Plate scale", title: "Large to extremely tiny",
    build(ctx) {
      const B = D.sizeBands;
      const ladder = h("div.ladder", null, B.names.map((name, i) => h("div.rung.r", { "data-i": i }, [
        h("div.rung-img", null, S.pixels("assets/img/journey_best.png", B.demoPx[i], { factor: true, targetW: 300 })),
        h("div.rung-name", null, name),
        h("div.rung-w.num", null, `${B.demoPx[i]} px wide · band ${B.widths[i]}`),
      ])));
      const dist = K.ui.bars(B.names.map((name, i) => ({ label: name, sub: B.widths[i], value: B.counts[i], color: i >= 2 ? "var(--bad)" : "var(--vehicle)" })), { max: Math.max(...B.counts) });
      const small = B.counts[2] + B.counts[3] + B.counts[4];
      ctx.el.append(
        S.title("The same real plate, given fewer pixels", "Large → Extremely tiny"),
        S.at(140, 330, 1640, null, ladder),
        S.at(140, 800, 1640, null, h("div.caption.r", { id: "demo" }, "Demonstration: the real crop of GJ23H1546 reduced to each band's width. The bands are the ones the detector is evaluated on.")),
        S.at(140, 300, 1080, null, h("div.dist", null, [h("div.h3.r", { id: "dist-h" }, "How many real plates fall in each band"), dist])),
        S.at(1300, 330, 500, null, h("div.stack", null, [
          K.ui.stat(`${Math.round((100 * small) / B.total)}%`, `of the ${B.total} plates in the test set are narrower than 22 px (${small} of ${B.total}).`, { cls: "big-stat" }),
        ])),
        S.tech([`<b>Bands</b> by plate width in pixels: ${B.names.map((n, i) => `${n} ${B.widths[i]}`).join(" · ")}`, `<b>Test set</b> ${B.total} plate boxes`], B.src)
      );
      return [
        () => ctx.in(".rung", { stagger: 650 }),
        () => ctx.in("#demo"),
        async () => {
          ctx.$(".ladder").classList.add("gone");
          ctx.$("#demo").classList.add("gone");
          await ctx.in("#dist-h");
          await ctx.in(".bar", { stagger: 200 });
          await ctx.in(".big-stat", { delay: 400 });
        },
      ];
    },
  });

  /* ---------------------------------------------------------------- 13 */
  K.scene({
    id: "tracking", act: "Act 11 · Tracking", title: "A video is not one image",
    build(ctx) {
      const T = K.TRACK || { sequence: [], full_frames: [] };
      const frames = (T.full_frames || []).map((f) => T.sequence.find((s) => s.frame === f)).filter(Boolean);
      const slot = (f) => ({ 1244: "frame_first", 1299: "frame_best", 1314: "frame_last" }[f]);
      const strip = h("div.tstrip", null, frames.map((s, i) => {
        const card = h("div.tcard.r", { "data-i": i }, [h("div.frame.tmedia", null, [K.media.img(slot(s.frame), { cls: "media-cover" }), h("div.fill.ov")]), h("div.caption.num", null, `frame ${s.frame}`)]);
        card._s = s;
        return card;
      }));
      const crops = T.sequence.filter((s) => s.file);
      const scrub = K.ui.scrubber(crops.length, (i) => h("div.stack", { style: { alignItems: "center", gap: "14px" } }, [
        S.pixels(crops[i].file, null, { targetW: 520 }),
        h("div.caption.num", null, `frame ${crops[i].frame} · plate ${crops[i].plate_px} px wide · detector ${crops[i].plate_conf}`),
      ]), { labels: crops.map((c) => String(c.frame)) });
      ctx.el.append(
        S.title("Tracking", "A video is not one image."),
        S.at(140, 300, 1640, null, strip),
        h("div.center.time-over", null, h("div.hero.r.soft", { id: "time" }, "Time.")),
        S.at(460, 300, 1000, null, h("div.r", { id: "scrub" }, scrub)),
        S.tech([
          `<b>Tracker</b> ${D.pipeline.tracker.name}: high ${D.pipeline.tracker.high}, low ${D.pipeline.tracker.low}, new track ${D.pipeline.tracker.newTrack}, buffer ${D.pipeline.tracker.buffer}, match ${D.pipeline.tracker.match}`,
          `<b>Close</b> a track settles after ${D.pipeline.tracker.closeAfter} frames unseen`,
          `<b>This car</b> ID ${T.track_id}, frames ${T.frames ? T.frames.join("–") : ""}, seen in ${T.seen}`,
        ], T.models || "")
      );
      ctx.$$(".tcard").forEach((card) => {
        const s = card._s;
        K.ui.boxes(card.querySelector(".ov"), [{ id: "t", kind: "track", ...S.box(s.vehicle), label: `ID ${T.track_id}` }]).show(ctx, "t");
      });
      return [
        () => ctx.in('.tcard[data-i="0"]'),
        () => ctx.in('.tcard[data-i="1"]'),
        () => ctx.in('.tcard[data-i="2"]'),
        async () => { ctx.$(".tstrip").classList.add("dimmed"); await ctx.in("#time"); },
        async () => { ctx.out("#time"); ctx.$(".tstrip").classList.add("gone"); await ctx.in("#scrub"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 14 */
  K.scene({
    id: "temporal", act: "Act 12 · Evidence over time", title: "One bad frame isn't the end",
    build(ctx) {
      const T = K.TRACK || { sequence: [] };
      const E = K.EVIDENCE || { frames: {} };
      const crops = T.sequence.filter((s) => s.file);
      const row = h("div.trow", null, crops.map((s) => {
        const bad = s.plate_conf < 0.65 || s.frame >= 1309;
        return h("div.tcrop.r" + (bad ? ".bad" : ""), null, [
          h("div.tcrop-img", null, S.pixels(s.file, null, { targetW: 150 })),
          h("div.tcrop-meta.num", null, [h("b", null, `${s.plate_px} px`), ` · ${s.plate_conf}`]),
          h("div.tcrop-f.num", null, `f${s.frame}`),
        ]);
      }));
      ctx.el.append(
        S.title("Evidence over time", "Different frames, different pieces of evidence."),
        S.at(120, 330, 1680, null, row),
        S.at(120, 640, 1680, null, h("div.lede.r", { id: "badnote" }, "The last two are mistakes: the car has left the picture, and the plate detector boxed its dashboard. Low confidence, no characters. They will not survive the checks.")),
        S.at(120, 790, 1680, null, h("div.ev-stats.r", { id: "fused" }, [
          h("span", null, [h("b.num", null, String(E.frames.fused || "?")), " best crops fused"]),
          h("span", null, [h("b.num", null, String(E.frames.agreeing || "?")), " frames agreeing on the reading"]),
          h("span", null, [h("b.num", null, String(E.hypotheses_total || "?")), " readings in all"]),
        ])),
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "honest" }, "Many frames don't invent detail. They let us choose, and combine, what is there.")),
        S.tech([`<b>Crop bank</b> ${D.pipeline.crops}`, "<b>Crops</b> detector output per frame (tools/track_evidence.py)", `<b>Fusion figures</b> from the engine's evidence pack for this car`], E.source || "")
      );
      return [
        () => ctx.in(".tcrop", { stagger: 140 }),
        () => ctx.in("#badnote"),
        () => ctx.in("#fused"),
        () => ctx.in("#honest"),
      ];
    },
  });
})();
