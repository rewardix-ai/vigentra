"""Render a Markdown document as one standalone, styled page (logo embedded, no network needed) that
reads well in a browser and prints cleanly to PDF. The Markdown stays the source.

    /Users/uchit/Downloads/ANPR/.venv/bin/python deliverables/anpr-keynote/tools/technical_html.py
        (TECHNICAL.md -> TECHNICAL.html)
    ... technical_html.py docs/api-methods.md docs/api-methods.html "API reference · methods and reasons"
"""
import base64
import datetime
import html
import sys
import re
from pathlib import Path

from markdown_it import MarkdownIt

HERE = Path(__file__).resolve().parent.parent

CSS = """
:root {
  --night: #0b0a1f; --paper: #f6f7fb; --card: #ffffff; --ink: #16182b; --ink-2: #4a4d66; --ink-3: #6b6e88;
  --line: #e3e5ef; --iris: #4c47d6; --iris-ink: #3a35b8; --iris-soft: #eeedfd; --lav: #b9b6ff;
  --text: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --display: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; scroll-behavior: smooth; }
body { margin: 0; background: var(--paper); color: var(--ink); font: 400 16.5px/1.6 var(--text); }
.hero { background: var(--night); color: #fff; padding: 36px 24px 76px; }
.hero-in { max-width: 1180px; margin: 0 auto; }
.brand { display: flex; align-items: center; gap: 12px; }
.brand .mark { height: 30px; }
.brand .word { height: 13px; }
.kicker { margin-top: 52px; font: 500 13px var(--mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--lav); }
h1 { margin: 12px 0 0; font: 650 clamp(34px, 5.4vw, 58px)/1.05 var(--display); letter-spacing: -0.025em; }
.lead { max-width: 780px; margin-top: 18px; font-size: 18px; color: rgba(255, 255, 255, 0.74); }
.lead p { margin: 0; }
.lead code { color: #fff; background: rgba(255, 255, 255, 0.1); }
.meta { margin-top: 24px; display: flex; flex-wrap: wrap; gap: 8px; }
.meta span { padding: 6px 12px; border-radius: 999px; background: rgba(255, 255, 255, 0.08); font: 500 13px var(--text); color: rgba(255, 255, 255, 0.82); }
.layout { max-width: 1180px; margin: -44px auto 0; padding: 0 24px 72px; display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 28px; align-items: start; }
.toc { position: sticky; top: 16px; margin-top: 64px; max-height: calc(100vh - 32px); overflow-y: auto; font-size: 14px; }
.toc b { display: block; margin: 0 10px 10px; font: 500 12px var(--mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-3); }
.toc a { display: block; padding: 6px 10px; border-radius: 8px; color: var(--ink-2); text-decoration: none; line-height: 1.35; }
.toc a:hover { background: var(--iris-soft); color: var(--iris-ink); }
main { min-width: 0; background: var(--card); border-radius: 18px; box-shadow: 0 0 0 1px var(--line); padding: 40px 48px 52px; }
main h2 { margin: 52px 0 14px; padding-top: 26px; border-top: 1px solid var(--line); font: 650 27px/1.2 var(--display); letter-spacing: -0.015em; scroll-margin-top: 16px; }
main h2:first-child { margin-top: 0; padding-top: 0; border-top: 0; }
main p { margin: 12px 0; }
main p > strong:first-child { color: var(--iris-ink); }
main strong { font-weight: 650; }
main a { color: var(--iris); text-decoration: none; border-bottom: 1px solid rgba(76, 71, 214, 0.3); }
main a:hover { border-bottom-color: var(--iris); }
code { font: 500 0.86em var(--mono); background: #f1f2f8; color: var(--iris-ink); padding: 2px 6px; border-radius: 6px; overflow-wrap: anywhere; }
main ul, main ol { margin: 10px 0; padding-left: 22px; }
main li { margin: 6px 0; }
main li::marker { color: var(--iris); }
main li > ul, main li > ol { margin: 6px 0; }
.tw { margin: 18px 0 24px; overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 15px; line-height: 1.45; }
th { padding: 11px 14px; background: #f4f5fa; border-bottom: 1px solid var(--line); text-align: left; font: 600 12px var(--mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-3); white-space: nowrap; }
td { padding: 11px 14px; border-bottom: 1px solid var(--line); vertical-align: top; }
tr:last-child td { border-bottom: 0; }
td:first-child { min-width: 150px; font-weight: 600; }
tbody tr:hover td { background: #fafaff; }
footer { margin-top: 44px; padding-top: 18px; border-top: 1px solid var(--line); font-size: 13px; color: var(--ink-3); }
@media (max-width: 920px) {
  .layout { display: block; margin-top: -40px; padding: 0 12px 48px; }
  .toc { position: static; max-height: none; margin: 0 0 14px; padding: 16px 8px 12px; background: var(--card); border-radius: 16px; box-shadow: 0 0 0 1px var(--line); display: grid; grid-template-columns: 1fr 1fr; gap: 2px; }
  .toc b { grid-column: 1 / -1; }
  main { padding: 26px 18px 36px; border-radius: 16px; }
  .hero { padding: 28px 16px 64px; }
  .kicker { margin-top: 36px; }
  body { font-size: 16px; }
}
@media (max-width: 560px) { .toc { grid-template-columns: 1fr; } }
@media print {
  @page { size: A4; margin: 14mm; }
  body { background: #fff; font-size: 11pt; }
  .hero { padding: 20px 24px 28px; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  .kicker { margin-top: 20px; }
  .toc { display: none; }
  .layout { display: block; margin: 0; padding: 0; }
  main { padding: 18px 0 0; box-shadow: none; border-radius: 0; }
  main h2 { break-after: avoid; }
  tr, li { break-inside: avoid; }
  .tw { overflow: visible; }
  main a { border: 0; color: inherit; }
}
"""


