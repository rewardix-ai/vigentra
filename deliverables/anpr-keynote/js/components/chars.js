/* A reading shown as plain characters (no plate graphic).
 *
 * K.ui.chars(text, { hidden })     one span per character, so a scene can reveal them in turn
 * K.ui.revealChars(ctx, el, gap, { scramble })   reveal them one at a time; with scramble, each
 *                                  flickers through other characters first, like a reader deciding
 */
(function () {
  const K = (window.K = window.K || {});
  K.ui = K.ui || {};

  K.ui.chars = function chars(text, { hidden = false, cls = "" } = {}) {
    return K.h("div.chars" + (cls ? "." + cls : ""), null, [...String(text)].map((ch) => K.h("span.ch" + (hidden ? "" : ".in"), null, ch)));
  };

  const POOL = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789";
  K.ui.revealChars = async function revealChars(ctx, el, gap = 120, { scramble = false } = {}) {
    for (const span of el.querySelectorAll(".ch")) {
      const real = span.textContent;
      span.classList.add("in");
      if (scramble && !ctx.instant && !ctx.fast) {
        span.classList.add("deciding");
        for (let k = 0; k < 6 && !ctx.fast; k += 1) {
          span.textContent = POOL[Math.floor(Math.random() * POOL.length)];
          await ctx.wait(38);
        }
        span.textContent = real;
        span.classList.remove("deciding");
      } else {
        await ctx.wait(gap);
      }
    }
  };
})();
