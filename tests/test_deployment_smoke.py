"""Regression tests for the read-only deployment smoke script."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_fake_curl(path: Path) -> None:
    path.write_text(
        """#!/bin/sh
method=GET
output=
url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --request) method=$2; shift 2 ;;
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



def _write_fake_python(path: Path) -> None:
    path.write_text(
        """#!/bin/sh
method=$2
url=$3
output=$4
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

def _run_smoke_test(
    tmp_path: Path,
    auth_status: str = "401",
    client: str = "curl",
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    if client == "curl":
        _write_fake_curl(fake_bin / "curl")
    elif client == "python":
        _write_fake_python(fake_bin / "python3")
    else:
        raise ValueError(f"unsupported test HTTP client: {client}")
    script = Path(__file__).resolve().parents[1] / "scripts" / "smoke-test.sh"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SMOKE_TEST_AUTH_STATUS"] = auth_status
    env["SMOKE_HTTP_CLIENT"] = client
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


def test_deployment_smoke_script_supports_python_http_client(tmp_path):
    result = _run_smoke_test(tmp_path, client="python")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All smoke checks passed" in result.stdout


def test_deployment_smoke_script_fails_if_search_api_is_public(tmp_path):
    result = _run_smoke_test(tmp_path, auth_status="200")
    assert result.returncode != 0
    assert "expected 401" in result.stderr
