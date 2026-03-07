from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from music_prod.video_sync.utils import run_capture, run_checked


@dataclass(frozen=True)
class StreamInfo:
    codec_type: str
    codec_name: str | None
    duration: float | None
    start_time: float | None
    sample_rate: int | None
    channels: int | None
    width: int | None
    height: int | None
    r_frame_rate: str | None


@dataclass(frozen=True)
class MediaProbe:
    path: Path
    format_duration: float
    streams: tuple[StreamInfo, ...]

    @property
    def video_stream(self) -> StreamInfo | None:
        return next((s for s in self.streams if s.codec_type == "video"), None)

    @property
    def audio_stream(self) -> StreamInfo | None:
        return next((s for s in self.streams if s.codec_type == "audio"), None)

    @property
    def has_video(self) -> bool:
        return self.video_stream is not None

    @property
    def has_audio(self) -> bool:
        return self.audio_stream is not None


def _safe_float(value: object) -> float | None:
    if value in (None, "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    if value in (None, "N/A"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def probe_media(ffprobe_bin: str, path: Path) -> MediaProbe:
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        (
            "format=duration:"
            "stream=codec_type,codec_name,duration,start_time,sample_rate,channels,"
            "width,height,r_frame_rate"
        ),
        "-of",
        "json",
        str(path),
    ]
    payload = json.loads(run_capture(cmd).stdout)
    duration = _safe_float(payload.get("format", {}).get("duration")) or 0.0
    streams: list[StreamInfo] = []
    for raw in payload.get("streams", []):
        streams.append(
            StreamInfo(
                codec_type=str(raw.get("codec_type", "")),
                codec_name=raw.get("codec_name"),
                duration=_safe_float(raw.get("duration")),
                start_time=_safe_float(raw.get("start_time")),
                sample_rate=_safe_int(raw.get("sample_rate")),
                channels=_safe_int(raw.get("channels")),
                width=_safe_int(raw.get("width")),
                height=_safe_int(raw.get("height")),
                r_frame_rate=raw.get("r_frame_rate"),
            )
        )
    return MediaProbe(path=path, format_duration=duration, streams=tuple(streams))


def extract_reference_audio(
    *,
    ffmpeg_bin: str,
    source: Path,
    wav_out: Path,
    sample_rate: int,
    dry_run: bool,
) -> None:
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(wav_out),
    ]
    if source.suffix.lower() == ".mp4":
        cmd.insert(4, "-vn")
    run_checked(cmd, dry_run=dry_run)


def probe_video_keyframe(
    *,
    ffprobe_bin: str,
    video_path: Path,
    trim_seconds: float,
    tolerance_seconds: float,
) -> bool:
    start = max(0.0, trim_seconds - 2.0)
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-skip_frame",
        "nokey",
        "-read_intervals",
        f"{start:.3f}%+4",
        "-show_entries",
        "frame=best_effort_timestamp_time",
        "-of",
        "json",
        str(video_path),
    ]
    payload = json.loads(run_capture(cmd).stdout)
    for frame in payload.get("frames", []):
        ts = _safe_float(frame.get("best_effort_timestamp_time"))
        if ts is None:
            continue
        if abs(ts - trim_seconds) <= tolerance_seconds:
            return True
    return False
