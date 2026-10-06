/* The CCTV wall.
 *
 * K.ui.wall(feeds, opts)        a grid of real feeds; click one to bring it full size.
 */
(function () {
  const K = (window.K = window.K || {});
  K.ui = K.ui || {};

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
