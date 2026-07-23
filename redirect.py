import http.server, os

TARGET = "https://vivid-dashboard.onrender.com"

class RedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(301)
        self.send_header("Location", TARGET + self.path)
        self.end_headers()
    def log_message(self, *a):
        pass

port = int(os.environ.get("PORT", 8000))
print(f"Redirecting all traffic to {TARGET} on port {port}")
http.server.HTTPServer(("", port), RedirectHandler).serve_forever()
