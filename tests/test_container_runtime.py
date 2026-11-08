"""Container runtime hardening contract tests."""
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_entrypoint_shell_syntax_and_non_root_supervision():
    result = subprocess.run(["sh", "-n", str(ROOT / "docker-entrypoint.sh")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    content = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    assert "gosu" in content
    assert "MOVIESYNC_UID" in content
    assert "/api/health" in content
    assert "PANSOU_FAILURES" in content


def test_dockerfile_installs_gosu_and_defaults_to_non_root():
    content = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "gosu" in content
    assert "MOVIESYNC_RUN_AS_NON_ROOT=true" in content
    assert "MOVIESYNC_UID=10001" in content
