/* Boxes drawn over real footage: vehicle (blue), plate (amber), track identity (teal).
 *
 * Coordinates are fractions of the media (0..1), so the same boxes work at any size.
 * K.ui.boxes(container, boxes) -> { show(ctx, id), hide(id), all(ctx) }
 *   box: { id, kind: "vehicle"|"plate"|"track", x, y, w, h, label }
 */
(function () {
  const K = (window.K = window.K || {});
  K.ui = K.ui || {};
  const NS = "http://www.w3.org/2000/svg";

  function svg(tag, attrs) {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, v));
    return node;
  }

  K.ui.boxes = function boxes(container, list) {
    const layer = svg("svg", { class: "boxes", viewBox: "0 0 1000 1000", preserveAspectRatio: "none" });
    container.appendChild(layer);
    const labels = K.h("div.box-labels");
    container.appendChild(labels);
    const items = {};
    list.forEach((b) => {
      const g = svg("g", { class: `box box-${b.kind}` });
      const rect = svg("rect", { x: b.x * 1000, y: b.y * 1000, width: b.w * 1000, height: b.h * 1000, rx: 6, "vector-effect": "non-scaling-stroke" });
      g.appendChild(rect);
      layer.appendChild(g);
      let tag = null;
      if (b.label) {
        tag = K.h("span.box-tag.box-" + b.kind, { style: { left: `${b.x * 100}%`, top: `${b.y * 100}%` } }, b.label);
        labels.appendChild(tag);
      }
      items[b.id] = { g, rect, tag };
    });
    return {
      async show(ctx, id, dur = 700) {
        const it = items[id];
        if (!it) return;
        it.g.classList.add("on");
        await ctx.draw(it.rect, dur);
        if (it.tag) it.tag.classList.add("on");
      },
      hide(id) {
        const it = items[id];
        if (!it) return;
        it.g.classList.remove("on");
        if (it.tag) it.tag.classList.remove("on");
      },
      async all(ctx, stagger = 150) {
        for (const id of Object.keys(items)) {
          this.show(ctx, id);
          await ctx.wait(stagger);
        }
      },
    };
  };

  /** Zoom a media frame onto a box: returns the CSS transform that centres and fills it. */
  K.ui.zoomTo = function zoomTo(box, { fill = 0.8 } = {}) {
    const scale = Math.min(fill / box.w, fill / box.h);
    const cx = box.x + box.w / 2;
    const cy = box.y + box.h / 2;
    return `scale(${scale}) translate(${(0.5 - cx) * 100}%, ${(0.5 - cy) * 100}%)`;
  };
})();
