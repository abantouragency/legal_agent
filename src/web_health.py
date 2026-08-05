"""
web_health.py
=============
A tiny HTTP server so Render (or any PaaS) keeps the bot alive via health
checks, AND it terminates Telegram **webhook** updates.

In webhook mode, Telegram POSTs updates to https://<your-app>/webhook and this
handler feeds them into the python-telegram-bot Application. The Application
object + its asyncio loop are injected by bot.main() via set_application(), so
each update is processed on the app's own loop (thread-safe). This avoids long
polling entirely, which is what caused the 409 Conflict on Render.

Run in a background thread from main().
"""
from __future__ import annotations

import os
import json
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Injected by bot.main()
_APP = None
_LOOP = None


def set_application(app, loop=None):
    global _APP, _LOOP
    _APP = app
    _LOOP = loop


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
                    # Schedule processing on the app's event loop (thread-safe).
                    if _LOOP is not None:
                        asyncio.run_coroutine_threadsafe(
                            _APP.process_update(update), _LOOP
                        )
                    else:
                        # Fallback: enqueue and let the app drain it.
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
