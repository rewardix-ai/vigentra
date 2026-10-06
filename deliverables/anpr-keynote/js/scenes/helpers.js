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
      return h("div.title-tl.auto", null, [h("div.kicker", null, kicker), h(`div.${size}`, { style: { marginTop: "18px" } }, text)]);
    },

    /** A centred statement made of lines; reveal them with ctx.in(). */
    statement(lines, { size = "h1", gap = 28, top = null } = {}) {
      const wrap = h("div.center");
      const col = h("div.stack", { style: { gap: `${gap}px`, marginTop: top ? `${top}px` : null } });
      lines.forEach((line, i) => col.appendChild(h(`div.${size}.r.soft`, { "data-line": i, html: line })));
      wrap.appendChild(col);
      return wrap;
    },

    /** The technical layer: only visible with T. */
    tech(lines, src) {
      return h("div.tech.tech-panel", null, [
        ...lines.map((l) => h("div", { html: l })),
        src ? h("span.src", null, src) : null,
      ]);
    },

    /** A framed piece of media at a position. */
    framed(node, x, y, w, hgt, extra = "") {
      const f = h("div.frame.abs" + (extra ? "." + extra : ""), { style: { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${hgt}px` } }, node);
      return f;
    },

    /** Upscale an image onto a canvas with hard pixels (what the camera really captured). */
    pixels(src, width, { factor = null, grid = false, targetW = null, scale: perPx = null } = {}) {
      const canvas = h("canvas.pix");
      const img = new Image();
      img.onload = () => {
        const srcW = factor ? Math.max(1, Math.round(width)) : img.naturalWidth;
        const srcH = Math.max(1, Math.round((img.naturalHeight / img.naturalWidth) * srcW));
        const tmp = document.createElement("canvas");
        tmp.width = srcW;
        tmp.height = srcH;
        tmp.getContext("2d").drawImage(img, 0, 0, srcW, srcH);
        const outW = targetW || srcW * (perPx || 8);
        const scale = outW / srcW;
        canvas.width = Math.round(srcW * scale);
        canvas.height = Math.round(srcH * scale);
        const g = canvas.getContext("2d");
        g.imageSmoothingEnabled = false;
        g.drawImage(tmp, 0, 0, canvas.width, canvas.height);
        if (grid && scale >= 6) {
          g.strokeStyle = "rgba(0,0,0,0.35)";
          g.lineWidth = 1;
          for (let x = 0; x <= srcW; x += 1) { g.beginPath(); g.moveTo(x * scale + 0.5, 0); g.lineTo(x * scale + 0.5, canvas.height); g.stroke(); }
          for (let y = 0; y <= srcH; y += 1) { g.beginPath(); g.moveTo(0, y * scale + 0.5); g.lineTo(canvas.width, y * scale + 0.5); g.stroke(); }
        }
      };
      img.onerror = () => canvas.replaceWith(h("div.missing", null, "[REAL PLATE CROP REQUIRED]"));
      img.src = src;
      return canvas;
    },

    fmt(n, d = 0) {
      return Number(n).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
    },

    /** The evidence vehicle's record at a frame (from tools/track_evidence.py). */
    at1299() {
      return (K.TRACK && K.TRACK.sequence.find((s) => s.frame === 1299)) || null;
    },

    box(norm) {
      return norm ? { x: norm[0], y: norm[1], w: norm[2], h: norm[3] } : null;
    },
  };
})();
