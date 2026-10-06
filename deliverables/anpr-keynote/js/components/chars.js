/* A reading shown as plain characters (no plate graphic).
 *
 * K.ui.chars(text, { hidden })     one span per character, so a scene can reveal them in turn
 * K.ui.revealChars(ctx, el, gap)   reveal them one at a time
 */
(function () {
  const K = (window.K = window.K || {});
  K.ui = K.ui || {};

  K.ui.chars = function chars(text, { hidden = false, cls = "" } = {}) {
    return K.h("div.chars" + (cls ? "." + cls : ""), null, [...String(text)].map((ch) => K.h("span.ch" + (hidden ? "" : ".in"), null, ch)));
  };

  K.ui.revealChars = async function revealChars(ctx, el, gap = 120) {
    for (const span of el.querySelectorAll(".ch")) {
      span.classList.add("in");
      await ctx.wait(gap);
    }
  };
})();
