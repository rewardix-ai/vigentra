/* The pipeline explorer and the CCTV wall.
 *
 * K.ui.pipeline(stages, opts)  stages in a column or row; pulses travel down it; clicking a stage
 *                               opens what happens there (its `detail` element or text).
 * K.ui.wall(feeds, opts)        a grid of real feeds; click one to bring it full size.
 */
(function () {
  const K = (window.K = window.K || {});
  K.ui = K.ui || {};

  K.ui.pipeline = function pipeline(stages, { direction = "column", onSelect, interactive = true } = {}) {
    const wrap = K.h("div.pipe.pipe-" + direction, interactive ? { "data-interactive": true } : null);
    stages.forEach((stage, i) => {
      if (i) wrap.appendChild(K.h("div.pipe-link.r", null, [K.h("span.pipe-pulse")]));
      const node = K.h(
        interactive ? "button.pipe-node.r" : "div.pipe-node.r",
        {
          "data-i": i,
          style: stage.color ? { "--c": stage.color } : null,
          onclick: interactive ? () => select(i) : null,
        },
        [K.h("span.pipe-name", null, stage.name), stage.hint ? K.h("span.pipe-hint", null, stage.hint) : null]
      );
      wrap.appendChild(node);
    });
    const select = (i) => {
      wrap.querySelectorAll(".pipe-node").forEach((n, j) => n.classList.toggle("on", i === j));
      if (onSelect) onSelect(stages[i], i);
    };
    wrap.select = select;
    wrap.flow = (on = true) => wrap.classList.toggle("flowing", on);
    return wrap;
  };

  K.ui.wall = function wall(feeds, { columns = 3 } = {}) {
    const wrap = K.h("div.wall", { style: { "--cols": columns }, "data-interactive": true });
    const big = K.h("div.wall-big", { hidden: true });
    feeds.forEach((feed) => {
      const tile = K.h("button.wall-tile.r", { "aria-label": feed.label }, [
        feed.media(),
        K.h("span.wall-label", null, [K.h("i.wall-dot"), feed.label]),
        feed.badge ? K.h("span.wall-badge", null, feed.badge) : null,
      ]);
      tile.addEventListener("click", () => {
        big.innerHTML = "";
        big.append(feed.media({ large: true }), K.h("span.wall-label.big", null, [K.h("i.wall-dot"), feed.label]), feed.detail ? K.h("div.wall-detail", null, feed.detail) : null);
        big.hidden = false;
      });
      wrap.appendChild(tile);
    });
    big.addEventListener("click", () => { big.hidden = true; big.innerHTML = ""; });
    wrap.appendChild(big);
    return wrap;
  };
})();
