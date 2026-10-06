/* Media slots: every video and image the presentation shows is named in js/data/assets.js.
 *
 * A slot that is missing renders as a labelled placeholder ("[REAL CAM 06 FOOTAGE REQUIRED]")
 * instead of an invented stand-in, so replacing footage later is one line in the manifest.
 */
(function () {
  const K = (window.K = window.K || {});

  function placeholder(slot, cls) {
    const label = (slot && slot.need) || "[REAL FOOTAGE REQUIRED]";
    return K.h("div.missing" + (cls ? "." + cls : ""), null, [K.h("span", null, label)]);
  }

  K.media = {
    slot(key) {
      return (K.ASSETS && K.ASSETS[key]) || null;
    },

    /** A muted, looping video for a slot, optionally limited to [start, end] seconds. */
    video(key, { start = 0, end = null, loop = true, autoplay = true, cls = "", rate = 1 } = {}) {
      const slot = K.media.slot(key);
      if (!slot || !slot.src) return placeholder(slot, cls);
      const v = K.h("video" + (cls ? "." + cls : ""), {
        src: slot.src,
        muted: true,
        playsinline: true,
        preload: "auto",
        "aria-label": slot.label || key,
      });
      v.muted = true;
      v.playbackRate = rate;
      v.addEventListener("loadedmetadata", () => {
        if (start) v.currentTime = start;
        v.playbackRate = rate;
        if (autoplay) v.play().catch(() => {});
      });
      v.addEventListener("timeupdate", () => {
        if (end != null && v.currentTime >= end) {
          if (loop) v.currentTime = start;
          else v.pause();
        }
      });
      if (loop && end == null && !start) v.loop = true;
      else if (loop && end == null) v.addEventListener("ended", () => { v.currentTime = start; v.play().catch(() => {}); });
      v.addEventListener("error", () => v.replaceWith(placeholder(slot, cls)));
      return v;
    },

    /** An image for a slot, or its placeholder. */
    img(key, { cls = "", alt = "" } = {}) {
      const slot = K.media.slot(key);
      if (!slot || !slot.src) return placeholder(slot, cls);
      const im = K.h("img" + (cls ? "." + cls : ""), { src: slot.src, alt: alt || slot.label || key, draggable: "false" });
      im.addEventListener("error", () => im.replaceWith(placeholder(slot, cls)));
      return im;
    },

    /** Draw an image into a canvas at `factor` of its resolution, then back up with hard
     * pixels: what the plate looks like when the camera captured fewer pixels of it. */
    pixelate(canvas, image, factor, { blur = 0, dark = 0, noise = 0 } = {}) {
      const w = canvas.width;
      const h = canvas.height;
      const g = canvas.getContext("2d");
      const sw = Math.max(1, Math.round(w * factor));
      const sh = Math.max(1, Math.round(h * factor));
      const tmp = document.createElement("canvas");
      tmp.width = sw;
      tmp.height = sh;
      const tg = tmp.getContext("2d");
      tg.filter = blur ? `blur(${blur * factor}px)` : "none";
      tg.drawImage(image, 0, 0, sw, sh);
      if (noise) {
        const data = tg.getImageData(0, 0, sw, sh);
        for (let i = 0; i < data.data.length; i += 4) {
          const n = (Math.random() - 0.5) * 255 * noise;
          data.data[i] += n;
          data.data[i + 1] += n;
          data.data[i + 2] += n;
        }
        tg.putImageData(data, 0, 0);
      }
      g.imageSmoothingEnabled = false;
      g.filter = dark ? `brightness(${1 - dark})` : "none";
      g.clearRect(0, 0, w, h);
      g.drawImage(tmp, 0, 0, w, h);
      g.filter = "none";
    },
  };
})();
