from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _require_exe(name: str, hint: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"{name} not found on PATH. {hint}")
    return path


def _build_dshow_input_spec(input_device: str, audio_device: str | None) -> str:
    if "audio=" in input_device:
        return input_device
    if not input_device.startswith("video="):
        return input_device

    if audio_device:
        return f"{input_device}:audio={audio_device}"

    video_name = input_device.split("video=", maxsplit=1)[1]
    return f"{input_device}:audio={video_name}"


def _build_record_cmd(
    *,
    ffmpeg_bin: str,
    output_path: Path,
    input_format: str,
    input_device: str,
    input_vcodec: str | None,
    framerate: int,
    video_size: str | None,
    video_codec: str,
    preset: str,
    crf: int,
    video_pixel_format: str,
    video_profile: str,
    video_level: str,
    audio_codec: str,
    audio_bitrate: str,
) -> list[str]:
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        input_format,
    ]
    if input_vcodec:
        # dshow expects an input codec via -vcodec; some other backends use -input_format.
        input_codec_flag = "-vcodec" if input_format == "dshow" else "-input_format"
        cmd += [input_codec_flag, input_vcodec]
    cmd += [
        "-framerate",
        str(framerate),
    ]
    if video_size:
        cmd += ["-video_size", video_size]
    cmd += [
        "-i",
        input_device,
        "-c:v",
        video_codec,
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        video_pixel_format,
        "-profile:v",
        video_profile,
        "-level",
        video_level,
        "-c:a",
        audio_codec,
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return cmd


def _run_checked(cmd: list[str]) -> None:
    print("\n>>", " ".join(cmd))
    subprocess.run(cmd, check=True)  # noqa: S603


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record a single camera file with webcam video + webcam audio."
    )
    parser.add_argument(
        "--output-file",
        default=str(Path("capture_out") / "camera_raw.mp4"),
        help="Output video path (default: capture_out/camera_raw.mp4).",
    )
    parser.add_argument(
        "--input-format",
        default="dshow",
        help="ffmpeg input format (Windows: dshow, macOS: avfoundation, Linux: v4l2).",
    )
    parser.add_argument(
        "--input-device",
        default="video=c922 Pro Stream Webcam",
        help=(
            "ffmpeg input device string. On Windows dshow, use a video device like "
            "'video=c922 Pro Stream Webcam' (default: video=c922 Pro Stream Webcam)."
        ),
    )
    parser.add_argument(
        "--audio-device",
        default="Microphone (C922 Pro Stream Webcam)",
        help=(
            "Optional dshow audio device name (default: Microphone (C922 Pro Stream Webcam)). "
            "If omitted and --input-device is 'video=...', the same name is used for audio."
        ),
    )
    parser.add_argument(
        "--input-vcodec",
        default="mjpeg",
        help="Requested camera input codec for capture (default: mjpeg).",
    )
    parser.add_argument("--framerate", type=int, default=30, help="Capture FPS (default: 30).")
    parser.add_argument(
        "--video-size",
        default="1920x1080",
        help='Capture size (default: "1920x1080").',
    )
    parser.add_argument("--video-codec", default="libx264", help="Video codec (default: libx264).")
    parser.add_argument("--preset", default="slow", help="Video preset (default: slow).")
    parser.add_argument("--crf", type=int, default=14, help="Video quality CRF (default: 14).")
    parser.add_argument(
        "--video-pixel-format",
        default="yuv420p",
        help="Pixel format (default: yuv420p).",
    )
    parser.add_argument("--video-profile", default="high", help="H.264 profile (default: high).")
    parser.add_argument("--video-level", default="4.1", help="H.264 level (default: 4.1).")
    parser.add_argument("--audio-codec", default="aac", help="Audio codec (default: aac).")
    parser.add_argument("--audio-bitrate", default="320k", help="Audio bitrate (default: 320k).")

    args = parser.parse_args()

    if args.framerate <= 0:
        print("--framerate must be > 0", file=sys.stderr)
        return 2

    ffmpeg_bin = _require_exe("ffmpeg", "Install ffmpeg first (Windows: winget install ffmpeg).")

    input_device = args.input_device
    if args.input_format == "dshow":
        input_device = _build_dshow_input_spec(args.input_device, args.audio_device)

    output_file = Path(args.output_file).expanduser().resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    input_vcodec = args.input_vcodec if args.input_format == "dshow" else None

    cmd = _build_record_cmd(
        ffmpeg_bin=ffmpeg_bin,
        output_path=output_file,
        input_format=args.input_format,
        input_device=input_device,
        input_vcodec=input_vcodec,
        framerate=args.framerate,
        video_size=args.video_size,
        video_codec=args.video_codec,
        preset=args.preset,
        crf=args.crf,
        video_pixel_format=args.video_pixel_format,
        video_profile=args.video_profile,
        video_level=args.video_level,
        audio_codec=args.audio_codec,
        audio_bitrate=args.audio_bitrate,
    )

    print("Recording. Press Ctrl+C to stop.")
    try:
        _run_checked(cmd)
    except KeyboardInterrupt:
        print("\nStop requested.")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"ffmpeg exited with code {exc.returncode}", file=sys.stderr)
        return 3

    print(f"Saved: {output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
