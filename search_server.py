"""Friend-facing search page for the Denver apartment hunt.

A tiny stdlib HTTP server: a form for budget, beds, baths, and home type,
which kicks off one build_html_digest.py run at a time as a subprocess and
serves the finished digest. The Cherry Creek ring is fixed; only criteria
that filter within it are adjustable.

Run:
  python3 search_server.py            # http://localhost:8787

Expose to friends (stable public URL, works while this Mac is awake):
  tailscale funnel 8787
"""

from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
LOG_PATH = WEB / "run.log"
RESULT_PATH = WEB / "result.html"
LAST_PATH = WEB / "last.json"

# Host/port from env so a cloud host (Render sets $PORT) can bind. Local default
# stays 127.0.0.1:8787 — cloud sets HOST=0.0.0.0 to accept outside traffic.
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8787"))

# One run at a time. ponytail: global single-run lock; per-user queueing only
# if friends actually collide.
_start_lock = threading.Lock()
_run: dict = {}  # proc, criteria (str), started (float)

# Input clamps. The page is public behind an unguessable URL, so never trust
# the form values.
BUDGET_MIN, BUDGET_MAX = 500, 25000
BEDS_MIN, BEDS_MAX = 1, 6
BATHS_MIN, BATHS_MAX = 1, 4


def _clamp(raw, lo, hi, default):
    try:
        return max(lo, min(hi, int(raw)))
    except (TypeError, ValueError):
        return default


def _criteria_label(budget_lo, budget, beds_lo, beds_hi, baths, any_type, garage, quick):
    beds = f"{beds_lo}BR" if beds_lo == beds_hi else f"{beds_lo} to {beds_hi}BR"
    kind = "any home type" if any_type else "houses"
    garage_bit = ", garage" if garage else ""
    depth = ", quick sweep" if quick else ""
    return f"{beds}, {baths}+ bath, ${budget_lo:,} to ${budget:,}/mo, {kind}{garage_bit}{depth}"


def _running() -> bool:
    proc = _run.get("proc")
    return proc is not None and proc.poll() is None


def _start_run(budget_lo, budget, beds_lo, beds_hi, baths, any_type, garage, quick) -> None:
    WEB.mkdir(exist_ok=True)
    cmd = [
        sys.executable, "-u", str(ROOT / "build_html_digest.py"),
        "--city", "denver",
        "--min-price", str(budget_lo),
        "--max-price", str(budget),
        "--min-beds", str(beds_lo),
        "--max-beds", str(beds_hi),
        "--min-baths", str(baths),
        "--out", str(RESULT_PATH),
    ]
    if any_type:
        cmd.append("--any-type")
    if garage:
        cmd.append("--require-garage")
    if quick:
        cmd.append("--no-enrich")
    log = open(LOG_PATH, "w")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    _run.update(
        proc=proc,
        criteria=_criteria_label(budget_lo, budget, beds_lo, beds_hi, baths, any_type, garage, quick),
        started=time.time(),
        quick=quick,
    )


# --------------------------------------------------------------------------- #
# Pages. Editorial Cream, same tokens as the digest.
# --------------------------------------------------------------------------- #

_STYLE = """
:root {
  --bg: #f4f0e6; --card: #fbf9f3; --ink: #211e1a; --muted: #8a8276;
  --line: #e3dbcb; --accent: #9a7b3f;
  --serif: "Cormorant Garamond", Georgia, "Times New Roman", serif;
  --sans: "Jost", -apple-system, system-ui, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
body { font-family: var(--sans); background: var(--bg); color: var(--ink);
  margin: 0; padding: 64px 24px 96px; line-height: 1.5;
  -webkit-font-smoothing: antialiased; }
.wrap { max-width: 640px; margin: 0 auto; }
.eyebrow { font-size: 11px; letter-spacing: 0.22em; text-transform: uppercase;
  color: var(--muted); margin: 0 0 10px; }
h1 { font-family: var(--serif); font-weight: 600; font-size: 42px;
  line-height: 1.05; margin: 0 0 16px; }
.rule { height: 1px; background: var(--line); margin: 28px 0; }
form { display: grid; gap: 22px; }
label { display: block; font-size: 11px; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--muted); margin-bottom: 8px; }
input[type=number] { font-family: var(--mono); font-size: 16px;
  color: var(--ink); background: var(--card); border: 1px solid var(--line);
  padding: 10px 12px; width: 130px; }
.row { display: flex; gap: 28px; flex-wrap: wrap; }
.radio { display: flex; gap: 18px; align-items: center; font-size: 14px; }
.radio input { accent-color: var(--ink); }
button { font-family: var(--sans); font-size: 12px; font-weight: 500;
  letter-spacing: 0.1em; text-transform: uppercase; color: var(--bg);
  background: var(--ink); border: 1px solid var(--ink); padding: 12px 28px;
  cursor: pointer; width: fit-content; }
button:hover { background: var(--accent); border-color: var(--accent); }
button.quiet { background: transparent; color: var(--ink); }
button.quiet:hover { color: var(--accent); border-color: var(--accent); }
a { color: var(--accent); }
.fig { font-family: var(--mono); color: var(--ink); }
.note { font-size: 13.5px; color: var(--muted); }
.status-line { font-family: var(--serif); font-size: 22px; margin: 0 0 6px; }
pre { font-family: var(--mono); font-size: 11.5px; line-height: 1.6;
  color: var(--muted); background: var(--card); border: 1px solid var(--line);
  padding: 16px 18px; overflow-x: auto; white-space: pre-wrap; }
"""

