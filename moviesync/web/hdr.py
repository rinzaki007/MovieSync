"""On-demand HDR detection and bounded HDR-to-SDR HLS transcoding.

The original provider URL is kept in memory only. Transcodes are temporary,
limited to one active software encode by default, and expire automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
HDR_PRIMARIES = {"bt2020"}


def _binary(name: str) -> str | None:
    return shutil.which(name)


def probe_hdr(url: str, timeout: int = 12) -> bool | None:
    """Return True for HDR, False for confidently SDR, or None when unknown."""
    if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"}:
        return None
    ffprobe = _binary("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-rw_timeout", "10000000", "-select_streams", "v:0",
             "-show_entries", "stream=color_transfer,color_primaries,color_space",
             "-of", "json", url],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        if result.returncode != 0:
            return None
        streams = json.loads(result.stdout).get("streams", [])
        if not streams:
            return None
        stream = streams[0]
        transfer = str(stream.get("color_transfer") or "").lower()
        primaries = str(stream.get("color_primaries") or "").lower()
        if transfer in HDR_TRANSFERS or primaries in HDR_PRIMARIES:
            return True
        if transfer in {"bt709", "smpte170m", "bt470bg"} and primaries not in HDR_PRIMARIES:
            return False
        return None
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None


def tonemap_filter() -> str:
    """A software filter chain mapping PQ/HLG BT.2020 to SDR BT.709."""
    return (
        "zscale=transfer=linear:npl=100,"
        "format=gbrpf32le,"
        "tonemap=tonemap=hable:desat=0.1,"
        "zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=tv,"
        "format=yuv420p"
    )


@dataclass
class _Job:
    job_id: str
    directory: Path
    process: subprocess.Popen
    created_at: float
    url_hash: str


class HDRTranscoder:
    """Small in-memory job registry for temporary HLS transcodes."""

    def __init__(self, root: str | Path | None = None, max_jobs: int = 1, ttl: int = 3 * 3600):
        self.root = Path(root or os.environ.get("MOVIESYNC_HDR_CACHE_DIR", "/app/data/hdr-cache"))
        self.max_jobs = max(1, min(int(max_jobs), 2))
        self.ttl = max(300, int(ttl))
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()

    def _cleanup_locked(self) -> None:
        now = time.time()
        for key, job in list(self._jobs.items()):
            expired = now - job.created_at > self.ttl
            finished = job.process.poll() is not None
            playlist = job.directory / "index.m3u8"
            completed = finished and playlist.exists()
            if expired or (finished and not completed):
                if job.process.poll() is None:
                    job.process.terminate()
                shutil.rmtree(job.directory, ignore_errors=True)
                self._jobs.pop(key, None)
        self.root.mkdir(parents=True, exist_ok=True)
        # Remove abandoned directories left by a container restart.
        cutoff = now - self.ttl
        for entry in self.root.iterdir():
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff and entry.name not in self._jobs:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue

    def start(self, url: str) -> str | None:
        """Start or reuse an HLS transcode; None means safely fall back to source."""
        if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"}:
            return None
        ffmpeg = _binary("ffmpeg")
        if not ffmpeg:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        with self._lock:
            self._cleanup_locked()
            for job in self._jobs.values():
                if job.url_hash == digest and job.process.poll() is None:
                    return job.job_id
            active = sum(job.process.poll() is None for job in self._jobs.values())
            if active >= self.max_jobs:
                return None
            job_id = hashlib.sha256(f"{digest}:{time.time_ns()}".encode()).hexdigest()[:32]
            directory = Path(tempfile.mkdtemp(prefix=f"{job_id}-", dir=self.root))
            playlist = directory / "index.m3u8"
            segment_pattern = directory / "segment_%05d.ts"
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-rw_timeout", "15000000", "-i", url,
                "-map", "0:v:0", "-map", "0:a:0?",
                "-sn", "-vf", tonemap_filter(),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                "-f", "hls", "-hls_time", "4", "-hls_list_size", "0",
                "-hls_flags", "temp_file+independent_segments",
                "-hls_segment_filename", str(segment_pattern), str(playlist),
            ]
            try:
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True,
                )
            except OSError:
                shutil.rmtree(directory, ignore_errors=True)
                return None
            self._jobs[job_id] = _Job(job_id, directory, process, time.time(), digest)
            return job_id

    def ready(self, job_id: str, wait_seconds: float = 8.0) -> bool:
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while time.monotonic() < deadline:
            with self._lock:
                job = self._jobs.get(job_id)
                if not job:
                    return False
                playlist = job.directory / "index.m3u8"
                if playlist.exists() and any(job.directory.glob("segment_*.ts")):
                    return True
                if job.process.poll() is not None:
                    return False
            time.sleep(0.2)
        return False

    def file_path(self, job_id: str, filename: str) -> Path | None:
        # Only serve known HLS assets; never accept arbitrary filesystem paths.
        if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
            return None
        if filename != "index.m3u8" and not (
            filename.startswith("segment_") and filename.endswith(".ts")
            and filename[8:-3].isdigit()
        ):
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or time.time() - job.created_at > self.ttl:
                return None
            path = job.directory / filename
            return path if path.is_file() else None


transcoder = HDRTranscoder()
