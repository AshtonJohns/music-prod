from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from music_prod.video_sync.media import MediaProbe, probe_video_keyframe
from music_prod.video_sync.utils import run_checked


@dataclass(frozen=True)
class SyncPlan:
    operation: str
    seconds: float
    needs_reencode: bool
    reason: str


def choose_sync_plan(
    *,
    offset_seconds: float,
    trim_copy_safe: bool,
    epsilon_seconds: float = 0.010,
) -> SyncPlan:
    if abs(offset_seconds) <= epsilon_seconds:
        return SyncPlan(
            operation="none",
            seconds=0.0,
            needs_reencode=False,
            reason="Offset is within epsilon tolerance.",
        )
    if offset_seconds > 0:
        return SyncPlan(
            operation="trim_start",
            seconds=offset_seconds,
            needs_reencode=not trim_copy_safe,
            reason=(
                "MP4 leads WAV and must be trimmed at start."
                if trim_copy_safe
                else "MP4 leads WAV but trim point is not a keyframe; using re-encode."
            ),
        )
    return SyncPlan(
        operation="pad_start",
        seconds=abs(offset_seconds),
        needs_reencode=True,
        reason="MP4 lags WAV and must be delayed with prepended black/silence.",
    )


def can_stream_copy_trim(
    *,
    ffprobe_bin: str,
    video_path: Path,
    trim_seconds: float,
    probe: MediaProbe,
) -> bool:
    if trim_seconds <= 0:
        return True
    if not probe.video_stream:
        return False
    fps = 30.0
    if probe.video_stream.r_frame_rate and probe.video_stream.r_frame_rate != "0/0":
        num_s, den_s = probe.video_stream.r_frame_rate.split("/")
        num = float(num_s)
        den = float(den_s)
        if den > 0:
            fps = num / den
    tolerance = max(1.0 / fps, 0.004)
    return probe_video_keyframe(
        ffprobe_bin=ffprobe_bin,
        video_path=video_path,
        trim_seconds=trim_seconds,
        tolerance_seconds=tolerance,
    )


def _build_copy_trim_cmd(
    *,
    ffmpeg_bin: str,
    input_mp4: Path,
    output_mp4: Path,
    trim_seconds: float,
) -> list[str]:
    return [
        ffmpeg_bin,
        "-y",
        "-ss",
        f"{trim_seconds:.3f}",
        "-i",
        str(input_mp4),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        str(output_mp4),
    ]


def _build_reencode_trim_cmd(
    *,
    ffmpeg_bin: str,
    input_mp4: Path,
    output_mp4: Path,
    trim_seconds: float,
) -> list[str]:
    return [
        ffmpeg_bin,
        "-y",
        "-i",
        str(input_mp4),
        "-filter_complex",
        (
            f"[0:v]trim=start={trim_seconds:.6f},setpts=PTS-STARTPTS[v];"
            f"[0:a]atrim=start={trim_seconds:.6f},asetpts=PTS-STARTPTS[a]"
        ),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_mp4),
    ]


def _build_pad_cmd(
    *,
    ffmpeg_bin: str,
    input_mp4: Path,
    output_mp4: Path,
    pad_seconds: float,
) -> list[str]:
    delay_ms = int(round(pad_seconds * 1000.0))
    return [
        ffmpeg_bin,
        "-y",
        "-i",
        str(input_mp4),
        "-filter_complex",
        (
            f"[0:v]tpad=start_duration={pad_seconds:.6f}:start_mode=add,setsar=1[v];"
            f"[0:a]adelay={delay_ms}:all=1[a]"
        ),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_mp4),
    ]


def apply_sync_plan(
    *,
    ffmpeg_bin: str,
    input_mp4: Path,
    output_mp4: Path,
    plan: SyncPlan,
    dry_run: bool,
) -> None:
    if plan.operation == "none":
        run_checked(
            [
                ffmpeg_bin,
                "-y",
                "-i",
                str(input_mp4),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output_mp4),
            ],
            dry_run=dry_run,
        )
        return

    if plan.operation == "trim_start":
        cmd = (
            _build_reencode_trim_cmd(
                ffmpeg_bin=ffmpeg_bin,
                input_mp4=input_mp4,
                output_mp4=output_mp4,
                trim_seconds=plan.seconds,
            )
            if plan.needs_reencode
            else _build_copy_trim_cmd(
                ffmpeg_bin=ffmpeg_bin,
                input_mp4=input_mp4,
                output_mp4=output_mp4,
                trim_seconds=plan.seconds,
            )
        )
        run_checked(cmd, dry_run=dry_run)
        return

    if plan.operation == "pad_start":
        run_checked(
            _build_pad_cmd(
                ffmpeg_bin=ffmpeg_bin,
                input_mp4=input_mp4,
                output_mp4=output_mp4,
                pad_seconds=plan.seconds,
            ),
            dry_run=dry_run,
        )
        return

    raise ValueError(f"Unsupported plan operation: {plan.operation}")
