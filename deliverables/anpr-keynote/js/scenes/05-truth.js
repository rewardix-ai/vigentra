/* Acts 26-35: evolution, failures, the boundary, camera vs AI, deployment, performance, results,
 * limits, the future, and the return to the opening frame. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const D = K.DATA;

  /* ---------------------------------------------------------------- 28 */
  K.scene({
    id: "evolution", act: "Act 26 · Before and after", title: "Evolution",
    build(ctx) {
      const B = D.baseline;
      const C = D.sameClips;
      const F = D.final;
      const col = (id, kicker, big, sub, rows) => h("div.evo.r", { id }, [h("div.kicker", null, kicker), h("div.evo-big.num", null, big), h("div.lede", null, sub), h("div.evo-rows", null, rows.map(([k, v]) => h("div", null, [h("span", null, k), h("b.num", null, v)])))]);
      ctx.el.append(
        S.title("Before and after", "Measured, every time."),
        S.at(140, 300, 500, null, col("e1", "First benchmark", `${B.read} / ${B.legible}`, "legible plates read", [["Wrong", String(B.wrong)]])),
        S.at(700, 300, 560, null, col("e2", `Same ${C.clips} clips, before → after`, `${C.before} → ${C.after}`, `correct, of ${C.of}`, [
          ["Junk plate candidates", `${S.fmt(C.candidates[0])} → ${S.fmt(C.candidates[1])}`],
          ["Images sent to OCR", `${S.fmt(C.ocrImages[0])} → ${S.fmt(C.ocrImages[1])}`],
          ["Processing time", `${S.fmt(C.seconds[0])} s → ${S.fmt(C.seconds[1])} s`],
        ])),
        S.at(1320, 300, 460, null, col("e3", `Final · ${F.clips} clips`, `${F.read} / ${F.legible}`, "legible plates read", [["Wrong", String(F.wrong)]])),
        S.tech(D.improvements.map(([a, b]) => `<b>${a}</b> ${b}`), `${B.src} · ${C.src}`)
      );
      return [() => ctx.in("#e1"), () => ctx.in("#e2"), () => ctx.in("#e3")];
    },
  });

  /* ---------------------------------------------------------------- 29 */
  K.scene({
    id: "failures", act: "Act 27 · Failure analysis", title: "Don't hide the failures",
    build(ctx) {
      const Fl = D.failures;
      const group = { camera: "Camera & plate size", reading: "Reading", ambiguous: "Ambiguous", quality: "Image quality", other: "Other" };
      const first = Fl.causes[0];
      const rest = Fl.causes.slice(1);
      const tabs = K.ui.tabs(K.FAILURES || [], (f) => h("div.fx", null, [
        h("div.fx-img", null, S.pixels(f.file, null, { targetW: 640 })),
        h("div.fx-meta", null, [h("div.h3", null, f.label), h("div.lede", null, `${f.width_px} × ${f.height_px} px · ${f.camera}`), h("div.lede.c-bad", null, f.reason ? f.reason.replace(/_/g, " ") : (f.status || "").toLowerCase())]),
      ]));
      ctx.el.append(
        S.title("Failure analysis", `${S.fmt(Fl.unresolved)} vehicles gave no confirmed plate. Why?`),
        S.at(140, 300, 1640, null, h("div.r", { id: "f1" }, [
          h("div.f-big", null, [h("b.num", null, S.fmt(first[1])), h("span", null, ` ${first[0].toLowerCase()} (${Math.round((100 * first[1]) / Fl.unresolved)}%) · ${group[first[2]]}`)]),
        ])),
        S.at(140, 470, 1640, null, h("div.r", { id: "f2" }, (() => { const b = K.ui.bars(rest.map(([label, n, g]) => ({ label, sub: group[g], value: n })), { max: rest[0][1], color: "var(--bad)" }); b.classList.add("compact"); return b; })())),
        S.at(140, 300, 1640, null, h("div.r", { id: "fx" }, tabs)),
        S.at(140, 940, 1640, null, h("div.caption.r", { id: "missed" }, `Of the ${D.final.legible} legible plates, ${Fl.missedLegible} were missed; ${Fl.missedDelhiSecondReader} of the ${Fl.missedDelhi} in Delhi because only one kind of reader agreed.`)),
        S.tech(["<b>Unit</b> a track: one vehicle the tracker followed", "<b>Final benchmark</b> 38 clips"], Fl.src)
      );
      return [
        () => ctx.in("#f1"),
        async () => { await ctx.in("#f2"); await ctx.in("#f2 .bar", { stagger: 120 }); },
        () => ctx.in("#missed"),
        async () => { ["#f1", "#f2", "#missed"].forEach((s) => ctx.$(s).classList.add("gone")); await ctx.in("#fx"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 30 */
  K.scene({
    id: "boundary", act: "Act 28 · What AI cannot do", title: "The boundary",
    build(ctx) {
      const diff = K.ui.difficulty("assets/img/journey_best.png");
      ctx.el.append(
        S.title("The boundary", "Take away what the camera captured."),
        h("div.center", null, h("div.r", { id: "diff" }, diff)),
        S.at(140, 960, 1640, null, h("div.caption.r", { id: "demo" }, "Demonstration on a real crop: fewer pixels, more blur, less light, more noise.")),
        h("div.cap-top.right", null, [h("div.h2.r.soft", { id: "can" }, "AI can infer patterns."), h("div.h2.r.soft.c-bad", { id: "cannot" }, "It cannot recover what was never captured.")])
      );
      return [
        async () => { await ctx.in("#diff"); await ctx.in("#demo"); },
        async () => { for (let v = 0; v <= 100; v += 4) { diff.set(v); await ctx.wait(70); } },
        async () => { await ctx.in("#can"); await ctx.in("#cannot", { delay: 700 }); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 31 */
  K.scene({
    id: "levers", act: "Act 29 · Camera vs AI", title: "The real lesson",
    build(ctx) {
      const tiny = (K.FAILURES || []).find((f) => f.label === "Tiny plate") || {};
      ctx.el.append(
        S.title("Two levers", "Better AI, or better pixels?"),
        S.at(140, 300, 780, null, h("div.lever.r", { id: "ai" }, [h("div.h3", null, "Better AI"), h("div.lede", null, "Better detectors, readers, rules. Each moves the result a little, on plates that carry enough pixels."), h("div.lever-bar", null, h("span", { style: { "--v": "35%" } }))])),
        S.at(1000, 300, 780, null, h("div.lever.r", { id: "cam" }, [h("div.h3", null, "Better camera data"), h("div.lede", null, "Placement, resolution, light. The same software reads one of these every time, and can never read the other."), h("div.lever-bar.cam", null, h("span", { style: { "--v": "92%" } }))])),
        S.at(140, 640, 1640, null, h("div.pairs.r", { id: "pair" }, [
          h("div", null, [S.pixels("assets/img/plate_1299.png", null, { scale: 2 }), h("div.caption", null, "124 px: read")]),
          h("div", null, [S.pixels(tiny.file || "", null, { scale: 2 }), h("div.caption", null, `${tiny.width_px || "?"} px: never readable`)]),
        ])),
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "lesson" }, "The best model cannot compensate forever for a bad camera."))
      );
      return [() => ctx.in("#ai"), () => ctx.in("#cam"), () => ctx.in("#pair"), () => ctx.in("#lesson")];
    },
  });

  /* ---------------------------------------------------------------- 32 */
  K.scene({
    id: "deployment", act: "Act 30 · Deployment", title: "From demo to deployment",
    build(ctx) {
      const parts = [
        ["Cameras", "department systems, unchanged"], ["Network", "RTSP over TCP · HLS for people"], ["Edge hardware", "GPU readers beside the cameras"],
        ["Models", "YOLO11s · YOLO11n · 3 readers"], ["Tracking", "one identity per vehicle"], ["Storage", "PostgreSQL: text, never video"],
        ["Monitoring", "camera-health alerts in ~40 s"], ["Alerts", "watchlist hits within 5 s, signed webhook"], ["People", "acknowledge or dismiss; every action audited"],
      ];
      ctx.el.append(
        S.title("A model is not a system", "Everything a real deployment needs."),
        h("div.center", null, h("div.core.r", { id: "core" }, "Model")),
        h("div.orbit", null, parts.map(([name, sub], i) => {
          const a = (i / parts.length) * Math.PI * 2 - Math.PI / 2;
          return h("div.orb.r", { style: { left: `${960 + Math.cos(a) * 620}px`, top: `${600 + Math.sin(a) * 330}px` } }, [h("b", null, name), h("span", null, sub)]);
        }))
      );
      return [() => ctx.in("#core"), () => ctx.in(".orb", { stagger: 200 })];
    },
  });

  /* ---------------------------------------------------------------- 33 */
  K.scene({
    id: "performance", act: "Act 31 · Performance", title: "Real-time means more than accuracy",
    build(ctx) {
      const P = D.speed;
      const rows = P.rows.map((r) => ({ label: r.res, sub: `${r.ms} ms per frame`, value: r.rt, color: r.rt >= 1 ? "var(--ok)" : "var(--warn)" }));
      ctx.el.append(
        S.title(`Measured on one ${P.machine}`, "Is it as fast as the camera?"),
        S.at(140, 320, 1300, null, h("div.r.perf", { id: "perf" }, [K.ui.bars(rows, { max: 1.5, format: (v) => `${v.toFixed(2)}×` }), h("div.rt-line", null, h("span", null, "real time"))])),
        S.at(1480, 320, 360, null, h("div.stack.perf-stats", null, [
          K.ui.stat(`${P.ingest}`, "plate reads a second, ingest with watchlist matching", { cls: "s1" }),
          K.ui.stat(`${(P.gpuMemMB[1] / 1000).toFixed(1)} GB`, "GPU memory per reader", { cls: "s2" }),
        ])),
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "rt" }, "Accurate but slower than the camera is not real-time.")),
        S.tech([`<b>Estimates, not measurements</b> about ${P.estimates.camerasPerT4} cameras per T4-class GPU with ANPR; about ${S.fmt(P.estimates.nodesFor80k)} accelerator nodes for 80,000 cameras`, `<b>Per reader</b> ${P.perReaderFps} frames a second with three readers sharing the M1`], P.src)
      );
      return [async () => { await ctx.in("#perf"); await ctx.in("#perf .bar", { stagger: 220 }); }, () => ctx.in(".s1, .s2", { stagger: 300 }), () => ctx.in("#rt")];
    },
  });

  /* ---------------------------------------------------------------- 34 */
  K.scene({
    id: "results", act: "Act 32 · Results", title: "What did we actually achieve?",
    build(ctx) {
      const F = D.final;
      const tile = (id, big, label) => h("div.res.r", { id }, [h("div.res-big.num", null, big), h("div.lede", null, label)]);
      ctx.el.append(
        S.title("Results", "What did we actually achieve?"),
        h("div.res-grid", null, [
          tile("r1", `${F.read} / ${F.legible}`, `legible plates read across ${F.clips} real clips`),
          tile("r2", `${F.wrong}`, "wrong plates. Not one."),
          tile("r3", `${F.cam06Noon[0]} / ${F.cam06Noon[1]}`, "CAM06, government camera, noon"),
          tile("r4", `${F.delhi[0]} / ${F.delhi[1]}`, "Delhi street, hand-held"),
          tile("r5", `${F.vehicles[1]}`, `vehicles counted once each, from ${F.vehicles[0]} tracker IDs`),
          tile("r6", `${F.day.plates}`, `plates across a day of CAM06, ${F.day.wrong} wrong`),
        ]),
        S.tech(["<b>Legible</b> counted by eye, frame by frame, before scoring"], F.src)
      );
      return [() => ctx.in("#r1"), () => ctx.in("#r2"), () => ctx.in("#r3, #r4", { stagger: 300 }), () => ctx.in("#r5, #r6", { stagger: 300 })];
    },
  });

  /* ---------------------------------------------------------------- 35 */
  K.scene({
    id: "limits", act: "Act 33 · Limitations", title: "What still doesn't work",
    build(ctx) {
      const items = [
        `Plates under 22 px: ${Math.round((100 * (D.sizeBands.counts[2] + D.sizeBands.counts[3] + D.sizeBands.counts[4])) / D.sizeBands.total)}% of plates on these cameras`,
        "Night, glare and heavy occlusion",
        "Badly placed cameras: too high, too far, too steep",
        "Live reading on the grid needs a GPU host; a laptop CPU in Docker is far too slow",
        "The deployed plate detector is not yet evaluated by size, on human-checked labels",
        "Rules about people need a person tracker",
      ];
      ctx.el.append(
        h("div.title-tl", null, [h("div.kicker.r", { id: "k" }, "Limitations"), h("div.h2.r", { id: "t1" }, "What still doesn't work."), h("div.h2.r.c-track", { id: "t2" }, "The next engineering problems.")]),
        S.at(140, 330, 1640, null, h("ul.limits", null, items.map((t) => h("li.r", null, t))))
      );
      return [
        async () => { await ctx.in("#k, #t1"); },
        () => ctx.in(".limits li", { stagger: 380 }),
        async () => { ctx.$("#t1").classList.add("gone"); await ctx.in("#t2"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 36 */
  K.scene({
    id: "future", act: "Act 34 · Where next", title: "Where do we go next?",
    build(ctx) {
      const steps = [
        ["Better cameras", "placement, optics, IR for night: the biggest lever"],
        ["Better plate models", "small-object detection trained on human-checked hard cases"],
        ["Better use of time", "more frames per vehicle, vehicle-level aggregation"],
        ["Better reading", "the text reader in every image; calibrated confidence"],
        ["Better deployment", "GPUs at the edge, many cameras, monitored, with alerts"],
      ];
      ctx.el.append(
        S.title("Roadmap", "Where do we go next?"),
        S.at(140, 360, 1640, null, h("div.road", null, steps.map(([a, b], i) => h("div.step.r", { "data-i": i }, [h("span.step-n.num", null, String(i + 1)), h("b", null, a), h("span", null, b)]))))
      );
      return [() => ctx.in(".road .step", { stagger: 420 })];
    },
  });

  /* ---------------------------------------------------------------- 37 */
  K.scene({
    id: "finale", act: "Act 35 · The answer", title: "From pixels to information", cls: "black",
    build(ctx) {
      const words = ["Camera", "Pixels", "Vehicle", "Plate", "Tracking", "Enhancement", "OCR", "Validation", "Result"];
      const layer = h("div.fill.r.slow.media-layer", null, [K.media.video("cam06_1080p", { start: 49.6, end: 54.05, cls: "media-cover" }), h("div.fill.shade-bottom")]);
      ctx.el.append(
        layer,
        h("div.word-rain", null, words.map((w, i) => h("span.r.soft", { style: { left: `${8 + (i % 5) * 19}%`, top: `${18 + Math.floor(i / 5) * 22 + (i % 2) * 6}%` } }, w))),
        h("div.center", null, h("div.stack.final-type", null, [h("div.hero.r.soft", { id: "fp" }, "From pixels"), h("div.hero.r.soft.c-track", { id: "ti" }, "to information."), h("div.h2.r.soft", { id: "that" }, "That's ANPR.")]))
      );
      return [
        () => ctx.in(layer),
        async () => { for (const w of ctx.$$(".word-rain span")) { w.classList.add("in"); await ctx.wait(420); } },
        async () => { ctx.$(".word-rain").classList.add("gone"); layer.classList.add("dim-strong"); await ctx.in("#fp"); },
        () => ctx.in("#ti"),
        () => ctx.in("#that"),
      ];
    },
  });
})();
