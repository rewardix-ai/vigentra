#!/usr/bin/env python3
"""Serve the keynote on this machine: python3 tools/serve.py [port]   (default 8765)

Opening index.html straight from disk also works. Serve it when you want the presenter window
(S), which talks to the stage over a channel browsers only allow between pages of one origin.
Unlike `python3 -m http.server` this answers byte ranges, so videos can start mid-clip, and it
disables caching so an edited file shows on reload.
"""
from __future__ import annotations

import functools
import http.server
import os
import re
import sys
from pathlib import Path


class _Window:
    """A file object that stops after `length` bytes."""

    def __init__(self, handle, length: int) -> None:
        self.handle, self.left = handle, length

    def read(self, size: int = -1) -> bytes:
        if self.left <= 0:
            return b""
        size = self.left if size < 0 else min(size, self.left)
        data = self.handle.read(size)
        self.left -= len(data)
        return data

    def close(self) -> None:
        self.handle.close()


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self):
        path = self.translate_path(self.path)
        match = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        if not match or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        start = int(match.group(1)) if match.group(1) else max(0, size - int(match.group(2) or 0))
        end = min(int(match.group(2)), size - 1) if match.group(1) and match.group(2) else size - 1
        if start >= size:
            self.send_error(416, "Range not satisfiable")
            return None
        handle = open(path, "rb")
        handle.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        return _Window(handle, end - start + 1)

    def log_message(self, fmt, *args) -> None:  # quiet: one line per error only
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(fmt, *args)


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    root = Path(__file__).resolve().parents[1]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Handler, directory=str(root)))
    print(f"Keynote at http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
