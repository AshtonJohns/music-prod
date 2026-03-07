from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import librosa
import numpy as np
from scipy.signal import correlate, correlation_lags


def _require_exe(name: str, hint: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"{name} not found on PATH. {hint}")
    return path


def _run_checked(cmd: list[str]) -> None:
    print("\n>>", " ".join(cmd))
    subprocess.run(cmd, check=True)  # noqa: S603


def _default_camera_video_for_audio(cakewalk_audio: Path) -> Path:
    return cakewalk_audio.parent.parent / "Video" / "camera_raw.mp4"


def _extract_video_audio(
    *,
    ffmpeg_bin: str,
    video_file: Path,
    wav_out: Path,
    sample_rate: int,
) -> None:
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_file),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(wav_out),
    ]
    _run_checked(cmd)


def _load_mono(path: Path, sample_rate: int, analyze_seconds: float) -> np.ndarray:
    duration = None if analyze_seconds <= 0 else analyze_seconds
    data, _ = librosa.load(path, sr=sample_rate, mono=True, duration=duration)
    if data.size == 0:
        raise ValueError(f"No audio samples loaded from: {path}")
    return data.astype(np.float32, copy=False)


def _estimate_offset_seconds(
    *,
    camera_audio: np.ndarray,
    cakewalk_audio: np.ndarray,
    sample_rate: int,
    max_offset_seconds: float,
) -> float:
    # Normalize to reduce gain bias before cross-correlation.
    camera = camera_audio - float(np.mean(camera_audio))
    cakewalk = cakewalk_audio - float(np.mean(cakewalk_audio))

    camera_std = float(np.std(camera))
    cakewalk_std = float(np.std(cakewalk))
    if camera_std > 0:
        camera = camera / camera_std
    if cakewalk_std > 0:
        cakewalk = cakewalk / cakewalk_std

    corr = correlate(camera, cakewalk, mode="full", method="fft")
    lags = correlation_lags(camera.size, cakewalk.size, mode="full")

    if max_offset_seconds > 0:
        max_lag = int(max_offset_seconds * sample_rate)
        mask = np.abs(lags) <= max_lag
        if not np.any(mask):
            raise ValueError("No lag candidates available inside --max-offset-seconds window.")
        corr = corr[mask]
        lags = lags[mask]

    best_idx = int(np.argmax(np.abs(corr)))
    return float(lags[best_idx]) / float(sample_rate)


def _build_mux_cmd(
    *,
    ffmpeg_bin: str,
    camera_video: Path,
    cakewalk_audio: Path,
    output_file: Path,
    offset_seconds: float,
    audio_codec: str,
    audio_bitrate: str,
) -> list[str]:
    video_trim_s = max(0.0, -offset_seconds)
    audio_trim_s = max(0.0, offset_seconds)

    cmd = [ffmpeg_bin, "-y", "-i", str(camera_video)]
    if audio_trim_s > 0:
        cmd += ["-ss", f"{audio_trim_s:.3f}"]
    cmd += ["-i", str(cakewalk_audio)]

    if video_trim_s > 0:
        cmd += [
            "-filter_complex",
            f"[0:v]trim=start={video_trim_s:.3f},setpts=PTS-STARTPTS[vsync]",
            "-map",
            "[vsync]",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
        ]
    else:
        cmd += ["-map", "0:v", "-c:v", "copy"]

    cmd += [
        "-map",
        "1:a",
        "-c:a",
        audio_codec,
        "-b:a",
        audio_bitrate,
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_file),
    ]
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Synchronize a camera video with a Cakewalk WAV using cross-correlation, then mux "
            "video + Cakewalk audio."
        )
    )
    parser.add_argument("--cakewalk-audio", required=True, help="Path to Cakewalk .wav file.")
    parser.add_argument(
        "--camera-video",
        default=None,
        help=(
            "Path to camera video file. Default: <cakewalk_audio parent>/../Video/camera_raw.mp4 "
            "(example: projects/<song>/Video/camera_raw.mp4)."
        ),
    )
    parser.add_argument(
        "--output-file",
        default=None,
        help="Output mp4 path (default: next to camera video as synced_output.mp4).",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=16000,
        help="Resample rate for alignment analysis (default: 16000).",
    )
    parser.add_argument(
        "--analyze-seconds",
        type=float,
        default=120.0,
        help="Audio duration to analyze from each source (default: 120; 0 means full).",
    )
    parser.add_argument(
        "--max-offset-seconds",
        type=float,
        default=20.0,
        help="Search window for lag detection in seconds (default: 20; 0 means unrestricted).",
    )
    parser.add_argument("--audio-codec", default="aac", help="Muxed audio codec (default: aac).")
    parser.add_argument(
        "--audio-bitrate",
        default="192k",
        help="Muxed audio bitrate (default: 192k).",
    )

    args = parser.parse_args()

    if args.sample_rate <= 0:
        print("--sample-rate must be > 0", file=sys.stderr)
        return 2

    ffmpeg_bin = _require_exe("ffmpeg", "Install ffmpeg first (Windows: winget install ffmpeg).")

    cakewalk_audio = Path(args.cakewalk_audio).expanduser().resolve()
    if not cakewalk_audio.exists() or not cakewalk_audio.is_file():
        print(f"Cakewalk audio file not found: {cakewalk_audio}", file=sys.stderr)
        return 2

    if args.camera_video:
        camera_video = Path(args.camera_video).expanduser().resolve()
    else:
        camera_video = _default_camera_video_for_audio(cakewalk_audio)

    if not camera_video.exists() or not camera_video.is_file():
        print(f"Camera video file not found: {camera_video}", file=sys.stderr)
        return 2

    if args.output_file:
        output_file = Path(args.output_file).expanduser().resolve()
    else:
        output_file = camera_video.parent / "synced_output.mp4"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="music-prod-sync-") as tmpdir:
            extracted_audio = Path(tmpdir) / "camera_audio.wav"
            _extract_video_audio(
                ffmpeg_bin=ffmpeg_bin,
                video_file=camera_video,
                wav_out=extracted_audio,
                sample_rate=args.sample_rate,
            )

            camera_audio = _load_mono(extracted_audio, args.sample_rate, args.analyze_seconds)
            cakewalk = _load_mono(cakewalk_audio, args.sample_rate, args.analyze_seconds)
            offset_s = _estimate_offset_seconds(
                camera_audio=camera_audio,
                cakewalk_audio=cakewalk,
                sample_rate=args.sample_rate,
                max_offset_seconds=args.max_offset_seconds,
            )

            print(f"Detected offset (camera vs cakewalk): {offset_s:+.3f}s")
            if offset_s < 0:
                print(f"Camera starts earlier. Trimming {abs(offset_s):.3f}s from camera video.")
            elif offset_s > 0:
                print(f"Camera starts later. Trimming {abs(offset_s):.3f}s from Cakewalk audio.")
            else:
                print("No offset detected.")

            mux_cmd = _build_mux_cmd(
                ffmpeg_bin=ffmpeg_bin,
                camera_video=camera_video,
                cakewalk_audio=cakewalk_audio,
                output_file=output_file,
                offset_seconds=offset_s,
                audio_codec=args.audio_codec,
                audio_bitrate=args.audio_bitrate,
            )
            _run_checked(mux_cmd)
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        return 4

    print("\n=== DONE ===")
    print(f"Cakewalk audio: {cakewalk_audio}")
    print(f"Camera video : {camera_video}")
    print(f"Synced output: {output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
