from pathlib import Path

from music_prod.sync_capture import FileSnapshot, _build_mux_cmd, _build_record_cmd, _is_stable


def test_build_record_cmd_includes_optional_video_size(tmp_path: Path) -> None:
    output_file = tmp_path / "raw.mp4"
    cmd = _build_record_cmd(
        ffmpeg_bin="ffmpeg",
        output_path=output_file,
        input_format="dshow",
        input_device="video=USB Camera",
        framerate=30,
        video_size="1280x720",
        video_codec="libx264",
        preset="veryfast",
        crf=23,
        video_pixel_format="yuv420p",
        video_profile="high",
        video_level="4.1",
    )

    assert cmd[:7] == ["ffmpeg", "-y", "-f", "dshow", "-framerate", "30", "-video_size"]
    assert str(output_file) in cmd


def test_build_mux_cmd_shortest_toggle(tmp_path: Path) -> None:
    raw_video = tmp_path / "raw.mp4"
    audio = tmp_path / "rec.wav"
    output = tmp_path / "mux.mp4"

    with_shortest = _build_mux_cmd(
        ffmpeg_bin="ffmpeg",
        raw_video=raw_video,
        audio_file=audio,
        output_file=output,
        audio_codec="aac",
        audio_bitrate="192k",
        shortest=True,
        trim_start_s=0.0,
    )
    without_shortest = _build_mux_cmd(
        ffmpeg_bin="ffmpeg",
        raw_video=raw_video,
        audio_file=audio,
        output_file=output,
        audio_codec="aac",
        audio_bitrate="192k",
        shortest=False,
        trim_start_s=0.0,
    )

    assert "-shortest" in with_shortest
    assert "-shortest" not in without_shortest


def test_build_mux_cmd_uses_accurate_trim_filter_when_trim_requested(tmp_path: Path) -> None:
    raw_video = tmp_path / "raw.mp4"
    audio = tmp_path / "rec.wav"
    output = tmp_path / "mux.mp4"

    cmd = _build_mux_cmd(
        ffmpeg_bin="ffmpeg",
        raw_video=raw_video,
        audio_file=audio,
        output_file=output,
        audio_codec="aac",
        audio_bitrate="192k",
        shortest=True,
        trim_start_s=1.25,
    )

    assert "-filter_complex" in cmd
    assert "[0:v]trim=start=1.250,setpts=PTS-STARTPTS[vtrim]" in cmd
    assert "[vtrim]" in cmd
    assert "copy" not in cmd


def test_is_stable_detects_size_or_mtime_changes(tmp_path: Path) -> None:
    audio_file = tmp_path / "recording.wav"
    audio_file.write_bytes(b"abc")
    baseline = FileSnapshot(size=3, mtime_ns=audio_file.stat().st_mtime_ns)

    assert _is_stable(audio_file, baseline)

    audio_file.write_bytes(b"abcd")
    assert not _is_stable(audio_file, baseline)
