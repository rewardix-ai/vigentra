/* Animation utilities, bound to one scene's lifetime.
 *
 * Every helper goes through a scene context (`ctx`) so that leaving a scene cancels whatever it
 * was still animating, and so that "go back" can rebuild a scene instantly: in instant mode all
 * waits resolve at once and CSS transitions are switched off for the scene.
 */
(function () {
  const K = (window.K = window.K || {});

  K.makeCtx = function makeCtx(el, opts) {
    const timers = new Set();
    const ctx = {
      el,
      alive: true,
      instant: !!(opts && opts.instant),
      fast: false,
      tech: () => document.body.classList.contains("tech"),
      $: (sel) => el.querySelector(sel),
      $$: (sel) => Array.from(el.querySelectorAll(sel)),

      html(markup) {
        el.innerHTML = markup;
        return el;
      },

      /** Resolves after `ms`, at once when instant or fast-forwarding, never after the scene left. */
      wait(ms) {
        return new Promise((resolve) => {
          if (!ctx.alive) return;
          if (ctx.instant || ctx.fast || !ms) return resolve();
          const t = setTimeout(() => {
            timers.delete(t);
            if (ctx.alive) resolve();
          }, ms);
          timers.add(t);
        });
      },

      /** Reveal elements (selector or nodes) by adding `.in`, optionally staggered. */
      async in(target, { stagger = 0, delay = 0 } = {}) {
        const nodes = typeof target === "string" ? ctx.$$(target) : [].concat(target).filter(Boolean);
        if (delay) await ctx.wait(delay);
        for (const node of nodes) {
          if (!ctx.alive) return;
          node.classList.add("in");
          if (stagger) await ctx.wait(stagger);
        }
      },

      out(target) {
        const nodes = typeof target === "string" ? ctx.$$(target) : [].concat(target).filter(Boolean);
        nodes.forEach((node) => node.classList.remove("in"));
      },

      /** Type text into an element, one character at a time. */
      async type(node, text, cps = 18) {
        if (typeof node === "string") node = ctx.$(node);
        if (!node) return;
        if (ctx.instant || ctx.fast) {
          node.textContent = text;
          return;
        }
        node.textContent = "";
        for (const ch of text) {
          if (!ctx.alive) return;
          node.textContent += ch;
          await ctx.wait(1000 / cps);
          if (ctx.fast) {
            node.textContent = text;
            return;
          }
        }
      },

      /** Count a number up from 0, formatted. */
      async count(node, to, { dur = 1400, decimals = 0, prefix = "", suffix = "" } = {}) {
        if (typeof node === "string") node = ctx.$(node);
        if (!node) return;
        const fmt = (v) => prefix + v.toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) + suffix;
        if (ctx.instant || ctx.fast) {
          node.textContent = fmt(to);
          return;
        }
        const t0 = performance.now();
        await new Promise((resolve) => {
          const tick = (now) => {
            if (!ctx.alive) return;
            const p = Math.min(1, (now - t0) / dur);
            const eased = 1 - Math.pow(1 - p, 3);
            node.textContent = fmt(to * eased);
            if (p < 1 && !ctx.fast) requestAnimationFrame(tick);
            else {
              node.textContent = fmt(to);
              resolve();
            }
          };
          requestAnimationFrame(tick);
        });
      },

      /** Draw an SVG stroke (path, line, rect, polyline) from nothing. */
      draw(target, dur = 900) {
        const nodes = typeof target === "string" ? ctx.$$(target) : [].concat(target).filter(Boolean);
        nodes.forEach((node) => {
          let len = 1000;
          try {
            len = node.getTotalLength();
          } catch (err) {
            len = 4000; // not rendered yet: animate over a generous length
          }
          node.style.strokeDasharray = len;
          node.style.strokeDashoffset = ctx.instant || ctx.fast ? 0 : len;
          node.getBoundingClientRect();
          node.style.transition = ctx.instant || ctx.fast ? "none" : `stroke-dashoffset ${dur}ms var(--ease)`;
          node.style.strokeDashoffset = 0;
        });
        // Once drawn, drop the dash: measured before a zoom (or with a non-scaling stroke), its
        // length no longer matches the line on screen and would leave the outline half drawn.
        return ctx.wait(dur).then(() => nodes.forEach((node) => { node.style.strokeDasharray = ""; node.style.strokeDashoffset = ""; }));
      },

      /** Set a CSS transform with a timed transition (zooms, pans). */
      move(node, transform, dur = 1200) {
        if (typeof node === "string") node = ctx.$(node);
        if (!node) return Promise.resolve();
        node.style.transition = ctx.instant || ctx.fast ? "none" : `transform ${dur}ms var(--ease)`;
        node.style.transform = transform;
        return ctx.wait(dur);
      },

      /** Register cleanup to run when the scene is left. */
      onLeave(fn) {
        (ctx._leave = ctx._leave || []).push(fn);
      },

      destroy() {
        ctx.alive = false;
        timers.forEach(clearTimeout);
        timers.clear();
        (ctx._leave || []).forEach((fn) => {
          try {
            fn();
          } catch (err) {
            console.warn(err);
          }
        });
        el.querySelectorAll("video").forEach((v) => {
          v.pause();
          v.removeAttribute("src");
          v.load();
        });
      },
    };
    return ctx;
  };

  /** Small DOM builder for components: K.h("div.cls", {attrs}, children). */
  K.h = function h(tag, attrs, children) {
    const [name, ...classes] = tag.split(".");
    const node = document.createElement(name || "div");
    if (classes.length) node.className = classes.join(" ");
    if (attrs) {
      for (const [key, value] of Object.entries(attrs)) {
        if (key === "style" && typeof value === "object") {
          // custom properties (--w, --s, --v ...) need setProperty; Object.assign drops them silently
          for (const [prop, v] of Object.entries(value)) {
            if (v == null) continue;
            if (prop.startsWith("--")) node.style.setProperty(prop, v);
            else node.style[prop] = v;
          }
        }
        else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
        else if (key === "html") node.innerHTML = value;
        else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
      }
    }
    [].concat(children == null ? [] : children).forEach((child) => {
      if (child == null || child === false) return;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  };
})();
