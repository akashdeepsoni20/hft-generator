import http.server
import os
import socketserver
import webbrowser
from api.generate import handler as ApiHandler

PORT = 5000
PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "public")


class LocalRequestHandler(ApiHandler):
    def do_GET(self):
        if self.path.startswith("/api/"):
            super().do_GET()
        else:
            # Serve public/index.html
            html_file = os.path.join(PUBLIC_DIR, "index.html")
            if os.path.exists(html_file):
                with open(html_file, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_response(404)
                self.end_headers()

    def log_message(self, format, *args):
        pass  # Mute server access logs


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print(f"Local server started at http://localhost:{PORT}", flush=True)
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass
    with ThreadedTCPServer(("", PORT), LocalRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")