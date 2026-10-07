/* The presentation engine.
 *
 * A scene is registered with K.scene({ id, act, title, notes, build(ctx) }). `build` draws the
 * scene into ctx.el and returns its steps: an array of (async) functions. Step 0 runs when the
 * scene opens; every "next" runs the following step; after the last step, "next" opens the next
 * scene. "Previous" rebuilds the scene instantly up to the step before, so going back never
 * replays animations.
 *
 * Keys:  → Space PageDown next · ← PageUp previous · Home / End · F fullscreen · N notes
 *        S presenter window · G scene grid · T technical layer · B blackout · A auto-advance
 *
 * Auto-advance: a step that plays video calls ctx.auto(seconds); when that time is up the deck
 * moves on by itself, unless the presenter has already moved (or turned it off with A).
 */
(function () {
  const K = (window.K = window.K || {});
  K.scenes = [];
  K.scene = (def) => K.scenes.push(def);

  const W = 1920;
  const H = 1080;
  const state = { index: 0, step: 0, ctx: null, steps: [], running: null, busy: false };
  let channel = null;

  /* ------------------------------------------------------------- layout */
  function fit() {
    const stage = document.getElementById("stage");
    const scale = Math.min(window.innerWidth / W, window.innerHeight / H);
    stage.style.transform = `translate(-50%, -50%) scale(${scale})`;
  }

  /* ---------------------------------------------------------- scenes */
  async function open(index, step, { instant = false } = {}) {
    if (index < 0 || index >= K.scenes.length) return;
    clearAuto();
    state.busy = true;
    const stage = document.getElementById("stage");
    if (state.ctx) state.ctx.destroy();

    const def = K.scenes[index];
    const el = K.h("section.scene" + (def.cls ? "." + def.cls.split(" ").join(".") : ""), {
      "data-id": def.id,
      "aria-label": def.title,
    });
    stage.appendChild(el);
    const ctx = K.makeCtx(el, { instant: true });
    state.ctx = ctx;
    state.index = index;
    state.steps = (def.build && def.build(ctx)) || [];
    // headlines and statements arrive word by word (css/scenes.css .words)
    el.querySelectorAll(".title-tl.auto > :last-child, .h1.r.soft, .h2.r.soft, .h3.r.soft").forEach((x) => ctx.words(x));
    if (!state.steps.length) state.steps = [() => {}];

    // Fast-forward silently to the requested step, with transitions off. A scene reached by
    // jumping or going back shows its title at once instead of replaying the intro.
    el.classList.add("instant");
    if (instant || step > 0) el.classList.add("no-intro");
    const target = Math.min(step, state.steps.length - 1);
    for (let i = 0; i < target; i += 1) await state.steps[i]();
    el.getBoundingClientRect();
    ctx.instant = instant;
    if (!instant) el.classList.remove("instant");

    // Swap now, not on the next animation frame: every other scene retires, whatever state a
    // quick succession of jumps left it in. The reflow above lets the fade-in transition run.
    stage.querySelectorAll(".scene").forEach((other) => {
      if (other === el || other.classList.contains("leaving")) return;
      other.classList.remove("current", "on");
      other.classList.add("leaving");
      setTimeout(() => other.remove(), 900);
    });
    el.classList.add("current", "on");
    state.step = target;
    updateHud();
    broadcast();
    state.busy = false;
    run(target, instant);
    if (instant) {
      setTimeout(() => {
        el.classList.remove("instant");
        ctx.instant = false;
      }, 60);
    }
  }

  async function run(i, instant) {
    const ctx = state.ctx;
    const fn = state.steps[i];
    if (!fn) return;
    const p = Promise.resolve(fn()).catch((err) => console.error(err));
    state.running = p;
    await p;
    if (state.running === p) state.running = null;
    if (ctx) ctx.fast = false;
  }

  /* ------------------------------------------------------- auto-advance */
  let autoTimer = null;
  K.autoOn = true;
  try { K.autoOn = localStorage.getItem("keynote-auto") !== "off"; } catch (err) { /* private window: stay on */ }
  function clearAuto() {
    clearTimeout(autoTimer);
    autoTimer = null;
    const bar = document.getElementById("auto-fill");
    if (bar) { bar.style.transition = "none"; bar.style.width = "0"; }
  }
  K.autoNext = (ms) => {
    clearAuto();
    if (!K.autoOn) return;
    const at = `${state.index}.${state.step}`;
    const bar = document.getElementById("auto-fill");
    if (bar) { bar.getBoundingClientRect(); bar.style.transition = `width ${ms}ms linear`; bar.style.width = "100%"; }
    autoTimer = setTimeout(function fire() {
      if (`${state.index}.${state.step}` !== at) return;
      if (state.running) { state.running.then(fire); return; } // let the step finish first
      next();
    }, ms);
  };
  function toggleAuto() {
    K.autoOn = !K.autoOn;
    try { localStorage.setItem("keynote-auto", K.autoOn ? "on" : "off"); } catch (err) { /* ignore */ }
    if (!K.autoOn) clearAuto();
    document.getElementById("nav-auto").classList.toggle("off", !K.autoOn);
    document.getElementById("nav-auto").textContent = K.autoOn ? "Auto" : "Auto off";
  }

  function next() {
    if (state.busy) return;
    clearAuto();
    if (state.running && state.ctx) {
      // First press finishes the step in progress; the next press advances.
      state.ctx.fast = true;
      return;
    }
    if (state.step < state.steps.length - 1) {
      state.step += 1;
      updateHud();
      broadcast();
      run(state.step, false);
    } else if (state.index < K.scenes.length - 1) {
      open(state.index + 1, 0);
    }
  }

  function prev() {
    if (state.busy) return;
    clearAuto();
    if (state.step > 0) open(state.index, state.step - 1, { instant: true });
    else if (state.index > 0) open(state.index - 1, 999, { instant: true });
  }

  function go(index) {
    open(Math.max(0, Math.min(K.scenes.length - 1, index)), 0);
  }

  /* --------------------------------------------------------------- HUD */
  function updateHud() {
    const def = K.scenes[state.index];
    const total = K.scenes.length;
    const bar = document.getElementById("progress-fill");
    const stepShare = state.steps.length > 1 ? state.step / (state.steps.length - 1) : 1;
    bar.style.width = `${((state.index + stepShare) / total) * 100}%`;
    document.getElementById("hud-act").textContent = def.act || "";
    document.body.classList.toggle("nobrand", /\bnobrand\b/.test(def.cls || ""));
    document.getElementById("hud-count").textContent = `${state.index + 1} / ${total}`;
    history.replaceState(null, "", `#${state.index + 1}`);
    renderNotes();
  }

  function renderNotes() {
    const def = K.scenes[state.index];
    const n = def.notes || (K.STORY && K.STORY[def.id]) || {};
    const panel = document.getElementById("notes");
    panel.innerHTML = "";
    panel.appendChild(
      K.h("div.notes-head", null, [
        K.h("span.notes-scene", null, `Scene ${state.index + 1} · ${def.title}`),
        K.h("span.notes-step", null, `step ${state.step + 1} of ${state.steps.length}`),
      ])
    );
    const grid = K.h("div.notes-grid");
    [
      ["Say", n.say],
      ["Audience sees", n.sees],
      ["What happens", n.happens],
      ["They should understand", n.understand],
      ["Animation", n.animation],
      ["Transition", n.transition],
    ].forEach(([label, text]) => {
      if (text) grid.appendChild(K.h("div.note", null, [K.h("b", null, label), K.h("p", null, text)]));
    });
    panel.appendChild(grid);
  }

  function toggleGrid(force) {
    const grid = document.getElementById("grid");
    const show = force === undefined ? grid.hidden : force;
    grid.hidden = !show;
    if (!show) return;
    grid.innerHTML = "";
    let act = null;
    let list = null;
    K.scenes.forEach((def, i) => {
      if (def.act !== act) {
        act = def.act;
        grid.appendChild(K.h("h3", null, act));
        list = K.h("div.grid-list");
        grid.appendChild(list);
      }
      list.appendChild(
        K.h(
          "button.grid-item" + (i === state.index ? ".current" : ""),
          { onclick: () => { toggleGrid(false); go(i); } },
          [K.h("span", null, String(i + 1)), def.title]
        )
      );
    });
  }

  /* ------------------------------------------------- presenter window */
  function broadcast() {
    if (!channel) return;
    const def = K.scenes[state.index];
    const nextDef = K.scenes[state.index + 1];
    channel.postMessage({
      index: state.index,
      total: K.scenes.length,
      step: state.step,
      steps: state.steps.length,
      title: def.title,
      act: def.act,
      notes: def.notes || (K.STORY && K.STORY[def.id]) || {},
      next: nextDef ? nextDef.title : null,
    });
  }

  function openPresenter() {
    window.open("notes.html", "anpr-presenter", "width=980,height=720");
    setTimeout(broadcast, 600);
  }

  /* ------------------------------------------------------------ input */
  function isInteractive(target) {
    return target && target.closest && target.closest("input, select, textarea, button, a, [data-interactive]");
  }

  function onKey(e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const grid = document.getElementById("grid");
    const typing = e.target && e.target.closest && e.target.closest("input, select, textarea");
    if (typing && !["Escape"].includes(e.key)) return;
    switch (e.key) {
      case "ArrowRight":
      case "PageDown":
      case " ":
        e.preventDefault();
        if (!grid.hidden) return;
        next();
        break;
      case "ArrowLeft":
      case "PageUp":
        e.preventDefault();
        if (!grid.hidden) return;
        prev();
        break;
      case "Home":
        go(0);
        break;
      case "End":
        go(K.scenes.length - 1);
        break;
      case "f":
      case "F":
        if (document.fullscreenElement) document.exitFullscreen();
        else document.documentElement.requestFullscreen().catch(() => {});
        break;
      case "n":
      case "N":
        document.getElementById("notes").hidden = !document.getElementById("notes").hidden;
        break;
      case "s":
      case "S":
        openPresenter();
        break;
      case "g":
      case "G":
        toggleGrid();
        break;
      case "t":
      case "T":
        document.body.classList.toggle("tech");
        break;
      case "b":
      case "B":
        document.body.classList.toggle("blackout");
        break;
      case "a":
      case "A":
        toggleAuto();
        break;
      case "Escape":
        toggleGrid(false);
        document.getElementById("notes").hidden = true;
        document.body.classList.remove("blackout");
        break;
      default:
        break;
    }
  }

  let idleTimer = null;
  function wake() {
    document.body.classList.remove("idle");
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => document.body.classList.add("idle"), 2500);
  }

  /* ------------------------------------------------------------- start */
  K.start = function start() {
    fit();
    window.addEventListener("resize", fit);
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousemove", wake);
    document.getElementById("stage").addEventListener("click", (e) => {
      if (isInteractive(e.target)) return;
      next();
    });
    document.getElementById("nav-prev").addEventListener("click", prev);
    document.getElementById("nav-next").addEventListener("click", next);
    document.getElementById("nav-grid").addEventListener("click", () => toggleGrid());
    document.getElementById("nav-tech").addEventListener("click", () => document.body.classList.toggle("tech"));
    document.getElementById("nav-auto").addEventListener("click", toggleAuto);
    if (!K.autoOn) { K.autoOn = true; toggleAuto(); }
    try {
      channel = new BroadcastChannel("anpr-keynote");
      channel.onmessage = (msg) => {
        if (msg.data === "next") next();
        else if (msg.data === "prev") prev();
        else if (msg.data === "hello") broadcast();
      };
    } catch (err) {
      channel = null;
    }
    wake();
    // #12 opens scene 12; #12.3 opens it already at step 3 (rehearsal)
    const m = /^#(\d+)(?:\.(\d+))?$/.exec(location.hash || "");
    const step = m && m[2] ? Number(m[2]) : 0;
    open(m ? Number(m[1]) - 1 : 0, step, { instant: step > 0 });
  };

  /** show(i, step): jump straight to a scene's step, already built (rehearsal and testing). */
  K.nav = { next, prev, go, show: (i, step = 0) => open(i, step, { instant: true }) };
})();