_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:'
    'wght@500;600&family=Jost:wght@400;500;600&display=swap" rel="stylesheet">'
)


def _page(title: str, body: str, refresh: int = 0) -> str:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{meta}<title>{html.escape(title)}</title>
{_FONTS}<style>{_STYLE}</style>
</head>
<body><div class="wrap">{body}</div></body>
</html>"""


def _form_page() -> str:
    last = ""
    if RESULT_PATH.exists() and LAST_PATH.exists():
        try:
            meta = json.loads(LAST_PATH.read_text())
            last = (
                '<div class="rule"></div>'
                f'<p class="note">Last search, {html.escape(meta["when"])}: '
                f'{html.escape(meta["criteria"])}. '
                '<a href="/result">Open it</a></p>'
            )
        except (json.JSONDecodeError, KeyError):
            pass
    running = ""
    if _running():
        running = (
            '<div class="rule"></div>'
            '<p class="note">A search is running right now. '
            '<a href="/status">Watch it</a></p>'
        )
    return _page("Denver rental search", f"""
<p class="eyebrow">Cherry Creek and the 10 minute ring</p>
<h1>Denver rental search</h1>
<p class="note">Sweeps Craigslist, Zillow, the big portals, institutional
landlords, by owner sites, and Reddit.</p>
<div class="rule"></div>
<form method="post" action="/search">
  <div class="row">
    <div><label>Price, min monthly</label>
      <input type="number" name="budget_lo" value="1000" min="{BUDGET_MIN}" max="{BUDGET_MAX}" step="100"></div>
    <div><label>Price, max monthly</label>
      <input type="number" name="budget" value="5000" min="{BUDGET_MIN}" max="{BUDGET_MAX}" step="100"></div>
    <div><label>Beds, min</label>
      <input type="number" name="beds_lo" value="3" min="{BEDS_MIN}" max="{BEDS_MAX}"></div>
    <div><label>Beds, max</label>
      <input type="number" name="beds_hi" value="3" min="{BEDS_MIN}" max="{BEDS_MAX}"></div>
    <div><label>Baths, min</label>
      <input type="number" name="baths" value="2" min="{BATHS_MIN}" max="{BATHS_MAX}"></div>
  </div>
  <div>
    <label>Home type</label>
    <div class="radio">
      <label style="all:unset;font-size:14px;display:flex;gap:7px;align-items:center;">
        <input type="radio" name="kind" value="house" checked> Houses only</label>
      <label style="all:unset;font-size:14px;display:flex;gap:7px;align-items:center;">
        <input type="radio" name="kind" value="any"> Any home type</label>
    </div>
  </div>
  <div>
    <label>Garage</label>
    <div class="radio">
      <label style="all:unset;font-size:14px;display:flex;gap:7px;align-items:center;">
        <input type="radio" name="garage" value="any" checked> Doesn't matter</label>
      <label style="all:unset;font-size:14px;display:flex;gap:7px;align-items:center;">
        <input type="radio" name="garage" value="yes"> Must have a garage</label>
    </div>
  </div>
  <div>
    <label>Depth</label>
    <div class="radio">
      <label style="all:unset;font-size:14px;display:flex;gap:7px;align-items:center;">
        <input type="radio" name="depth" value="full" checked> Full, verifies garage and baths on each listing, up to an hour</label>
      <label style="all:unset;font-size:14px;display:flex;gap:7px;align-items:center;">
        <input type="radio" name="depth" value="quick"> Quick sweep, about 10 minutes</label>
    </div>
  </div>
  <button type="submit">Search</button>
