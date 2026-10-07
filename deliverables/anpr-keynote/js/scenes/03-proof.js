/* Field test: CAM06 and Delhi, the measured result, and the known limits. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const D = K.DATA;
  const F = D.final;

  /* ---------------------------------------------------------------- field */
  K.scene({
    id: "field", act: "Field test", title: "CAM06 and Delhi", src: "SRC Vigentra output · CAM06 18-06-2026 11:46 · Delhi street clip",
    build(ctx) {
      const readouts = (id, legible, read) => h("div.chal-nums", { id }, [
        h("div.ro", null, [h("span.k", null, "Readable by eye"), h("b.v.num", { "data-to": legible }, "–")]),
        h("div.ro", null, [h("span.k", null, "Read by Vigentra"), h("b.v.num.c-ok", { "data-to": read }, "–")]),
        h("div.ro", null, [h("span.k", null, "Wrong"), h("b.v.num.c-ok", { "data-to": 0 }, "–")]),
      ]);
      ctx.el.append(
        S.title("Field test", "Can it read enough, and never guess?"),
        // CAM06's readings start 29 s into its result video; Delhi's video is 30 s long
        S.panel(80, 220, 860, 524, "CAM06 · government camera · midday", S.monitor(K.media.video("cam06_vigentra", { start: 29, end: 56, cls: "media-cover" })), { id: "c6", right: S.rec() }),
        S.panel(980, 220, 860, 524, "Delhi · busy street · hand-held", S.monitor(K.media.video("delhi_vigentra", { autoplay: false, cls: "media-cover" })), { id: "dl", right: S.rec() }),
        S.at(80, 770, 860, null, readouts("n6", F.cam06Noon[1], F.cam06Noon[0])),
        S.at(980, 770, 860, null, readouts("nd", F.delhi[1], F.delhi[0])),
        S.tech(["<b>Readable by eye</b> every legible plate counted frame by frame, before scoring", "<b>Videos</b> Vigentra's own output: each plate beside the crop it was read from"], F.src)
      );
      const countIn = async (id) => {
        ctx.$(`#${id}`).classList.add("in");
        for (const b of ctx.$$(`#${id} b[data-to]`)) { await ctx.count(b, Number(b.dataset.to), { dur: 800 }); await ctx.wait(250); }
      };
      return [
        async () => { ctx.auto(24); await ctx.in("#c6"); await countIn("n6"); },
        async () => {
          ctx.auto(28);
          const v = ctx.$("#dl video");
          if (v && v.play) { v.currentTime = 0; v.play().catch(() => {}); }
          await ctx.in("#dl");
          await countIn("nd");
        },
      ];
    },
  });

  /* ---------------------------------------------------------------- results */
  K.scene({
    id: "results", act: "Field test", title: "What Vigentra achieved", src: "SRC benchmark final2 · legible = readable by eye, counted frame by frame",
    build(ctx) {
      const share = F.read / F.legible;
      const R = 200;
      const C = 2 * Math.PI * R;
      const ring = h("div.ring", { html: `<svg viewBox="0 0 500 500"><circle class="ring-bg" cx="250" cy="250" r="${R}"/><circle class="ring-fg" cx="250" cy="250" r="${R}" style="stroke-dasharray:${C};stroke-dashoffset:${C}"/></svg>` });
      const fast = D.speed.rows.find((r) => r.res === "720p");
      const row = (id, big, cls, k, s) => h("div.res-row", { id }, [h("b.num" + (cls ? "." + cls : ""), null, big), h("div", null, [h("span.k", null, k), h("span.s", null, s)])]);
      ctx.el.append(
        S.title("Results", "Every plate a person could read, scored."),
        S.panel(80, 220, 640, 660, "Legible plates read", h("div.ring-box", null, [ring, h("div.ring-in", null, [h("div.res-big.num", { id: "n-read" }, "0"), h("div.res-of", null, `of ${F.legible}`)])]), { id: "rp" }),
        S.panel(760, 220, 1080, 660, "Outcome", h("div.res-rows", null, [
          row("r1", String(F.wrong), "c-ok", "Wrong plates", "Not one, across every test clip."),
          row("r2", String(F.legible - F.read), "c-warn", "Missed", "Nothing was saved for them, rather than a wrong plate."),
          row("r3", "Live", "c-track", "Speed", `Keeps pace with a ${fast.res} camera on ${D.speed.machine}.`),
        ]), { id: "op" }),
        S.tech([`<b>Per resolution</b> ${D.speed.rows.map((r) => `${r.res} ${r.ms} ms a frame (${r.rt.toFixed(2)}× live)`).join(" · ")}`], `${F.src} · ${D.speed.src}`)
      );
      return [
        async () => {
          await ctx.in("#rp");
          ctx.$(".ring-fg").style.strokeDashoffset = String(C * (1 - share));
          await ctx.count("#n-read", F.read, { dur: 1400 });
        },
        async () => { await ctx.in("#op"); ctx.$("#r1").classList.add("in"); },
        () => ctx.$("#r2").classList.add("in"),
        () => ctx.$("#r3").classList.add("in"),
      ];
    },
  });

  /* ---------------------------------------------------------------- limits */
  K.scene({
    id: "limits", act: "Known limits", title: "What still doesn't work", src: "Demonstration on the real crop · limits from the project's own reports",
    build(ctx) {
      const diff = K.ui.difficulty("assets/img/journey_best.png");
      const items = [
        ["Night and glare", "Plates are found after dark, but rarely legible."],
        ["Small, distant plates", "Under 22 pixels wide, Vigentra will not read a plate."],
        ["GPU servers for every feed", "One laptop keeps pace with one camera; all thirty would need GPU servers."],
      ];
      ctx.el.append(
        S.title("Known limits", "What still doesn't work."),
        S.panel(80, 220, 980, 600, "Signal quality · demonstration on the real crop", h("div.center", null, diff), { id: "dp", right: "drag" }),
        S.panel(1090, 220, 750, 600, "Known limits", h("div.lim", null, items.map(([a, b], i) => h("div.lim-i", null, [h("span.lim-n.num", null, String(i + 1).padStart(2, "0")), h("div", null, [h("b", null, a), h("span", null, b)])]))), { id: "kp" }),
        S.banner("The boundary", "AI cannot recover what the camera never captured.", { id: "b1", tone: "bad" })
      );
      return [
        () => ctx.in("#dp"),
        async () => { for (let v = 0; v <= 100; v += 4) { diff.set(v); await ctx.wait(60); } await S.show(ctx, "#b1"); },
        async () => { S.hide(ctx, "#b1"); await ctx.in("#kp"); for (const i of ctx.$$(".lim-i")) { i.classList.add("in"); await ctx.wait(350); } },
      ];
    },
  });
})();
