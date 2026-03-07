from pathlib import Path

import numpy as np

from music_prod.video_audio_sync import (
    _build_mux_cmd,
    _default_camera_video_for_audio,
    _estimate_offset_seconds,
)


def test_default_camera_video_for_audio() -> None:
    audio_file = Path("C:/repo/projects/the_living_dead-ashton/Audio/the_living_dead-ashton.wav")
    result = _default_camera_video_for_audio(audio_file)
    assert result.parts[-4:] == ("projects", "the_living_dead-ashton", "Video", "camera_raw.mp4")


def test_estimate_offset_detects_camera_early() -> None:
    sr = 100
    cakewalk = np.array([0, 0, 1, 2, 1, 0, 0], dtype=np.float32)
    # camera starts 2 samples earlier than cakewalk
    camera = np.array([1, 2, 1, 0, 0, 0, 0], dtype=np.float32)
    offset = _estimate_offset_seconds(
        camera_audio=camera,
        cakewalk_audio=cakewalk,
        sample_rate=sr,
        max_offset_seconds=1.0,
    )
    assert round(offset, 2) == -0.02


def test_build_mux_cmd_trims_video_when_camera_early(tmp_path) -> None:
    cmd = _build_mux_cmd(
        ffmpeg_bin="ffmpeg",
        camera_video=tmp_path / "camera_raw.mp4",
        cakewalk_audio=tmp_path / "song.wav",
        output_file=tmp_path / "out.mp4",
        offset_seconds=-1.5,
        audio_codec="aac",
        audio_bitrate="192k",
    )
    assert "-filter_complex" in cmd
    assert "[0:v]trim=start=1.500,setpts=PTS-STARTPTS[vsync]" in cmd
