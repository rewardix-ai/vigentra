/* Acts 1-5: the question, why ANPR exists, the camera, the stream, what the camera really sees. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const D = K.DATA;

  /* ---------------------------------------------------------------- 1 */
  K.scene({
    id: "opening", act: "Act 1 · The question", title: "A camera sees a vehicle", cls: "black",
    build(ctx) {
      const plate = S.box(S.at1299() && S.at1299().plate);
      const zoom = h("div.fill.zoombox", null, [
        K.media.video("cam06_1080p", { start: 49.6, end: 54.05, loop: false, autoplay: false, cls: "media-cover" }),
        h("div.fill.still", null, K.media.img("best_frame", { cls: "media-cover" })),
      ]);
      const layer = h("div.fill.r.slow.media-layer", null, [zoom, h("div.fill.shade-bottom")]);
      ctx.el.append(
        layer,
        h("div.cap-bottom", null, [h("div.h2.r.soft", { id: "l1" }, "A camera sees a vehicle."), h("div.h2.r.soft", { id: "l2" }, "But can it read it?")]),
        h("div.center", null, h("div.stack.title-anpr", null, [h("div.hero.r.slow", { id: "anpr" }, "ANPR"), h("div.lede.r", { id: "anpr-sub" }, "Automatic Number Plate Recognition")]))
      );
      const video = ctx.$("video");
      return [
        async () => {
          await ctx.wait(500);
          ctx.in(layer);
          if (video && video.play) video.play().catch(() => {});
          await ctx.wait(4500);
          ctx.$(".still").classList.add("in");
        },
        async () => { ctx.$(".still").classList.add("in"); await ctx.in("#l1"); },
        async () => {
          ctx.out("#l1");
          if (plate) ctx.move(zoom, K.ui.zoomTo(plate, { fill: 0.5 }), 3200);
          await ctx.wait(1400);
          await ctx.in("#l2");
        },
        async () => {
          ctx.out("#l2");
          layer.classList.add("gone");
          await ctx.wait(900);
          await ctx.in("#anpr");
          await ctx.in("#anpr-sub", { delay: 500 });
        },
      ];
    },
  });

  /* ---------------------------------------------------------------- 2 */
  K.scene({
    id: "problem", act: "Act 2 · Why this exists", title: "Hours of video, nobody watching",
    build(ctx) {
      const feeds = [
        ["wall_cam01", "GRID · cam01"], ["wall_cam02", "GRID · cam02"], ["cam06_noon", "GRID · cam06"],
        ["wall_cam04", "GRID · cam04"], ["delhi_raw", "DELHI · street"], ["wall_cam05", "GRID · cam05"],
      ].map(([slot, label]) => ({ label, media: () => K.media.video(slot, { cls: "media-cover" }) }));
      const wall = K.ui.wall(feeds, { columns: 3 });
      const wallBox = S.at(140, 150, 1640, null, wall, "wall-box");
      const stats = h("div.center.stats-over", null, h("div.stack", { style: { gap: "34px" } }, [
        h("div.h2.r", { id: "math" }, [h("span.num", { id: "cams" }, "0"), " cameras × ", h("span.num", { id: "hrs" }, "0"), " hours"]),
        h("div.hero.r.num", { id: "total" }, "0"),
        h("div.lede.r", { id: "total-l" }, "hours of video, in a sandbox."),
      ]));
      const state = h("div.center.stats-over", null, h("div.stack", { style: { gap: "26px" } }, [
        h("div.kicker.r", { id: "guj-k" }, "Gujarat"),
        h("div.hero.r.num", { id: "guj" }, "0"),
        h("div.lede.r", { id: "guj-l" }, "cameras across the state."),
      ]));
      const end = S.statement(["Watching doesn't scale.", "<span class='c-track'>Turn footage into searchable information.</span>"], { size: "h1" });
      end.classList.add("stats-over");
      ctx.el.append(wallBox, stats, state, end);
      return [
        () => ctx.in(".wall-tile", { stagger: 160 }),
        async () => {
          wallBox.classList.add("dim");
          await ctx.in("#math");
          ctx.count("#cams", D.sandbox.cameras, { dur: 900 });
          await ctx.count("#hrs", D.sandbox.hoursPerCamera, { dur: 900 });
          await ctx.in("#total");
          await ctx.count("#total", D.sandbox.cameras * D.sandbox.hoursPerCamera, { dur: 1300 });
          await ctx.in("#total-l");
        },
        async () => {
          ctx.out("#math, #total, #total-l");
          await ctx.wait(500);
          await ctx.in("#guj-k");
          await ctx.in("#guj");
          await ctx.count("#guj", D.sandbox.gujaratCameras, { dur: 1600, suffix: "+" });
          await ctx.in("#guj-l");
        },
        async () => {
          ctx.out("#guj-k, #guj, #guj-l");
          wallBox.classList.add("dimmer");
          await ctx.wait(400);
          await ctx.in('[data-line="0"]');
          await ctx.in('[data-line="1"]', { delay: 900 });
        },
      ];
    },
  });

  /* ---------------------------------------------------------------- 3 */
  K.scene({
    id: "camera", act: "Act 3 · The camera comes first", title: "Before AI, there is a camera",
    build(ctx) {
      ctx.el.append(
        S.title("Before AI", "There is a camera."),
        h("div.fill", {
          html: `<svg class="cam-svg" viewBox="0 0 1920 1080">
            <defs><linearGradient id="cone" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5ab0ff" stop-opacity=".38"/><stop offset="1" stop-color="#5ab0ff" stop-opacity=".05"/></linearGradient></defs>
            <line class="ground draw" x1="80" y1="930" x2="1120" y2="930"/>
            <line class="pole draw" x1="250" y1="930" x2="250" y2="330"/>
            <g class="cam-head r"><rect x="232" y="296" width="150" height="58" rx="10" transform="rotate(24 250 330)"/><circle cx="372" cy="380" r="13"/></g>
            <g class="dim-h r"><line x1="190" y1="335" x2="190" y2="930"/><text x="172" y="640" transform="rotate(-90 172 640)">height</text></g>
            <polygon class="cone r" points="372,382 600,930 1110,930"/>
            <text class="lbl r angle" x="430" y="470">angle</text>
            <text class="lbl r fov" x="760" y="720">field of view</text>
            <g class="car r"><rect x="0" y="0" width="120" height="44" rx="10"/></g>
          </svg>`,
        }),
        S.at(1190, 300, 600, null, h("div.stack", { style: { gap: "16px" } }, [
          h("div.frame.cam-screen.r", null, K.media.img("best_frame", { cls: "media-cover" })),
          h("div.caption.r", { id: "cap-shot" }, "What this camera captures: CAM06, Madhuram Bypass Road"),
        ])),
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "only" }, "The AI can only work with what the camera captures."))
      );
      return [
        async () => { await ctx.draw(".ground, .pole", 900); await ctx.in(".cam-head"); },
        async () => { await ctx.in(".dim-h"); await ctx.in(".cone"); await ctx.in(".angle, .fov", { stagger: 300 }); },
        async () => { await ctx.in(".car"); ctx.$(".car").classList.add("drive"); },
        async () => { await ctx.in(".cam-screen"); await ctx.in("#cap-shot"); await ctx.in("#only", { delay: 600 }); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 4 */
  K.scene({
    id: "placement", act: "Act 3 · The camera comes first", title: "Placement decides what can be read",
    build(ctx) {
      const tiny = (K.FAILURES || []).find((f) => f.label === "Tiny plate");
      const conds = (K.FAILURES || []).map((f) => {
        const why = { "Tiny plate": "too high, too far", "Low light": "darkness", "Headlight glare": "headlights", "Motion blur": "movement", "Low contrast": "backlight, haze" }[f.label] || "";
        return h("div.cond.r", null, [
          h("div.cond-img", null, S.pixels(f.file, null, { targetW: 220 })),
          h("div.cond-name", null, f.label),
          h("div.cond-why", null, `${why} · ${f.width_px}×${f.height_px} px`),
        ]);
      });
      ctx.el.append(
        S.title("Where the camera is", "Placement decides what can be read."),
        S.at(140, 330, 780, 420, h("div.cmp.r", { id: "good" }, [
          h("div.cmp-head", null, [h("span.pill.ok", null, "Good"), "CAM06 · 1080p · close, moderate angle"]),
          h("div.cmp-body", null, S.pixels("assets/img/plate_1299.png", null, { scale: 3 })),
          h("div.cmp-foot", null, [h("b.num", null, "124 px"), " wide plate. Readable."]),
        ])),
        S.at(1000, 330, 780, 420, h("div.cmp.r", { id: "poor" }, [
          h("div.cmp-head", null, [h("span.pill.bad", null, "Typical"), "Grid camera · high and far"]),
          h("div.cmp-body", null, [h("div.same-scale", null, S.pixels(tiny ? tiny.file : "", null, { scale: 3 })), h("div.blown.r", { id: "blown" }, S.pixels(tiny ? tiny.file : "", null, { targetW: 520 }))]),
          h("div.cmp-foot", null, [h("b.num", null, `${tiny ? tiny.width_px : "?"} px`), " wide, shown at the same scale. Median across the grid dataset: ", h("b.num", null, `${D.dataset.medianWidth} px`), "."]),
        ])),
        S.at(140, 790, 1640, 200, h("div.conds", null, conds)),
        h("div.center.end-over", null, h("div.h1.r.soft", { id: "eng" }, "ANPR starts with camera engineering,<br>not AI."))
      );
      ctx.$("#eng").innerHTML = "ANPR starts with camera engineering,<br>not AI.";
      return [
        () => ctx.in("#good"),
        async () => { await ctx.in("#poor"); await ctx.wait(1800); ctx.in("#blown"); },
        () => ctx.in(".cond", { stagger: 180 }),
        async () => { ctx.el.classList.add("hush"); await ctx.in("#eng"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 5 */
  K.scene({
    id: "stream", act: "Act 4 · Connecting the cameras", title: "From camera to computer",
    build(ctx) {
      const film = h("div.film", null, Array.from({ length: 9 }, () => h("span")));
      ctx.el.append(
        S.title("From camera to computer", "The camera doesn't send photographs."),
        h("div.fill", {
          html: `<svg class="net-svg" viewBox="0 0 1920 1080">
            <g class="cam-icon r"><rect x="150" y="470" width="190" height="96" rx="16"/><circle cx="320" cy="518" r="24"/><line x1="245" y1="566" x2="245" y2="640"/></g>
            <path class="link link-ai draw" d="M360 518 H 960"/>
            <path class="link link-hls draw" d="M300 566 C 420 780, 700 830, 960 830"/>
            <text class="lbl r l-ai" x="470" y="490">RTSP over TCP · for the AI</text>
            <text class="lbl r l-hls" x="520" y="900">HLS · for people watching</text>
          </svg>`,
        }),
        S.at(430, 498, 520, 44, film, "film-wrap r"),
        S.framed(K.media.video("cam06_noon", { cls: "media-cover" }), 980, 350, 600, 338, "r proc"),
        S.at(980, 700, 600, 40, h("div.caption", null, "Processing: the video, continuously, about 25 frames a second"), "r proc-cap"),
        S.at(980, 780, 420, 120, h("div.browser", null, [h("span.dots", null, "● ● ●"), h("span", null, "Console in a browser")]), "r br"),
        h("div.cap-bottom", null, h("div.h3.r.soft", { id: "video" }, "It sends video. Continuously.")),
        S.tech([
          `<b>Capture</b> ${D.pipeline.capture}`,
          "<b>Timing</b> from each frame's own timestamp; the declared frame rate is not trusted",
          "<b>Codecs</b> H.264 and H.265, mixed resolutions; a loop in the recording resets tracks",
          `<b>Viewing</b> ${D.pipeline.viewing}`,
        ], "EW/app/grid.py · sandbox integrator's guide")
      );
      return [
        async () => { await ctx.in(".cam-icon"); await ctx.draw(".link-ai", 900); ctx.in(".film-wrap"); film.classList.add("run"); },
        async () => { await ctx.in("#video"); await ctx.in(".proc"); await ctx.in(".proc-cap"); await ctx.in(".l-ai"); },
        async () => { ctx.out("#video"); await ctx.draw(".link-hls", 900); await ctx.in(".br"); await ctx.in(".l-hls"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- 6 */
  K.scene({
    id: "perfect", act: "Act 5 · What the camera sees", title: "Looks simple",
    build(ctx) {
      const plate = S.box(S.at1299() && S.at1299().plate);
      const zoom = h("div.fill.zoombox", null, K.media.img("best_frame", { cls: "media-cover" }));
      const crisp = h("div.center.crisp.r.zoom", null, h("div.frame.crisp-frame", null, K.media.img("journey_best", { cls: "media-fit" })));
      ctx.el.append(h("div.fill.r.layer", null, zoom), crisp, h("div.cap-bottom", null, h("div.h1.r.soft", { id: "simple" }, "Looks simple.")));
      return [
        () => ctx.in(".layer"),
        async () => {
          if (plate) await ctx.move(zoom, K.ui.zoomTo(plate, { fill: 0.6 }), 2200);
          ctx.$(".layer").classList.add("gone");
          await ctx.in(crisp);
        },
        () => ctx.in("#simple"),
      ];
    },
  });

  /* ---------------------------------------------------------------- 7 */
  K.scene({
    id: "reality", act: "Act 5 · What the camera sees", title: "This is the real input", cls: "black",
    build(ctx) {
      const fails = K.FAILURES || [];
      const pick = (label) => fails.find((f) => f.label === label);
      const items = [
        { node: () => K.media.video("cam06_night", { cls: "media-cover" }), label: "Darkness · headlights", sub: "CAM06 at night" },
        { node: () => K.media.video("tfl_low", { cls: "media-cover pixelated" }), label: "Low resolution", sub: "352 × 288 camera" },
        { node: () => K.media.video("cam06_noon", { start: 30, cls: "media-cover" }), label: "Compression · small plates", sub: "854 × 480 grid stream" },
        { node: () => K.media.video("delhi_raw", { start: 6, cls: "media-cover" }), label: "Moving camera · angles", sub: "Delhi, hand-held" },
        ...["Tiny plate", "Motion blur", "Headlight glare", "Low contrast"].map((label) => {
          const f = pick(label);
          return { node: () => (f ? h("div.fill.pixfill", null, S.pixels(f.file, null, { targetW: 1200 })) : h("div.missing", null, "[REAL CROP REQUIRED]")), label, sub: f ? `${f.width_px} × ${f.height_px} px · ${f.camera}` : "" };
        }),
      ];
      const reel = h("div.fill.reel");
      items.forEach((it, i) => reel.appendChild(h("div.fill.cut", { "data-i": i }, [it.node(), h("div.cut-label", null, [h("div.h2", null, it.label), h("div.caption", null, it.sub)])])));
      ctx.el.append(reel, h("div.center.end-over", null, h("div.h1.r.soft", { id: "real" }, "This is the real input.")));
      const cuts = ctx.$$(".cut");
      return [
        async () => {
          for (let i = 0; i < cuts.length; i += 1) {
            cuts.forEach((c, j) => c.classList.toggle("on", i === j));
            await ctx.wait(1700);
          }
          reel.classList.add("collage");
          cuts.forEach((c) => c.classList.add("on"));
        },
        async () => { reel.classList.add("collage", "dim"); cuts.forEach((c) => c.classList.add("on")); await ctx.in("#real"); },
      ];
    },
  });
})();
