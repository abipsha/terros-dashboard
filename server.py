"""
Vivid Terros Dashboard — Local Proxy Server
Run with: python server.py
Then open: http://localhost:3000/vivid-terros-dashboard.html
"""
import http.server
import urllib.request
import urllib.error
import os
import sys

API_KEY  = 'atQjJCg13du0c4aAeU4hc'
API_BASE = 'https://api.terros.com'
PORT     = 3000
DIR      = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    # ── Proxy POST /api/* → https://api.terros.com/* ──────────────
    def do_POST(self):
        if self.path.startswith('/api/'):
            target = API_BASE + self.path[4:]          # strip /api prefix
            length = int(self.headers.get('Content-Length', 0))
            body   = self.rfile.read(length)

            req = urllib.request.Request(
                target, data=body, method='POST',
                headers={
                    'Content-Type':  'application/json',
                    'Authorization': f'ApiKey {API_KEY}',
                }
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    payload = r.read()
                    self.send_response(200)
                    self._cors()
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(payload)
            except urllib.error.HTTPError as e:
                self.send_response(e.code)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(e.read())
        else:
            self.send_error(405)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.end_headers()

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()}  {fmt % args}")

if __name__ == '__main__':
    url = f'http://localhost:{PORT}/vivid-terros-dashboard.html'
    print(f'\n  ✓  Vivid Terros Dashboard')
    print(f'     {url}')
    print(f'\n     Press Ctrl+C to stop.\n')

    # Try to open the browser automatically
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass

    try:
        with http.server.ThreadingHTTPServer(('localhost', PORT), Handler) as s:
            s.serve_forever()
    except KeyboardInterrupt:
        print('\n  Stopped.')
        sys.exit(0)
    except OSError as e:
        print(f'\n  ERROR: {e}')
        print(f'  (Is port {PORT} already in use? Try closing other instances.)')
        input('\n  Press Enter to exit…')
        sys.exit(1)
