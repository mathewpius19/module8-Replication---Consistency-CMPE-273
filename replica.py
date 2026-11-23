#!/usr/bin/env python3
import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class State:
    def __init__(self, role, stock, peer_url, delay):
        self.role = role
        self.stock = stock
        self.peer_url = peer_url.rstrip("/") if peer_url else None
        self.delay = delay
        self.paused = False
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            return self.stock, self.paused

    def replicate(self, value):
        if not self.peer_url:
            return
        def send():
            time.sleep(self.delay)
            with self.lock:
                if self.paused:
                    print(f"[{self.role}] replication paused; value={value} is waiting", flush=True)
                    return
            payload = json.dumps({"stock": value}).encode()
            request = urllib.request.Request(
                f"{self.peer_url}/replicate", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    response.read()
                print(f"[{self.role}] replicated stock={value} to secondary", flush=True)
            except (urllib.error.URLError, TimeoutError) as exc:
                print(f"[{self.role}] replication failed: {exc}", flush=True)
        threading.Thread(target=send, daemon=True).start()


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def send_json(self, status, body):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path == "/stock":
                stock, paused = state.snapshot()
                self.send_json(200, {"role": state.role, "stock": stock, "replication_paused": paused})
                return
            if self.path == "/health":
                self.send_json(200, {"status": "ok", "role": state.role})
                return
            self.send_json(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self.send_json(400, {"error": "invalid JSON"})
                return

            if self.path == "/update" and state.role == "primary":
                if not isinstance(body.get("stock"), int):
                    self.send_json(400, {"error": "stock must be an integer"})
                    return
                with state.lock:
                    state.stock = body["stock"]
                    paused = state.paused
                print(f"[primary] updated stock={body['stock']} paused={paused}", flush=True)
                if not paused:
                    state.replicate(body["stock"])
                self.send_json(200, {"role": "primary", "stock": body["stock"], "replication_paused": paused})
                return

            if self.path == "/pause" and state.role == "primary":
                with state.lock:
                    state.paused = True
                print("[primary] replication paused", flush=True)
                self.send_json(200, {"replication_paused": True})
                return

            if self.path == "/resume" and state.role == "primary":
                with state.lock:
                    state.paused = False
                    value = state.stock
                print(f"[primary] replication resumed; sending stock={value}", flush=True)
                state.replicate(value)
                self.send_json(200, {"replication_paused": False, "stock_sent": value})
                return

            if self.path == "/replicate" and state.role == "secondary":
                if not isinstance(body.get("stock"), int):
                    self.send_json(400, {"error": "stock must be an integer"})
                    return
                with state.lock:
                    state.stock = body["stock"]
                print(f"[secondary] applied replicated stock={body['stock']}", flush=True)
                self.send_json(200, {"role": "secondary", "stock": body["stock"]})
                return

            self.send_json(404, {"error": "not found for this replica"})

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Primary/secondary inventory replica")
    parser.add_argument("--role", choices=("primary", "secondary"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--stock", type=int, default=10)
    parser.add_argument("--peer-url")
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()
    state = State(args.role, args.stock, args.peer_url, args.delay)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state))
    print(f"[{args.role}] listening on http://127.0.0.1:{args.port} stock={args.stock}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n[{args.role}] stopped", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
