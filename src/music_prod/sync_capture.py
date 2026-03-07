from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


@dataclass(frozen=True)
class FileSnapshot:
    size: int
    mtime_ns: int


def _require_exe(name: str, hint: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"{name} not found on PATH. {hint}")
    return path


def _snapshot(path: Path) -> FileSnapshot:
    stat = path.stat()
    return FileSnapshot(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _is_stable(path: Path, baseline: FileSnapshot) -> bool:
    current = _snapshot(path)
    return current == baseline


def _build_record_cmd(
    *,
    ffmpeg_bin: str,
    output_path: Path,
    input_format: str,
    input_device: str,
    framerate: int,
    video_size: str | None,
    video_codec: str,
    preset: str,
    crf: int,
    video_pixel_format: str,
    video_profile: str,
    video_level: str,
) -> list[str]:
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        input_format,
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
        str(output_path),
    ]
    return cmd


def _build_trim_cmd(
    *,
    ffmpeg_bin: str,
    raw_video: Path,
    output_file: Path,
    trim_start_s: float,
) -> list[str]:
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(raw_video),
    ]
    # Use an accurate trim path so pre-roll is removed even when the source
    # has sparse keyframes. This requires re-encoding the video stream.
    cmd += [
        "-filter_complex",
        f"[0:v]trim=start={max(0.0, trim_start_s):.3f},setpts=PTS-STARTPTS[vtrim]",
        "-map",
        "[vtrim]",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-movflags",
        "+faststart",
    ]
    cmd.append(str(output_file))
    return cmd


class _TargetFileWatcher(FileSystemEventHandler):
    def __init__(self, target_path: Path) -> None:
        super().__init__()
        self.target_path = target_path.resolve()
        self.seen = target_path.exists()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        event_path = Path(event.src_path).resolve()
        if event_path == self.target_path:
            self.seen = True


def _wait_for_target_file(
    *,
    target_file: Path,
    timeout_s: float,
    poll_interval_s: float,
) -> None:
    if target_file.exists():
        return

    parent = target_file.parent
    parent.mkdir(parents=True, exist_ok=True)

    watcher = _TargetFileWatcher(target_file)
    observer = Observer()
    observer.schedule(watcher, str(parent), recursive=False)
    observer.start()
    start = time.monotonic()
    try:
        while True:
            if watcher.seen and target_file.exists():
                return
            if timeout_s > 0 and (time.monotonic() - start) > timeout_s:
                raise TimeoutError(f"Timed out waiting for file creation: {target_file}")
            time.sleep(poll_interval_s)
    finally:
        observer.stop()
        observer.join(timeout=2.0)


def _wait_for_new_wav_file(
    *,
    audio_dir: Path,
    timeout_s: float,
    poll_interval_s: float,
) -> Path:
    start = time.monotonic()
    initial: set[Path] = set()
    if audio_dir.exists():
        initial = {p.resolve() for p in audio_dir.glob("*.wav") if p.is_file()}

    while True:
        if audio_dir.exists():
            current = {p.resolve() for p in audio_dir.glob("*.wav") if p.is_file()}
            new_files = current - initial
            if new_files:
                # If multiple files appear between polls, pick the most recently written.
                return max(new_files, key=lambda p: p.stat().st_mtime_ns)
        if timeout_s > 0 and (time.monotonic() - start) > timeout_s:
            raise TimeoutError(f"Timed out waiting for new .wav file in: {audio_dir}")
        time.sleep(poll_interval_s)


def _wait_for_file_inactivity(
    *,
    target_file: Path,
    inactivity_s: float,
    poll_interval_s: float,
    max_wait_s: float,
) -> None:
    baseline = _snapshot(target_file)
    last_change = time.monotonic()
    start = last_change

    while True:
        time.sleep(poll_interval_s)
        if not target_file.exists():
            raise FileNotFoundError(f"Audio file disappeared while waiting for inactivity: {target_file}")
        if _is_stable(target_file, baseline):
            if (time.monotonic() - last_change) >= inactivity_s:
                return
        else:
            baseline = _snapshot(target_file)
            last_change = time.monotonic()

        if max_wait_s > 0 and (time.monotonic() - start) > max_wait_s:
            raise TimeoutError(
                f"Timed out waiting for audio inactivity after {max_wait_s:.1f}s: {target_file}"
            )


def _start_recording(cmd: list[str]) -> subprocess.Popen[bytes]:
    print("\n>>", " ".join(cmd))
    return subprocess.Popen(  # noqa: S603
        cmd,
        stdin=subprocess.PIPE,
        stdout=None,
        stderr=None,
    )


