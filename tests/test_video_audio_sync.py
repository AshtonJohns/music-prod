from pathlib import Path

import numpy as np

from music_prod.video_sync.analysis import detect_offset
from music_prod.video_sync.sync import SyncPlan, choose_sync_plan


def _write_wav(path: Path, sample_rate: int, signal: np.ndarray) -> None:
    from scipy.io import wavfile

    wavfile.write(path, sample_rate, (signal * 32767).astype(np.int16))


def test_detect_offset_zero(tmp_path: Path) -> None:
    sr = 16_000
    t = np.arange(sr * 2) / sr
    tone = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    video = tone.copy()
    clean = tone.copy()
    video_wav = tmp_path / "video.wav"
    clean_wav = tmp_path / "clean.wav"
    _write_wav(video_wav, sr, video)
    _write_wav(clean_wav, sr, clean)

    result = detect_offset(
        video_wav=video_wav,
        clean_wav=clean_wav,
        analysis_sample_rate=sr,
        analysis_seconds=None,
        max_offset_seconds=2.0,
    )
    assert abs(result.offset_seconds) < 0.02


def test_detect_offset_positive_trim_case(tmp_path: Path) -> None:
    sr = 16_000
    clean = np.zeros(sr * 3, dtype=np.float32)
    clean[sr : sr + 8000] = np.sin(2 * np.pi * 300 * (np.arange(8000) / sr)).astype(np.float32)
    video = np.concatenate([np.zeros(2000, dtype=np.float32), clean[:-2000]])

    video_wav = tmp_path / "video.wav"
    clean_wav = tmp_path / "clean.wav"
    _write_wav(video_wav, sr, video)
    _write_wav(clean_wav, sr, clean)

    result = detect_offset(
        video_wav=video_wav,
        clean_wav=clean_wav,
        analysis_sample_rate=sr,
        analysis_seconds=None,
        max_offset_seconds=2.0,
    )
    assert result.offset_seconds > 0.10
    plan = choose_sync_plan(offset_seconds=result.offset_seconds, trim_copy_safe=True)
    assert plan.operation == "trim_start"


def test_detect_offset_negative_pad_case(tmp_path: Path) -> None:
    sr = 16_000
    clean = np.zeros(sr * 3, dtype=np.float32)
    clean[sr : sr + 8000] = np.sin(2 * np.pi * 260 * (np.arange(8000) / sr)).astype(np.float32)
    video = np.concatenate([clean[1800:], np.zeros(1800, dtype=np.float32)])

    video_wav = tmp_path / "video.wav"
    clean_wav = tmp_path / "clean.wav"
    _write_wav(video_wav, sr, video)
    _write_wav(clean_wav, sr, clean)

    result = detect_offset(
        video_wav=video_wav,
        clean_wav=clean_wav,
        analysis_sample_rate=sr,
        analysis_seconds=None,
        max_offset_seconds=2.0,
    )
    assert result.offset_seconds < -0.08
    plan = choose_sync_plan(offset_seconds=result.offset_seconds, trim_copy_safe=False)
    assert plan.operation == "pad_start"
    assert plan.needs_reencode


def test_choose_sync_plan_zero() -> None:
    plan = choose_sync_plan(offset_seconds=0.002, trim_copy_safe=True)
    assert plan == SyncPlan(
        operation="none",
        seconds=0.0,
        needs_reencode=False,
        reason="Offset is within epsilon tolerance.",
    )


def test_detect_offset_with_noise(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    sr = 16_000
    base = np.zeros(sr * 3, dtype=np.float32)
    base[6000:12000] = np.sin(2 * np.pi * 200 * np.arange(6000) / sr).astype(np.float32)
    clean = base.copy()
    video = np.concatenate([np.zeros(1200, dtype=np.float32), base[:-1200]])
    video += (rng.normal(0, 0.02, size=video.shape)).astype(np.float32)

    video_wav = tmp_path / "video.wav"
    clean_wav = tmp_path / "clean.wav"
    _write_wav(video_wav, sr, video)
    _write_wav(clean_wav, sr, clean)

    result = detect_offset(
        video_wav=video_wav,
        clean_wav=clean_wav,
        analysis_sample_rate=sr,
        analysis_seconds=None,
        max_offset_seconds=2.0,
    )
    assert result.offset_seconds > 0.05


def test_detect_offset_mismatched_sample_rates(tmp_path: Path) -> None:
    sr_video = 22_050
    sr_clean = 48_000
    duration_s = 2.5
    video_t = np.arange(int(sr_video * duration_s), dtype=np.float32) / sr_video
    clean_t = np.arange(int(sr_clean * duration_s), dtype=np.float32) / sr_clean
    video = (0.4 * np.sin(2 * np.pi * 330 * video_t)).astype(np.float32)
    clean = (0.4 * np.sin(2 * np.pi * 330 * clean_t)).astype(np.float32)

    video_wav = tmp_path / "video.wav"
    clean_wav = tmp_path / "clean.wav"
    _write_wav(video_wav, sr_video, video)
    _write_wav(clean_wav, sr_clean, clean)

    result = detect_offset(
        video_wav=video_wav,
        clean_wav=clean_wav,
        analysis_sample_rate=16_000,
        analysis_seconds=None,
        max_offset_seconds=1.0,
    )
    assert abs(result.offset_seconds) < 0.03
