"""Gameplay clip timing, encoding, access, and source retention."""

import json
import subprocess
from pathlib import Path

import pytest
from public_site.clips import clip_argv, clip_path, extract_clips, missing_clips
from test_public_site import _finish_video, _moment_entry, _sample_video, _write_index


def test_clip_window_and_unsafe_id(tmp_path: Path) -> None:
    """Beginning clips shorten rather than extending beyond anchor plus three."""
    for anchor, start, duration in [(20, '15.000', '8.000'), (2, '0.000', '5.000')]:
        argv = clip_argv(tmp_path / 'in.mp4', tmp_path / 'out.mp4', anchor)
        assert argv[argv.index('-ss') + 1] == start
        assert argv[argv.index('-t') + 1] == duration
    assert clip_path(tmp_path, '../escape') is None


def test_failed_clip_is_retryable(tmp_path: Path) -> None:
    """Failed writes never publish a partial clip and can be retried."""
    _write_index(tmp_path, [_moment_entry('death_10', 10, 1)])
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')

    def fail(argv: list[str]) -> int:
        Path(argv[-1]).write_bytes(b'partial')
        return 1

    extract_clips(tmp_path, source, run=fail)
    assert missing_clips(tmp_path)
    assert not list((tmp_path / 'public_clips').iterdir())

    def succeed(argv: list[str]) -> int:
        Path(argv[-1]).write_bytes(b'complete')
        return 0

    extract_clips(tmp_path, source, run=succeed)
    assert not missing_clips(tmp_path)


@pytest.mark.parametrize(
    ("fps", "anchor", "duration"), [(60, 6, 8), (30, 2, 5), (60, 9, 6)],
)
def test_real_clip_preserves_720p60_and_window(
    tmp_path: Path, fps: int, anchor: int, duration: int,
) -> None:
    """Decode a real 1080p60 source to verify timing, dimensions and cadence."""
    source = tmp_path / 'source.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        f'color=c=blue:s=1920x1080:r={fps}:d=10', '-c:v', 'libx264',
        '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(source),
    ], check=True, capture_output=True)
    _write_index(tmp_path, [_moment_entry('death_6', anchor, 1)])
    extract_clips(tmp_path, source)
    dest = clip_path(tmp_path, 'death_6')
    result = subprocess.run([
        'ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(dest),
    ], check=True, capture_output=True, text=True)
    stream = json.loads(result.stdout)['streams'][0]
    assert (stream['width'], stream['height']) == (1280, 720)
    assert stream['avg_frame_rate'] == f'{fps}/1'
    assert float(stream['duration']) == pytest.approx(duration, abs=0.04)


def test_clip_served_with_range_after_source_deleted(tmp_path: Path) -> None:
    """A completed clip survives source deletion and uses guest authorization."""
    video = _sample_video(tmp_path / 'in.mp4')
    client, token = _finish_video(
        tmp_path, video, [_moment_entry('death_1', 1, 1)], dev_mode=False,
    )
    view = client.get(f'/api/submissions/{token}').json()
    assert view['result']['moments'][0]['clip'] is True
    row = client.app.state.store.list_submissions()[0]
    assert client.app.state.store.get_video(row.id).object_key == ''
    url = f'/api/submissions/{token}/moments/0/clip'
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers['content-type'] == 'video/mp4'
    partial = client.get(url, headers={'Range': 'bytes=0-31'})
    assert partial.status_code == 206
    assert partial.content == response.content[:32]
    assert client.get(url.replace(token, 'invalid')).status_code == 404
    assert client.get(url.replace('/0/', '/-1/')).status_code == 404


def test_source_retained_when_clip_extraction_fails(tmp_path: Path, monkeypatch) -> None:
    """A failed clip never causes premature source deletion."""
    monkeypatch.setattr('public_site.worker.extract_clips', lambda *a, **k: None)
    video = _sample_video(tmp_path / 'in.mp4')
    client, _ = _finish_video(
        tmp_path, video, [_moment_entry('death_1', 1, 1)], dev_mode=False,
    )
    row = client.app.state.store.list_submissions()[0]
    assert row.status == 'complete'
    assert client.app.state.store.get_video(row.id).object_key
