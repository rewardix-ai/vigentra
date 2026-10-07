/* The control room comes up; the problem (too many cameras, hard footage); one car on CAM06. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const D = K.DATA;

  /* ---------------------------------------------------------------- boot */
  K.scene({
    id: "boot", act: "Vigentra", title: "Vigentra control room", cls: "black nochrome", src: "",
    build(ctx) {
      const tiles = [["cam06_1080p", 30], ["delhi_raw", 4], ["reel_cam04", 0], ["reel_cam15", 0]];
      const bg = h("div.fill.brand-bg", null, tiles.map(([slot, start]) => h("div.bg-tile", null, K.media.video(slot, { start, cls: "media-cover" }))));
      const lines = [
        ["Cameras", "30 grid · Delhi street"],
        ["Vehicle detector", "YOLO11s"],
        ["Tracker", "ByteTrack"],
        ["Plate detector", "YOLO11n"],
        ["Readers", "CRNN · vote across frames"],
        ["Plate rules", "Indian formats"],
      ];
      const boot = h("div.bootlog", null, [
        ...lines.map(([k]) => h("div.bl", null, [h("span.bl-k", null, k), h("span.bl-dots"), h("span.bl-v")])),
        h("div.bl.final"),
      ]);
      ctx.el.append(bg, h("div.fill.brand-shade"), h("div.center", null, h("div.stack.boot-stack", null, [S.lockup({ size: 0.75 }), boot])));
      return [
        async () => {
          ctx.auto(11);
          await ctx.wait(200);
          bg.classList.add("in");
          await ctx.in(".lk-mark");
          ctx.$(".lk-word").classList.add("in");
          await ctx.wait(900);
          await ctx.in(".lk-tag");
          const rows = ctx.$$(".bl:not(.final)");
          for (let i = 0; i < rows.length; i += 1) {
            rows[i].classList.add("in");
            await ctx.type(rows[i].querySelector(".bl-v"), lines[i][1], 45);
            await ctx.wait(110);
          }
          const fin = ctx.$(".bl.final");
          fin.classList.add("in");
          await ctx.type(fin, "Number-plate recognition on real CCTV", 45);
        },
      ];
    },
  });

  /* ---------------------------------------------------------------- wall */
  K.scene({
    id: "wall", act: "The problem", title: "Nobody can watch them all", src: "SRC grid camera recordings cam01–cam16 · Delhi street clip",
    build(ctx) {
      const grid = ["01", "02", "04", "05", "06", "07", "08", "10", "11", "12", "13", "14", "15", "16"]
        .map((n) => ({ label: `cam${n}`, media: () => K.media.video(`wall_cam${n}`, { cls: "media-cover" }) }));
      const feeds = [...grid.slice(0, 5), { label: "cam06 · 480p", media: () => K.media.video("cam06_noon", { start: 20, cls: "media-cover" }) },
        ...grid.slice(5, 11), { label: "Delhi · street", media: () => K.media.video("delhi_raw", { cls: "media-cover" }) }, ...grid.slice(11)];
      const [X, Y, W] = [111, 64, 1698];
      const wallBox = S.at(X, Y, W, null, K.ui.wall(feeds, { columns: 4 }), "wall-box");
      ctx.el.append(
        wallBox,
        S.banner("The grid", `<span class="num" id="n30">0</span> cameras.`, { id: "b1" }),
        S.banner("The problem", "Nobody can watch them all.", { id: "b2", tone: "bad" }),
        S.banner("Vigentra", "It turns this video into plates you can search.", { id: "b3", tone: "ok" })
      );
      return [
        async () => {
          ctx.auto(7);
          const tiles = ctx.$$(".wall-tile");
          tiles.forEach((t) => t.classList.add("in"));
          if (ctx.instant) return;
          // one camera, CAM06, full screen; then the room pulls back to all sixteen
          const tile = tiles[5];
          const k = 1920 / tile.offsetWidth;
          wallBox.style.transformOrigin = "0 0";
          wallBox.style.transition = "none";
          wallBox.style.transform = `translate(${-X - k * tile.offsetLeft}px, ${-Y - k * tile.offsetTop}px) scale(${k})`;
          wallBox.getBoundingClientRect();
          await ctx.wait(900);
          wallBox.style.transition = "transform 2.2s var(--ease-io), opacity 0.9s var(--ease)";
          wallBox.style.transform = "none";
        },
        async () => { ctx.auto(4.5); wallBox.classList.add("dim"); await S.show(ctx, "#b1"); ctx.count("#n30", D.grid.cameras, { dur: 900 }); },
        async () => { ctx.auto(3.5); S.hide(ctx, "#b1"); await S.show(ctx, "#b2"); },
        async () => { ctx.auto(4.5); S.hide(ctx, "#b2"); wallBox.classList.add("dimmer"); await S.show(ctx, "#b3"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- reality */
  K.scene({
    id: "reality", act: "The problem", title: "This is the real input", src: "SRC grid cam07 · cam15 · cam01 · cam06 · cam04 · Delhi street clip",
    build(ctx) {
      const items = [
        ["reel_cam07", 0, "Night", "cam07"],
        ["reel_cam15", 0, "Headlights", "cam15 · 21:00"],
        ["reel_cam01", 0, "Far away", "cam01 · Chiman bhai Bridge"],
        ["cam06_noon", 30, "Low resolution", "cam06 · 854 × 480"],
        ["delhi_raw", 6, "Moving camera", "Delhi · hand-held"],
        ["reel_cam04", 0, "Crowded", "cam04 · 21:00"],
      ];
      const panels = items.map(([slot, start, label, cam], i) => S.panel(40 + (i % 3) * 620, 74 + Math.floor(i / 3) * 398, 600, 378, label,
        S.monitor(K.media.video(slot, { start, cls: "media-cover" })), { right: cam }));
      ctx.el.append(...panels, S.banner("Signal conditions", "This is the real input.", { id: "b1" }));
      return [
        async () => {
          ctx.auto(8);
          for (const p of panels) { p.classList.add("in"); await ctx.wait(450); }
        },
        async () => { ctx.auto(4); await S.show(ctx, "#b1"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- target */
  K.scene({
    id: "target", act: "One vehicle", title: "A camera sees a vehicle", cls: "black", src: "SRC CAM06 · Madhuram Bypass Road · 1920 × 1080 · 17-06-2026",
    build(ctx) {
      const s = S.at1299() || {};
      const plate = S.box(s.plate);
      const pw = s.plate_px || (plate ? Math.round(plate.w * 1920) : 0); // the detector's own width, as in the evidence buffer
      const ph = plate ? Math.round(plate.h * 1080) : 0;
      const zoom = h("div.fill.zoombox", null, [
        K.media.video("cam06_1080p", { start: 49.6, end: 54.05, loop: false, autoplay: false, cls: "media-cover" }),
        h("div.fill.still", null, K.media.img("best_frame", { cls: "media-cover" })),
      ]);
      const layer = h("div.fill.r.slow.media-layer", null, S.monitor(zoom, { tl: "CAM06 · Madhuram Bypass Rd", tr: S.rec(), full: true }));
      const readout = S.panel(1180, 96, 680, 600, "Plate · frame 1299", h("div.pix-read", null, [
        S.pixels("assets/img/plate_1299.png", { scale: 3, grid: true }),
        h("div.ro", null, [h("span.k", null, "Size on the camera"), h("span.v.c-plate", null, `${pw} × ${ph} px`), h("span.s", null, `${((100 * pw * ph) / (1920 * 1080)).toFixed(2)}% of the picture`)]),
      ]), { id: "pr" });
      ctx.el.append(
        layer, readout,
        S.banner("CAM06", "A camera sees a vehicle.", { id: "b1" }),
        S.banner("The question", "Can it tell us which one?", { id: "b2" }),
        S.banner("Vigentra", "It can, from a plate this small.", { id: "b3", tone: "ok" })
      );
      const video = ctx.$("video");
      return [
        async () => {
          ctx.auto(5.5);
          await ctx.wait(300);
          ctx.in(layer);
          if (video && video.play) video.play().catch(() => {});
          await ctx.wait(1500);
          S.show(ctx, "#b1");
          await ctx.wait(2900);
          ctx.$(".still").classList.add("in");
        },
        async () => {
          ctx.auto(4.5);
          ctx.$(".still").classList.add("in");
          S.hide(ctx, "#b1");
          if (plate) ctx.move(zoom, `translateX(-330px) ${K.ui.zoomTo(plate, { fill: 0.42 })}`, 3000);
          await ctx.wait(1200);
          await S.show(ctx, "#b2");
        },
        async () => { ctx.auto(5); S.hide(ctx, "#b2"); await ctx.in(readout); await S.show(ctx, "#b3"); },
      ];
    },
  });
})();
