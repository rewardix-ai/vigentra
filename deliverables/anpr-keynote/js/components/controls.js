/* Interactive controls the presenter (or a volunteer) drives on stage.
 *
 * K.ui.beforeAfter(left, right, labels)   drag to compare two frames
 * K.ui.scrubber(frames, render)           step through frames of one vehicle
 * K.ui.difficulty(image, opts)            good footage -> poor footage, on a real plate
 * K.ui.tabs(cases, render)                a set of real examples, one at a time
 * All are marked data-interactive, so clicking them never advances the presentation.
 */
(function () {
  const K = (window.K = window.K || {});
  K.ui = K.ui || {};

  K.ui.beforeAfter = function beforeAfter(left, right, [leftLabel, rightLabel] = ["Before", "After"]) {
    const wrap = K.h("div.ba", { "data-interactive": true });
    const a = K.h("div.ba-pane.ba-left", null, [left, K.h("span.ba-label", null, leftLabel)]);
    const b = K.h("div.ba-pane.ba-right", null, [right, K.h("span.ba-label.right", null, rightLabel)]);
    const handle = K.h("div.ba-handle", null, [K.h("span")]);
    wrap.append(b, a, handle);
    const set = (p) => {
      const pct = Math.max(0, Math.min(100, p));
      a.style.clipPath = `inset(0 ${100 - pct}% 0 0)`;
      handle.style.left = `${pct}%`;
    };
    set(50);
    const move = (e) => {
      const r = wrap.getBoundingClientRect();
      const x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
      set((x / r.width) * 100);
    };
    let dragging = false;
    wrap.addEventListener("pointerdown", (e) => { dragging = true; move(e); wrap.setPointerCapture(e.pointerId); });
    wrap.addEventListener("pointermove", (e) => { if (dragging) move(e); });
    wrap.addEventListener("pointerup", () => { dragging = false; });
    wrap.set = set;
    return wrap;
  };

  K.ui.scrubber = function scrubber(count, render, { labels } = {}) {
    const wrap = K.h("div.scrub", { "data-interactive": true });
    const view = K.h("div.scrub-view");
    const input = K.h("input.scrub-range", { type: "range", min: 0, max: count - 1, value: 0, step: 1, "aria-label": "Frame" });
    const ticks = K.h("div.scrub-ticks");
    for (let i = 0; i < count; i += 1) {
      ticks.appendChild(K.h("button.scrub-tick", { onclick: () => { input.value = i; update(); } }, labels ? labels[i] : String(i + 1)));
    }
    const update = () => {
      const i = Number(input.value);
      view.innerHTML = "";
      view.appendChild(render(i));
      ticks.querySelectorAll(".scrub-tick").forEach((t, j) => t.classList.toggle("on", j === i));
    };
    input.addEventListener("input", update);
    wrap.append(view, input, ticks);
    update();
    wrap.go = (i) => { input.value = i; update(); };
    return wrap;
  };

  /** One real plate image, degraded as the slider moves from good footage to poor footage. */
  K.ui.difficulty = function difficulty(imageSrc, { width = 900, height = 300, labels = ["Good footage", "Poor footage"] } = {}) {
    const wrap = K.h("div.diff", { "data-interactive": true });
    const canvas = K.h("canvas.diff-canvas", { width, height });
    const input = K.h("input.diff-range", { type: "range", min: 0, max: 100, value: 0, "aria-label": "Footage quality" });
    const readout = K.h("div.diff-readout");
    const legend = K.h("div.diff-legend", null, [K.h("span", null, labels[0]), K.h("span", null, labels[1])]);
    wrap.append(canvas, input, legend, readout);
    const img = new Image();
    const draw = () => {
      const t = Number(input.value) / 100;
      // resolution falls fastest; blur, darkness and noise follow - the order the footage shows
      const factor = Math.max(0.025, 1 - t * 0.975);
      K.media.pixelate(canvas, img, factor, { blur: t * 6, dark: t * 0.55, noise: t * 0.35 });
      const px = Math.max(1, Math.round((img.naturalWidth || width) * factor));
      readout.textContent = `plate ≈ ${px} px wide`;
    };
    img.onload = draw;
    img.onerror = () => wrap.replaceWith(K.h("div.missing", null, "[REAL PLATE CROP REQUIRED]"));
    img.src = imageSrc;
    input.addEventListener("input", draw);
    wrap.set = (v) => { input.value = v; draw(); };
    return wrap;
  };

  K.ui.tabs = function tabs(cases, render) {
    const wrap = K.h("div.tabs", { "data-interactive": true });
    const bar = K.h("div.tabs-bar");
    const view = K.h("div.tabs-view");
    const select = (i) => {
      bar.querySelectorAll("button").forEach((b, j) => b.classList.toggle("on", i === j));
      view.innerHTML = "";
      view.appendChild(render(cases[i], i));
    };
    cases.forEach((c, i) => bar.appendChild(K.h("button", { onclick: () => select(i) }, c.label)));
    wrap.append(bar, view);
    select(0);
    wrap.select = select;
    return wrap;
  };
})();
