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

  /** The deployed models' real output replayed over the playing CAM06 video, frame-accurate.
   * K.TRACK.replay holds, for every frame, its own timestamp and every vehicle the models saw.
   * Layers switch on with set({ ids, trail, plate }); vehicle boxes are always drawn. */
  K.ui.replay = function replay(ctx, video, opts = {}) {
    const T = K.TRACK || { replay: [], sequence: [] };
    const frames = T.replay || [];
    const plates = new Map((T.sequence || []).filter((s) => s.plate).map((s) => [s.frame, s]));
    const show = { ids: false, trail: false, plate: false, ...opts };
    const wrap = K.h("div.fill.replay");
    const layer = svg("svg", { class: "boxes live", viewBox: "0 0 1000 1000", preserveAspectRatio: "none" });
    const tags = K.h("div.box-labels");
    wrap.append(layer, tags);
    const pool = {};
    const trail = svg("polyline", { class: "trail", "vector-effect": "non-scaling-stroke" });
    layer.appendChild(trail);
    const path = [];
    const item = (key, kind) => {
      if (!pool[key]) {
        const rect = svg("rect", { rx: 6, "vector-effect": "non-scaling-stroke" });
        const g = svg("g", { class: `box box-${kind} on` });
        g.appendChild(rect);
        layer.appendChild(g);
        const tag = K.h(`span.box-tag.box-${kind}.on`);
        tags.appendChild(tag);
        pool[key] = { g, rect, tag };
      }
      return pool[key];
    };
    const place = (it, b, label) => {
      it.rect.setAttribute("x", b[0] * 1000); it.rect.setAttribute("y", b[1] * 1000);
      it.rect.setAttribute("width", b[2] * 1000); it.rect.setAttribute("height", b[3] * 1000);
      it.tag.style.left = `${b[0] * 100}%`; it.tag.style.top = `${b[1] * 100}%`;
      it.tag.textContent = label;
      it.g.style.display = ""; it.tag.style.display = "";
    };
    const frameAt = (t) => {
      let lo = 0, hi = frames.length - 1, best = -1;
      while (lo <= hi) { const mid = (lo + hi) >> 1; if (frames[mid].t <= t + 0.02) { best = mid; lo = mid + 1; } else hi = mid - 1; }
      return best >= 0 && t - frames[best].t < 0.2 ? frames[best] : null;
    };
    let last = null;
    const draw = (t) => {
      const fr = frameAt(t);
      if (fr === last) return;
      last = fr;
      Object.values(pool).forEach((it) => { it.g.style.display = "none"; it.tag.style.display = "none"; });
      if (!fr) { path.length = 0; trail.setAttribute("points", ""); return; }
      let seen = false;
      fr.boxes.forEach(([id, cls, conf, x, y, w, hgt]) => {
        const target = id === T.track_id;
        seen = seen || target;
        const kind = show.ids ? "track" : "vehicle";
        // one type per vehicle: the class of its most confident detection (track_evidence.py)
        const type = (T.types && T.types[id]) || cls;
        place(item("v" + id, kind), [x, y, w, hgt], show.ids ? `${type} · ID ${id}` : type);
        if (target) {
          if (path.length && path[path.length - 1].f >= fr.f) path.length = 0; // the video looped
          path.push({ f: fr.f, x: (x + w / 2) * 1000, y: (y + hgt) * 1000 });
        }
      });
      if (!seen) path.length = 0; // the car has left: its trail goes with it
      trail.setAttribute("points", show.trail ? path.map((p) => `${p.x},${p.y}`).join(" ") : "");
      const p = plates.get(fr.f);
      if (show.plate && p) place(item("plate", "plate"), p.plate, `plate ${p.plate_conf.toFixed(2)}`);
    };
    let alive = true;
    const tick = () => {
      if (!alive) return;
      if (video.requestVideoFrameCallback) video.requestVideoFrameCallback((_, meta) => { draw(meta.mediaTime); tick(); });
      else requestAnimationFrame(() => { draw(video.currentTime); tick(); });
    };
    if (video.tagName === "VIDEO") tick();
    ctx.onLeave(() => { alive = false; });
    wrap.set = (o) => { Object.assign(show, o); last = null; draw(video.currentTime || 0); };
    wrap.span = frames.length ? [frames[0].t, frames[frames.length - 1].t] : [0, 0];
    return wrap;
  };

  /** Zoom a media frame onto a box: returns the CSS transform that centres and fills it. */
  K.ui.zoomTo = function zoomTo(box, { fill = 0.8 } = {}) {
    const scale = Math.min(fill / box.w, fill / box.h);
    const cx = box.x + box.w / 2;
    const cy = box.y + box.h / 2;
    return `scale(${scale}) translate(${(0.5 - cx) * 100}%, ${(0.5 - cy) * 100}%)`;
  };
})();
