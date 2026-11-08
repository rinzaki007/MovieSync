"""Tests for durable MovieSync data backup behavior."""
import os
import subprocess
import tarfile
from pathlib import Path


def test_backup_script_preserves_secrets_and_cards_but_skips_cache_and_logs(tmp_path):
    data = tmp_path / "data"
    output = tmp_path / "backups"
    (data / "cards").mkdir(parents=True)
    (data / "pansou" / "cache").mkdir(parents=True)
    (data / "logs").mkdir()
    (data / ".secret_key").write_text("keep-this", encoding="utf-8")
    (data / "subscriptions.json").write_text("[]", encoding="utf-8")
    (data / "cards" / "sample.py").write_text("# trusted card", encoding="utf-8")
    (data / "pansou" / "cache" / "large.cache").write_text("cache", encoding="utf-8")
    (data / "logs" / "app.log").write_text("log", encoding="utf-8")

    script = Path(__file__).resolve().parents[1] / "scripts" / "backup-data.sh"
    env = os.environ.copy()
    env["MOVIESYNC_DATA_DIR"] = str(data)
    result = subprocess.run(
        ["sh", str(script), str(output)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    archive_path = Path(result.stdout.strip())
    assert archive_path.is_file()
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
    assert "./.secret_key" in names
    assert "./subscriptions.json" in names
    assert "./cards/sample.py" in names
    assert not any("large.cache" in name or "app.log" in name for name in names)
