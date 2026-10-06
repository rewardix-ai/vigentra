/* Live flowcharts: boxes, connectors, and packets that travel the routes, lighting each box
 * they pass. Click a box for its one-line explanation (it never advances the slide).
 *
 * const f = K.ui.flow({ nodes, edges })
 *   node: { id, x, y, label, sub, kind: "main" | "ok" | "bad" | "io", w, h, detail }  (x, y = centre)
 *   edge: { from, to, kind: "main" | "bad" | "dash", d }  an elbow between the nearest sides, or the
 *         path `d` given (stage pixels) where an elbow would cross a box
 * await f.reveal(ctx)           boxes and connectors appear in order
 * f.run(ctx, routes)            packets flow until the scene is left
 *   route: { path: [ids], every: ms, offset: ms, cls, carry(nodeId, el) }  (carry: change what
 *          the packet holds as it passes a box, e.g. a plate crop that becomes text). A packet
 *          pauses inside each box it passes, as if being worked on.
 */
(function () {
  const K = (window.K = window.K || {});
  K.ui = K.ui || {};
  const NS = "http://www.w3.org/2000/svg";

  K.ui.flow = function flow({ nodes, edges }) {
    const byId = Object.fromEntries(nodes.map((n) => [n.id, { w: 250, h: 104, kind: "main", ...n }]));
    const wrap = K.h("div.fill.flow", { "data-interactive": true });
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "flow-svg");
    svg.setAttribute("viewBox", "0 0 1920 1080");
    const packets = K.h("div.fill.flow-packets");
    const tip = K.h("div.flow-tip");
    wrap.append(svg, packets);

    const side = (n, dx, dy) => (Math.abs(dx) >= Math.abs(dy)
      ? [n.x + Math.sign(dx) * n.w / 2, n.y]
      : [n.x, n.y + Math.sign(dy) * n.h / 2]);
    const paths = {};
    edges.forEach((e) => {
      const a = byId[e.from];
      const b = byId[e.to];
      const [x1, y1] = side(a, b.x - a.x, b.y - a.y);
      const [x2, y2] = side(b, a.x - b.x, a.y - b.y);
      const horiz = Math.abs(b.x - a.x) >= Math.abs(b.y - a.y);
      const d = e.d || (horiz
        ? `M${x1},${y1} H${(x1 + x2) / 2} V${y2} H${x2}`
        : `M${x1},${y1} V${(y1 + y2) / 2} H${x2} V${y2}`);
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", d);
      p.setAttribute("class", `flow-edge ${e.kind || "main"}`);
      svg.appendChild(p);
      paths[`${e.from}>${e.to}`] = p;
    });

    const boxes = {};
    Object.values(byId).forEach((n) => {
      const box = K.h(`button.flow-node.${n.kind}`, {
        style: { left: `${n.x - n.w / 2}px`, top: `${n.y - n.h / 2}px`, width: `${n.w}px`, height: `${n.h}px` },
        onclick: () => {
          if (!n.detail) return;
          tip.innerHTML = "";
          tip.append(K.h("b", null, n.label), K.h("span", null, n.detail));
          tip.classList.add("on");
        },
      }, [K.h("span.fn-label", null, n.label), n.sub ? K.h("span.fn-sub", null, n.sub) : null]);
      boxes[n.id] = box;
      wrap.appendChild(box);
    });
    wrap.appendChild(tip);

    wrap.reveal = async (ctx, { gap = 160 } = {}) => {
      const shown = new Set();
      for (const n of Object.keys(byId)) {
        boxes[n].classList.add("in");
        shown.add(n);
        edges.filter((e) => shown.has(e.from) && shown.has(e.to) && !paths[`${e.from}>${e.to}`].dataset.on).forEach((e) => {
          const p = paths[`${e.from}>${e.to}`];
          p.dataset.on = "1";
          p.classList.add("in");
          if (!p.classList.contains("dash")) ctx.draw(p, 500); // drawing would overwrite the dashes
        });
        await ctx.wait(gap);
      }
    };

    wrap.run = (ctx, routes, { speed = 320, dwell = 420 } = {}) => {
      let alive = true;
      ctx.onLeave(() => { alive = false; });
      const live = [];
      const spawn = (r) => {
        const segs = [];
        for (let i = 1; i < r.path.length; i += 1) segs.push(paths[`${r.path[i - 1]}>${r.path[i]}`]);
        const el = K.h("div.packet" + (r.cls ? "." + r.cls : ""));
        packets.appendChild(el);
        if (r.carry) r.carry(r.path[0], el);
        live.push({ r, segs, el, seg: 0, dist: 0, wait: 0 });
      };
      routes.forEach((r) => {
        const start = () => { if (!alive) return; spawn(r); setTimeout(start, r.every || 3000); };
        setTimeout(start, r.offset || 0);
      });
      let last = performance.now();
      const frame = (now) => {
        if (!alive) return;
        const dt = Math.min(0.05, (now - last) / 1000);
        last = now;
        for (let i = live.length - 1; i >= 0; i -= 1) {
          const pk = live[i];
          const path = pk.segs[pk.seg];
          if (!path) { pk.el.remove(); live.splice(i, 1); continue; }
          if (pk.wait > 0) { pk.wait -= dt * 1000; continue; }
          pk.dist += speed * dt;
          const len = path.getTotalLength();
          if (pk.dist >= len) {
            const node = pk.r.path[pk.seg + 1];
            const box = boxes[node];
            box.classList.remove("hit");
            void box.offsetWidth;
            box.classList.add("hit");
            if (pk.r.carry) pk.r.carry(node, pk.el);
            pk.seg += 1;
            pk.dist = 0;
            pk.wait = dwell;
            if (pk.segs[pk.seg]) { const p0 = pk.segs[pk.seg].getPointAtLength(0); pk.el.style.transform = `translate(${p0.x}px, ${p0.y}px)`; }
            if (pk.seg >= pk.segs.length) { pk.el.classList.add("done"); setTimeout(() => pk.el.remove(), 600); live.splice(i, 1); }
            continue;
          }
          const pt = path.getPointAtLength(pk.dist);
          pk.el.style.transform = `translate(${pt.x}px, ${pt.y}px)`;
        }
        requestAnimationFrame(frame);
      };
      requestAnimationFrame(frame);
    };
    return wrap;
  };
})();
