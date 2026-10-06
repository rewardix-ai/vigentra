/* Vigentra, the question, and why it is hard: the brand, one car, the camera wall, pixels, and
 * the real footage. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const D = K.DATA;

  /* ---------------------------------------------------------------- brand */
  K.scene({
    id: "brand", act: "Vigentra", title: "Vigentra", cls: "black nobrand",
    build(ctx) {
      const tiles = [["cam06_1080p", 30], ["delhi_raw", 4], ["reel_cam04", 0], ["reel_cam15", 0]];
      const bg = h("div.fill.brand-bg", null, tiles.map(([slot, start]) => h("div.bg-tile", null, K.media.video(slot, { start, cls: "media-cover" }))));
      const lockup = S.lockup();
      const sub = h("div.lede.r.brand-sub", null, "Number-plate recognition on real CCTV.");
      ctx.el.append(bg, h("div.fill.brand-shade"), h("div.center", null, h("div.stack.brand-stack", null, [lockup, sub])));
      return [
        async () => {
          ctx.auto(5);
          await ctx.wait(200);
          bg.classList.add("in");
          await ctx.in(".lk-mark");
          ctx.$(".lk-word").classList.add("in");
          await ctx.wait(1000);
          await ctx.in(".lk-tag");
        },
        () => { ctx.auto(4); return ctx.in(sub); },
      ];
    },
  });

  /* ---------------------------------------------------------------- opening */
  K.scene({
    id: "opening", act: "The question", title: "A camera sees a vehicle", cls: "black",
    build(ctx) {
      const plate = S.box(S.at1299() && S.at1299().plate);
      const zoom = h("div.fill.zoombox", null, [
        K.media.video("cam06_1080p", { start: 49.6, end: 54.05, loop: false, autoplay: false, cls: "media-cover" }),
        h("div.fill.still", null, K.media.img("best_frame", { cls: "media-cover" })),
      ]);
      const layer = h("div.fill.r.slow.media-layer", null, [zoom, h("div.fill.shade-bottom")]);
      ctx.el.append(
        layer,
        h("div.cap-bottom", null, [
          h("div.h2.r.soft", { id: "l1" }, "A camera sees a vehicle."),
          h("div.h2.r.soft", { id: "l2" }, "Can it tell us which one?"),
          h("div.h2.r.soft", { id: "l3" }, ["Vigentra ", h("span.c-ok", null, "can.")]),
        ])
      );
      const video = ctx.$("video");
      return [
        async () => {
          ctx.auto(5.5);
          await ctx.wait(400);
          ctx.in(layer);
          if (video && video.play) video.play().catch(() => {});
          await ctx.wait(1600);
          ctx.in("#l1");
          await ctx.wait(2900);
          ctx.$(".still").classList.add("in");
        },
        async () => {
          ctx.auto(4.5);
          ctx.$(".still").classList.add("in");
          ctx.out("#l1");
          if (plate) ctx.move(zoom, K.ui.zoomTo(plate, { fill: 0.5 }), 3200);
          await ctx.wait(1400);
          await ctx.in("#l2");
        },
        async () => { ctx.auto(3); ctx.out("#l2"); await ctx.wait(400); await ctx.in("#l3"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- wall */
  K.scene({
    id: "wall", act: "The problem", title: "Nobody can watch them all",
    build(ctx) {
      const grid = ["01", "02", "04", "05", "06", "07", "08", "10", "11", "12", "13", "14", "15", "16"]
        .map((n) => ({ label: `GRID · cam${n}`, media: () => K.media.video(`wall_cam${n}`, { cls: "media-cover" }) }));
      const feeds = [...grid.slice(0, 5), { label: "GRID · cam06 · 480p", media: () => K.media.video("cam06_noon", { start: 20, cls: "media-cover" }) },
        ...grid.slice(5, 11), { label: "DELHI · street", media: () => K.media.video("delhi_raw", { cls: "media-cover" }) }, ...grid.slice(11)];
      const wallBox = S.at(60, 40, 1800, null, K.ui.wall(feeds, { columns: 4 }), "wall-box");
      const say = (id, cls, html) => h(`div.${cls}.r.soft`, { id, html });
      ctx.el.append(
        wallBox,
        h("div.center.over", null, h("div.stack.over-stack", null, [
          h("div.hero.r.num", { id: "cams" }, "0"),
          say("cams-l", "h2", "cameras on the grid."),
        ])),
        h("div.center.over", null, say("nobody", "h1", "Nobody can watch them all.")),
        h("div.center.over", null, say("reads", "h1", "Vigentra turns this video<br><span class='c-track'>into plates you can search.</span>"))
      );
      return [
        () => { ctx.auto(7); return ctx.in(".wall-tile", { stagger: 70 }); },
        async () => {
          ctx.auto(4);
          wallBox.classList.add("dim");
          await ctx.in("#cams");
          ctx.count("#cams", D.grid.cameras, { dur: 1100 });
          await ctx.in("#cams-l", { delay: 300 });
        },
        async () => { ctx.auto(3.5); ctx.out("#cams, #cams-l"); await ctx.in("#nobody"); },
        async () => { ctx.auto(4); ctx.out("#nobody"); wallBox.classList.add("dimmer"); await ctx.in("#reads"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- pixels */
  K.scene({
    id: "pixels", act: "The problem", title: "The plate is a tiny part of the picture",
    build(ctx) {
      const s = S.at1299() || {};
      const v = S.box(s.vehicle);
      const p = S.box(s.plate);
      const pw = p ? Math.round(p.w * 1920) : 0;
      const ph = p ? Math.round(p.h * 1080) : 0;
      const share = p ? ((100 * pw * ph) / (1920 * 1080)).toFixed(2) : "?";
      const zoom = h("div.fill.zoombox.kb", null, [K.media.img("best_frame", { cls: "media-cover" }), h("div.fill.ov")]);
      const boxes = K.ui.boxes(zoom.querySelector(".ov"), [v && { id: "v", kind: "vehicle", ...v }, p && { id: "p", kind: "plate", ...p }].filter(Boolean));
      const pixView = h("div.center.r.zoom", { id: "pix" }, h("div.stack", { style: { alignItems: "center", gap: "26px" } }, [
        S.pixels("assets/img/plate_1299.png", { scale: 7, grid: true }),
        h("div.h3", null, [h("span.c-plate.num", null, `${pw} × ${ph} pixels`), ` · ${share}% of the picture`]),
        h("div.caption", null, "The real pixels the camera recorded. Every square is one pixel."),
      ]));
      ctx.el.append(
        h("div.fill.frame-layer", null, zoom),
        h("div.cap-bottom", null, h("div.h2.r.soft", { id: "find" }, "Somewhere in this picture is a number plate.")),
        pixView
      );
      return [
        async () => { await ctx.wait(600); await ctx.in("#find"); },
        async () => { ctx.out("#find"); if (v) ctx.move(zoom, K.ui.zoomTo(v, { fill: 0.8 }), 1800); await boxes.show(ctx, "v"); await ctx.wait(500); boxes.hide("v"); if (p) await ctx.move(zoom, K.ui.zoomTo(p, { fill: 0.45 }), 2000); await boxes.show(ctx, "p"); },
        async () => { ctx.$(".frame-layer").classList.add("gone"); await ctx.in("#pix"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- reality */
  K.scene({
    id: "reality", act: "The problem", title: "This is the real input", cls: "black",
    build(ctx) {
      const items = [
        ["reel_cam07", 0, "Night", "Grid cam07"],
        ["reel_cam15", 0, "Headlights", "Grid cam15 · 21:00"],
        ["reel_cam01", 0, "Far away", "Grid cam01 · Chiman bhai Bridge"],
        ["cam06_noon", 30, "Low resolution", "Grid cam06 · 854 × 480 stream"],
        ["delhi_raw", 6, "Moving camera", "Delhi · hand-held"],
        ["reel_cam04", 0, "Crowded junction", "Grid cam04 · 21:00"],
      ];
      const reel = h("div.fill.reel");
      items.forEach(([slot, start, label, sub], i) => reel.appendChild(h("div.fill.cut", { "data-i": i }, [
        K.media.video(slot, { start, cls: "media-cover" }),
        h("div.cut-label", null, [h("div.h1", null, label), h("div.caption", null, sub)]),
      ])));
      ctx.el.append(reel, h("div.center.over", null, h("div.h1.r.soft.shadowed", { id: "real" }, "This is the real input.")));
      const cuts = ctx.$$(".cut");
      return [
        async () => {
          ctx.auto(10.5);
          for (let i = 0; i < cuts.length; i += 1) {
            cuts.forEach((c, j) => c.classList.toggle("on", i === j));
            await ctx.wait(1500);
          }
          reel.classList.add("collage");
          cuts.forEach((c) => c.classList.add("on"));
        },
        async () => { ctx.auto(4); reel.classList.add("collage", "dim"); cuts.forEach((c) => c.classList.add("on")); await ctx.in("#real"); },
      ];
    },
  });
})();
