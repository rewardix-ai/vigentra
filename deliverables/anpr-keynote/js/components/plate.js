/* The number plate and the reading of it.
 *
 * K.ui.plate(text)          an HSRP-style plate (white, black glyphs, blue IND strip); each
 *                           character is its own span so a scene can reveal or flag it.
 * K.ui.readings(list)       several OCR readings of one vehicle, stacked so the agreeing and
 *                           disagreeing characters line up - the vote, made visible.
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

  /** Mark characters (by index) with a state class: "doubt", "wrong", "fixed", "ok". */
  K.ui.markChars = function markChars(plateEl, indexes, state) {
    plateEl.querySelectorAll(".pc").forEach((span) => {
      if (indexes.includes(Number(span.dataset.i))) span.classList.add(state);
    });
  };

  /** Readings of one plate from several frames, aligned per character against `truth`. */
  K.ui.readings = function readings(list, truth) {
    const wrap = K.h("div.readings");
    list.forEach((reading) => {
      const row = K.h("div.reading.r");
      row.appendChild(K.h("span.reading-src", null, reading.src || ""));
      const chars = K.h("span.reading-chars");
      [...reading.text].forEach((ch, i) => {
        const wrong = truth && truth[i] !== ch;
        chars.appendChild(K.h("span" + (wrong ? ".x" : ""), null, ch));
      });
      row.appendChild(chars);
      if (reading.note) row.appendChild(K.h("span.reading-note", null, reading.note));
      wrap.appendChild(row);
    });
    return wrap;
  };
})();