def _ensure_recording_started(proc: subprocess.Popen[bytes], startup_grace_s: float = 0.75) -> None:
    """Fail fast if ffmpeg exits immediately (common with bad input device names)."""
    deadline = time.monotonic() + startup_grace_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                "ffmpeg recording failed to start. Check --input-format/--input-device "
                "(Windows example: run `ffmpeg -list_devices true -f dshow -i dummy`)."
            )
        time.sleep(0.05)


def _stop_recording(proc: subprocess.Popen[bytes], stop_timeout_s: float) -> None:
    if proc.poll() is not None:
        return

    if proc.stdin:
        try:
            proc.stdin.write(b"q\n")
            proc.stdin.flush()
        except OSError:
            pass

    try:
        proc.wait(timeout=stop_timeout_s)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def _run_checked(cmd: list[str]) -> None:
    print("\n>>", " ".join(cmd))
    subprocess.run(cmd, check=True)  # noqa: S603


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watch for an audio recording file, record camera video, stop on file inactivity, then trim."
    )
    audio_source_group = parser.add_mutually_exclusive_group(required=True)
    audio_source_group.add_argument("--audio-file", help="Path to recorder output file to watch.")
    audio_source_group.add_argument(
        "--cwp-audio",
        help="Path to Cakewalk project Audio folder; waits for a new .wav file to appear.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Directory for raw video and muxed output "
            "(default: capture_out, or <cwp parent>/Video when --cwp-audio is used)."
        ),
    )
    parser.add_argument(
        "--raw-video-name",
        default="camera_raw.mp4",
        help="Raw recorded video filename inside output dir (default: camera_raw.mp4).",
    )
    parser.add_argument(
        "--muxed-name",
        default="synced_output.mp4",
        help="Final trimmed video filename inside output dir (default: synced_output.mp4).",
    )

    parser.add_argument(
        "--wait-for-audio-timeout",
        type=float,
        default=0,
        help="Seconds to wait for audio file creation; 0 disables timeout (default: 0).",
    )
    parser.add_argument(
        "--inactivity-seconds",
        type=float,
        default=4.0,
        help="Stop recording when file size+mtime are unchanged this long (default: 4.0).",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=0.5,
        help="Polling interval for file checks (default: 0.5).",
    )
    parser.add_argument(
        "--max-record-seconds",
        type=float,
        default=0,
        help="Fail-safe max wait for inactivity after recording starts; 0 disables (default: 0).",
    )
    parser.add_argument(
        "--ffmpeg-stop-timeout",
        type=float,
        default=8.0,
        help="Seconds to wait for ffmpeg to stop gracefully (default: 8.0).",
    )

    parser.add_argument(
        "--input-format",
        default="dshow",
        help="ffmpeg input format for camera capture (Windows: dshow, macOS: avfoundation, Linux: v4l2).",
    )
    parser.add_argument(
        "--input-device",
        required=True,
        help="ffmpeg input device string (examples: video=USB Camera, 0, 0:none).",
    )
    parser.add_argument("--framerate", type=int, default=30, help="Camera capture FPS (default: 30).")
    parser.add_argument(
        "--video-size",
        default=None,
        help='Optional capture size (example: "1920x1080").',
    )
    parser.add_argument("--video-codec", default="libx264", help="Video codec for raw capture (default: libx264).")
    parser.add_argument("--preset", default="fast", help="Encoder preset (default: fast).")
    parser.add_argument("--crf", type=int, default=18, help="Encoder CRF quality (default: 18).")
    parser.add_argument(
        "--video-pixel-format",
        default="yuv420p",
        help="Pixel format for raw capture (default: yuv420p).",
    )
    parser.add_argument(
        "--video-profile",
        default="high",
        help="H.264 profile for raw capture (default: high).",
    )
    parser.add_argument(
        "--video-level",
        default="4.1",
        help="H.264 level for raw capture (default: 4.1).",
    )

    parser.add_argument(
        "--skip-mux",
        action="store_true",
        help="Skip final trim step; only produce raw camera video.",
    )
    args = parser.parse_args()

    if args.inactivity_seconds <= 0:
        print("--inactivity-seconds must be > 0", file=sys.stderr)
        return 2
    if args.poll_seconds <= 0:
        print("--poll-seconds must be > 0", file=sys.stderr)
        return 2

    ffmpeg_bin = _require_exe("ffmpeg", "Install ffmpeg first (Windows: winget install ffmpeg).")

    if args.audio_file:
        audio_file: Path | None = Path(args.audio_file).expanduser().resolve()
    else:
        audio_file = None

    cwp_audio_dir: Path | None = None
    if args.cwp_audio:
        cwp_audio_dir = Path(args.cwp_audio).expanduser().resolve()
        if not cwp_audio_dir.exists():
            print(f"--cwp-audio directory does not exist: {cwp_audio_dir}", file=sys.stderr)
            return 2
        if not cwp_audio_dir.is_dir():
            print(f"--cwp-audio must point to a directory: {cwp_audio_dir}", file=sys.stderr)
            return 2

    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
    elif cwp_audio_dir is not None:
        output_dir = cwp_audio_dir.parent / "Video"
    else:
        output_dir = Path("capture_out").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_video = output_dir / args.raw_video_name
    muxed_output = output_dir / args.muxed_name

    record_cmd = _build_record_cmd(
        ffmpeg_bin=ffmpeg_bin,
        output_path=raw_video,
        input_format=args.input_format,
        input_device=args.input_device,
        framerate=args.framerate,
        video_size=args.video_size,
        video_codec=args.video_codec,
        preset=args.preset,
        crf=args.crf,
        video_pixel_format=args.video_pixel_format,
        video_profile=args.video_profile,
        video_level=args.video_level,
    )
    print("Starting camera recording now (pre-roll enabled).")
    proc = _start_recording(record_cmd)
    record_started_at = time.monotonic()
    trim_start_s = 0.0
    audio_detected = False
    manual_stop_requested = False
    try:
        _ensure_recording_started(proc)
        if cwp_audio_dir is not None:
            print(f"Waiting for new .wav file in: {cwp_audio_dir}")
            audio_file = _wait_for_new_wav_file(
                audio_dir=cwp_audio_dir,
                timeout_s=args.wait_for_audio_timeout,
                poll_interval_s=args.poll_seconds,
            )
        else:
            assert audio_file is not None
            print(f"Waiting for audio file: {audio_file}")
            _wait_for_target_file(
                target_file=audio_file,
                timeout_s=args.wait_for_audio_timeout,
                poll_interval_s=args.poll_seconds,
            )

        assert audio_file is not None
        audio_detected = True
        trim_start_s = max(0.0, time.monotonic() - record_started_at)
        print(f"Audio trigger detected: {audio_file}")
        print(f"Trim lead-in: {trim_start_s:.3f}s")
        _wait_for_file_inactivity(
            target_file=audio_file,
            inactivity_s=args.inactivity_seconds,
            poll_interval_s=args.poll_seconds,
            max_wait_s=args.max_record_seconds,
        )
        print("Audio file became inactive. Stopping camera recording.")
    except KeyboardInterrupt:
        manual_stop_requested = True
        print("\nManual stop requested (Ctrl+C). Stopping camera recording.")
    except Exception as exc:
        print(f"Stopping recording due to error: {exc}", file=sys.stderr)
        _stop_recording(proc, args.ffmpeg_stop_timeout)
        return 4

    _stop_recording(proc, args.ffmpeg_stop_timeout)
    if proc.returncode not in (0, None):
        # On Ctrl+C, ffmpeg can exit non-zero even though MP4 was finalized.
        can_continue_after_manual_stop = (
            manual_stop_requested and audio_detected and raw_video.exists() and raw_video.stat().st_size > 0
        )
        if can_continue_after_manual_stop:
            print(
                f"ffmpeg recording exited with code {proc.returncode} after manual stop; "
                "continuing to trim finalized raw video.",
                file=sys.stderr,
            )
        else:
            print(f"ffmpeg recording exited with code: {proc.returncode}", file=sys.stderr)
            return 5

    if manual_stop_requested and not audio_detected:
        print("\n=== DONE (manual stop, no audio trigger) ===")
        print(f"Raw video: {raw_video}")
        print("Skipped trim because no audio trigger was detected.")
        return 0

    if args.skip_mux:
        print("\n=== DONE ===")
        print(f"Raw video: {raw_video}")
        return 0

    trim_cmd = _build_trim_cmd(
        ffmpeg_bin=ffmpeg_bin,
        raw_video=raw_video,
        output_file=muxed_output,
        trim_start_s=trim_start_s,
    )
    try:
        _run_checked(trim_cmd)
    except subprocess.CalledProcessError as exc:
        print(f"ffmpeg trim failed with code {exc.returncode}", file=sys.stderr)
        return 7

    print("\n=== DONE ===")
    print(f"Audio file: {audio_file}")
    print(f"Raw video : {raw_video}")
    print(f"Trimmed out: {muxed_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
