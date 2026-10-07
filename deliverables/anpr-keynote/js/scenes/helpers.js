/* Scene helpers: the few layouts the scenes share, so each scene file reads as its story. */
(function () {
  const K = window.K;
  const h = K.h;

  K.S = {
    /** Absolutely placed block on the 1920×1080 stage. */
    at(x, y, w, hgt, child, cls = "") {
      const style = { left: `${x}px`, top: `${y}px` };
      if (w != null) style.width = `${w}px`;
      if (hgt != null) style.height = `${hgt}px`;
      return h("div.abs" + (cls ? "." + cls.split(" ").join(".") : ""), { style }, child);
    },

    /** Kicker + headline, top left. */
    title(kicker, text, { size = "h2" } = {}) {
      // .auto: fades in by itself when the scene opens (css/scenes.css); no step needed
      return h("div.title-tl.auto", null, [h("div.kicker", null, kicker), h(`div.${size}`, { style: { marginTop: "18px" }, html: text })]);
    },

    /** The technical layer: only visible with T. */
    tech(lines, src) {
      return h("div.tech.tech-panel", null, [...lines.map((l) => h("div", { html: l })), src ? h("span.src", null, src) : null]);
    },

    /** A framed piece of media at a position. */
    framed(node, x, y, w, hgt, extra = "") {
      return h("div.frame.abs" + (extra ? "." + extra.split(" ").join(".") : ""), { style: { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${hgt}px` } }, node);
    },

    /** An image drawn with hard pixels (what the camera really captured), at `scale` canvas
     * pixels per image pixel or at `targetW` wide. */
    pixels(src, { targetW = null, scale = null, grid = false } = {}) {
      const canvas = h("canvas.pix");
      const img = new Image();
      img.onload = () => {
        const s = targetW ? targetW / img.naturalWidth : scale || 8;
        canvas.width = Math.round(img.naturalWidth * s);
        canvas.height = Math.round(img.naturalHeight * s);
        const g = canvas.getContext("2d");
        g.imageSmoothingEnabled = false;
        g.drawImage(img, 0, 0, canvas.width, canvas.height);
        if (grid && s >= 6) {
          g.strokeStyle = "rgba(0,0,0,0.35)";
          for (let x = 0; x <= img.naturalWidth; x += 1) { g.beginPath(); g.moveTo(x * s + 0.5, 0); g.lineTo(x * s + 0.5, canvas.height); g.stroke(); }
          for (let y = 0; y <= img.naturalHeight; y += 1) { g.beginPath(); g.moveTo(0, y * s + 0.5); g.lineTo(canvas.width, y * s + 0.5); g.stroke(); }
        }
      };
      img.onerror = () => canvas.replaceWith(h("div.missing", null, "[REAL PLATE CROP REQUIRED]"));
      img.src = src;
      return canvas;
    },

    fmt(n, d = 0) {
      return Number(n).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
    },

    /** The evidence vehicle's record at its best frame (from tools/track_evidence.py). */
    at1299() {
      return (K.TRACK && K.TRACK.sequence.find((s) => s.frame === 1299)) || null;
    },

    box(norm) {
      return norm ? { x: norm[0], y: norm[1], w: norm[2], h: norm[3] } : null;
    },

    /** CAM06 playing with the deployed models' real boxes over it (K.ui.replay). */
    replay(ctx, { rate = 0.5, ...show } = {}) {
      const R = (K.TRACK && K.TRACK.replay) || [];
      const video = K.media.video("cam06_1080p", { start: R.length ? R[0].t : 49.6, end: R.length ? R[R.length - 1].t : 55.4, rate, cls: "media-cover" });
      const overlay = K.ui.replay(ctx, video, show);
      return { el: h("div.fill", null, [video, overlay]), overlay };
    },

    /** A small view of the evidence car at its best frame, zoomed in, with its real vehicle box
     * (and plate box) drawn on: for flowchart cards. */
    carThumb({ plate = false } = {}) {
      const s = K.S.at1299() || {};
      const v = K.S.box(s.vehicle);
      const p = K.S.box(s.plate);
      const rect = (b, kind) => (b ? `<g class="box box-${kind} on"><rect x="${b.x * 1000}" y="${b.y * 1000}" width="${b.w * 1000}" height="${b.h * 1000}" rx="6" vector-effect="non-scaling-stroke"/></g>` : "");
      const zoom = h("div.fill.zoombox", null, [
        K.media.img("best_frame", { cls: "media-cover" }),
        h("div.fill", { html: `<svg class="boxes" viewBox="0 0 1000 1000" preserveAspectRatio="none">${rect(v, "track")}${plate ? rect(p, "plate") : ""}</svg>` }),
      ]);
      if (v) zoom.style.transform = K.ui.zoomTo(v, { fill: 0.55 });
      const T = K.TRACK || {};
      return h("div.zoom169", null, [zoom, h("span.thumb-tag", null, `${(T.types && T.types[T.track_id]) || "vehicle"} · ID ${T.track_id}`)]);
    },

    /** "How Vigentra reads a plate": where this scene sits in the pipeline. */
    ribbon(active) {
      const names = ["Vehicle", "Track", "Plate", "Best frames", "Quality", "Read", "Vote"];
      const el = h("div.ribbon", null, names.map((n) => h("span.rb", null, [h("i"), n])));
      el.set = (i) => el.querySelectorAll(".rb").forEach((b, j) => { b.classList.toggle("done", j < i); b.classList.toggle("now", j === i); });
      el.set(active);
      return el;
    },

    /** The Vigentra lockup: mark, wordmark, tagline (brand/, cut from the logo). */
    lockup({ size = 1 } = {}) {
      return h("div.lockup", { style: { "--s": size } }, [
        h("img.lk-mark.r.soft", { src: "brand/vigentra-mark.png", alt: "" }),
        h("img.lk-word", { src: "brand/vigentra-wordmark.png", alt: "Vigentra" }),
        h("img.lk-tag.r", { src: "brand/vigentra-tagline.png", alt: "Vigilance · Intelligence · Safer roads" }),
      ]);
    },
  };
})();
