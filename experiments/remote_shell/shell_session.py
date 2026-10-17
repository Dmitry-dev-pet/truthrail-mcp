"""Stateful non-interactive Bash executor, run ONLY inside ChatGPT's sandbox.

The relay and the Mac terminal must never import or instantiate this worker.
This is not a PTY: no interactive stdin, terminal UI, or streaming yet.
"""
from __future__ import annotations

import os
import re
import select
import signal
import subprocess
import time
from uuid import uuid4


class ShellSession:
    def __init__(self, cwd: str | None = None, *, timeout: float = 20, max_output: int = 60000):
        self.timeout = timeout
        self.max_output = max_output
        self.buffer = bytearray()
        self.process = subprocess.Popen(
            ["/bin/bash", "--noprofile", "--norc"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=cwd, start_new_session=True, bufsize=0,
        )

    def run(self, command: str) -> dict:
        if (not isinstance(command, str) or not command or
                "\n" in command or "\r" in command or "\x00" in command):
            raise ValueError("expected-single-line-shell-command")
        if self.process.poll() is not None:
            raise RuntimeError("shell-has-exited")
        nonce = uuid4().hex
        marker = f"__ALFRED_END_{nonce}__".encode("ascii")
        pattern = re.compile(re.escape(marker) + rb"([0-9]{1,3})\n")
        script = command + "\n" + f'printf "{marker.decode()}%s\\n" "$?"' + "\n"
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(script.encode("utf-8"))
        self.process.stdin.flush()
        fd = self.process.stdout.fileno()
        deadline = time.monotonic() + self.timeout
        while True:
            match = pattern.search(self.buffer)
            if match:
                output = bytes(self.buffer[:match.start()])
                self.buffer = self.buffer[match.end():]
                if len(output) > self.max_output:
                    self.close()
                    raise OverflowError("shell-output-exceeded-limit")
                return {
                    "stdout": output.decode("utf-8", errors="replace"),
                    "stderr": "", "exit_code": int(match.group(1)),
                }
            if len(self.buffer) > self.max_output + 100:
                self.close()
                raise OverflowError("shell-output-exceeded-limit")
            remain = deadline - time.monotonic()
            if remain <= 0:
                self.close()
                raise TimeoutError("shell-command-timed-out-and-session-terminated")
            ready, _, _ = select.select([fd], [], [], min(remain, 0.5))
            if ready:
                chunk = os.read(fd, 65536)
                if not chunk:
                    self.close()
                    raise RuntimeError("shell-exited-before-result")
                self.buffer.extend(chunk)
            elif self.process.poll() is not None:
                self.close()
                raise RuntimeError("shell-exited-before-result")

    def close(self):
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