def data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def main() -> int:
    args = sys.argv[1:]
    src_path = Path(args[0]) if args else HERE / "TECHNICAL.md"
    out_path = Path(args[1]) if len(args) > 1 else HERE / "TECHNICAL.html"
    kicker = args[2] if len(args) > 2 else "Technical brief · for the keynote's questions"
    src = src_path.read_text()
    head, _, rest = src.partition("\n")
    lead, _, body = rest.strip().partition("\n## ")
    md = MarkdownIt("commonmark", {"html": False}).enable("table")

    toc = []

    def anchor(m: re.Match) -> str:
        text = html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))
        toc.append((slug(text), text))
        return f'<h2 id="{slug(text)}">{m.group(1)}</h2>'

    body_html = re.sub(r"<h2>(.*?)</h2>", anchor, md.render("## " + body))
    body_html = body_html.replace("<table>", '<div class="tw"><table>').replace("</table>", "</table></div>")
    title = head.lstrip("# ").split(": ", 1)[-1]
    title = title[0].upper() + title[1:]
    today = datetime.date.today()
    nav = "".join(f'<a href="#{sid}">{html.escape(text)}</a>' for sid, text in toc)
    brand = HERE / "brand"
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vigentra · {html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header class="hero"><div class="hero-in">
  <div class="brand"><img class="mark" src="{data_uri(brand / 'vigentra-mark.png')}" alt="Vigentra"><img class="word" src="{data_uri(brand / 'vigentra-wordmark.png')}" alt=""></div>
  <div class="kicker">{html.escape(kicker)}</div>
  <h1>{html.escape(title)}</h1>
  <div class="lead">{md.render(lead)}</div>
  <div class="meta"><span>{len(toc)} sections</span><span>Sources cited by file</span><span>Estimates marked as estimates</span><span>Updated {today.day} {today:%b %Y}</span></div>
</div></header>
<div class="layout">
  <nav class="toc" aria-label="Contents"><b>Contents</b>{nav}</nav>
  <main>
{body_html}
    <footer>Generated from {src_path.name} by deliverables/anpr-keynote/tools/technical_html.py. Links open from inside the repository.</footer>
  </main>
</div>
</body>
</html>
"""
    out_path.write_text(page)
    print(f"{out_path.name}: {len(toc)} sections, {len(page) // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
