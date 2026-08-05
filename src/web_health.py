"""
web_health.py
=============
A tiny HTTP server so Render (or any PaaS) keeps the bot alive via health
checks, AND it terminates Telegram **webhook** updates.

When running in webhook mode (recommended on Render to avoid the 409 Conflict
that long-polling causes when multiple instances share a token), Telegram posts
updates to https://<your-app>.onrender.com/webhook and this handler feeds them
into the python-telegram-bot Application via update_queue.

Run in a background thread from main().
"""
from __future__ import annotations

import os
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The Application is injected by bot.main() so we can enqueue incoming updates.
_APP = None


def set_application(app):
    global _APP
    _APP = app


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

    def do_POST(self):
        # Telegram webhook endpoint
        if self.path.rstrip("/").endswith("/webhook"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                data = json.loads(body.decode("utf-8"))
                if _APP is not None:
                    from telegram import Update
                    update = Update.de_json(data, _APP.bot)
                    _APP.update_queue.put_nowait(update)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                    return
            except Exception:
                pass
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):  # silence noisy logs
        pass


def start_health_server():
    port = int(os.environ.get("PORT", "8080"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    print(f"🩺 health+webhook server listening on :{port}")
