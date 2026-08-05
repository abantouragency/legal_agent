"""
web_health.py
=============
A tiny HTTP server so Render (or any PaaS) keeps the bot alive via health
checks, AND it terminates Telegram **webhook** updates.

In webhook mode, Telegram POSTs updates to https://<your-app>/webhook and this
handler feeds them into the python-telegram-bot Application via update_queue.
This avoids long-polling entirely, which is what caused the 409 Conflict on
Render (multiple workers polling the same token).

Run in a background thread from main(), which also calls set_application(app).
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
                    try:
                        update = Update.de_json(data, _APP.bot)
                    except Exception:
                        update = Update(data)
                    _APP.update_queue.put_nowait(update)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                    return
            except Exception as e:
                print(f"[webhook POST error] {e}")
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):  # silence noisy logs
        pass


def start_health_server(port=None):
    port = int(port if port is not None else os.environ.get("PORT", "8080"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    print(f"🩺 health+webhook server listening on :{port}")
