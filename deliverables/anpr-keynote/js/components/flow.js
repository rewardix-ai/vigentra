/* Live flowcharts that explain themselves: numbered cards showing the real data at each step,
 * decisions with Yes / No arrows, outcomes, and a dot that travels the main route. As the dot
 * reaches a card the card lights up and the caption beside the title says what happens there.
 * Clicking a card shows its caption too (it never advances the slide).
 *
 * const f = K.ui.flow({ nodes, edges, caption })
 *   node: { id, x, y, w, h, type: "card" | "decision" | "end", n, title, sub, media(), tone, say }
 *         x, y = centre on the 1920×1080 stage; tone: "bad" | "ok" | "io"
 *   edge: { from, to, out: "right" | "left" | "top" | "bottom", in: side, via: [[x, y]], kind: "yes" | "no" | "alert" | "video", label, at: [x, y] }
 *   caption: the element that shows the current step
 * await f.reveal(ctx)            cards and arrows appear in order
 * f.run(ctx, routes)             dots travel until the scene is left
 *   route: { path: [ids], every, offset, main, cls }   main: the route the caption follows
 */
(function () {
  const K = (window.K = window.K || {});
  K.ui = K.ui || {};
  const NS = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs) => {
    const el = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
    return el;
  };

  K.ui.flow = function flow({ nodes, edges, caption }) {
    const byId = Object.fromEntries(nodes.map((n) => [n.id, { type: "card", w: 300, h: 240, ...n }]));
    const wrap = K.h("div.fill.flow", { "data-interactive": true });
    const svg = svgEl("svg", { class: "flow-svg", viewBox: "0 0 1920 1080" });
    const defs = svgEl("defs");
    [["main", "#9aa6b8"], ["no", "#ff5f5f"], ["video", "#5ab0ff"]].forEach(([k, color]) => {
      const m = svgEl("marker", { id: `ah-${k}`, viewBox: "0 0 10 10", refX: 8, refY: 5, markerWidth: 9, markerHeight: 9, orient: "auto" });
      m.appendChild(svgEl("path", { d: "M0,0 L10,5 L0,10 z", fill: color }));
      defs.appendChild(m);
    });
    svg.appendChild(defs);
    const dots = K.h("div.fill.flow-dots");
    wrap.append(svg, dots);

    const anchor = (n, side) => ({
      left: [n.x - n.w / 2, n.y], right: [n.x + n.w / 2, n.y], top: [n.x, n.y - n.h / 2], bottom: [n.x, n.y + n.h / 2],
    }[side]);
    const paths = {};
    const labels = [];
    edges.forEach((e) => {
      const pts = [anchor(byId[e.from], e.out || "right"), ...(e.via || []), anchor(byId[e.to], e.in || "left")];
      const tone = { no: "no", alert: "no", video: "video" }[e.kind] || "main"; // "yes" is a normal arrow with a green label
      const p = svgEl("path", { d: "M" + pts.map((q) => q.join(",")).join(" L"), class: `fedge ${tone}` });
      p.dataset.kind = tone;
      svg.appendChild(p);
      paths[`${e.from}>${e.to}`] = p;
      if (e.label) {
        const t = svgEl("text", { x: e.at[0], y: e.at[1], class: `flabel ${e.kind || "main"}` });
        t.textContent = e.label;
        svg.appendChild(t);
        labels.push({ t, key: `${e.from}>${e.to}` });
      }
    });

    const say = (n) => {
      if (!caption || !n.say) return;
      caption.innerHTML = "";
      caption.append(K.h("span.fs-n" + (n.n ? "" : ".blank"), null, n.n ? String(n.n) : ""), K.h("div", null, [K.h("b", null, n.title), K.h("span", null, n.say)]));
      caption.classList.add("on");
    };
    const els = {};
    Object.values(byId).forEach((n) => {
      const style = { left: `${n.x - n.w / 2}px`, top: `${n.y - n.h / 2}px`, width: `${n.w}px`, height: `${n.h}px` };
      let el;
      if (n.type === "decision") {
        const shape = svgEl("svg", { viewBox: `0 0 ${n.w} ${n.h}`, preserveAspectRatio: "none" });
        shape.appendChild(svgEl("polygon", { points: `${n.w / 2},2 ${n.w - 2},${n.h / 2} ${n.w / 2},${n.h - 2} 2,${n.h / 2}` }));
        el = K.h("button.fnode.decision", { style }, [shape, K.h("div.fd-text", null, [K.h("b", null, n.title), n.sub ? K.h("span", null, n.sub) : null])]);
      } else if (n.type === "end") {
        el = K.h(`button.fnode.end.tone-${n.tone || "io"}`, { style }, [
          n.media ? K.h("div.fe-media", null, n.media()) : null,
          K.h("div.fe-text", null, [K.h("b", null, n.title), n.sub ? K.h("span", null, n.sub) : null]),
        ]);
      } else {
        el = K.h(`button.fnode.card.tone-${n.tone || "main"}`, { style }, [
          K.h("div.fc-head", null, [n.n ? K.h("span.fc-n", null, String(n.n)) : null, K.h("span.fc-title", null, n.title)]),
          n.media ? K.h("div.fc-media", null, n.media()) : null,
          n.sub ? K.h("div.fc-sub", null, n.sub) : null,
        ]);
      }
      el.addEventListener("click", () => say(n));
      els[n.id] = el;
      wrap.appendChild(el);
    });

    wrap.reveal = async (ctx, { gap = 170 } = {}) => {
      const shown = new Set();
      for (const id of Object.keys(byId)) {
        els[id].classList.add("in");
        shown.add(id);
        for (const e of edges) {
          const key = `${e.from}>${e.to}`;
          const p = paths[key];
          if (p.dataset.on || !shown.has(e.from) || !shown.has(e.to)) continue;
          p.dataset.on = "1";
          p.classList.add("in");
          // drawing would undo a dashed line's dashes, so those just fade in
          const drawn = p.dataset.kind === "video" ? Promise.resolve() : ctx.draw(p, 450);
          drawn.then(() => p.setAttribute("marker-end", `url(#ah-${p.dataset.kind})`));
          labels.filter((l) => l.key === key).forEach((l) => l.t.classList.add("in"));
        }
        await ctx.wait(gap);
      }
    };

    wrap.run = (ctx, routes, { dwell = 1500 } = {}) => {
      let alive = true;
      ctx.onLeave(() => { alive = false; });
      const live = [];
      const enter = (pk, id) => {
        const el = els[id];
        if (pk.r.main) {
          Object.values(els).forEach((x) => x.classList.toggle("on", x === el));
          say(byId[id]);
        } else if (byId[id].type === "end") {
          el.classList.remove("hit");
          void el.offsetWidth;
          el.classList.add("hit");
        }
      };
      const spawn = (r) => {
        const segs = r.path.slice(1).map((to, i) => paths[`${r.path[i]}>${to}`]);
        const el = K.h("div.fdot" + (r.cls ? "." + r.cls : ""));
        dots.appendChild(el);
        const pk = { r, segs, el, seg: 0, t: 0, wait: r.main ? dwell : 300 };
        enter(pk, r.path[0]);
        const p0 = segs[0].getPointAtLength(0);
        el.style.transform = `translate(${p0.x}px, ${p0.y}px)`;
        live.push(pk);
      };
      routes.forEach((r) => {
        const start = () => { if (!alive) return; spawn(r); setTimeout(start, r.every); };
        setTimeout(start, r.offset || 0);
      });
      let last = performance.now();
      const frame = (now) => {
        if (!alive) return;
        const dt = Math.min(50, now - last);
        last = now;
        for (let i = live.length - 1; i >= 0; i -= 1) {
          const pk = live[i];
          if (pk.wait > 0) { pk.wait -= dt; continue; }
          const path = pk.segs[pk.seg];
          const len = path.getTotalLength();
          pk.t += dt / Math.max(450, Math.min(1300, len * 1.6)); // long arrows a little slower, never dull
          if (pk.t >= 1) {
            pk.seg += 1;
            pk.t = 0;
            enter(pk, pk.r.path[pk.seg]);
            if (pk.seg >= pk.segs.length) { pk.el.classList.add("done"); setTimeout(() => pk.el.remove(), 500); live.splice(i, 1); continue; }
            pk.wait = pk.r.main ? dwell : 300;
            const p0 = pk.segs[pk.seg].getPointAtLength(0);
            pk.el.style.transform = `translate(${p0.x}px, ${p0.y}px)`;
            continue;
          }
          const pt = path.getPointAtLength(len * pk.t);
          pk.el.style.transform = `translate(${pt.x}px, ${pt.y}px)`;
        }
        requestAnimationFrame(frame);
      };
      requestAnimationFrame(frame);
    };
    return wrap;
  };
})();
