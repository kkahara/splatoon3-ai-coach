"""Gameplay clip timing, encoding, access, and source retention."""

import json
import subprocess
from pathlib import Path

import pytest
from public_site.clips import clip_argv, clip_path, extract_clips, has_clip, missing_clips
from test_public_site import _finish_video, _moment_entry, _sample_video, _write_index

# Values planted in the upload; none may survive into a clip.
_CANARIES = (
    'CanaryTitle',
    'canary.owner@example.com',
    '139.6917',
    'CanaryDevice',
    'CanaryCam',
    'CanaryMic',
    'CanaryChapter',
)
_FORMAT_TAGS = {'major_brand', 'minor_version', 'compatible_brands', 'encoder'}
_STREAM_TAGS = {'language', 'handler_name', 'vendor_id', 'encoder'}
_METADATA_FLAGS = ['-map_metadata', '-1', '-map_chapters', '-1']
_CHAPTERS = """;FFMETADATA1
[CHAPTER]
TIMEBASE=1/1000
START=0
END=2000
title=CanaryChapter
"""


def test_clip_window_and_unsafe_id(tmp_path: Path) -> None:
    """Beginning clips shorten rather than extending beyond anchor plus three."""
    for anchor, start, duration in [
        (20, '5.000', '18.000'), (10, '0.000', '13.000'), (2, '0.000', '5.000'),
    ]:
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
    ("fps", "anchor", "duration"), [(60, 6, 9), (30, 2, 5), (60, 9, 10), (30, 18, 18)],
)
def test_real_clip_preserves_720p60_and_window(
    tmp_path: Path, fps: int, anchor: int, duration: int,
) -> None:
    """Decode a real 1080p60 source to verify timing, dimensions and cadence."""
    source = tmp_path / 'source.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        f'color=c=blue:s=1920x1080:r={fps}:d={10 if anchor < 15 else anchor + 3}',
        '-c:v', 'libx264',
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


def _tagged_source(path: Path) -> bytes:
    """Upload with the title, email, GPS, device names and chapters a phone may add."""
    chapters = path.with_name('chapters.txt')
    chapters.write_text(_CHAPTERS, encoding='utf-8')
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y',
        '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=4',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4',
        '-i', str(chapters),
        '-map', '0:v', '-map', '1:a', '-map_chapters', '2',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
        '-movflags', 'use_metadata_tags',
        '-metadata', 'title=CanaryTitle',
        '-metadata', 'comment=canary.owner@example.com',
        '-metadata', 'location=+35.6895+139.6917/',
        '-metadata', 'com.apple.quicktime.location.ISO6709=+35.6895+139.6917+040.000/',
        '-metadata', 'com.apple.quicktime.model=CanaryDevice',
        '-metadata:s:v:0', 'handler_name=CanaryCam',
        '-metadata:s:a:0', 'handler_name=CanaryMic',
        str(path),
    ], check=True, capture_output=True)
    return path.read_bytes()


def _probe_metadata(path: Path) -> dict:
    result = subprocess.run([
        'ffprobe', '-v', 'error',
        '-show_entries', 'format_tags:stream_tags:chapters', '-of', 'json', str(path),
    ], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def _inherited(path: Path) -> list[str]:
    """Planted values visible to ffprobe or present anywhere in the file bytes."""
    probed = json.dumps(_probe_metadata(path))
    raw = path.read_bytes()
    return [c for c in _CANARIES if c in probed or c.encode() in raw]


def _assert_no_inherited_metadata(clip: Path) -> None:
    probed = _probe_metadata(clip)
    assert set(probed.get('format', {}).get('tags', {})) <= _FORMAT_TAGS
    for stream in probed['streams']:
        tags = stream.get('tags', {})
        assert set(tags) <= _STREAM_TAGS
        assert tags.get('handler_name') in {None, 'VideoHandler', 'SoundHandler'}
    assert probed.get('chapters', []) == []
    assert _inherited(clip) == []


def _decoded(path: Path, stream: str) -> list[str]:
    """Per-frame MD5 of the decoded video or audio stream."""
    result = subprocess.run([
        'ffmpeg', '-v', 'error', '-i', str(path),
        '-map', f'0:{stream}:0', '-f', 'framemd5', '-',
    ], check=True, capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if not line.startswith('#')]


def test_clip_argv_strips_metadata(tmp_path: Path) -> None:
    """Global, stream and chapter metadata are dropped for every clip."""
    argv = clip_argv(tmp_path / 'in.mp4', tmp_path / 'out.mp4', 20)
    start = argv.index('-map_metadata')
    assert argv[start:start + 4] == _METADATA_FLAGS
    assert start < argv.index(str(tmp_path / 'out.mp4'))


def test_metadata_strip_keeps_pixels_and_audio(tmp_path: Path) -> None:
    """Only metadata changes: decoded frames and samples match the previous encoding."""
    source = tmp_path / 'source.mp4'
    _tagged_source(source)
    assert set(_inherited(source)) == set(_CANARIES)
    clip, previous = tmp_path / 'clip.mp4', tmp_path / 'previous.mp4'
    argv = clip_argv(source, clip, 3.0)
    start = argv.index('-map_metadata')
    without_strip = argv[:start] + argv[start + 4:-1] + [str(previous)]
    subprocess.run(argv, check=True, capture_output=True)
    subprocess.run(without_strip, check=True, capture_output=True)
    assert _inherited(previous)
    _assert_no_inherited_metadata(clip)
    for stream in ('v', 'a'):
        decoded = _decoded(clip, stream)
        assert decoded
        assert decoded == _decoded(previous, stream)


def test_worker_clip_has_no_source_metadata(tmp_path: Path) -> None:
    """Clips made when coaching completes carry none of the upload's metadata."""
    video = _tagged_source(tmp_path / 'in.mp4')
    client, _ = _finish_video(
        tmp_path, video, [_moment_entry('death_2', 2, 1)], dev_mode=False,
    )
    row = client.app.state.store.list_submissions()[0]
    assert row.status == 'complete'
    clip = clip_path(Path(row.analysis_dir), 'death_2')
    assert clip is not None and clip.is_file()
    _assert_no_inherited_metadata(clip)


def test_repaired_clip_has_no_source_metadata(tmp_path: Path, monkeypatch) -> None:
    """Media filled in on a later view carry none of the upload's metadata."""
    with monkeypatch.context() as patch:
        patch.setattr('public_site.worker.extract_clips', lambda *a, **k: None)
        patch.setattr('public_site.worker.extract_frames', lambda *a, **k: None)
        video = _tagged_source(tmp_path / 'in.mp4')
        client, token = _finish_video(
            tmp_path, video, [_moment_entry('death_2', 2, 1)], dev_mode=False,
        )
    row = client.app.state.store.list_submissions()[0]
    analysis = Path(row.analysis_dir)
    assert row.status == 'complete'
    assert not has_clip(analysis, 'death_2')
    assert client.get(f'/api/submissions/{token}/moments/0/frame').status_code == 200
    clip = clip_path(analysis, 'death_2')
    assert clip is not None and clip.is_file()
    _assert_no_inherited_metadata(clip)
