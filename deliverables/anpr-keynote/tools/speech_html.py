#!/usr/bin/env python3
"""The presenter's speech guide (SPEECH.html), built from the deck's own speaker notes.

Scene order and titles come from the scene files, the words from js/data/storyboard.js, the same
sources as STORYBOARD.md, so the guide can never drift from the deck.

    python deliverables/anpr-keynote/tools/speech_html.py
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "tools"))
from storyboard import scenes, story  # noqa: E402

CSS = """
:root { --bg:#f6f7fb; --card:#fff; --ink:#16182b; --ink2:#4a4d66; --ink3:#6b6e88; --line:#e3e5ef; --accent:#4c47d6;
        --soft:#eeedfd; --say:#0f5132; --saybg:#e8f5ee; --night:#0b0a1f; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#11101f; --card:#191830; --ink:#ecebff; --ink2:#b7b6d6;
        --ink3:#8f8eb2; --line:#2b2a48; --accent:#a9a5ff; --soft:#24224a; --say:#9fe0bd; --saybg:#16301f; color-scheme:dark; } }
:root[data-theme="dark"] { --bg:#11101f; --card:#191830; --ink:#ecebff; --ink2:#b7b6d6; --ink3:#8f8eb2; --line:#2b2a48;
        --accent:#a9a5ff; --soft:#24224a; --say:#9fe0bd; --saybg:#16301f; color-scheme:dark; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.6 "DM Sans", system-ui, -apple-system, sans-serif; }
.mast { background:var(--night); color:#e9e8ff; padding:36px 16px 30px; }
.in { max-width:880px; margin:0 auto; padding:0 16px; }
.mast h1 { font:800 clamp(26px,4vw,38px)/1.15 "Montserrat", system-ui, sans-serif; margin:6px 0 8px; }
.mast p { margin:0; color:#b9b6ff; max-width:64ch; }
.k { font:600 12px "JetBrains Mono", ui-monospace, monospace; letter-spacing:.1em; text-transform:uppercase; color:#8d89e8; }
main.in { padding-block:24px 64px; }
.tips { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:12px; margin:0 0 28px; }
.tip { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; font-size:15px; }
.tip b { display:block; margin-bottom:2px; }
.act { font:600 12px "JetBrains Mono", ui-monospace, monospace; letter-spacing:.12em; text-transform:uppercase; color:var(--accent);
       margin:30px 0 8px; }
.sc { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 18px; margin-bottom:12px; }
.sc h2 { margin:0 0 10px; font:700 20px/1.3 "Montserrat", system-ui, sans-serif; display:flex; gap:12px; align-items:baseline; }
.sc h2 .n { font:600 13px "JetBrains Mono", ui-monospace, monospace; color:var(--ink3); min-width:2.5em; }
.row { display:grid; grid-template-columns:92px minmax(0,1fr); gap:10px; margin:6px 0; font-size:15px; }
.row .l { font:600 11px/2 "JetBrains Mono", ui-monospace, monospace; letter-spacing:.08em; text-transform:uppercase; color:var(--ink3); }
.say { background:var(--saybg); color:var(--ink); border-radius:8px; padding:10px 12px; font-size:16.5px; line-height:1.55; }
.say .cue { font:600 12px "JetBrains Mono", ui-monospace, monospace; color:var(--say); background:transparent; padding:0 2px; }
.muted { color:var(--ink2); }
@media (max-width:560px) { .row { grid-template-columns:1fr; gap:2px; } }
"""


def say_html(text: str) -> str:
    out = html.escape(text)
    return re.sub(r"\[(pause|beat)\]", lambda m: f'<span class="cue">{m.group(1).upper()}</span>', out)


def main() -> int:
    notes = story()
    order = scenes()
    words = sum(len(re.sub(r"\[(pause|beat)\]", "", notes.get(sid, {}).get("say", "")).split()) for sid, _, _ in order)
    minutes = words / 130
    parts = [f"<title>Vigentra Keynote Speech</title>", f"<style>{CSS}</style>",
             '<header class="mast"><div class="in"><div class="k">Presenter guide · From pixels to information</div>',
             f"<h1>What to say, scene by scene</h1><p>{len(order)} scenes, {words:,} words: about {minutes:.0f} minutes at a calm 130 words a minute, "
             "a little more with the live moments. Read the green line aloud; the rest is for you. Generated from the deck's own "
             "speaker notes (js/data/storyboard.js), which the presenter view (S) and the stage notes (N) also show.</p></div></header>",
             '<main class="in"><div class="tips">',
             '<div class="tip"><b>Start</b>python3 deliverables/anpr-keynote/tools/serve.py, open http://127.0.0.1:8765, press S for this view on your screen, F for fullscreen.</div>',
             '<div class="tip"><b>Move</b>→ or Space for the next beat, ← to go back. Nothing moves on by itself: every scene waits for you.</div>',
             '<div class="tip"><b>Cues</b><span class="muted">PAUSE</span> stop for a breath. <span class="muted">BEAT</span> a half-second, let the screen land.</div>',
             '<div class="tip"><b>Questions</b>G shows all scenes to jump to; T shows the models and sources behind any scene.</div>',
             "</div>"]
    last_act = None
    for i, (sid, act, title) in enumerate(order, 1):
        n = notes.get(sid, {})
        if act != last_act:
            parts.append(f'<div class="act">{html.escape(act)}</div>')
            last_act = act
        rows = [("On screen", html.escape(n.get("sees", ""))), ("Say", f'<div class="say">{say_html(n.get("say", ""))}</div>'),
                ("Land this", html.escape(n.get("understand", ""))), ("Then", html.escape(n.get("transition", "")))]
        body = "".join(f'<div class="row"><span class="l">{l}</span><div>{v}</div></div>' for l, v in rows if v)
        parts.append(f'<section class="sc" id="s{i}"><h2><span class="n">{i:02d}</span>{html.escape(title)}</h2>{body}</section>')
    parts.append("</main>")
    (HERE / "SPEECH.html").write_text("\n".join(parts), encoding="utf-8")
    print(f"SPEECH.html: {len(order)} scenes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
