import json
from pathlib import Path
from unittest.mock import Mock, patch

from moviesync.web.hdr import HDRTranscoder, _Job, probe_hdr, tonemap_filter


def test_probe_hdr_detects_pq_transfer():
    completed = Mock(returncode=0, stdout=json.dumps({
        "streams": [{"color_transfer": "smpte2084", "color_primaries": "bt2020"}]
    }))
    with patch("moviesync.web.hdr._binary", return_value="/usr/bin/ffprobe"), patch(
        "moviesync.web.hdr.subprocess.run", return_value=completed
    ):
        assert probe_hdr("https://media.example/video.mp4") is True


def test_probe_hdr_detects_sdr_transfer():
    completed = Mock(returncode=0, stdout=json.dumps({
        "streams": [{"color_transfer": "bt709", "color_primaries": "bt709"}]
    }))
    with patch("moviesync.web.hdr._binary", return_value="/usr/bin/ffprobe"), patch(
        "moviesync.web.hdr.subprocess.run", return_value=completed
    ):
        assert probe_hdr("https://media.example/video.mp4") is False


def test_probe_hdr_returns_unknown_when_probe_unavailable():
    with patch("moviesync.web.hdr._binary", return_value=None):
        assert probe_hdr("https://media.example/video.mp4") is None
    assert probe_hdr("file:///etc/passwd") is None


def test_tonemap_filter_targets_sdr_bt709():
    chain = tonemap_filter()
    assert "tonemap=tonemap=hable" in chain
    assert "primaries=bt709:transfer=bt709:matrix=bt709" in chain
    assert chain.endswith("format=yuv420p")


def test_transcoder_only_serves_known_hls_files(tmp_path: Path):
    transcoder = HDRTranscoder(root=tmp_path)
    directory = tmp_path / "job-dir"
    directory.mkdir()
    playlist = directory / "index.m3u8"
    segment = directory / "segment_00000.ts"
    playlist.write_text("#EXTM3U\nsegment_00000.ts\n", encoding="utf-8")
    segment.write_bytes(b"segment")
    process = Mock()
    process.poll.return_value = None
    job = _Job("safe-job-id", directory, process, __import__("time").time(), "hash")
    transcoder._jobs[job.job_id] = job

    assert transcoder.file_path(job.job_id, "index.m3u8") == playlist
    assert transcoder.file_path(job.job_id, "segment_00000.ts") == segment
    assert transcoder.file_path(job.job_id, "../index.m3u8") is None
    assert transcoder.file_path(job.job_id, "secrets.txt") is None


def test_probe_hdr_returns_unknown_when_ffprobe_times_out():
    from subprocess import TimeoutExpired

    with patch("moviesync.web.hdr._binary", return_value="/usr/bin/ffprobe"), patch(
        "moviesync.web.hdr.subprocess.run", side_effect=TimeoutExpired(cmd="ffprobe", timeout=1)
    ):
        assert probe_hdr("https://media.example/video.mp4", timeout=1) is None
