"""Experimental authenticated mailbox.  No command execution occurs on the VPS.

Never expose this loopback HTTP server directly to the public internet.
The production TLS, OAuth and identity boundary is deliberately not shipped.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

MAX_COMMAND = 4096
MAX_RESULT = 65536
MAX_BODY = 150000
MAX_ACTIVE = 64
MAX_WAIT = 20
COMMAND_TTL = 300
RESULT_TTL = 300
ID_RE = re.compile(r"[a-f0-9]{32}\Z")


@dataclass
class Job:
    command_id: str
    command: str
    created: float
    status: str = "queued"
    result: dict | None = None
    finished: float | None = None


class Mailbox:
    def __init__(self, now=time.monotonic):
        self.now = now
        self.jobs: dict[str, Job] = {}
        self.pending: deque[str] = deque()
        self.cv = threading.Condition()

    def _prune(self):
        moment = self.now()
        for ident, job in list(self.jobs.items()):
            if job.status in {"queued", "leased"} and moment - job.created >= COMMAND_TTL:
                job.status, job.finished = "expired", moment
            if job.finished is not None and moment - job.finished >= RESULT_TTL:
                del self.jobs[ident]

    def submit(self, command: str) -> str:
        if not isinstance(command, str) or not command or len(command.encode("utf-8")) > MAX_COMMAND:
            raise ValueError("invalid-command")
        if any(ord(char) < 32 for char in command):
            raise ValueError("invalid-command")
        with self.cv:
            self._prune()
            if sum(j.status in {"queued", "leased"} for j in self.jobs.values()) >= MAX_ACTIVE:
                raise OverflowError("queue-full")
            ident = secrets.token_hex(16)
            self.jobs[ident] = Job(ident, command, self.now())
            self.pending.append(ident)
            self.cv.notify_all()
            return ident

    def receive(self, wait_seconds: float) -> dict:
        if not 0 <= wait_seconds <= MAX_WAIT:
            raise ValueError("invalid-wait")
        with self.cv:
            deadline = self.now() + wait_seconds
            while True:
                self._prune()
                while self.pending:
                    job = self.jobs.get(self.pending.popleft())
                    if job and job.status == "queued":
                        job.status = "leased"
                        return {"status": "command", "command_id": job.command_id, "command": job.command}
                remain = deadline - self.now()
                if remain <= 0:
                    return {"status": "empty"}
                self.cv.wait(min(remain, 1))

    def reply(self, ident: str, result: dict) -> bool:
        if type(ident) is not str or not ID_RE.fullmatch(ident):
            raise ValueError("invalid-id")
        if type(result) is not dict or set(result) != {"stdout", "stderr", "exit_code"}:
            raise ValueError("invalid-result")
        if type(result["stdout"]) is not str or type(result["stderr"]) is not str:
            raise ValueError("invalid-result")
        if type(result["exit_code"]) is not int or not 0 <= result["exit_code"] <= 255:
            raise ValueError("invalid-result")
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_RESULT:
            raise ValueError("output-too-large")
        with self.cv:
            self._prune()
            job = self.jobs.get(ident)
            if job is None or job.status != "leased":
                return False
            job.result = result
            job.status = "complete"
            job.finished = self.now()
            self.cv.notify_all()
            return True

    def get(self, ident: str) -> dict | None:
        if type(ident) is not str or not ID_RE.fullmatch(ident):
            raise ValueError("invalid-id")
        with self.cv:
            self._prune()
            job = self.jobs.get(ident)
            if job is None:
                return None
            response = {"status": job.status, "command_id": ident}
            if job.result is not None:
                response["result"] = job.result
            return response


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, mailbox: Mailbox, mac_token: str, agent_token: str):
        if (len(mac_token) < 32 or len(agent_token) < 32 or
                compare_digest(mac_token, agent_token)):
            raise ValueError("distinct-tokens-of-at-least-32-characters-required")
        self.mailbox = mailbox
        self.mac_token = mac_token
        self.agent_token = agent_token
        super().__init__(address, RelayHandler)


class RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        # Commands and credentials must never enter access logs.
        pass

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, role: str) -> bool:
        target = self.server.mac_token if role == "mac" else self.server.agent_token
        supplied = self.headers.get("Authorization", "")
        if not compare_digest(supplied, "Bearer " + target):
            self._json(401, {"error": "unauthorized"})
            return False
        return True

    def _body(self) -> dict:
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or not 0 < int(length) <= MAX_BODY:
            raise ValueError("invalid-body-length")
        content = self.rfile.read(int(length))
        if len(content) != int(length):
            raise ValueError("incomplete-body")
        obj = json.loads(content)
        if type(obj) is not dict:
            raise ValueError("invalid-json-object")
        return obj

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            if path == "/v1/commands":
                if not self._authorized("mac"):
                    return
                request = self._body()
                if set(request) != {"command"}:
                    raise ValueError("invalid-command-request")
                ident = self.server.mailbox.submit(request["command"])
                return self._json(201, {"status": "queued", "command_id": ident})
            if path.startswith("/v1/results/"):
                if not self._authorized("agent"):
                    return
                ident = path.removeprefix("/v1/results/")
                if not self.server.mailbox.reply(ident, self._body()):
                    return self._json(409, {"error": "not-leased-or-not-found"})
                return self._json(200, {"status": "accepted"})
            return self._json(404, {"error": "not-found"})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": str(exc)[:80]})
        except OverflowError:
            return self._json(429, {"error": "queue-full"})

    def do_GET(self):
        parts = urlsplit(self.path)
        try:
            if parts.path == "/v1/commands/next":
                if not self._authorized("agent"):
                    return
                params = parse_qs(parts.query)
                if set(params) - {"wait"} or len(params.get("wait", ["0"])) != 1:
                    raise ValueError("invalid-query")
                wait = float(params.get("wait", ["0"])[0])
                return self._json(200, self.server.mailbox.receive(wait))
            if parts.path.startswith("/v1/commands/"):
                if not self._authorized("mac"):
                    return
                job = self.server.mailbox.get(parts.path.removeprefix("/v1/commands/"))
                return self._json(200, job) if job else self._json(404, {"error": "not-found"})
            return self._json(404, {"error": "not-found"})
        except (ValueError, TypeError):
            return self._json(400, {"error": "invalid-request"})


def main():
    parser = argparse.ArgumentParser(description="Experimental loopback-only relay")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("only loopback binding permitted")
    server = RelayServer(
        (args.host, args.port), Mailbox(),
        os.environ.get("ALFRED_RELAY_MAC_TOKEN", ""),
        os.environ.get("ALFRED_RELAY_AGENT_TOKEN", ""),
    )
    try:
        print(f"Experimental relay listening on {args.host}:{args.port}", flush=True)
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
