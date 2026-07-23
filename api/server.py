"""
Vivid API Server
Run: python server.py
Endpoints:
  GET  /                              → health check
  GET  /api/test                      → verify Terros API connection
  GET  /api/workflow                  → workflow info + action map
  GET  /api/users                     → all reps
  GET  /api/weekly?start=YYYY-MM-DD&end=YYYY-MM-DD       → Terros weekly report
  GET  /api/kpi?start=YYYY-MM-DD&end=YYYY-MM-DD          → raw KPI data (debug)
  GET  /api/odoo/report?start=YYYY-MM-DD&end=YYYY-MM-DD  → Odoo CRM revenue report
  POST /api/raw/<path>                → raw proxy to Terros (for browser dashboard)

Python standard library only — no pip installs needed.
"""
import http.server
import json
import urllib.parse
import urllib.request
import urllib.error
import sys
import os
import hmac
import hashlib
import secrets
import base64
import time
from datetime import datetime, timezone, timedelta

# Put the api/ folder on the path so we can import terros.py and odoo.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terros
import odoo

PORT          = int(os.environ.get("PORT", 8000))  # Render sets PORT automatically
ROOT_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # terros-dashboard/
HTML_FILE     = os.path.join(ROOT_DIR, "index.html")
CRM_HTML_FILE = os.path.join(ROOT_DIR, "vivid-crm-dashboard.html")
LOGIN_FILE    = os.path.join(ROOT_DIR, "login.html")

# ── Google OAuth ──────────────────────────────────────────────
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
# SESSION_SECRET: generate a stable random key if not set (restarts will invalidate sessions)
SESSION_SECRET       = os.environ.get("SESSION_SECRET", secrets.token_hex(32)).encode()
ALLOWED_DOMAIN       = "vividwindows.com"
SESSION_HOURS        = 8
AUTH_ENABLED         = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

# In-memory CSRF state store  {state_token: expire_timestamp}
_oauth_states: dict = {}

def _redirect_uri(host: str) -> str:
    if "localhost" in host or "127.0.0.1" in host:
        return f"http://{host}/oauth/callback"
    return "https://vivid-dashboard.onrender.com/oauth/callback"

def _make_session_cookie(email: str, name: str) -> str:
    exp     = int(time.time()) + SESSION_HOURS * 3600
    payload = base64.urlsafe_b64encode(
        json.dumps({"e": email, "n": name, "x": exp}).encode()
    ).decode().rstrip("=")
    sig = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"

def _verify_session_cookie(raw: str):
    """Return session dict or None."""
    try:
        payload, sig = raw.rsplit(".", 1)
        expected = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        padding = 4 - len(payload) % 4
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * padding).decode())
        if data.get("x", 0) < time.time():
            return None
        return data
    except Exception:
        return None

