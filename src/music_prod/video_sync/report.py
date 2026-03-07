from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from music_prod.video_sync.analysis import OffsetResult
from music_prod.video_sync.sync import SyncPlan


@dataclass(frozen=True)
class SyncReport:
    video_file: str
    wav_file: str
    output_file: str
    offset_seconds: float
    confidence: float
    method: str
    operation: str
    operation_seconds: float
    needs_reencode: bool
    reason: str
    dry_run: bool


def build_report(
    *,
    video_file: Path,
    wav_file: Path,
    output_file: Path,
    detection: OffsetResult,
    plan: SyncPlan,
    dry_run: bool,
) -> SyncReport:
    return SyncReport(
        video_file=str(video_file),
        wav_file=str(wav_file),
        output_file=str(output_file),
        offset_seconds=detection.offset_seconds,
        confidence=detection.confidence,
        method=detection.method,
        operation=plan.operation,
        operation_seconds=plan.seconds,
        needs_reencode=plan.needs_reencode,
        reason=plan.reason,
        dry_run=dry_run,
    )


def write_report(path: Path, report: SyncReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(report)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
