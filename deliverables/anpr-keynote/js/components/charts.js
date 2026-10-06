/* Numbers, drawn to scale and revealed only after the idea they measure.
 *
 * K.ui.bars(rows, opts)     horizontal bars on one shared scale; animate with ctx.in(".bar")
 * K.ui.stat(value, label)   one large figure, counted up by the scene
 * K.ui.loop(stages)         the engineering loop, with a highlight that travels round it
 */
(function () {
  const K = (window.K = window.K || {});
  K.ui = K.ui || {};

  K.ui.bars = function bars(rows, { max = 1, format = (v) => v, unit = "", color = "var(--vehicle)" } = {}) {
    const wrap = K.h("div.bars");
    rows.forEach((row) => {
      const pct = row.value == null ? 0 : Math.max(0, Math.min(1, row.value / max)) * 100;
      const fill = K.h("div.bar-fill", { style: { "--w": `${pct}%`, background: row.color || color } });
      wrap.appendChild(
        K.h("div.bar.r", null, [
          K.h("div.bar-label", null, [K.h("span", null, row.label), row.sub ? K.h("small", null, row.sub) : null]),
          K.h("div.bar-track", null, [fill]),
          K.h("div.bar-value.num", null, row.value == null ? "—" : `${format(row.value)}${unit}`),
        ])
      );
    });
    return wrap;
  };

  K.ui.stat = function stat(display, label, { cls = "" } = {}) {
    return K.h("div.stat.r" + (cls ? "." + cls : ""), null, [K.h("div.stat-value.num", null, display), K.h("div.stat-label", null, label)]);
  };

  K.ui.loop = function loop(stages, { radius = 330 } = {}) {
    const wrap = K.h("div.loop", { style: { "--r": `${radius}px` } });
    const ring = K.h("div.loop-ring");
    wrap.appendChild(ring);
    stages.forEach((label, i) => {
      const angle = (i / stages.length) * Math.PI * 2 - Math.PI / 2;
      wrap.appendChild(
        K.h("div.loop-node.r", {
          style: { left: `calc(50% + ${Math.cos(angle) * radius}px)`, top: `calc(50% + ${Math.sin(angle) * radius}px)` },
          "data-i": i,
        }, label)
      );
    });
    let timer = null;
    wrap.spin = (ctx, ms = 1100) => {
      let i = 0;
      const nodes = wrap.querySelectorAll(".loop-node");
      const tick = () => {
        nodes.forEach((n, j) => n.classList.toggle("hot", j === i % nodes.length));
        i += 1;
      };
      tick();
      timer = setInterval(tick, ms);
      ctx.onLeave(() => clearInterval(timer));
    };
    return wrap;
  };
})();
