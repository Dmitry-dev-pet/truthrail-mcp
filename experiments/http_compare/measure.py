"""Measure resident memory of two isolated, persistent localhost HTTP MCP servers.

Both servers expose the *same* five synthetic tool functions. The exact same
mcp==2.3.0 client from a separate harness process calls each via HTTP; only
SERVER child PID RSS/HWM is measured, not the client, test runner, or VPS.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import sys
import time

from experiments.http_compare.server import PORTS
from experiments.sdk_comparison.benchmark import CASE_ARGS, NAMES, REQUIRED, expected, normalize, schema

EXPECTED_VER = {"mcp2": "2.3.0", "fastmcp4": "4.1.0"}
IDLE_SECONDS = 5
CALL_CYCLES = 20
TIMEOUT = 30


def metrics(pid: int) -> dict[str, float]:
    """Kernel-reported memory numbers only; do not read cmdline or environ."""
    values = {}
    with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as f:
        for line in f:
            key = line.split(":", 1)[0]
            if key in {"VmRSS", "VmHWM", "VmSwap"}:
                value = line.split(":", 1)[1].strip().split(" ", 1)[0]
                values[key] = round(int(value) / 1024, 2)
    if not all(key in values for key in ("VmRSS", "VmHWM", "VmSwap")):
        raise AssertionError("memory_status_unavailable")
    return values


def ready(proc: subprocess.Popen, port: int) -> None:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError("server_exited")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return
        except (OSError, TimeoutError):
            time.sleep(0.12)
    raise AssertionError("server_startup_timeout")


async def call_fixture(port: int) -> tuple[bool, list[float]]:
    from mcp import Client
    latencies = []
    async with Client(f"http://127.0.0.1:{port}/mcp", raise_exceptions=True) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
        if set(listed) != set(NAMES):
            raise AssertionError("tool_name_drift")
        for name in NAMES:
            props, required = schema(listed[name])
            if not REQUIRED[name].issubset(props) or required != REQUIRED[name]:
                raise AssertionError("tool_schema_drift")
        for cycle in range(CALL_CYCLES + 1):
            for name, args in CASE_ARGS.items():
                t0 = time.perf_counter()
                actual = normalize(await client.call_tool(name, args))
                if actual != json.dumps(expected(name, args), sort_keys=True,
                                        ensure_ascii=False, separators=(",", ":")):
                    raise AssertionError("tool_result_drift")
                if cycle:
                    latencies.append((time.perf_counter() - t0) * 1000)
        try:
            reply = await client.call_tool("arbitrary_shell", {})
        except Exception:
            denied = True
        else:
            denied = bool(getattr(reply, "is_error", False))
        if not denied:
            raise AssertionError("unknown_tool_accepted")
    return True, latencies


def one(mode: str, interpreter: str) -> dict:
    if mode not in PORTS or not Path(interpreter).is_file():
        raise AssertionError("unsupported_sdk_or_interpreter")
    port = PORTS[mode]
    command = [
        interpreter, "-B", "-m", "experiments.http_compare.server", mode,
    ]
    process_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("RUNNER_TEMP", "/tmp"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    started = time.monotonic()
    proc = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[2],
                            env=process_env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=True)
    try:
        ready(proc, port)
        startup = time.monotonic() - started
        time.sleep(IDLE_SECONDS)
        idle = metrics(proc.pid)
        ok, timings = asyncio.run(asyncio.wait_for(call_fixture(port), timeout=TIMEOUT))
        loaded = metrics(proc.pid)
        time.sleep(IDLE_SECONDS)
        after = metrics(proc.pid)
        if proc.poll() is not None or not ok:
            raise AssertionError("persistent_process_unavailable")
        return {
            "sdk": mode,
            "sdk_version": EXPECTED_VER[mode],
            "python": platform.python_version(),
            "machine": platform.machine(),
            "verified": True,
            "synthetic": True,
            "transport": "localhost_streamable_http",
            "real_atlas_tested": False,
            "authentication_tested": False,
            "server_pid_reported": False,
            "tool_contract_verified": True,
            "unknown_tool_denied": True,
            "requests_measured": len(timings),
            "startup_s": round(startup, 2),
            "idle_rss_mib": idle["VmRSS"],
            "loaded_rss_mib": loaded["VmRSS"],
            "after_idle_rss_mib": after["VmRSS"],
            "peak_rss_mib": after["VmHWM"],
            "swap_mib": after["VmSwap"],
            "median_http_call_ms": round(statistics.median(timings), 2),
            "persistent_seconds": round(time.monotonic() - started, 2),
        }
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)


def main() -> int:
    # Executable locations are provided by the source-only isolated CI job.
    if len(sys.argv) != 3 or platform.system() != "Linux":
        print('{"verified":false,"reason":"invalid_runner"}')
        return 2
    try:
        first = one("mcp2", sys.argv[1])
        second = one("fastmcp4", sys.argv[2])
        if first["requests_measured"] != 100 or second["requests_measured"] != 100:
            raise AssertionError("wrong_sample_count")
    except Exception as exc:
        # No paths, URLs, HTTP bodies, stack traces, headers or untrusted output.
        print(json.dumps({"verified": False, "reason": type(exc).__name__},
                         separators=(",", ":")))
        return 2
    print("mcp2_http: " + json.dumps(first, sort_keys=True))
    print("fastmcp4_http: " + json.dumps(second, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
