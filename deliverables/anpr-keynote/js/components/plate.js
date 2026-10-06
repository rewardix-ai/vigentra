/* The number plate.
 *
 * K.ui.plate(text)          an HSRP-style plate (white, black glyphs, blue IND strip); each
 *                           character is its own span so a scene can reveal it.
 */
(function () {
  const K = (window.K = window.K || {});
  K.ui = K.ui || {};

  K.ui.plate = function plate(text, { size = 1, hidden = false, cls = "" } = {}) {
    const chars = K.h("div.plate-chars");
    [...String(text)].forEach((ch, i) => {
      chars.appendChild(K.h("span.pc" + (hidden ? "" : ".in") + (ch === " " ? ".gap" : ""), { "data-i": i }, ch === " " ? " " : ch));
    });
    return K.h("div.hsrp" + (cls ? "." + cls : ""), { style: { "--s": size } }, [
      K.h("div.hsrp-strip", null, [K.h("span.hsrp-chakra"), K.h("span", null, "IND")]),
      chars,
    ]);
  };

  /** Reveal a plate's characters one at a time. */
  K.ui.revealPlate = async function revealPlate(ctx, plateEl, gap = 120) {
    for (const span of plateEl.querySelectorAll(".pc")) {
      span.classList.add("in");
      await ctx.wait(gap);
    }
  };
})();
