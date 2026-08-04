"""
web_health.py
=============
A tiny HTTP server so Render (or any PaaS) keeps the bot alive via health
checks. The bot itself uses long-polling; this server only answers /healthz on
$PORT so the platform does not kill the process for being "unhealthy".

Run in a background thread from main().
"""
from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/healthz", "/ping"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("OK bot alive".encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # silence noisy logs
        pass


def start_health_server():
    port = int(os.environ.get("PORT", "8080"))
    srv = HTTPServer(("0.0.0.0", port), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    print(f"🩺 health server listening on :{port}")
