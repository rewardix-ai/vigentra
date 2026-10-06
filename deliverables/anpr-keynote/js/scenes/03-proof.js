/* Proof: CAM06 and Delhi, the measured result, and the limit no model passes. */
(function () {
  const K = window.K;
  const h = K.h;
  const S = K.S;
  const D = K.DATA;
  const F = D.final;

  /* ---------------------------------------------------------------- challenge */
  K.scene({
    id: "challenge", act: "Proof", title: "CAM06 and Delhi",
    build(ctx) {
      const panel = (id, slot, title, legible, read) => h("div.chal.r", { id }, [
        h("div.frame.chal-media", null, K.media.video(slot, { cls: "media-cover" })),
        h("div.chal-title.h3", null, title),
        h("div.chal-nums", null, [
          h("div", null, [h("span.kicker", null, "Readable by eye"), h("b.num", { "data-to": legible }, "–")]),
          h("div", null, [h("span.kicker", null, "Read by Vigentra"), h("b.num.c-ok", { "data-to": read }, "–")]),
          h("div", null, [h("span.kicker", null, "Wrong"), h("b.num.c-ok", { "data-to": 0 }, "–")]),
        ]),
      ]);
      ctx.el.append(
        S.title("Real footage, real test", "Can it read enough, and never guess?"),
        S.at(140, 280, 790, null, panel("c6", "cam06_vigentra", "CAM06 · government camera · midday", F.cam06Noon[1], F.cam06Noon[0])),
        S.at(990, 280, 790, null, panel("dl", "delhi_vigentra", "Delhi · busy street · hand-held", F.delhi[1], F.delhi[0])),
        S.tech(["<b>Readable by eye</b> every legible plate counted frame by frame, before scoring", "<b>Videos</b> Vigentra's own output: each plate beside the crop it was read from"], F.src)
      );
      const countIn = async (panelId) => {
        for (const b of ctx.$$(`#${panelId} b[data-to]`)) {
          await ctx.count(b, Number(b.dataset.to), { dur: 800 });
          await ctx.wait(250);
        }
      };
      return [
        async () => { await ctx.in("#c6"); await countIn("c6"); },
        async () => { await ctx.in("#dl"); await countIn("dl"); },
      ];
    },
  });

  /* ---------------------------------------------------------------- results */
  K.scene({
    id: "results", act: "Proof", title: "What Vigentra achieved",
    build(ctx) {
      const share = F.read / F.legible;
      const R = 210;
      const C = 2 * Math.PI * R;
      const ring = h("div.ring", {
        html: `<svg viewBox="0 0 500 500"><circle class="ring-bg" cx="250" cy="250" r="${R}"/><circle class="ring-fg" cx="250" cy="250" r="${R}" style="stroke-dasharray:${C};stroke-dashoffset:${C}"/></svg>`,
      });
      const fast = D.speed.rows.find((r) => r.res === "720p");
      ctx.el.append(
        S.title("Results", "Every plate a person could read, scored."),
        S.at(150, 280, 500, 500, h("div.r", { id: "ring" }, [ring, h("div.ring-in", null, [h("div.res-big.num", { id: "n-read" }, "0"), h("div.lede", null, `of ${F.legible} legible plates read`)])])),
        S.at(760, 330, 1020, null, h("div.stack", { style: { gap: "54px" } }, [
          h("div.res-line.r", { id: "wrong" }, [h("b.num.c-ok", null, String(F.wrong)), h("span", null, "wrong plates. Not one.")]),
          h("div.res-line.r", { id: "missed" }, [h("b.num.c-warn", null, String(F.legible - F.read)), h("span", null, "missed. For those it said nothing, rather than give a wrong plate.")]),
          h("div.res-line.r", { id: "speed" }, [h("b.c-track", null, "Live"), h("span", null, `keeps pace with a ${fast.res} camera on ${D.speed.machine}.`)]),
        ])),
        h("div.cap-bottom", null, h("div.caption.r", { id: "def" }, "Legible means readable by eye, counted frame by frame before scoring.")),
        S.tech([`<b>Per resolution</b> ${D.speed.rows.map((r) => `${r.res} ${r.ms} ms a frame (${r.rt.toFixed(2)}× live)`).join(" · ")}`], `${F.src} · ${D.speed.src}`)
      );
      return [
        async () => {
          await ctx.in("#ring");
          ctx.$(".ring-fg").style.strokeDashoffset = String(C * (1 - share));
          await ctx.count("#n-read", F.read, { dur: 1400 });
          await ctx.in("#def");
        },
        () => ctx.in("#wrong"),
        () => ctx.in("#missed"),
        () => ctx.in("#speed"),
      ];
    },
  });

  /* ---------------------------------------------------------------- lesson */
  K.scene({
    id: "lesson", act: "Proof", title: "The camera decides",
    build(ctx) {
      const diff = K.ui.difficulty("assets/img/journey_best.png");
      ctx.el.append(
        S.title("The boundary", "Take away what the camera captured…"),
        h("div.center", null, h("div.r", { id: "diff" }, diff)),
        S.at(140, 960, 1640, null, h("div.caption.r", { id: "demo" }, "Demonstration on the real crop: fewer pixels, more blur, less light, more noise. Drag it.")),
        h("div.center.over", null, h("div.stack", { style: { alignItems: "center", gap: "22px" } }, [
          h("div.h1.r.soft", { id: "can" }, "AI can infer patterns."),
          h("div.h1.r.soft.c-bad", { id: "cannot", html: "It cannot recover what<br>was never captured." }),
        ]))
      );
      return [
        async () => { await ctx.in("#diff"); await ctx.in("#demo"); },
        async () => { for (let v = 0; v <= 100; v += 4) { diff.set(v); await ctx.wait(60); } },
        async () => { ctx.$("#diff").classList.add("dimmed"); ctx.$("#demo").classList.add("gone"); await ctx.in("#can"); await ctx.in("#cannot", { delay: 600 }); },
      ];
    },
  });
})();
