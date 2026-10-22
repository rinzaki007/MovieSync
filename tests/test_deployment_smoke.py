"""Regression tests for the read-only deployment smoke script."""
from __future__ import annotations

import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


def _write_fake_curl(path: Path) -> None:
    path.write_text(
        """#!/bin/sh
output=
url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --request) shift 2 ;;
        --output) output=$2; shift 2 ;;
        --write-out|--data|--header|--connect-timeout|--max-time) shift 2 ;;
        --silent|--show-error) shift ;;
        *) url=$1; shift ;;
    esac
done
case "$url" in
    */healthz) status=200; body='{"status":"ok","scheduler":"disabled"}' ;;
    */api/search-candidates)
        status="${SMOKE_TEST_AUTH_STATUS:-401}"
        body='{"message":"auth required"}'
        ;;
    *) status=200; body='ok' ;;
esac
printf '%s' "$body" > "$output"
printf '%s' "$status"
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_smoke_test(tmp_path: Path, auth_status: str = "401") -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_curl(fake_bin / "curl")
    script = Path(__file__).resolve().parents[1] / "scripts" / "smoke-test.sh"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SMOKE_TEST_AUTH_STATUS"] = auth_status
    env["SMOKE_HTTP_CLIENT"] = "curl"
    env["MOVIESYNC_BASE_URL"] = "http://moviesync.test:8099"
    return subprocess.run(
        ["sh", str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def test_deployment_smoke_script_passes_expected_read_only_checks(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "smoke-test.sh"
    syntax = subprocess.run(
        ["sh", "-n", str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert syntax.returncode == 0, syntax.stderr

    result = _run_smoke_test(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GET   /healthz" in result.stdout
    assert "POST  /api/search-candidates" in result.stdout
    assert "All smoke checks passed" in result.stdout


def test_deployment_smoke_script_fails_if_search_api_is_public(tmp_path):
    result = _run_smoke_test(tmp_path, auth_status="200")
    assert result.returncode != 0
    assert "expected 401" in result.stderr


def test_deployment_smoke_script_python_client_uses_real_local_http_server():
    class SmokeHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (
                b'{"status":"ok","scheduler":"disabled"}'
                if self.path == "/healthz"
                else b"ok"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            status = 401 if self.path == "/api/search-candidates" else 404
            body = b'{"message":"auth required"}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SmokeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env["SMOKE_HTTP_CLIENT"] = "python"
        env["MOVIESYNC_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
        script = Path(__file__).resolve().parents[1] / "scripts" / "smoke-test.sh"
        result = subprocess.run(
            ["sh", str(script)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "All smoke checks passed" in result.stdout
