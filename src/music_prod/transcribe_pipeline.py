from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    """Run a command, streaming stdout/stderr. Raises on failure."""
    print("\n>>", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}")


def which_or_die(exe: str, hint: str) -> str:
    path = shutil.which(exe)
    if not path:
        raise FileNotFoundError(f"Required executable not found on PATH: {exe}\nHint: {hint}")
    return path


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_url(s: str) -> bool:
    """
    Very small heuristic for URL detection.
    Accepts http(s) URLs.
    """
    try:
        u = urlparse(s)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


def download_audio_ytdlp(url: str, out_dir: Path, *, audio_format: str = "mp3") -> Path:
    """
    Download audio from a URL using yt-dlp and extract to audio_format.
    Returns the downloaded audio file path.
    """
    which_or_die("yt-dlp", "Install yt-dlp: uv add yt-dlp   (or: pip install -U yt-dlp)")
    ensure_dir(out_dir)

    # Output template: title.ext (yt-dlp will fill ext)
    output_template = out_dir / "%(title)s.%(ext)s"

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", audio_format,
        "-o", str(output_template),
        url,
    ]
    run(cmd)

    # Best-effort: find most recent file with that extension
    candidates = sorted(out_dir.glob(f"*.{audio_format}"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(
            f"yt-dlp completed but no *.{audio_format} was found in: {out_dir}"
        )
    return candidates[0]


def to_wav_ffmpeg(input_path: Path, wav_out: Path, *, sr: int = 44100, channels: int = 2) -> None:
    """Convert arbitrary media to WAV using ffmpeg."""
    which_or_die("ffmpeg", "Install ffmpeg (Windows): winget install ffmpeg")
    ensure_dir(wav_out.parent)

    cmd = [
        "ffmpeg",
        "-y",  # overwrite
        "-i", str(input_path),
        "-ar", str(sr),
        "-ac", str(channels),
        str(wav_out),
    ]
    run(cmd)


def run_demucs(input_wav: Path, out_dir: Path, *, model: str = "htdemucs", device: str = "auto") -> Path:
    """
    Run demucs separation. Returns the folder containing stems for this track.

    Demucs output layouts differ by version. Common layouts:
      1) out_dir / "separated" / model / <trackname>
      2) out_dir / model / <trackname>
    """
    which_or_die("demucs", "Ensure demucs is installed in this env: uv add demucs   (or: pip install -U demucs)")
    ensure_dir(out_dir)

    cmd = ["demucs", "-n", model, "-o", str(out_dir)]
    if device and device != "auto":
        cmd += ["-d", device]
    cmd += [str(input_wav)]
    run(cmd)

    track = input_wav.stem

    # Try known layouts first
    candidates = [
        out_dir / "separated" / model / track,
        out_dir / model / track,
    ]
    for p in candidates:
        if p.exists():
            return p

    # Fallback: find a folder under either base that looks like the track folder
    search_bases = [
        out_dir / "separated" / model,
        out_dir / model,
        out_dir,  # last resort
    ]

    for base in search_bases:
        if base.exists():
            # Prefer directories that start with the track name and contain wav outputs
            for d in sorted(base.glob(f"{track}*")):
                if d.is_dir() and any(d.glob("*.wav")):
                    return d

    raise FileNotFoundError(
        "Could not locate stems output folder. Looked in: "
        f"{out_dir / 'separated' / model} and {out_dir / model}"
    )



def run_basic_pitch(stem_wavs: Iterable[Path], midi_out_dir: Path) -> list[Path]:
    """Run basic-pitch on one or more stem wav files. Returns generated MIDI paths."""
    which_or_die("basic-pitch", "Ensure basic-pitch is installed in this env: uv add basic-pitch   (or: pip install -U basic-pitch)")
    ensure_dir(midi_out_dir)

    cmd = ["basic-pitch", str(midi_out_dir)]
    cmd += [str(p) for p in stem_wavs]
    run(cmd)

    # Basic Pitch naming convention: <input>_basic_pitch.mid in midi_out_dir
    midis: list[Path] = []
    for p in stem_wavs:
        expected = midi_out_dir / f"{p.stem}_basic_pitch.mid"
        if expected.exists():
            midis.append(expected)
        else:
            matches = list(midi_out_dir.glob(f"{p.stem}*basic*pitch*.mid*"))
            if matches:
                midis.append(matches[0])

    return midis


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Demucs → stems → Basic Pitch → MIDI (MuseScore import) pipeline"
    )
    ap.add_argument(
        "input",
        type=str,
        help="Local file path (mp4/mp3/wav/etc.) OR a URL (http/https) to download with yt-dlp",
    )
    ap.add_argument(
        "--workdir",
        type=str,
        default="transcribe_out",
        help="Base output folder (default: transcribe_out)",
    )
    ap.add_argument(
        "--model",
        type=str,
        default="htdemucs",
        help="Demucs model name (default: htdemucs)",
    )
    ap.add_argument(
        "--device",
        type=str,
        default="auto",
        help="Demucs device: auto | cpu | cuda (default: auto)",
    )
    ap.add_argument(
        "--stems",
        type=str,
        default="vocals,bass,other",
        help="Comma-separated stems to transcribe with Basic Pitch (default: vocals,bass,other). "
             "Typical demucs stems: vocals, drums, bass, other",
    )
    ap.add_argument(
        "--audio-format",
        type=str,
        default="mp3",
        help="If input is a URL, yt-dlp will download/extract audio to this format (default: mp3). "
             "Common: mp3, wav, flac, m4a",
    )
    ap.add_argument(
        "--download-dir",
        type=str,
        default="00_downloads",
        help="If input is a URL, store downloaded audio here (relative to workdir). Default: 00_downloads",
    )
    ap.add_argument(
        "--keep-wav",
        action="store_true",
        help="Keep the converted full-mix WAV file (default behavior is to keep it in workdir anyway).",
    )
    ap.add_argument(
        "--analyze-key",
        action="store_true",
        help="Analyze key/scale after transcription and write a report (default: off).",
    )
    ap.add_argument(
        "--key-source",
        default="midi",
        choices=["midi", "audio", "both"],
        help="Key detection source (default: midi).",
    )
    ap.add_argument(
        "--key-stems",
        default="bass,other",
        help="Comma-separated stems to use for key analysis (default: bass,other).",
    )
    args = ap.parse_args()

    workdir = Path(args.workdir).expanduser().resolve()
    raw_dir = ensure_dir(workdir / "00_input")
    stems_base = ensure_dir(workdir / "10_stems")
    midi_dir = ensure_dir(workdir / "20_midi")

    # 0) Resolve input: URL → download, else treat as local file
    if is_url(args.input):
        dl_dir = ensure_dir(workdir / args.download_dir)
        try:
            input_path = download_audio_ytdlp(args.input, dl_dir, audio_format=args.audio_format).resolve()
        except Exception as e:
            print(f"Failed to download audio via yt-dlp: {e}", file=sys.stderr)
            return 10
    else:
        input_path = Path(args.input).expanduser().resolve()
        if not input_path.exists():
            print(f"Input not found: {input_path}", file=sys.stderr)
            return 2

    # 1) Convert to wav (even if already wav, we normalize name/path)
    wav_path = raw_dir / f"{input_path.stem}.wav"
    if input_path.suffix.lower() == ".wav":
        shutil.copy2(input_path, wav_path)
    else:
        to_wav_ffmpeg(input_path, wav_path, sr=44100, channels=2)

    # 2) Demucs separation
    stems_dir = run_demucs(wav_path, stems_base, model=args.model, device=args.device)

    # 3) Choose stems to transcribe
    requested = [s.strip().lower() for s in args.stems.split(",") if s.strip()]
    stem_paths: list[Path] = []
    for stem in requested:
        p = stems_dir / f"{stem}.wav"
        if p.exists():
            stem_paths.append(p)
        else:
            print(f"Warning: requested stem not found: {p}")

    if not stem_paths:
        print("No stems found to transcribe. Check --stems and demucs output.", file=sys.stderr)
        return 3

    # 4) Basic Pitch transcription
    track_midi_out = ensure_dir(midi_dir / wav_path.stem)
    generated_midis = run_basic_pitch(stem_paths, track_midi_out)

    # 4.5) Optional: key/scale analysis
    if args.analyze_key:
        from music_prod.analysis import analyze_key_and_scale

        key_stems = [s.strip().lower() for s in args.key_stems.split(",") if s.strip()]

        midi_files: list[Path] = []
        audio_files: list[Path] = []

        if args.key_source in ("midi", "both"):
            # Prefer explicit mapping by stem name (matches your generated file naming)
            for stem in key_stems:
                midi_candidate = track_midi_out / f"{stem}.mid"
                if midi_candidate.exists():
                    midi_files.append(midi_candidate)
                else:
                    # Fallback: try to find any MIDI whose filename starts with the stem name
                    # (handles small naming differences)
                    matches = list(track_midi_out.glob(f"{stem}*.mid"))
                    if matches:
                        midi_files.append(matches[0])

        if args.key_source in ("audio", "both"):
            for stem in key_stems:
                audio_candidate = stems_dir / f"{stem}.wav"
                if audio_candidate.exists():
                    audio_files.append(audio_candidate)

        report = analyze_key_and_scale(
            out_dir=workdir,
            midi_files=midi_files if midi_files else None,
            audio_files=audio_files if audio_files else None,
        )

        # Console summary
        if report.get("results"):
            print("\nKey / Scale Analysis:")
            for r in report["results"]:
                conf = f" (confidence {r['confidence']:.2f})" if r.get("confidence") is not None else ""
                print(f" - {r['source']}: {r['key']}{conf} | scale: {', '.join(r['scale_degrees'])}")
        else:
            print("\nKey / Scale Analysis: no results (no suitable MIDI/audio inputs found).")


    # 5) Summary
    print("\n=== DONE ===")
    print(f"Source      : {args.input}")
    print(f"Resolved in : {input_path}")
    print(f"Input WAV   : {wav_path}")
    print(f"Stems dir   : {stems_dir}")
    print(f"MIDI out    : {track_midi_out}")
    if generated_midis:
        print("\nGenerated MIDI files:")
        for m in generated_midis:
            print(" -", m)
    else:
        print("\nNo MIDI files detected (Basic Pitch may still have output; check folder).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
