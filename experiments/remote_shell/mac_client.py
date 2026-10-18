"""macOS terminal client.  The client does NOT execute shell commands locally."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


def verify_relay_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("relay-url-cannot-contain-credentials-query-or-fragment")
    if parts.path not in {"", "/"}:
        raise ValueError("relay-url-must-be-origin")
    if (parts.scheme != "https" and not
            (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"})):
        raise ValueError("relay-must-use-https-outside-loopback")
    if not parts.hostname:
        raise ValueError("invalid-relay-url")
    return url.rstrip("/")


def call(url: str, token: str, method: str, path: str, payload: dict | None = None,
         timeout: int = 30) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url + path, data=body, method=method, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"relay returned HTTP {exc.code}") from exc


def run_command(url: str, token: str, command: str, *, wait_timeout: float = 320) -> dict:
    queued = call(url, token, "POST", "/v1/commands", {"command": command})
    ident = queued["command_id"]
    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        job = call(url, token, "GET", "/v1/commands/" + quote(ident))
        if job["status"] == "complete":
            return job["result"]
        if job["status"] == "expired":
            raise RuntimeError("ChatGPT did not complete command before expiry")
        time.sleep(0.25)
    raise TimeoutError("no reply from ChatGPT session")


def main():
    parser = argparse.ArgumentParser(description="Alfred remote ChatGPT sandbox shell client")
    parser.add_argument("--relay", default=os.environ.get("ALFRED_RELAY_URL", ""))
    parser.add_argument("--once", help="one command only")
    args = parser.parse_args()
    url = verify_relay_url(args.relay)
    token = os.environ.get("ALFRED_RELAY_MAC_TOKEN", "")
    if len(token) < 32:
        parser.error("ALFRED_RELAY_MAC_TOKEN must be set with at least 32 characters")

    def dispatch(command):
        response = run_command(url, token, command)
        if response["stdout"]:
            print(response["stdout"], end="" if response["stdout"].endswith("\n") else "\n")
        if response["stderr"]:
            print(response["stderr"], file=sys.stderr,
                  end="" if response["stderr"].endswith("\n") else "\n")
        if response["exit_code"]:
            print(f"[exit {response['exit_code']}]")
        return response["exit_code"]

    if args.once is not None:
        raise SystemExit(dispatch(args.once))
    while True:
        try:
            command = input("alfred$ ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if command in {":exit", ":quit"}:
            return
        if not command:
            continue
        try:
            dispatch(command)
        except (RuntimeError, TimeoutError, OSError) as exc:
            print(f"[connection error: {exc}]")


if __name__ == "__main__":
    main()