def _get_session(headers) -> dict | None:
    for part in headers.get("Cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == "vw_session":
            return _verify_session_cookie(v)
    return None

def _clean_states():
    now = time.time()
    for s in [k for k, v in _oauth_states.items() if v < now]:
        del _oauth_states[s]

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

    # ── Auth helpers ─────────────────────────────────────────────

    def _redirect(self, location: str, clear_session: bool = False):
        self.send_response(302)
        self._cors_headers()
        if clear_session:
            self.send_header("Set-Cookie", "vw_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        self.send_header("Location", location)
        self.end_headers()

    def _require_auth(self) -> dict | None:
        """Return session dict if authenticated. If not, redirect to /login and return None."""
        if not AUTH_ENABLED:
            return {"e": "dev@vividwindows.com", "n": "Dev Mode"}
        session = _get_session(self.headers)
        if session:
            return session
        self._redirect("/login")
        return None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"
        qs     = urllib.parse.parse_qs(parsed.query)

        try:
            # ── Auth routes (public) ──────────────────────────────
            if path == "/login":
                if not AUTH_ENABLED:
                    self._redirect("/crm"); return
                # Already logged in?
                if _get_session(self.headers):
                    self._redirect("/crm"); return
                # Serve the login page
                if os.path.exists(LOGIN_FILE):
                    with open(LOGIN_FILE, "rb") as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    self._json({"error": "login.html not found"}, status=500)
                return

            if path == "/logout":
                self._redirect("/login", clear_session=True); return

            if path == "/oauth/callback":
                _clean_states()
                code  = (qs.get("code")  or [None])[0]
                state = (qs.get("state") or [None])[0]
                error = (qs.get("error") or [None])[0]

                if error:
                    self._json({"error": f"Google returned: {error}"}, status=403); return
                if not code or not state:
                    self._json({"error": "Missing code or state"}, status=400); return
                if state not in _oauth_states or _oauth_states[state] < time.time():
                    self._json({"error": "Invalid or expired state. Please try again.", "retry": "/login"}, status=403); return
                del _oauth_states[state]

                host = self.headers.get("Host", "localhost:8000")
                ruri = _redirect_uri(host)

                # Exchange code for tokens
                token_data = urllib.parse.urlencode({
                    "code": code, "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": ruri, "grant_type": "authorization_code",
                }).encode()
                try:
                    with urllib.request.urlopen(
                        urllib.request.Request(
                            "https://oauth2.googleapis.com/token",
                            data=token_data,
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                        ), timeout=10
                    ) as r:
                        tokens = json.loads(r.read())
                except urllib.error.HTTPError as e:
                    self._json({"error": "Token exchange failed", "detail": e.read().decode()}, status=502); return

                access_token = tokens.get("access_token")
                if not access_token:
                    self._json({"error": "No access token in response"}, status=502); return

                # Fetch user info
                try:
                    req = urllib.request.Request(
                        "https://www.googleapis.com/oauth2/v2/userinfo",
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    with urllib.request.urlopen(req, timeout=10) as r:
                        user = json.loads(r.read())
                except Exception as e:
                    self._json({"error": f"Userinfo failed: {e}"}, status=502); return

                email = (user.get("email") or "").lower()
                if not email.endswith(f"@{ALLOWED_DOMAIN}"):
                    self._json({
                        "error": f"Access restricted to @{ALLOWED_DOMAIN} accounts.",
                        "email": email,
                    }, status=403); return

                name   = user.get("name", email.split("@")[0])
                cookie = _make_session_cookie(email, name)

                self.send_response(302)
                self._cors_headers()
                self.send_header(
                    "Set-Cookie",
                    f"vw_session={cookie}; Path=/; Max-Age={SESSION_HOURS * 3600}; HttpOnly; SameSite=Lax"
                )
                self.send_header("Location", "/crm")
                self.end_headers()
                return

            # ── Google auth initiation (redirect to Google) ───────
            if path == "/oauth/start":
                if not AUTH_ENABLED:
                    self._redirect("/crm"); return
                host  = self.headers.get("Host", "localhost:8000")
                state = secrets.token_urlsafe(24)
                _oauth_states[state] = time.time() + 600  # 10 min
                params = urllib.parse.urlencode({
                    "client_id":     GOOGLE_CLIENT_ID,
                    "redirect_uri":  _redirect_uri(host),
                    "response_type": "code",
                    "scope":         "openid email profile",
                    "hd":            ALLOWED_DOMAIN,
                    "state":         state,
                    "prompt":        "select_account",
                })
                self._redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")
                return

            # ── Health (always public) ────────────────────────────
            if path == "/health" or path == "/api" or path == "/api/":
                self._json({"status": "ok", "message": "Vivid Terros API is running"})

            # ── All other routes require auth ─────────────────────
            else:
                if not self._require_auth(): return

                if path == "/api/debug-odoo":
                    import urllib.request as _ur
                    try:
                        with _ur.urlopen("https://api.ipify.org?format=json", timeout=5) as r:
                            outbound_ip = json.loads(r.read()).get("ip", "unknown")
                    except Exception as ip_err:
                        outbound_ip = f"error: {ip_err}"
                    import xmlrpc.client as _xrc
                    xmlrpc_result = None; xmlrpc_error = None
                    try:
                        common = _xrc.ServerProxy(f"{odoo.ODOO_URL}/xmlrpc/2/common")
                        uid = common.authenticate(odoo.ODOO_DB, odoo.ODOO_USER, odoo.ODOO_PASS, {})
                        xmlrpc_result = {"uid": uid, "success": bool(uid)}
                    except Exception as e:
                        xmlrpc_error = str(e)
                    pw = odoo.ODOO_PASS
                    pw_masked = pw[:4] + ("*" * (len(pw) - 4)) if len(pw) > 4 else "****"
                    self._json({
                        "outbound_ip": outbound_ip, "odoo_url": odoo.ODOO_URL,
                        "odoo_db": odoo.ODOO_DB, "odoo_user": odoo.ODOO_USER,
                        "password_len": len(pw), "password_preview": pw_masked,
                        "xmlrpc_result": xmlrpc_result, "xmlrpc_error": xmlrpc_error,
                    })

                elif path == "/api/debug-activity":
                    wf = terros.get_workflow()
                    raw = terros._post("/activity/list", {"workflowId": wf["workflowId"], "limit": 5, "offset": 0})
                    actions = raw.get("actions", raw.get("activities", raw.get("data", [])))
                    sample = actions[:3] if actions else []
                    self._json({
                        "topLevelKeys": list(raw.keys()), "recordCount": len(actions),
                        "sampleRecords": sample, "firstKeys": list(sample[0].keys()) if sample else [],
                    })

                elif path == "/api/test":
                    wf = terros.get_workflow()
                    self._json({"status": "ok", "workflowId": wf.get("workflowId"),
                                "name": wf.get("name"), "actions": len(wf.get("actions", []))})

                elif path == "/api/workflow":
                    wf = terros.get_workflow(); am = terros.get_action_map()
                    self._json({"workflow": wf, "actionMap": am})

                elif path == "/api/users":
                    users = terros.get_users()
                    self._json({"count": len(users), "users": users})

                elif path == "/api/weekly":
                    start_s = (qs.get("start") or [None])[0]
                    end_s   = (qs.get("end")   or [None])[0]
                    force   = (qs.get("force")  or [None])[0] == "1"
                    if start_s and end_s:
                        start_ms = date_to_ms(start_s); end_ms = date_to_ms(end_s, end_of_day=True)
                    else:
                        start_ms, end_ms = current_week_range()
                    self._json(terros.build_weekly_report(start_ms, end_ms, force=force))

                elif path == "/api/kpi":
                    start_s = (qs.get("start") or [None])[0]
                    end_s   = (qs.get("end")   or [None])[0]
                    if start_s and end_s:
                        start_ms = date_to_ms(start_s); end_ms = date_to_ms(end_s, end_of_day=True)
                    else:
                        start_ms, end_ms = current_week_range()
                    self._json(terros.get_kpi_report(start_ms, end_ms))

                elif path == "/api/odoo/deals":
                    today = datetime.now(tz=timezone.utc)
                    start_s = (qs.get("start") or [today.strftime("%Y-%m-01")])[0]
                    end_s   = (qs.get("end")   or [today.strftime("%Y-%m-%d")])[0]
                    force   = (qs.get("force")  or [None])[0] == "1"
                    self._json(odoo.get_deals_list(start_s, end_s, force=force))

                elif path == "/api/odoo/report":
                    today = datetime.now(tz=timezone.utc)
                    start_s = (qs.get("start") or [today.strftime("%Y-%m-01")])[0]
                    end_s   = (qs.get("end")   or [today.strftime("%Y-%m-%d")])[0]
                    force   = (qs.get("force")  or [None])[0] == "1"
                    self._json(odoo.build_report(start_s, end_s, force=force))

                elif path == "/crm":
                    if os.path.exists(CRM_HTML_FILE):
                        with open(CRM_HTML_FILE, "rb") as f: content = f.read()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(content)))
                        self.end_headers(); self.wfile.write(content)
                    else:
                        self._json({"error": "CRM dashboard HTML not found"}, status=404)

                elif path == "/" or path == "" or path == "/dashboard":
                    self._redirect("/crm")

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

    def do_HEAD(self):
        """HEAD /health — used by uptime monitors (UptimeRobot, etc.)"""
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()

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


def _warm_cache():
    """Pre-warm the Odoo cache on startup, then refresh every 25 minutes."""
    import threading
    def _run():
        from datetime import datetime, timezone
        while True:
            try:
                today = datetime.now(tz=timezone.utc)
                start = today.strftime("%Y-%m-01")
                end   = today.strftime("%Y-%m-%d")
                print(f"  [cache] warming Odoo deals cache ({start} → {end})…")
                odoo.get_deals_list(start, end, force=True)
                print(f"  [cache] warm complete")
            except Exception as e:
                print(f"  [cache] warm failed: {e}")
            import time
            time.sleep(2 * 60)  # refresh every 2 min
    t = threading.Thread(target=_run, daemon=True)
    t.start()


if __name__ == "__main__":
    print()
    print("  Vivid Dashboard Server")
    print(f"  Terros Dashboard  →  http://localhost:{PORT}/")
    print(f"  CRM Dashboard     →  http://localhost:{PORT}/crm")
    print()
    print("  API Endpoints:")
    print(f"    GET  http://localhost:{PORT}/api/weekly?start=2026-06-09&end=2026-06-15")
    print(f"    GET  http://localhost:{PORT}/api/odoo/report?start=2026-07-01&end=2026-07-10")
    print()
    print("  Press Ctrl+C to stop.")
    print()

    _warm_cache()  # start background cache warmer

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
