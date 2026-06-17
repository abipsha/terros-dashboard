"""
Vivid Terros API Server
Run: python server.py
Endpoints:
  GET  /                              → health check
  GET  /api/test                      → verify Terros API connection
  GET  /api/workflow                  → workflow info + action map
  GET  /api/users                     → all reps
  GET  /api/weekly?start=YYYY-MM-DD&end=YYYY-MM-DD  → full weekly report
  GET  /api/kpi?start=YYYY-MM-DD&end=YYYY-MM-DD     → raw KPI data (debug)
  POST /api/raw/<path>                → raw proxy to Terros (for browser dashboard)

Python standard library only — no pip installs needed.
"""
import http.server
import json
import urllib.parse
import sys
import os
from datetime import datetime, timezone, timedelta

# Put the api/ folder on the path so we can import terros.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terros

PORT      = int(os.environ.get("PORT", 8000))  # Render sets PORT automatically
ROOT_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # terros-dashboard/
HTML_FILE = os.path.join(ROOT_DIR, "vivid-terros-dashboard.html")

# Terros uses Central Daylight Time (CDT = UTC-5) for all date boundaries.
# Confirmed: stats URL start=1780290000000 = Jun 1, 2026 00:00 CDT exactly.
TERROS_TZ = timezone(timedelta(hours=-5))


def date_to_ms(date_str: str, end_of_day=False) -> int:
    """Convert YYYY-MM-DD to milliseconds in Terros's timezone (CDT = UTC-5)."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TERROS_TZ)
    if end_of_day:
        dt = dt + timedelta(days=1) - timedelta(milliseconds=1)
    return int(dt.timestamp() * 1000)


def current_week_range():
    """Return (start_ms, end_ms) for Mon–Sun of the current week in CDT."""
    today = datetime.now(tz=TERROS_TZ)
    monday = (today - timedelta(days=today.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59, milliseconds=999)
    return int(monday.timestamp() * 1000), int(sunday.timestamp() * 1000)


class Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"
        qs     = urllib.parse.parse_qs(parsed.query)

        try:
            if path == "/health" or path == "/api" or path == "/api/":
                self._json({"status": "ok", "message": "Vivid Terros API is running"})

            elif path == "/api/debug-activity":
                # Fetch ONE page of raw activities and return the first record
                # so we can inspect the actual field names the API uses
                wf = terros.get_workflow()
                body = {
                    "workflowId": wf["workflowId"],
                    "limit": 5,
                    "offset": 0,
                }
                raw = terros._post("/activity/list", body)
                actions = raw.get("actions", raw.get("activities", raw.get("data", [])))
                sample = actions[:3] if actions else []
                self._json({
                    "topLevelKeys":  list(raw.keys()),
                    "recordCount":   len(actions),
                    "sampleRecords": sample,
                    "firstKeys":     list(sample[0].keys()) if sample else [],
                })

            elif path == "/api/test":
                wf = terros.get_workflow()
                self._json({
                    "status":     "ok",
                    "workflowId": wf.get("workflowId"),
                    "name":       wf.get("name"),
                    "actions":    len(wf.get("actions", [])),
                })

            elif path == "/api/workflow":
                wf = terros.get_workflow()
                am = terros.get_action_map()
                self._json({"workflow": wf, "actionMap": am})

            elif path == "/api/users":
                users = terros.get_users()
                self._json({"count": len(users), "users": users})

            elif path == "/api/weekly":
                start_s = (qs.get("start") or [None])[0]
                end_s   = (qs.get("end")   or [None])[0]
                force   = (qs.get("force")  or [None])[0] == "1"
                if start_s and end_s:
                    start_ms = date_to_ms(start_s)
                    end_ms   = date_to_ms(end_s, end_of_day=True)
                else:
                    start_ms, end_ms = current_week_range()
                report = terros.build_weekly_report(start_ms, end_ms, force=force)
                self._json(report)

            elif path == "/api/kpi":
                start_s = (qs.get("start") or [None])[0]
                end_s   = (qs.get("end")   or [None])[0]
                if start_s and end_s:
                    start_ms = date_to_ms(start_s)
                    end_ms   = date_to_ms(end_s, end_of_day=True)
                else:
                    start_ms, end_ms = current_week_range()
                data = terros.get_kpi_report(start_ms, end_ms)
                self._json(data)

            elif path == "/" or path == "" or path == "/dashboard":
                # Serve the HTML dashboard
                if os.path.exists(HTML_FILE):
                    with open(HTML_FILE, "rb") as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    self._json({"error": "Dashboard HTML not found", "path": HTML_FILE}, status=404)

            else:
                self._json({"error": "Not found"}, status=404)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json({"error": str(e)}, status=500)

    def do_POST(self):
        """Raw proxy: POST /api/raw/<terros-path> → https://api.terros.com/<terros-path>"""
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        if path.startswith("/api/raw/"):
            terros_path = "/" + path[len("/api/raw/"):]
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)

            import urllib.request, urllib.error
            req = urllib.request.Request(
                terros.API_BASE + terros_path,
                data=body, method="POST",
                headers={
                    "Content-Type":  "application/json",
                    "Authorization": f"ApiKey {terros.API_KEY}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    self._raw(r.read(), 200)
            except urllib.error.HTTPError as e:
                self._raw(e.read(), e.code)
        else:
            self._json({"error": "Not found"}, status=404)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _json(self, data: dict, status: int = 200):
        payload = json.dumps(data, indent=2, default=str).encode()
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _raw(self, payload: bytes, status: int):
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def log_message(self, fmt, *args):
        print(f"  {fmt % args}")


if __name__ == "__main__":
    print()
    print("  Vivid Terros API Server")
    print(f"  Dashboard → http://localhost:{PORT}/")
    print()
    print("  API Endpoints:")
    print(f"    GET  http://localhost:{PORT}/api/test")
    print(f"    GET  http://localhost:{PORT}/api/weekly?start=2026-06-09&end=2026-06-15")
    print(f"    GET  http://localhost:{PORT}/api/kpi?start=2026-06-09&end=2026-06-15")
    print()
    print("  Press Ctrl+C to stop.")
    print()

    try:
        import webbrowser
        webbrowser.open(f"http://localhost:{PORT}/")
    except Exception:
        pass


    try:
        bind_host = "0.0.0.0"  # required for cloud platforms (Render, Railway, etc.)
        with http.server.ThreadingHTTPServer((bind_host, PORT), Handler) as s:
            s.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        sys.exit(0)
    except OSError as e:
        print(f"\n  ERROR: {e}")
        print(f"  (Port {PORT} already in use?)")
        input("\n  Press Enter to exit...")
        sys.exit(1)
