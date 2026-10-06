/* Acts 13-19: the quality gate, enhancement, OCR, validation, Indian plates, the hardest cases,
 * and the stress test on CAM06 and Delhi. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const D = K.DATA;

  /* ---------------------------------------------------------------- 15 */
  K.scene({
    id: "gate", act: "Act 13 · Quality", title: "Should we even try?",
    build(ctx) {
      const E = K.EVIDENCE || { quality: {} };
      const G = D.pipeline.gate;
      const fail = (label) => (K.FAILURES || []).find((f) => f.label === label) || {};
      const sharpFrom = (reason) => { const m = /sharpness_below_gate:([\d.]+)/.exec(reason || ""); return m ? Number(m[1]) : null; };
      const cases = [
        { label: "GJ23H1546", file: "assets/img/journey_best.png", w: E.quality.width_px, hgt: E.quality.height_px, sharp: E.quality.sharpness_lap, contrast: E.quality.local_contrast, reason: "" },
        { label: "Tiny plate", file: fail("Tiny plate").file, w: fail("Tiny plate").width_px, hgt: fail("Tiny plate").height_px, sharp: null, contrast: fail("Tiny plate").contrast, reason: fail("Tiny plate").reason },
        { label: "Low contrast", file: fail("Low contrast").file, w: fail("Low contrast").width_px, hgt: fail("Low contrast").height_px, sharp: sharpFrom(fail("Low contrast").reason), contrast: fail("Low contrast").contrast, reason: fail("Low contrast").reason },
      ];
      const meter = (name, value, min, unit = "") => {
        const ok = value == null ? null : value >= min;
        const pct = value == null ? 0 : Math.min(100, (value / (min * 2)) * 100);
        return h("div.meter" + (ok === false ? ".fail" : ok ? ".pass" : ""), null, [
          h("span.m-name", null, name),
          h("span.m-track", null, [h("span.m-fill", { style: { width: `${pct}%` } }), h("span.m-min", { style: { left: "50%" } })]),
          h("span.m-val.num", null, value == null ? "not measured" : `${S.fmt(value, value < 100 ? 1 : 0)}${unit} (needs ≥ ${min}${unit})`),
        ]);
      };
      const tabs = K.ui.tabs(cases, (c) => {
        const pass = c.w >= G.width && c.hgt >= G.height && (c.sharp == null || c.sharp >= G.sharpness) && (c.contrast == null || c.contrast >= G.contrast) && !c.reason;
        return h("div.gate", null, [
          h("div.gate-img", null, c.file ? S.pixels(c.file, null, { targetW: 520 }) : h("div.missing", null, "[REAL CROP REQUIRED]")),
          h("div.gate-meters", null, [
            meter("Width", c.w, G.width, " px"), meter("Height", c.hgt, G.height, " px"),
            meter("Sharpness", c.sharp, G.sharpness), meter("Contrast", c.contrast, G.contrast),
          ]),
          h("div.verdict" + (pass ? ".pass" : ".fail"), null, pass ? "Read it." : `Don't read it: ${c.reason.replace(/_/g, " ").replace(":", " · ")}`),
        ]);
      });
      ctx.el.append(
        S.title("The quality gate", "Is this image good enough to read?"),
        S.at(140, 300, 1640, null, h("div.r", { id: "tabs" }, tabs)),
        S.tech([`<b>Gate</b> width ≥ ${G.width} px, height ≥ ${G.height} px, sharpness ≥ ${G.sharpness}, contrast ≥ ${G.contrast}`, `<b>Reading floor</b> crops narrower than ${G.readFloor} px are never read`], D.pipeline.src)
      );
      return [() => ctx.in("#tabs"), () => tabs.select(1), () => tabs.select(2)];
    },
  });

  /* ---------------------------------------------------------------- 16 */
  K.scene({
    id: "enhance", act: "Act 14 · Enhancement", title: "Can we make it better?",
    build(ctx) {
      const steps = D.pipeline.enhancement.map((s) => h("li.r", null, s));
      const trio = h("div.trio", null, [
        ["journey_best", "Best single crop"], ["journey_fused", "12 frames aligned and combined"], ["journey_enhanced", "Glare, noise, blur treated"],
      ].map(([slot, cap], i) => h("div.trio-item.r", { "data-i": i }, [h("div.frame.trio-img", null, K.media.img(slot, { cls: "media-fit" })), h("div.caption", null, cap)])));
      const ba = K.ui.beforeAfter(K.media.img("journey_best", { cls: "media-fit" }), K.media.img("journey_enhanced", { cls: "media-fit" }), ["Captured", "Enhanced"]);
      ctx.el.append(
        S.title("Enhancement", "Can we make it better?"),
        S.at(140, 300, 420, null, h("ol.enh-steps", null, steps)),
        S.at(620, 300, 1180, null, trio),
        S.at(620, 300, 1180, 380, h("div.r.ba-wrap", { id: "ba" }, ba)),
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "limit" }, "Enhancement cannot create information the camera never captured.")),
        S.tech([`<b>Chain</b> ${D.pipeline.enhancement.join(" → ")}`, `<b>Super-resolution</b> ${D.pipeline.sr}`], D.pipeline.src)
      );
      return [
        () => ctx.in(".enh-steps li", { stagger: 220 }),
        () => ctx.in(".trio-item", { stagger: 450 }),
        async () => { ctx.$(".trio").classList.add("gone"); await ctx.in("#ba"); },
        () => ctx.in("#limit"),
      ];
    },
  });

  /* ---------------------------------------------------------------- 17 */
  K.scene({
    id: "ocr", act: "Act 15 · Reading", title: "From image to characters",
    build(ctx) {
      const E = K.EVIDENCE || { plate: "", per_char_conf: [] };
      const plate = K.ui.plate(E.plate, { size: 1.05, hidden: true });
      const confs = h("div.charconf", null, [...E.plate].map((ch, i) => {
        const c = E.per_char_conf[i] || 0;
        return h("div.cc", null, [h("div.cc-bar", { style: { "--v": `${Math.round(c * 100)}%` } }), h("div.cc-v.num", null, c.toFixed(2))]);
      }));
      ctx.el.append(
        S.title("Optical character recognition", "From pixels to characters."),
        S.at(560, 260, 800, 190, h("div.frame.r", { id: "src" }, K.media.img("journey_enhanced", { cls: "media-fit" }))),
        h("div.center.ocr-plate", null, plate),
        S.at(560, 780, 800, null, h("div.r", { id: "confs" }, [confs, h("div.caption", null, "Confidence of each character after the readers' votes are combined")])),
        h("div.where-what", null, [h("div.h3.r", { id: "where" }, [h("span.c-plate", null, "Detection"), " tells us where."]), h("div.h3.r", { id: "what" }, [h("span.c-ok", null, "OCR"), " tells us what."])]),
        S.tech([`<b>Readers</b> ${D.pipeline.readers.join(" · ")}`, `<b>Fusion</b> ${D.pipeline.rover}`, "<b>Note</b> the text reader runs on the Mac readers; the Docker image falls back to the two CRNNs"], D.pipeline.src)
      );
      return [
        () => ctx.in("#src"),
        () => K.ui.revealPlate(ctx, plate, 260),
        async () => { await ctx.in("#confs"); ctx.$(".charconf").classList.add("in"); },
        async () => { await ctx.in("#where"); await ctx.in("#what", { delay: 500 }); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 18 */
  K.scene({
    id: "validation", act: "Act 16 · Validation", title: "OCR can be wrong",
    build(ctx) {
      const E = K.EVIDENCE || { top_readings: [], plate: "", frames: {} };
      const total = E.hypotheses_total || 0;
      const rows = (E.top_readings || []).slice(0, 6).map((r) => ({ src: `${r.count}×`, text: r.text, note: `best score ${r.best}` }));
      const checks = [
        [`${E.frames.agreeing} frames agree on one reading`, true],
        ["It is a valid Indian plate format", E.valid_format],
        ["The crops contain real character shapes (glyph check)", true],
        [`Runner-up ${E.alternates && E.alternates[0] ? E.alternates[0].plate + " at " + E.alternates[0].confidence : ""} is far behind`, true],
      ];
      ctx.el.append(
        S.title("Validation", `${total} readings of one plate. Which do we believe?`),
        S.at(140, 300, 1000, null, h("div.r", { id: "reads" }, K.ui.readings(rows, E.plate))),
        S.at(1200, 300, 600, null, h("div.checks", null, checks.map(([t, ok]) => h("div.check.r", null, [h("span.tick" + (ok ? ".ok" : ".no"), null, ok ? "✓" : "✕"), t])))),
        S.at(1200, 650, 600, null, h("div.confirmed.r", { id: "conf" }, [h("div.kicker", null, "Status"), h("div.h2.c-ok", null, "CONFIRMED"), h("div.mono.h3", null, E.plate)])),
        S.at(140, 300, 1640, 600, h("div.frame.r.sheet", { id: "sheet" }, [K.media.img("verify_sheet", { cls: "media-fit" }), h("div.sheet-cap", null, "Readings checked by eye against the engine: where the evidence was not there, it refused instead of guessing.")])),
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "believe" }, "Reading a plate is not enough. We decide whether we believe it.")),
        S.tech([`<b>Confirm rule</b> ${D.pipeline.confirm}`, `<b>Glyph check</b> ${D.pipeline.glyph}`], E.source || "")
      );
      return [
        async () => { await ctx.in("#reads"); await ctx.in(".reading", { stagger: 160 }); },
        () => ctx.in(".check", { stagger: 260 }),
        () => ctx.in("#conf"),
        async () => { ["#reads", ".checks", "#conf"].forEach((s) => ctx.$(s) && ctx.$(s).closest(".abs").classList.add("gone")); await ctx.in("#sheet"); },
        () => ctx.in("#believe"),
      ];
    },
  });

  /* ---------------------------------------------------------------- 19 */
  K.scene({
    id: "india", act: "Act 17 · Indian plates", title: "Plates are not random text",
    build(ctx) {
      const seg = (txt, label, cls) => h("div.seg.r", null, [h("div.seg-txt.mono" + (cls ? "." + cls : ""), null, txt), h("div.seg-label", null, label)]);
      ctx.el.append(
        S.title("Domain knowledge", "Indian plates follow a structure."),
        h("div.center.segs-wrap", null, h("div.segs", null, [seg("GJ", "State"), seg("23", "District office (RTO)"), seg("H", "Series"), seg("1546", "Number")])),
        S.at(140, 760, 1640, null, h("div.fix.r", { id: "fix" }, [
          h("span.mono.fix-in", null, ["6", "J23H1546"].map((t, i) => h("span" + (i === 0 ? ".bad-ch" : ""), null, t))),
          h("span.fix-arrow", null, "→"),
          h("span.mono.fix-out", null, [h("span.ok-ch", null, "G"), "J23H1546"]),
          h("span.fix-why", null, "The first slot must be a letter, and 6 is a common misread of G."),
        ])),
        S.at(140, 900, 1640, null, h("div.caption.r", { id: "fmt" }, `Formats the grammar knows: ${D.grammar.formats.join(" · ")}. ${D.grammar.stateCodes} state codes; Gujarat RTOs ${D.grammar.gujaratRto}. (Example of the rule; not a specific read.)`)),
        S.tech([`<b>Grammar</b> positional character types per format`, `<b>Codes</b> ${D.grammar.stateCodes} state codes; GJ RTO ${D.grammar.gujaratRto}`], D.grammar.src)
      );
      return [() => ctx.in(".seg", { stagger: 380 }), () => ctx.in("#fix"), () => ctx.in("#fmt")];
    },
  });

  /* ---------------------------------------------------------------- 20 */
  K.scene({
    id: "breakit", act: "Act 18 · The hardest cases", title: "Now break it.", cls: "black",
    build(ctx) {
      const cuts = (K.FAILURES || []).map((f) => ({
        node: () => h("div.fill.pixfill", null, S.pixels(f.file, null, { targetW: 1300 })),
        label: f.label, sub: `${f.width_px} × ${f.height_px} px · ${f.reason ? "refused: " + f.reason.replace(/_/g, " ") : f.status.toLowerCase()}`,
      }));
      cuts.push({ node: () => K.media.video("cam06_night", { start: 10, cls: "media-cover" }), label: "Night", sub: "CAM06 after dark" });
      cuts.push({ node: () => K.media.video("tfl_low", { cls: "media-cover pixelated" }), label: "352 × 288", sub: "the whole frame" });
      const reel = h("div.fill.reel");
      cuts.forEach((c, i) => reel.appendChild(h("div.fill.cut", { "data-i": i }, [c.node(), h("div.cut-label", null, [h("div.h2", null, c.label), h("div.caption.c-bad", null, c.sub)])])));
      ctx.el.append(
        h("div.center", null, h("div.hero.r.soft", { id: "bk" }, "Now break it.")),
        reel,
        h("div.center.end-over", null, h("div.h1.r.soft", { id: "begins" }, "This is where the real engineering begins."))
      );
      const nodes = ctx.$$(".cut");
      return [
        () => ctx.in("#bk"),
        async () => {
          ctx.out("#bk");
          for (let i = 0; i < nodes.length; i += 1) {
            nodes.forEach((n, j) => n.classList.toggle("on", i === j));
            await ctx.wait(1300);
          }
        },
        async () => { reel.classList.add("collage", "dim"); nodes.forEach((n) => n.classList.add("on")); await ctx.in("#begins"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 21 */
  K.scene({
    id: "challenge", act: "Act 19 · Real stress test", title: "CAM06 and Delhi",
    build(ctx) {
      const F = D.final;
      const panel = (id, slot, title, legible, read) => h("div.chal.r", { id }, [
        h("div.frame.chal-media", null, K.media.video(slot, { cls: "media-cover" })),
        h("div.chal-title.h3", null, title),
        h("div.chal-nums", null, [
          h("div", null, [h("span.kicker", null, "Readable by eye"), h("b.num", { "data-to": legible }, "–")]),
          h("div", null, [h("span.kicker", null, "Read by the system"), h("b.num.c-ok", { "data-to": read }, "–")]),
          h("div", null, [h("span.kicker", null, "Wrong"), h("b.num.c-ok.zero", { "data-to": 0 }, "–")]),
        ]),
      ]);
      ctx.el.append(
        S.title("Real stress test", "Can it read enough, and never guess?"),
        S.at(140, 280, 790, null, panel("c6", "cam06_vigentra", "CAM06 · government camera · noon", F.cam06Noon[1], F.cam06Noon[0])),
        S.at(990, 280, 790, null, panel("dl", "delhi_vigentra", "Delhi · busy street · hand-held", F.delhi[1], F.delhi[0])),
        S.tech(["<b>Ground truth</b> every legible plate counted by eye, frame by frame", `<b>Videos</b> the system's own output; each plate is shown beside the crop it was read from`], F.src)
      );
      const countIn = async (panelId) => {
        for (const b of ctx.$$(`#${panelId} b[data-to]`)) {
          await ctx.count(b, Number(b.dataset.to), { dur: 900 });
          await ctx.wait(350);
        }
      };
      return [
        () => ctx.in("#c6"),
        () => countIn("c6"),
        () => ctx.in("#dl"),
        () => countIn("dl"),
      ];
    },
  });
})();