</form>
{running}{last}""")


def _status_page() -> str:
    proc = _run.get("proc")
    if proc is None:
        return _page("Search status", """
<p class="eyebrow">Search status</p>
<h1>No search running</h1>
<p class="note"><a href="/">Start one</a></p>""")

    criteria = html.escape(_run.get("criteria", ""))
    elapsed = int(time.time() - _run.get("started", time.time()))
    mins, secs = divmod(elapsed, 60)
    tail = ""
    if LOG_PATH.exists():
        lines = LOG_PATH.read_text(errors="replace").splitlines()
        tail = html.escape("\n".join(lines[-25:]))

    rc = proc.poll()
    if rc is None:
        return _page("Searching", f"""
<p class="eyebrow">Search running</p>
<h1>Sweeping the sources</h1>
<p class="note">{criteria}</p>
<p class="note">Elapsed <span class="fig">{mins}m {secs:02d}s</span>. A quick sweep
takes about 10 minutes, a full run can take up to an hour. This page refreshes itself.</p>
<pre>{tail or "Starting up."}</pre>
<form method="post" action="/cancel"><button class="quiet" type="submit">Cancel run</button></form>""", refresh=6)

    if rc == 0 and RESULT_PATH.exists():
        LAST_PATH.write_text(json.dumps({
            "criteria": _run.get("criteria", ""),
            "when": time.strftime("%Y-%m-%d %H:%M"),
        }))
        _run.clear()
        return _page("Search done", """
<meta http-equiv="refresh" content="0;url=/result">
<p class="note">Done. <a href="/result">See the results</a></p>""")

    _run.clear()
    return _page("Search failed", f"""
<p class="eyebrow">Search status</p>
<h1>That run failed</h1>
<p class="note">{criteria}</p>
<pre>{tail or "No log output."}</pre>
<p class="note"><a href="/">Try again</a></p>""")


def _result_page() -> bytes:
    if not RESULT_PATH.exists():
        return _page("No results yet", """
<p class="eyebrow">Results</p>
<h1>No results yet</h1>
<p class="note"><a href="/">Run a search</a></p>""").encode()
    doc = RESULT_PATH.read_text()
    # ponytail: inject a back link into the finished digest instead of
    # re-rendering it.
    bar = ('<div style="max-width:800px;margin:0 auto 28px;">'
           '<a href="/" style="font-family:Jost,sans-serif;font-size:12px;'
           'letter-spacing:0.1em;text-transform:uppercase;color:#9a7b3f;'
           'text-decoration:none;">New search</a></div>')
    return doc.replace("<body>", "<body>" + bar, 1).encode()


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, code: int = 200, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, where: str):
        self.send_response(303)
        self.send_header("Location", where)
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._send(_form_page().encode())
        elif path == "/status":
            self._send(_status_page().encode())
        elif path == "/result":
            self._send(_result_page())
        else:
            self._send(b"not found", code=404, ctype="text/plain")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode(errors="replace"))

        def field(name, default=""):
            return (form.get(name) or [default])[0]

        if path == "/search":
            budget_lo = _clamp(field("budget_lo"), BUDGET_MIN, BUDGET_MAX, 1000)
            budget = max(budget_lo, _clamp(field("budget"), BUDGET_MIN, BUDGET_MAX, 5000))
            beds_lo = _clamp(field("beds_lo"), BEDS_MIN, BEDS_MAX, 3)
            beds_hi = max(beds_lo, _clamp(field("beds_hi"), BEDS_MIN, BEDS_MAX, 3))
            baths = _clamp(field("baths"), BATHS_MIN, BATHS_MAX, 2)
            any_type = field("kind") == "any"
            garage = field("garage") == "yes"
            quick = field("depth") == "quick"
            with _start_lock:
                if not _running():
                    _start_run(budget_lo, budget, beds_lo, beds_hi, baths, any_type, garage, quick)
            self._redirect("/status")
        elif path == "/cancel":
            proc = _run.get("proc")
            if proc is not None and proc.poll() is None:
                proc.kill()
            _run.clear()
            self._redirect("/")
        else:
            self._send(b"not found", code=404, ctype="text/plain")

    def log_message(self, fmt, *fmt_args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % fmt_args))


def main() -> int:
    WEB.mkdir(exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Serving on http://{HOST}:{PORT}")
    if HOST == "127.0.0.1":
        print(f"Expose with: tailscale funnel {PORT}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
