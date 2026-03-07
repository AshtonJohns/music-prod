from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from music_prod.video_sync.analysis import detect_offset
from music_prod.video_sync.media import extract_reference_audio, probe_media
from music_prod.video_sync.report import build_report, write_report
from music_prod.video_sync.sync import apply_sync_plan, can_stream_copy_trim, choose_sync_plan
from music_prod.video_sync.utils import LOG, configure_logging, require_executable


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync-video-to-wav",
        description=(
            "Detect offset between MP4 embedded audio and external WAV, "
            "then create a corrected MP4 "
            "by trimming or padding the MP4 timeline only."
        ),
    )
    parser.add_argument(
        "--video",
        required=True,
        help="Input MP4 containing video + embedded reference audio.",
    )
    parser.add_argument("--audio", required=True, help="External WAV used as sync reference.")
    parser.add_argument("--output", required=True, help="Output corrected MP4 path.")
    parser.add_argument(
        "--report-json",
        default=None,
        help="Optional JSON report path (default: <output>.sync-report.json).",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=16000,
        help="Analysis sample rate (default: 16000).",
    )
    parser.add_argument(
        "--analysis-seconds",
        type=float,
        default=None,
        help="Limit analysis duration in seconds (default: full file).",
    )
    parser.add_argument(
        "--max-offset-seconds",
        type=float,
        default=120.0,
        help="Maximum lag search window during analysis (default: 120).",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.2,
        help="Warn when confidence drops below this threshold (default: 0.2).",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite output if it exists.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing output media.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    configure_logging(args.verbose)

    if args.sample_rate <= 0:
        print("--sample-rate must be > 0", file=sys.stderr)
        return 2
    if args.analysis_seconds is not None and args.analysis_seconds <= 0:
        print("--analysis-seconds must be > 0 when provided", file=sys.stderr)
        return 2
    if not 0.0 <= args.min_confidence <= 1.0:
        print("--min-confidence must be between 0 and 1", file=sys.stderr)
        return 2

    video_path = Path(args.video).expanduser().resolve()
    wav_path = Path(args.audio).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    report_path = (
        Path(args.report_json).expanduser().resolve()
        if args.report_json
        else output_path.with_suffix(output_path.suffix + ".sync-report.json")
    )

    if not video_path.exists() or not video_path.is_file():
        print(f"Video file not found: {video_path}", file=sys.stderr)
        return 2
    if not wav_path.exists() or not wav_path.is_file():
        print(f"WAV file not found: {wav_path}", file=sys.stderr)
        return 2
    if output_path.exists() and not args.force:
        print(f"Output already exists: {output_path} (use --force to overwrite)", file=sys.stderr)
        return 2
    if video_path == output_path:
        print("--output must be different from --video", file=sys.stderr)
        return 2

    ffmpeg_bin = require_executable(
        "ffmpeg",
        "Install ffmpeg first (Windows: winget install ffmpeg).",
    )
    ffprobe_bin = require_executable(
        "ffprobe",
        "Install ffmpeg first (Windows: winget install ffmpeg).",
    )

    try:
        video_probe = probe_media(ffprobe_bin, video_path)
        wav_probe = probe_media(ffprobe_bin, wav_path)
        if not video_probe.has_video:
            raise ValueError("Input --video does not contain a video stream.")
        if not video_probe.has_audio:
            raise ValueError("Input --video does not contain an audio stream.")
        if not wav_probe.has_audio:
            raise ValueError("Input --audio does not contain an audio stream.")

        with tempfile.TemporaryDirectory(prefix="video-sync-") as tmpdir:
            tmp_root = Path(tmpdir)
            video_ref_wav = tmp_root / "video_ref.wav"
            clean_ref_wav = tmp_root / "clean_ref.wav"

            extract_reference_audio(
                ffmpeg_bin=ffmpeg_bin,
                source=video_path,
                wav_out=video_ref_wav,
                sample_rate=args.sample_rate,
                dry_run=False,
            )
            extract_reference_audio(
                ffmpeg_bin=ffmpeg_bin,
                source=wav_path,
                wav_out=clean_ref_wav,
                sample_rate=args.sample_rate,
                dry_run=False,
            )
            detection = detect_offset(
                video_wav=video_ref_wav,
                clean_wav=clean_ref_wav,
                analysis_sample_rate=args.sample_rate,
                analysis_seconds=args.analysis_seconds,
                max_offset_seconds=args.max_offset_seconds,
            )

        trim_copy_safe = can_stream_copy_trim(
            ffprobe_bin=ffprobe_bin,
            video_path=video_path,
            trim_seconds=max(detection.offset_seconds, 0.0),
            probe=video_probe,
        )
        plan = choose_sync_plan(
            offset_seconds=detection.offset_seconds,
            trim_copy_safe=trim_copy_safe,
        )

        if detection.confidence < args.min_confidence:
            LOG.warning(
                "Low confidence match: %.3f < %.3f",
                detection.confidence,
                args.min_confidence,
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        apply_sync_plan(
            ffmpeg_bin=ffmpeg_bin,
            input_mp4=video_path,
            output_mp4=output_path,
            plan=plan,
            dry_run=args.dry_run,
        )

        report = build_report(
            video_file=video_path,
            wav_file=wav_path,
            output_file=output_path,
            detection=detection,
            plan=plan,
            dry_run=args.dry_run,
        )
        write_report(report_path, report)
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        return 4

    print(f"Offset: {detection.offset_seconds:+.3f}s ({detection.method})")
    print(f"Confidence: {detection.confidence:.3f}")
    print(f"Operation: {plan.operation} ({plan.seconds:.3f}s), reencode={plan.needs_reencode}")
    print(f"Output MP4: {output_path}")
    print(f"Report JSON: {report_path}")
    print("WAV input was not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
